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


@pytest.fixture(autouse=True)
def sandboxed_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every UI test at a throwaway config dir and away from any live compositor.

    Autouse and unconditional, because the machine most likely to run this tier is a
    developer's own Hyprland box: `HyprtweakerApplication` builds a `Session` over
    `ConfigPaths.default()` and `Instance.current()`, and a test that boots the app would
    otherwise attach to the user's running session and their real `~/.config/hypr`.
    Read-only today, but "the test suite cannot reach your config" should be a property of
    the tier rather than of what the code currently happens to do.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)


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


# trylast: pytest applies -k/-m deselection in its own copy of this hook, so
# running after it means `items` is the final selection. Otherwise `-k` picking
# only engine tests would still see UI items here and trip the gate below.
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # This hook is global: even though it lives in tests/ui/conftest.py, pytest
    # hands it every collected item in the run. Skipping the lot would silently
    # disable the engine tiers, so match on path first. `item.path` is typed
    # optional, and a pathless item from some future plugin should not become
    # the INTERNALERROR this gate exists to avoid.
    ui_items = [
        item for item in items if item.path is not None and UI_TESTS_DIR in item.path.parents
    ]

    # Selecting no UI test at all is not a failure, whatever REQUIRE_UI says --
    # and returning here also keeps `ui_unavailable()` from opening a display
    # during collection for runs that never touch the UI.
    if not ui_items:
        return

    reason = ui_unavailable()
    if reason is None:
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
