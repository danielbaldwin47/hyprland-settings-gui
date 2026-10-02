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

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

APP_VERSION = "0.0.0-test"
APP_ID = "io.github.danielbaldwin47.Hyprtweaker.Test"

CONF = "general {\n    gaps_in = 5\n}\n"


def build_window(tmp_path: Path, live: object = None):  # type: ignore[no-untyped-def]
    """A window over a session pointed at a throwaway config root, with no compositor.

    `live` is the `LiveHyprland` the session believes is running, if any.
    """
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
        read_live=lambda: live,
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

    def test_a_hyprland_without_lua_gets_its_banner_not_the_offer(self, tmp_path: Path) -> None:
        """Below 0.56 every config is hyprlang, and converting it would leave a Lua file
        the running compositor cannot read: the Banner says what is needed instead (#176)."""
        from hyprtweaker.engine.ipc import LiveHyprland
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(CONF, encoding="utf-8")

        live = LiveHyprland("0.55.0", ({"name": "general:border_size"},))
        window, session = build_window(tmp_path, live)
        window.route_first_run()
        session.start()
        window.sync()

        assert window._banner.get_revealed()
        assert window._banner.get_title() == (
            "Hyprland 0.55.0 is running, and this app needs Hyprland 0.56 or newer"
            " — settings are read-only."
        )
        assert window._banner.get_button_label() in ("", None)
        assert session.live is False
        assert not paths.entrypoint.exists()

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


class TestTheRescueRow:
    """The one instruction a locked-out user types from a TTY, as the dialog renders it.

    Asserted here rather than only in the engine tier because both failure modes are things
    only the real widget shows: a line meant for the *other* import path, and the report's
    Markdown arriving in a Row that renders backticks and asterisks literally (#131).
    """

    def test_the_legacy_path_offers_the_command_that_removes_the_generated_file(
        self, tmp_path: Path
    ) -> None:
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(CONF, encoding="utf-8")

        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")

        shown = _text_under(dialog)
        assert "rm ~/.config/hypr/hyprland.lua" in shown
        assert ".bak" not in shown

    def test_the_lua_path_offers_the_command_that_restores_the_backup(
        self, tmp_path: Path
    ) -> None:
        """Built from the widget rather than driven through the dialog: the Lua path's
        preview needs an evaluation consent this tier has no compositor to ask for, and the
        thing under test is what the group renders, not how the user reaches it."""
        from hyprtweaker.engine.paths import ConfigPaths
        from hyprtweaker.ui.dialogs.migration import _rescue_group

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.entrypoint.write_text("hl.config({ general = {} })\n", encoding="utf-8")

        window, _ = build_window(tmp_path)
        flow = window.migration_flow()
        flow.detect()

        shown = _text_under(_rescue_group(flow.rescue_command))
        assert "mv ~/.config/hypr/hyprland.lua.bak ~/.config/hypr/hyprland.lua" in shown
        assert "rm ~/.config/hypr/hyprland.lua" not in shown

    def test_the_row_shows_a_command_rather_than_the_report_markdown(
        self, tmp_path: Path
    ) -> None:
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(CONF, encoding="utf-8")

        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")

        shown = _text_under(dialog)
        assert "**If Hyprland will not start:**" not in shown
        assert "`rm" not in shown


