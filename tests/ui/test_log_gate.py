"""A UI test fails when the toolkit complains while it runs, and says what it said (#228).

`_log_gate_cases.py` runs in a child pytest on the tier's own conftest, so its failures are
the gate's report, read here, and never this run's.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = "tests/ui/_log_gate_cases.py"
QUIET_CHILD = ("-p", "no:cacheprovider", "-p", "no:xdist", "-p", "no:warnings")


def section(report: str, heading: str) -> str:
    """The text pytest printed under `heading` (a test's name, or `ERROR at ...`)."""
    header = re.search(rf"^_+ {re.escape(heading)} _+\n", report, re.M)
    assert header, f"no section {heading!r} in:\n{report}"
    body = report[header.end() :]
    return re.split(r"^(?:_{3,}|-{3,}|={3,}) ", body, maxsplit=1, flags=re.M)[0].strip("\n")


def test_each_complaint_fails_its_test_and_the_report_names_it() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", CASES, "-q", "-rfE", "--color=no", *QUIET_CHILD],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = result.stdout
    swallowed = "Python exception in a callback, printed and swallowed:\nTraceback"

    assert "4 failed, 2 passed, 1 error" in report, report + result.stderr
    assert section(report, "test_removing_a_child_the_box_does_not_hold") == (
        "GTK, libadwaita or GLib complained during call (1):\n"
        "Gtk-CRITICAL: gtk_box_remove: assertion"
        " 'gtk_widget_get_parent (child) == (GtkWidget *)box' failed"
    )
    handler = section(report, "test_a_signal_handler_that_raises")
    assert handler.startswith(
        f"GTK, libadwaita or GLib complained during call (1):\n{swallowed}"
    )
    assert handler.endswith("ValueError: the handler broke")
    idle = section(report, "test_an_idle_that_raises")
    assert idle.startswith(f"GTK, libadwaita or GLib complained during call (1):\n{swallowed}")
    assert idle.endswith("ValueError: the idle broke")
    assert section(report, "test_notifying_a_property_the_object_lacks") == (
        "GTK, libadwaita or GLib complained during call (1):\n"
        "GLib-GObject-CRITICAL: g_object_notify:"
        " object class 'GObject' has no property named 'no-such-property'"
    )
    assert section(
        report, "ERROR at teardown of test_a_fixture_that_complains_on_teardown"
    ) == (
        "GTK, libadwaita or GLib complained during teardown (1):\n"
        "Gtk-CRITICAL: gtk_box_remove: assertion"
        " 'gtk_widget_get_parent (child) == (GtkWidget *)box' failed"
    )
