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
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import main_loop
import pytest
from started_app import started_application

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

APP_VERSION = "0.0.0-test"

CONF = "general {\n    gaps_in = 5\n}\n"

READING = "Reading"
"""The progress page's title, shown while a read runs (#216)."""

READER_THREAD = "hyprtweaker: reading a config"
"""The name the wizard gives its read worker, so a test can see that none is left."""


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
    app = started_application()

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
            "Reading your hyprland.lua means running it once: none of its commands run and "
            "no files change."
        ]
        assert "Could not read the configuration" not in _text_under(dialog)
        assert dialog.get_default_widget().get_label() == "Not now"

        _read(dialog, "Read it")

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
        _read(dialog, "Read it")

        assert _page_title(dialog) == "Commands"
        assert _row_titles(dialog) == [command]
        assert dialog.get_default_widget().get_label() == "Not now"
        run = _button(dialog, "Run them and read")
        assert not run.has_css_class("suggested-action")
        assert not marker.exists()

        _read(dialog, "Run them and read")

        assert marker.exists()
        assert _page_title(dialog) == "Preview"
        assert dialog._flow.preview.model.get("general:gaps_in") == _gaps(5)
        assert dialog.get_default_widget() is None

    def test_every_wizard_run_asks_again(self, tmp_path: Path) -> None:
        from hyprtweaker.engine.prefs import PrefsStore

        _foreign_root(tmp_path)
        window, session = build_window(tmp_path)
        first = window.show_migration()
        _click(first, "Convert...")
        _read(first, "Read it")
        assert _page_title(first) == "Preview"
        first.close()

        second = window.show_migration()
        _click(second, "Convert...")

        assert _page_title(second) == "Read your config"
        assert not PrefsStore(session.paths.state_dir).path.exists()

    def test_an_app_written_config_opens_no_wizard(self, tmp_path: Path) -> None:
        from hyprtweaker.engine.migration.detect import ConfigKind

        build_window(tmp_path / "app")[0].route_first_run()  # writes the app's own config
        app_window, _ = build_window(tmp_path / "app")
        _foreign_root(tmp_path / "foreign")
        foreign_window, _ = build_window(tmp_path / "foreign")

        assert app_window.route_first_run().kind is ConfigKind.APP_GENERATED
        assert foreign_window.route_first_run().kind is ConfigKind.FOREIGN_LUA
        main_loop.settle("the first-run offer's idle to present the wizard")

        assert app_window.get_visible_dialog() is None
        assert _page_title(foreign_window.get_visible_dialog()) == "Detect"


OMARCHY_ROW = (
    "Omarchy's theme menu and Omarchy updates will no longer change your Hyprland settings"
)
OMARCHY_ROW_HELP = (
    "Change colors on the Theming page, or set up a color tool there. Restoring the backup "
    "this wizard makes puts you back."
)


def _omarchy_root(root: Path) -> Path:
    """The shape of an Omarchy entrypoint: it requires `default.hypr.omarchy`, which here
    is a module beside it, since the real one lives under `/usr/share/omarchy/`."""
    entrypoint = _foreign_root(
        root, 'require("default.hypr.omarchy")\nhl.config({ general = { gaps_in = 7 } })\n'
    )
    module = entrypoint.parent / "default" / "hypr" / "omarchy.lua"
    module.parent.mkdir(parents=True)
    module.write_text("hl.config({ general = { border_size = 3 } })\n", encoding="utf-8")
    return entrypoint