class TestTheMigrationClient:
    """The wizard's `reload full-reset` goes to the Session's compositor, never the ambient one.

    A sandboxed app, or a Harness test, hands its Session a nested instance; a client built
    from the environment would reload whatever compositor the shell names (#201).
    """

    def test_the_client_talks_to_the_instance_the_session_was_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        import re
        import socket

        from hyprtweaker.engine.ipc import Instance, IpcError
        from hyprtweaker.engine.paths import ConfigPaths
        from hyprtweaker.session import Session
        from hyprtweaker.ui.shell.window import MainWindow

        # The ambient compositor: a live socket the environment names. Short path: a unix
        # socket path is capped at 108 bytes, and xdist lengthens tmp_path.
        (tmp_path / "r" / "hypr" / "a").mkdir(parents=True)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "r"))
        monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "a")
        given = Instance(tmp_path / "nested")

        with socket.socket(socket.AF_UNIX) as ambient:
            ambient.bind(str(tmp_path / "r" / "hypr" / "a" / ".socket.sock"))
            session = Session(
                spawn=lambda coro: coro.close(),
                paths=ConfigPaths.rooted_at(tmp_path),
                app_version=APP_VERSION,
                connect=lambda: given,
            )
            flow = MainWindow(session).migration_flow()

            assert flow.client is not None
            with pytest.raises(IpcError, match=re.escape(str(given.command_socket))):
                asyncio.run(flow.client.configerrors())


FOREIGN_LUA = "hl.config({ general = { gaps_in = 7 } })\n"


def _gaps(size: int):  # type: ignore[no-untyped-def]
    """`general:gaps_in` as the model holds it: four equal sides."""
    from hyprtweaker.engine.model.values import CssGaps

    return CssGaps(size, size, size, size)


def _foreign_root(root: Path, source: str = FOREIGN_LUA) -> Path:
    """A config dir holding a `hyprland.lua` with no Manifest: detection says foreign."""
    from hyprtweaker.engine.paths import ConfigPaths

    paths = ConfigPaths.rooted_at(root)
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    paths.entrypoint.write_text(source, encoding="utf-8")
    return paths.entrypoint


