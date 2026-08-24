"""The wizard and the first-run routing, as real widgets.

The flow itself is proven headless in `tests/unit/test_migration_flow.py`. What can only be
checked here is that the routing actually runs at startup, that each case ends up in the
right state, and that the dialog builds its pages against a real libadwaita rather than
against what the author assumed libadwaita would accept.

Toolkit imports go inside the test functions, as everywhere in this tier: importing Gtk at
module scope makes collection itself fail on a machine with no display.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

APP_VERSION = "0.0.0-test"
APP_ID = "io.github.danielbaldwin47.Hyprtweaker.Test"

CONF = "general {\n    gaps_in = 5\n}\n"


def build_window(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A window over a session pointed at a throwaway config root, with no compositor."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    Adw.init()
    app = Adw.Application(application_id=APP_ID)

    def no_compositor():  # type: ignore[no-untyped-def]
        raise NoInstance("no compositor in the test tier")

    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    return MainWindow(session, application=app), session


class TestFirstRunRouting:
    def test_a_fresh_machine_gets_a_working_entrypoint(self, tmp_path: Path) -> None:
        from hyprtweaker.engine.migration.detect import ConfigKind

        window, session = build_window(tmp_path)

        detection = window.route_first_run()

        assert detection.kind is ConfigKind.FRESH
        assert session.paths.entrypoint.is_file()

    def test_a_legacy_tree_leaves_the_session_read_only(self, tmp_path: Path) -> None:
        """Settings show but cannot be saved until the offered import is accepted."""
        from hyprtweaker.engine.migration.detect import ConfigKind
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(CONF, encoding="utf-8")

        window, session = build_window(tmp_path)
        detection = window.route_first_run()

        assert detection.kind is ConfigKind.LEGACY_CONF
        assert not session.paths.entrypoint.exists()
        assert session.offline_reason

    def test_routing_never_writes_over_a_foreign_lua(self, tmp_path: Path) -> None:
        """The outcome ADR-0009 forbids outright, asserted at the level that could do it."""
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        original = "hl.config({ general = { gaps_in = 7 } })\n"
        paths.entrypoint.write_text(original, encoding="utf-8")

        window, _ = build_window(tmp_path)
        window.route_first_run()

        assert paths.entrypoint.read_text(encoding="utf-8") == original


class TestTheWizardBuilds:
    def test_the_detect_page_names_the_file_it_found(self, tmp_path: Path) -> None:
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(CONF, encoding="utf-8")

        window, _ = build_window(tmp_path)
        dialog = window.show_migration()

        assert "hyprland.conf" in _text_under(dialog)

    def test_the_preview_page_shows_the_report_before_anything_is_written(
        self, tmp_path: Path
    ) -> None:
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(CONF, encoding="utf-8")

        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")

        assert "Nothing has been written yet" in _text_under(dialog)
        assert not paths.entrypoint.exists()


class TestExport:
    def test_the_menu_offers_import_and_export(self, tmp_path: Path) -> None:
        window, _ = build_window(tmp_path)

        from hyprtweaker.ui.shell.window import EXPORT_ACTION, IMPORT_ACTION

        assert window.lookup_action(IMPORT_ACTION) is not None
        assert window.lookup_action(EXPORT_ACTION) is not None

    def test_exporting_writes_a_standalone_file(self, tmp_path: Path) -> None:
        window, _ = build_window(tmp_path)
        window.route_first_run()
        target = tmp_path / "exported.lua"

        window._write_export(target)

        text = target.read_text(encoding="utf-8")
        assert text.startswith("-- Hyprland config exported")
        assert "__host_require" in text


# --- widget-tree helpers ---------------------------------------------------------------------


def _walk(widget):  # type: ignore[no-untyped-def]
    yield widget
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _text_under(dialog) -> str:  # type: ignore[no-untyped-def]
    """Every label, title and subtitle in the dialog, as one blob to assert against."""
    import gi

    gi.require_version("Adw", "1")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Adw, Gtk

    parts: list[str] = []
    for widget in _walk(dialog):
        if isinstance(widget, Gtk.Label):
            parts.append(widget.get_label() or "")
        if isinstance(widget, Adw.PreferencesGroup | Adw.ActionRow):
            parts.append(widget.get_title() or "")
        if isinstance(widget, Adw.PreferencesGroup):
            parts.append(widget.get_description() or "")
        if isinstance(widget, Adw.ActionRow):
            parts.append(widget.get_subtitle() or "")
    return "\n".join(parts)


def _click(dialog, label: str) -> None:  # type: ignore[no-untyped-def]
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    for widget in _walk(dialog):
        if isinstance(widget, Gtk.Button) and widget.get_label() == label:
            widget.emit("clicked")
            return
    raise AssertionError(f"no button labelled {label!r} in the dialog")
