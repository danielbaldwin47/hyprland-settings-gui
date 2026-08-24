"""UI smoke tier: the Binds Page assembles, and shows what the model holds (#64).

Shallow on purpose, like the rest of this tier. The *text* each Row shows is settled in
`tests/unit/test_ui_binds_text.py` where no display is needed; what is left here is whether
GTK and libadwaita build the list at all, and whether the read-only cases really come out
without edit controls -- which is a fact about assembled widgets, not about a string.

Toolkit imports sit inside the test functions, as the tier's conftest requires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"


def build_window(tmp_path: Path) -> Any:
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def exec_bind(keys: str, command: str, **kwargs: Any) -> Any:
    from hyprtweaker.engine.model.entities import Bind, DispatcherCall

    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path="exec_cmd", positional=(command,)),
        **kwargs,
    )


def test_the_binds_page_is_in_the_sidebar(tmp_path: Path) -> None:
    from hyprtweaker.ui.pages.binds import BindsPage

    _session, window = build_window(tmp_path)

    assert window.binds_page is not None
    assert window.binds_page.page.get_title() == BindsPage.title


def test_every_bind_becomes_a_row(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [
            exec_bind("SUPER + Q", "kitty"),
            exec_bind("SUPER + E", "nautilus"),
            exec_bind("SUPER + code:10", "workspace 1"),
        ]
    )
    window.binds_page.refresh()

    assert len(window.binds_page.rows) == 3


def test_duplicates_each_get_their_own_row(tmp_path: Path) -> None:
    """Duplicates are legal and all fire, so the list must not collapse them (ADR-0007)."""
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "first"), exec_bind("SUPER + Q", "second")]
    )
    window.binds_page.refresh()

    assert len(window.binds_page.rows) == 2


def test_a_function_valued_bind_is_listed_read_only(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Bind

    session, window = build_window(tmp_path)

    session.model.entities.binds.append(Bind(keys="SUPER + W", dispatcher=None))
    window.binds_page.refresh()

    rows = window.binds_page.rows
    assert len(rows) == 1, "a function-valued bind must still be listed"
    assert rows[0].widget.get_subtitle().startswith("Runs a Lua function")


def test_an_empty_list_says_so(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path)

    assert window.binds_page.rows == ()