class TestWhatSwitchingAnOmarchyConfigEnds:
    """The Preview page tells an Omarchy user what the switch costs (#234)."""

    def test_an_omarchy_config_is_told_its_theme_menu_and_updates_stop(
        self, tmp_path: Path
    ) -> None:
        entrypoint = _omarchy_root(tmp_path)
        omarchy = entrypoint.parent / "default" / "hypr" / "omarchy.lua"
        before = (entrypoint.read_bytes(), omarchy.read_bytes())
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")
        _read(dialog, "Read it")

        assert _page_title(dialog) == "Preview"
        assert (OMARCHY_ROW, OMARCHY_ROW_HELP) in _rows(dialog)
        # Information, not a gate: Back up stays offered.
        assert "Back up and convert" in [
            button.get_label() for button in _action_buttons(dialog)
        ]
        assert (entrypoint.read_bytes(), omarchy.read_bytes()) == before

    def test_another_config_is_not_told_anything_about_omarchy(self, tmp_path: Path) -> None:
        _foreign_root(tmp_path)
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")
        _read(dialog, "Read it")

        assert _page_title(dialog) == "Preview"
        assert "Omarchy" not in _text_under(dialog)

    def test_a_hyprlang_conf_is_not_told_anything_about_omarchy(self, tmp_path: Path) -> None:
        from hyprtweaker.engine.paths import ConfigPaths

        paths = ConfigPaths.rooted_at(tmp_path)
        paths.hypr_dir.mkdir(parents=True, exist_ok=True)
        paths.hyprland_conf.write_text(
            'require("default.hypr.omarchy")\n' + CONF, encoding="utf-8"
        )
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")

        assert _page_title(dialog) == "Preview"
        assert "Omarchy" not in _text_under(dialog)


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
        _read(dialog, "Read it")
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


