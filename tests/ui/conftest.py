"""Gating for the UI smoke tier.

This tier runs only where GTK4 + libadwaita and a usable display are present
(ADR-0011 testing tiers; spec #48 seam 5). Everywhere else it skips rather than
fails, so the engine tiers stay runnable on a bare machine.

Two constraints shape how the gate is applied, and they pull against each other:

* It must not import ``gi`` at collection time, or a machine without PyGObject
  gets a collection *error* instead of a skip. That is why the test modules keep
  their toolkit imports inside the test functions.
* The tests must still be collected. A module-level skip collects nothing, and
  pytest exits 5 ("no tests ran") for an empty run, which ``meson test`` reads as
  a failure.

So the gate marks collected items as skipped. Living in conftest means each new
``tests/ui`` module inherits it instead of repeating it.

Set ``HYPRTWEAKER_REQUIRE_UI=1`` to turn the skip into a hard failure. CI sets it
on the job that installs GTK, so a broken install surfaces as a red build rather
than a green one that quietly skipped everything.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

UI_TESTS_DIR = Path(__file__).parent


def ui_unavailable() -> str | None:
    """Return why this tier cannot run here, or None when it can."""
    try:
        import gi
    except ImportError:
        return "PyGObject (gi) is not installed"

    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Adw", "1")
    except ValueError as exc:
        return f"GTK4 / libadwaita typelibs unavailable: {exc}"

    from gi.repository import Gdk, Gtk

    # Gtk.init_check() alone is not enough: on GTK4 it can report success while
    # no display was ever opened, and the failure only surfaces later as a
    # RuntimeError from the first widget. The opened display is the real signal.
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        return "no usable display"

    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    reason = ui_unavailable()
    if reason is None:
        return

    # This hook is global: even though it lives in tests/ui/conftest.py, pytest
    # hands it every collected item in the run. Skipping the lot would silently
    # disable the engine tiers, so match on path.
    ui_items = [item for item in items if UI_TESTS_DIR in Path(item.path).parents]
    if not ui_items:
        return

    if os.environ.get("HYPRTWEAKER_REQUIRE_UI") == "1":
        # pytest.exit rather than raise: a bare exception here surfaces as an
        # INTERNALERROR traceback, which buries the one line explaining why.
        pytest.exit(
            f"HYPRTWEAKER_REQUIRE_UI=1 but the UI smoke tier cannot run: {reason}",
            returncode=1,
        )

    skip = pytest.mark.skip(reason=f"UI smoke tier: {reason}")
    for item in ui_items:
        item.add_marker(skip)