class TestReadingAForeignLua:
    """Reading a `hyprland.lua` the app did not write runs it, so the wizard asks (#190)."""

    def test_convert_asks_before_running_the_file_and_read_it_reaches_preview(
        self, tmp_path: Path
    ) -> None:
        entrypoint = _foreign_root(tmp_path)
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()

        _click(dialog, "Convert...")

        assert _page_title(dialog) == "Read your config"
        assert _descriptions(dialog) == [
            "Reading your hyprland.lua means running it once in a sandbox: no commands run, "
            "no files change."
        ]
        assert "Could not read the configuration" not in _text_under(dialog)
        assert dialog.get_default_widget().get_label() == "Not now"

        _click(dialog, "Read it")

        assert _page_title(dialog) == "Preview"
        assert dialog._flow.preview.model.get("general:gaps_in") == _gaps(7)
        assert entrypoint.read_text(encoding="utf-8") == FOREIGN_LUA

    def test_not_now_closes_the_wizard_having_read_nothing(self, tmp_path: Path) -> None:
        _foreign_root(tmp_path)
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        closed: list[bool] = []
        dialog.connect("closed", lambda _dialog: closed.append(True))
        _click(dialog, "Convert...")

        _click(dialog, "Not now")

        assert closed == [True]
        assert dialog._flow.preview is None

    def test_a_config_built_from_a_pipe_runs_its_command_only_when_asked_to(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "ran"
        command = f"touch {marker}; echo 5"
        _foreign_root(
            tmp_path,
            f'local f = io.popen("{command}")\n'
            'hl.config({ general = { gaps_in = tonumber(f:read("*a")) } })\n',
        )
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")
        _click(dialog, "Read it")

        assert _page_title(dialog) == "Commands"
        assert _row_titles(dialog) == [command]
        assert dialog.get_default_widget().get_label() == "Not now"
        run = _button(dialog, "Run them and read")
        assert not run.has_css_class("suggested-action")
        assert not marker.exists()

        _click(dialog, "Run them and read")

        assert marker.exists()
        assert _page_title(dialog) == "Preview"
        assert dialog._flow.preview.model.get("general:gaps_in") == _gaps(5)
        assert dialog.get_default_widget() is None

    def test_every_wizard_run_asks_again(self, tmp_path: Path) -> None:
        _foreign_root(tmp_path)
        window, _ = build_window(tmp_path)
        first = window.show_migration()
        _click(first, "Convert...")
        _click(first, "Read it")
        assert _page_title(first) == "Preview"
        first.close()

        second = window.show_migration()
        _click(second, "Convert...")

        assert _page_title(second) == "Read your config"
        assert not (tmp_path / "state" / "hyprtweaker" / "prefs.json").exists()

    def test_an_app_written_config_opens_no_wizard(self, tmp_path: Path) -> None:
        from hyprtweaker.engine.migration.detect import ConfigKind

        build_window(tmp_path / "app")[0].route_first_run()  # writes the app's own config
        app_window, _ = build_window(tmp_path / "app")
        _foreign_root(tmp_path / "foreign")
        foreign_window, _ = build_window(tmp_path / "foreign")

        assert app_window.route_first_run().kind is ConfigKind.APP_GENERATED
        assert foreign_window.route_first_run().kind is ConfigKind.FOREIGN_LUA
        _drain_main_loop()

        assert app_window.get_visible_dialog() is None
        assert _page_title(foreign_window.get_visible_dialog()) == "Detect"


class TestImportAChosenFile:
    """Import... reads the file the user chose, not the one detection found."""

    def test_a_chosen_lua_opens_on_the_consent_page_and_reads_that_file(
        self, tmp_path: Path
    ) -> None:
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(CONF, encoding="utf-8")
        chosen = tmp_path / "chosen.lua"
        chosen.write_text(FOREIGN_LUA, encoding="utf-8")
        window, _ = build_window(tmp_path)

        dialog = window.show_migration(chosen)

        assert _page_title(dialog) == "Read your config"
        assert str(chosen) in _text_under(dialog)
        _click(dialog, "Read it")
        assert _page_title(dialog) == "Preview"
        assert dialog._flow.preview.detection.source == chosen
        assert dialog._flow.preview.model.get("general:gaps_in") == _gaps(7)

    def test_a_chosen_conf_is_the_file_convert_reads(self, tmp_path: Path) -> None:
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(CONF, encoding="utf-8")
        chosen = tmp_path / "chosen.conf"
        chosen.write_text("general {\n    gaps_in = 9\n}\n", encoding="utf-8")
        window, _ = build_window(tmp_path)
        dialog = window.show_migration(chosen)

        _click(dialog, "Convert...")

        assert dialog._flow.preview.detection.source == chosen
        assert dialog._flow.preview.model.get("general:gaps_in") == _gaps(9)


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


def _visible(dialog):  # type: ignore[no-untyped-def]
    """The page the user is looking at. Pages underneath stay in the widget tree."""
    return dialog._view.get_visible_page()


def _page_title(dialog) -> str:  # type: ignore[no-untyped-def]
    return str(_visible(dialog).get_title())


def _of_type(dialog, kind):  # type: ignore[no-untyped-def]
    return [widget for widget in _walk(_visible(dialog)) if isinstance(widget, kind)]


def _descriptions(dialog) -> list[str]:  # type: ignore[no-untyped-def]
    from gi.repository import Adw

    return [group.get_description() for group in _of_type(dialog, Adw.PreferencesGroup)]


def _row_titles(dialog) -> list[str]:  # type: ignore[no-untyped-def]
    from gi.repository import Adw

    return [row.get_title() for row in _of_type(dialog, Adw.ActionRow)]


def _button(dialog, label: str):  # type: ignore[no-untyped-def]
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    for widget in _of_type(dialog, Gtk.Button):
        if widget.get_label() == label:
            return widget
    raise AssertionError(f"no button labelled {label!r} on the visible page")


def _click(dialog, label: str) -> None:  # type: ignore[no-untyped-def]
    _button(dialog, label).emit("clicked")


def _drain_main_loop() -> None:
    """Run what `GLib.idle_add` queued, such as the first-run offer."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.iteration(False):
        pass