class TestTheCommandsPage:
    """The second offer, after a blocked read that tried to run commands (#190, and the
    #150 review: findings 6, 7, 19 and 20, owner call 2)."""

    PARTLY_READ = (
        "hl.config({ general = { gaps_in = 7, gaps_out = 9 } })\n"
        'hl.bind("SUPER + Q", hl.dsp.exec_cmd("kitty"))\n'
        'local n = tonumber(io.popen("{command}"):read("*a"))\n'
        "hl.config({ general = { border_size = n + 1 } })\n"
    )

    def _commands_dialog(self, tmp_path: Path, source: str):  # type: ignore[no-untyped-def]
        _foreign_root(tmp_path, source)
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")
        _read(dialog, "Read it")
        assert _page_title(dialog) == "Commands"
        return dialog

    def test_a_read_that_got_settings_says_so_and_can_continue_without_running(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "ran"
        dialog = self._commands_dialog(
            tmp_path, self.PARTLY_READ.replace("{command}", f"touch {marker}; echo 5")
        )

        assert _descriptions(dialog) == [
            "Without running them, the app read 3 settings from this file. Anything these "
            "commands build is missing.\n\n"
            "Reading it fully means running these for real, and any others the config goes "
            "on to run:"
        ]
        assert [b.get_label() for b in _action_buttons(dialog)] == [
            "Run them and read",
            "Continue without running them",
            "Not now",
        ]
        assert dialog.get_default_widget().get_label() == "Continue without running them"

        _click(dialog, "Continue without running them")

        assert _page_title(dialog) == "Preview"
        assert dialog._flow.preview.model.get("general:gaps_in") == _gaps(7)
        assert not marker.exists()

    def test_a_read_that_got_nothing_has_no_continue(self, tmp_path: Path) -> None:
        dialog = self._commands_dialog(
            tmp_path,
            'local f = io.popen("echo 5")\n'
            'hl.config({ general = { gaps_in = tonumber(f:read("*a")) } })\n',
        )

        assert _descriptions(dialog) == [
            "Reading it fully means running these for real, and any others the config goes "
            "on to run:"
        ]
        assert [b.get_label() for b in _action_buttons(dialog)] == [
            "Run them and read",
            "Not now",
        ]

    def test_not_now_closes_the_wizard_having_run_nothing(self, tmp_path: Path) -> None:
        marker = tmp_path / "ran"
        dialog = self._commands_dialog(
            tmp_path, self.PARTLY_READ.replace("{command}", f"touch {marker}; echo 5")
        )
        closed: list[bool] = []
        dialog.connect("closed", lambda _dialog: closed.append(True))

        _click(dialog, "Not now")

        assert closed == [True]
        assert not marker.exists()

    def test_file_operations_and_repeats_are_listed_with_what_they_do(
        self, tmp_path: Path
    ) -> None:
        dialog = self._commands_dialog(
            tmp_path,
            'os.remove("stale.lua")\n'
            'local a = io.popen("echo 5")\n'
            'local b = io.popen("echo 5")\n'
            'os.rename("a.lua", "b.lua")\n'
            "hl.config({ general = { gaps_in = tonumber(a:read('*a')) } })\n",
        )

        assert _rows(dialog) == [
            ("stale.lua", "Deletes this file"),
            ("echo 5", "Runs 2 times"),
            ("a.lua -> b.lua", "Moves or renames this file"),
        ]

    def test_a_second_click_while_a_read_runs_starts_nothing(self, tmp_path: Path) -> None:
        """One read at a time: a second press of the button that started it, before its
        page has gone, does not start another read or run the commands twice."""
        marker = tmp_path / "ran"
        _foreign_root(
            tmp_path,
            f'local f = io.popen("echo x >> {marker}; echo 5")\n'
            'hl.config({ general = { gaps_in = tonumber(f:read("*a")) } })\n',
        )
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")
        read = _button(dialog, "Read it")

        read.emit("clicked")
        read.emit("clicked")

        assert [_title(page) for page in _stack(dialog)] == [
            "Detect",
            "Read your config",
            READING,
        ]
        _wait_for_the_read(dialog, "Read it")
        assert [_title(page) for page in _stack(dialog)] == [
            "Detect",
            "Read your config",
            "Commands",
        ]
        run = _button(dialog, "Run them and read")

        run.emit("clicked")
        run.emit("clicked")

        _wait_for_the_read(dialog, "Run them and read")
        assert marker.read_text(encoding="utf-8") == "x\n"
        assert _page_title(dialog) == "Preview"

    def test_a_file_name_with_markup_characters_shows_as_written(self, tmp_path: Path) -> None:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        chosen = tmp_path / "<mine> & co.lua"
        chosen.write_text(FOREIGN_LUA, encoding="utf-8")
        window, _ = build_window(tmp_path)

        dialog = window.show_migration(chosen)

        shown = [w.get_text() for w in _of_type(dialog, Gtk.Label)]
        assert "Read <mine> & co.lua?" in shown


NEVER_ENDS = "hl.config({ general = { gaps_in = 7 } })\nwhile true do end\n"
"""A config that only stops when it is stopped: a read the user has to cancel."""


class HeldRead:
    """`MigrationFlow.read_preview`, held until the test releases it, then read for real.

    A read whose length the test controls, with no sleeping: while held, it gives up when
    the wizard sets its cancel token (as the real read does), unless `ignore_cancel` makes
    it the read that finishes just as the wizard closes.
    """

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.ignore_cancel = False

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hyprtweaker.engine.importer.lua.sandbox import Cancelled
        from hyprtweaker.engine.migration.flow import MigrationFlow

        real = MigrationFlow.read_preview

        def held(flow, *args, cancel=None, **kwargs):  # type: ignore[no-untyped-def]
            self.started.set()
            deadline = time.monotonic() + main_loop.SETTLE_SECONDS
            while not self.release.wait(0.005):
                if cancel is not None and cancel.is_set() and not self.ignore_cancel:
                    raise Cancelled("cancelled while held")
                if time.monotonic() > deadline:
                    raise AssertionError("the held read was never released")
            return real(flow, *args, **kwargs)

        monkeypatch.setattr(MigrationFlow, "read_preview", held)


@pytest.fixture
def held_read(monkeypatch: pytest.MonkeyPatch) -> Iterator[HeldRead]:
    held = HeldRead()
    held.install(monkeypatch)
    yield held
    held.release.set()
    for thread in _readers():
        thread.join(main_loop.SETTLE_SECONDS)


class TestAReadOffTheMainLoop:
    """Reading runs the config, which can take up to a minute: the wizard reads in a
    worker behind a progress page, and Cancel or Close stops the read (#216)."""

    def _on_consent(self, tmp_path: Path, source: str = FOREIGN_LUA):  # type: ignore[no-untyped-def]
        _foreign_root(tmp_path, source)
        window, _ = build_window(tmp_path)
        dialog = window.show_migration()
        _click(dialog, "Convert...")
        return dialog

    def test_the_window_stays_live_behind_a_progress_page_until_the_read_ends(
        self, tmp_path: Path, held_read: HeldRead
    ) -> None:
        from gi.repository import Adw, GLib

        dialog = self._on_consent(tmp_path)
        consent = _visible(dialog)

        _click(dialog, "Read it")
        assert held_read.started.wait(main_loop.SETTLE_SECONDS)

        turns: list[int] = []
        GLib.timeout_add(1, lambda: turns.append(1) or len(turns) < 3)
        main_loop.wait_until(lambda: len(turns) == 3, "three main-loop turns during the read")
        assert _page_title(dialog) == READING
        status = _status(dialog)
        assert status.get_title() == "Reading your config…"
        assert status.get_description() in (None, "")
        assert isinstance(status.get_paintable(), Adw.SpinnerPaintable)
        assert [b.get_label() for b in _action_buttons(dialog)] == ["Cancel"]
        assert {
            label: button.is_sensitive() for label, button in _buttons_on(consent).items()
        } == {"Read it": False, "Not now": False}
        assert dialog._flow.preview is None

        held_read.release.set()
        _wait_for_the_read(dialog, "Read it")

        assert _page_title(dialog) == "Preview"
        assert dialog._flow.preview.model.get("general:gaps_in") == _gaps(7)
        assert [_title(page) for page in _stack(dialog)] == [
            "Detect",
            "Read your config",
            "Preview",
        ]

    def test_a_long_read_says_it_can_take_up_to_a_minute(
        self, tmp_path: Path, held_read: HeldRead, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hyprtweaker.ui.dialogs import migration

        monkeypatch.setattr(migration, "LONG_READ_SECONDS", 0.0)
        dialog = self._on_consent(tmp_path)

        _click(dialog, "Read it")

        main_loop.wait_until(
            lambda: _status(dialog).get_description() == "This can take up to a minute.",
            "the progress page's second line",
        )

    def test_cancel_stops_the_read_and_returns_to_the_page_it_came_from(
        self, tmp_path: Path
    ) -> None:
        dialog = self._on_consent(tmp_path, NEVER_ENDS)
        _click(dialog, "Read it")
        assert _page_title(dialog) == READING

        _click(dialog, "Cancel")

        assert _page_title(dialog) == "Read your config"
        main_loop.wait_until(lambda: not _readers(), "the cancelled read's worker to end")
        assert dialog._flow.preview is None
        assert {
            label: button.is_sensitive()
            for label, button in _buttons_on(_visible(dialog)).items()
        } == {"Read it": True, "Not now": True}
        assert dialog.get_default_widget().get_label() == "Not now"
        main_loop.settle("anything the cancelled read left queued")
        assert _page_title(dialog) == "Read your config"
        assert dialog._flow.preview is None

    def test_cancel_on_the_commands_page_read_returns_to_the_commands_page(
        self, tmp_path: Path
    ) -> None:
        # Read blocked, the pipe is faked and comes back empty; run for real, it never ends.
        dialog = self._on_consent(
            tmp_path,
            'if io.popen("echo 5"):read("*a") ~= "" then while true do end end\n',
        )
        _read(dialog, "Read it")
        assert _page_title(dialog) == "Commands"
        blocked = dialog._flow.preview

        _click(dialog, "Run them and read")
        assert _page_title(dialog) == READING
        _click(dialog, "Cancel")

        assert _page_title(dialog) == "Commands"
        main_loop.wait_until(lambda: not _readers(), "the cancelled read's worker to end")
        assert _button(dialog, "Run them and read").is_sensitive()
        assert dialog.get_default_widget().get_label() == "Not now"
        assert dialog._flow.preview is blocked  # the cancelled read held nothing

    def test_closing_the_wizard_mid_read_stops_the_read(self, tmp_path: Path) -> None:
        dialog = self._on_consent(tmp_path, NEVER_ENDS)
        _click(dialog, "Read it")

        dialog.close()

        main_loop.wait_until(lambda: not _readers(), "the read's worker to end on close")
        main_loop.settle("the closed wizard's release")
        assert dialog._flow.preview is None

    def test_a_read_that_ends_as_the_wizard_closes_is_dropped(
        self, tmp_path: Path, held_read: HeldRead
    ) -> None:
        held_read.ignore_cancel = True
        dialog = self._on_consent(tmp_path)
        _click(dialog, "Read it")
        dialog.close()
        main_loop.settle("the closed wizard's release")

        held_read.release.set()

        main_loop.wait_until(lambda: not _readers(), "the late read's worker to end")
        main_loop.settle("the late read's result reaching the main loop")
        assert dialog._flow.preview is None


def test_reading_without_lua_stops_on_a_page_that_says_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hyprtweaker.engine.importer.lua import sandbox

    monkeypatch.setattr(sandbox, "lua_binary", lambda: None)
    _foreign_root(tmp_path)
    window, _ = build_window(tmp_path)
    dialog = window.show_migration()
    _click(dialog, "Convert...")

    _read(dialog, "Read it")

    assert _page_title(dialog) == "Stopped"
    assert _descriptions(dialog) == [
        "Reading a Lua config needs Lua, which is not installed. Install Lua (lua5.5, "
        "lua5.4, lua5.3, lua or luajit) and try again."
    ]


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


def _import_entry(window):  # type: ignore[no-untyped-def]
    """The main menu's Import entry as a user meets it: its widget in the popover."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from hyprtweaker.ui.shell.window import IMPORT_LABEL

    button = next(w for w in _walk(window) if isinstance(w, Gtk.MenuButton) and w.get_popover())
    entries = [
        w
        for w in _walk(button.get_popover())
        if type(w).__name__ == "GtkModelButton" and w.get_property("text") == IMPORT_LABEL
    ]
    assert len(entries) == 1, "the main menu should have exactly one Import entry"
    return entries[0]


class TestImportBelowTheFloor:
    """Below Hyprland 0.56 the compositor cannot read Lua, so Import must lead nowhere it
    cannot finish (#101): the entry is unavailable and says why in the Banner's own words."""

    def _window_under(self, tmp_path: Path, version: str):  # type: ignore[no-untyped-def]
        from hyprtweaker.engine.ipc import LiveHyprland

        live = LiveHyprland(version, ({"name": "general:border_size"},))
        window, session = build_window(tmp_path, live)
        window.route_first_run()
        session.start()
        window.sync()
        return window, session

    def test_the_entry_is_unavailable_and_says_why(self, tmp_path: Path) -> None:
        window, _ = self._window_under(tmp_path, "0.55.0")
        entry = _import_entry(window)

        assert entry.get_property("text") == "Import..."
        assert entry.get_sensitive() is False
        assert entry.get_tooltip_text() == (
            "Hyprland 0.55.0 is running, and this app needs Hyprland 0.56 or newer."
        )

    def test_activating_it_anyway_opens_no_wizard_and_no_file_picker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hyprtweaker.ui.shell import window as shell

        opened: list[str] = []
        monkeypatch.setattr(shell, "import_dialog", lambda *args: opened.append("picker"))
        monkeypatch.setattr(
            shell, "migration_dialog", lambda *args, **kw: opened.append("wizard")
        )
        window, _ = self._window_under(tmp_path, "0.55.0")

        window.activate_action("import-config", None)
        window._on_import(None, None)

        assert opened == []

    def test_a_supported_hyprland_keeps_the_entry_as_it_was(self, tmp_path: Path) -> None:
        window, _ = self._window_under(tmp_path, "0.56.2")

        entry = _import_entry(window)
        assert entry.get_sensitive() is True
        assert entry.get_tooltip_text() is None


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


def _rows(dialog) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    from gi.repository import Adw

    return [(row.get_title(), row.get_subtitle()) for row in _of_type(dialog, Adw.ActionRow)]


def _action_buttons(dialog) -> list:  # type: ignore[type-arg]
    """The visible page's bottom-bar buttons, left to right."""
    from gi.repository import Adw, Gtk

    toolbar = _visible(dialog).get_child()
    assert isinstance(toolbar, Adw.ToolbarView)
    return [
        widget
        for widget in _walk(toolbar)
        if isinstance(widget, Gtk.Button)
        and widget.get_label()
        and widget.get_ancestor(Adw.HeaderBar) is None
        and widget.get_ancestor(Gtk.ScrolledWindow) is None
    ]


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


def _readers() -> list[threading.Thread]:
    """The wizard's read workers still running in this process."""
    return [thread for thread in threading.enumerate() if thread.name == READER_THREAD]


def _wait_for_the_read(dialog, label: str) -> None:  # type: ignore[no-untyped-def]
    main_loop.wait_until(
        lambda: _page_title(dialog) != READING and not _readers(),
        f"the read {label!r} started to reach its page and its worker to end",
    )


def _read(dialog, label: str) -> None:  # type: ignore[no-untyped-def]
    """Press `label`, which reads the config in a worker, and wait for where it lands."""
    _click(dialog, label)
    _wait_for_the_read(dialog, label)


def _stack(dialog) -> list:  # type: ignore[type-arg]
    stack = dialog._view.get_navigation_stack()
    return [stack.get_item(i) for i in range(stack.get_n_items())]


def _title(page) -> str:  # type: ignore[no-untyped-def]
    return str(page.get_title())


def _buttons_on(page) -> dict[str, object]:  # type: ignore[no-untyped-def]
    from gi.repository import Gtk

    return {
        widget.get_label(): widget
        for widget in _walk(page)
        if isinstance(widget, Gtk.Button) and widget.get_label()
    }


def _status(dialog):  # type: ignore[no-untyped-def]
    from gi.repository import Adw

    (status,) = _of_type(dialog, Adw.StatusPage)
    return status


MATUGEN_TOML = """\
[templates.hyprland]
input_path = '~/.config/matugen/templates/hyprland-colors.lua'
output_path = '~/.config/hypr/colors.lua'
"""

WALLUST_TOML = """\
[templates]
kitty = { template = 'kitty.conf', target = '~/.config/kitty/colors.conf' }
"""


class LiveClient:
    """A compositor that loaded the switched config cleanly. Nothing reaches a real one."""

    async def configerrors(self) -> tuple[str, ...]:
        return ()

    async def bind_count(self) -> int:
        return 0

    async def workspace_rule_count(self) -> int:
        return 0

    async def monitors(self) -> tuple[dict[str, object], ...]:
        return ()

    async def reload_full_reset(self) -> None:
        return None


def _run_the_switch_only(coro) -> None:  # type: ignore[no-untyped-def]
    """The wizard's `spawn`: runs Switch to its end; the 60-s countdown after it is not run."""
    import asyncio

    if coro.__name__ == "_switch":
        asyncio.run(coro)
    else:
        coro.close()


def _wizard(  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
    stub_tool: Callable[..., Path],
    tools: tuple[str, ...],
    *,
    live: bool = True,
):
    """The wizard over a `hyprland.conf` in the fenced home, with `tools` installed (stubs
    on the fenced tool path, never run) and matugen's and wallust's configs present. The
    static gate is stood in for: `test_bridge_verify_config.py` runs the real one."""
    import subprocess

    from hyprtweaker.engine.migration import flow as flow_module
    from hyprtweaker.engine.migration.flow import MigrationFlow
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.schema import load_schema
    from hyprtweaker.ui.dialogs.migration import MigrationDialog

    paths = ConfigPaths.default()
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    paths.hyprland_conf.write_text(CONF, encoding="utf-8")
    for relpath, text in (
        ("matugen/config.toml", MATUGEN_TOML),
        ("wallust/wallust.toml", WALLUST_TOML),
    ):
        (paths.config_home / relpath).parent.mkdir(parents=True, exist_ok=True)
        (paths.config_home / relpath).write_text(text, encoding="utf-8")
    for tool in tools:
        stub_tool(tool, "exit 99")

    monkeypatch.setattr(
        flow_module,
        "_verify_config",
        lambda entrypoint, runtime: subprocess.CompletedProcess([], 0, "", ""),
    )
    monkeypatch.setattr(flow_module, "_hyprland_installed", lambda: True)
    flow = MigrationFlow(
        paths=paths,
        schema=load_schema("0.56.2", ROOT / "data" / "schema"),
        app_version=APP_VERSION,
        client=LiveClient() if live else None,
    )
    started_application()
    from started_app import presented

    dialog = presented(MigrationDialog(flow, spawn=_run_the_switch_only))
    _click(dialog, "Convert...")
    _click(dialog, "Back up and convert")
    return dialog, flow, paths


def _tool_configs(paths) -> dict[str, bytes]:  # type: ignore[no-untyped-def]
    root = paths.config_home
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_relative_to(paths.hypr_dir)
    }


def _row_button(dialog, title: str):  # type: ignore[no-untyped-def]
    from gi.repository import Adw, Gtk

    (row,) = [row for row in _of_type(dialog, Adw.ActionRow) if row.get_title() == title]
    (button,) = [widget for widget in _walk(row) if isinstance(widget, Gtk.Button)]
    return button


class TestBridgeSetup:
    """#187: the back-up step offers each installed theming tool, each behind its own
    confirm; skipping all is the default and nothing of a tool's is written before Switch."""

    def test_two_installed_tools_are_listed_and_confirming_one_wires_only_that_one(
        self, monkeypatch: pytest.MonkeyPatch, stub_tool: Callable[..., Path]
    ) -> None:
        dialog, flow, paths = _wizard(monkeypatch, stub_tool, ("matugen", "wallust"))
        wallust = (paths.config_home / "wallust/wallust.toml").read_bytes()

        assert _page_title(dialog) == "Theming tools"
        assert _rows(dialog) == [("matugen", "Not set up"), ("wallust", "Not set up")]
        assert dialog.get_default_widget().get_label() == "Continue"

        _row_button(dialog, "matugen").emit("clicked")
        confirm = dialog._confirm
        assert confirm.get_heading() == "Set up matugen?"
        assert confirm.get_default_response() == "cancel"
        assert confirm.get_response_label("agree") == "Set up matugen"
        assert "~/.config/matugen/config.toml (changed)" in confirm.lines
        confirm.emit("response", "agree")

        assert _rows(dialog) == [
            ("matugen", "Set up when you switch"),
            ("wallust", "Not set up"),
        ]
        assert _row_button(dialog, "matugen").get_label() == "Don't set up"
        assert (paths.config_home / "matugen/config.toml").read_text() == MATUGEN_TOML

        _click(dialog, "Continue")
        assert _page_title(dialog) == "Back up"
        assert ("Set up when you switch", "matugen") in _rows(dialog)
        _click(dialog, "Switch and verify")

        assert _page_title(dialog) == "Keep or roll back"
        assert "bridge/matugen.lua" in (paths.config_home / "matugen/config.toml").read_text()
        assert (paths.config_home / "wallust/wallust.toml").read_bytes() == wallust
        assert "matugen is set up. Its colors load from matugen's next run." in _row_titles(
            dialog
        )
        flow.keep()

    def test_declining_the_confirm_leaves_every_tool_config_as_it_was(
        self, monkeypatch: pytest.MonkeyPatch, stub_tool: Callable[..., Path]
    ) -> None:
        dialog, flow, paths = _wizard(monkeypatch, stub_tool, ("matugen", "wallust"))
        before = _tool_configs(paths)

        _row_button(dialog, "matugen").emit("clicked")
        dialog._confirm.emit("response", "cancel")
        assert _rows(dialog)[0] == ("matugen", "Not set up")
        _click(dialog, "Continue")
        _click(dialog, "Switch and verify")

        assert _page_title(dialog) == "Keep or roll back"
        assert flow.consents == ()
        assert _tool_configs(paths) == before
        assert "matugen" not in _text_under(dialog._view.get_visible_page())
        flow.keep()

    def test_a_confirmed_tool_can_be_unchecked_before_the_switch(
        self, monkeypatch: pytest.MonkeyPatch, stub_tool: Callable[..., Path]
    ) -> None:
        dialog, flow, _paths = _wizard(monkeypatch, stub_tool, ("matugen",))
        _row_button(dialog, "matugen").emit("clicked")
        dialog._confirm.emit("response", "agree")

        _row_button(dialog, "matugen").emit("clicked")

        assert _rows(dialog) == [("matugen", "Not set up")]
        assert flow.consents == ()

    def test_with_no_tool_installed_the_wizard_goes_straight_to_the_back_up_page(
        self, monkeypatch: pytest.MonkeyPatch, stub_tool: Callable[..., Path]
    ) -> None:
        dialog, _flow, _paths = _wizard(monkeypatch, stub_tool, ())

        assert _page_title(dialog) == "Back up"
        assert "Theming tools" not in [_title(page) for page in _stack(dialog)]

    def test_without_a_compositor_no_tool_is_offered(
        self, monkeypatch: pytest.MonkeyPatch, stub_tool: Callable[..., Path]
    ) -> None:
        dialog, _flow, _paths = _wizard(
            monkeypatch, stub_tool, ("matugen", "wallust"), live=False
        )

        assert _page_title(dialog) == "Back up"


def test_a_relaunched_roll_back_says_which_tool_file_it_left(tmp_path: Path) -> None:
    """Finding 21 of the #153 review: the relaunched app's Roll back dropped its notes, so a
    tool config left as the user changed it was never mentioned."""
    from datetime import UTC, datetime

    from hyprtweaker.engine.bridge.wire import WireConsent, plan_wire, wire
    from hyprtweaker.engine.migration import sentinel as sentinels
    from hyprtweaker.engine.paths import ConfigPaths

    paths = ConfigPaths.rooted_at(tmp_path)
    config = paths.config_home / "matugen/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[config]\n", encoding="utf-8")
    plan = plan_wire("matugen", paths=paths)
    wire(plan, WireConsent(plan), register=lambda _t: True, unregister=lambda _t: True)
    config.write_text(config.read_text(encoding="utf-8") + "# mine\n", encoding="utf-8")
    edited = config.read_bytes()
    sentinels.write(paths, kind="legacy-conf", bridge_tools=("matugen",), now=datetime.now(UTC))
    window, _ = build_window(tmp_path)

    window.route_first_run()
    offer = window.get_visible_dialog()
    assert offer.get_heading() == "A configuration switch was not finished"
    offer.emit("response", "roll-back")
    offer.force_close()
    main_loop.settle("the notes to show")

    said = window.get_visible_dialog()
    assert said.get_heading() == "Rolled back"
    assert said.get_body() == (
        f"{tmp_path}/matugen/config.toml changed after matugen was set up, so it was left "
        f"as it is. The copy from before setup is in {tmp_path}/state/bridge-backups/."
    )
    assert config.read_bytes() == edited
    said.force_close()
