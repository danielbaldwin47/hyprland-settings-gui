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


def test_duplicate_triggers_badge_both_rows_with_fire_order(tmp_path: Path) -> None:
    """ADR-0007: saved duplicates keep a warn badge on both rows stating fire order."""
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "first"), exec_bind("SUPER + Q", "second")]
    )
    window.binds_page.refresh()

    rows = window.binds_page.rows
    assert rows[0].conflict_badge is not None
    assert rows[1].conflict_badge is not None
    assert rows[0].conflict is not None and "fires 1st of 2" in rows[0].conflict.badge_text
    assert rows[1].conflict is not None and "fires 2nd of 2" in rows[1].conflict.badge_text
    assert rows[0].conflict.rivals[0].index == 1


def test_same_trigger_in_different_submaps_is_not_a_conflict(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "a", submap="resize"), exec_bind("SUPER + Q", "b")]
    )
    window.binds_page.refresh()

    assert all(row.conflict_badge is None for row in window.binds_page.rows)


def test_a_universal_conflict_across_submaps_is_badged_without_order(tmp_path: Path) -> None:
    """Cross-submap rivals never share a firing sequence, so no 1st-of-N is claimed."""
    from hyprtweaker.engine.model.entities import BindOptions

    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [
            exec_bind("SUPER + Q", "everywhere", options=BindOptions(submap_universal=True)),
            exec_bind("SUPER + Q", "resize-only", submap="resize"),
        ]
    )
    window.binds_page.refresh()

    rows = window.binds_page.rows
    assert rows[0].conflict is not None and rows[1].conflict is not None
    assert not rows[0].conflict.ordered
    assert not rows[1].conflict.ordered
    assert "fires" not in rows[0].conflict.badge_text
    assert rows[0].conflict.rivals[0].same_submap is False


def test_a_disabled_bind_is_badged_and_does_not_conflict(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "kept"), exec_bind("SUPER + Q", "off", enabled=False)]
    )
    window.binds_page.refresh()

    rows = window.binds_page.rows
    assert rows[1].badge_label is not None
    assert rows[1].badge_label.get_label() == "Disabled"
    assert rows[0].conflict_badge is None, "a disabled bind must not count as a conflict"


# --- the four badge states (#139) -------------------------------------------------------


def editable_row(bind: Any) -> tuple[Any, list[tuple[Any, ...]]]:
    """One `BindRow` as a live session builds it, with every verb recorded."""
    from hyprtweaker.ui.pages.binds import BindActions, BindRow

    calls: list[tuple[Any, ...]] = []
    actions = BindActions(
        add=lambda submap: calls.append(("add", submap)),
        edit=lambda index: calls.append(("edit", index)),
        remove=lambda index: calls.append(("remove", index)),
        enable=lambda index, on: calls.append(("enable", index, on)),
        rebind=lambda index: calls.append(("rebind", index)),
        recapture=lambda index: calls.append(("recapture", index)),
        swap=lambda first, second: calls.append(("swap", first, second)),
        edit_submap=lambda name: calls.append(("edit_submap", name)),
    )
    row = BindRow(bind, 3, actions=actions, on_jump=lambda _index: None, editable=True)
    return row, calls


def no_xkb_for_notakey(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hyprtweaker.engine.importer.binds.known_keysym", lambda name: name != "notakey"
    )


def test_a_user_disabled_bind_enables_in_one_click(tmp_path: Path) -> None:
    row, calls = editable_row(exec_bind("SUPER + Q", "kitty", enabled=False))

    assert row.badge_label.get_label() == "Disabled"
    assert row.enable_button.get_label() == "Enable"
    row.enable_button.emit("clicked")
    assert calls == [("enable", 3, True)]
    assert row.edit_button is not None and row.remove_button is not None


def test_a_dead_keysym_bind_wears_an_error_badge_and_enable_recaptures(
    tmp_path: Path, monkeypatch: Any
) -> None:
    no_xkb_for_notakey(monkeypatch)
    row, calls = editable_row(exec_bind("SUPER + notakey", "kitty", enabled=False))

    assert row.badge_label.get_label() == 'Unknown key "notakey"'
    assert "error" in row.badge_label.get_css_classes()
    assert row.enable_button.get_label() == "Fix trigger…"
    row.enable_button.emit("clicked")
    assert calls == [("recapture", 3)], "an error-disabled bind must never enable in one click"
    assert row.edit_button is not None and row.remove_button is not None


def test_a_multi_key_bind_offers_only_remove(tmp_path: Path, monkeypatch: Any) -> None:
    no_xkb_for_notakey(monkeypatch)
    row, calls = editable_row(exec_bind("SUPER + A&B", "kitty", enabled=False))

    assert row.badge_label.get_label() == "Multi-key: Hyprland 0.56 can't load it"
    assert "error" not in row.badge_label.get_css_classes()
    assert (row.enable_button, row.edit_button) == (None, None)
    row.remove_button.emit("clicked")
    assert calls == [("remove", 3)]


def test_a_lua_function_bind_offers_no_controls(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Bind

    row, _calls = editable_row(Bind(keys="SUPER + W", dispatcher=None))

    assert row.badge_label.get_label() == "Defined by a Lua function in user.lua"
    assert (row.enable_button, row.edit_button, row.remove_button) == (None, None, None)


def test_fix_trigger_enables_the_bind_with_the_captured_key(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import hyprtweaker.ui.shell.window as window_module

    no_xkb_for_notakey(monkeypatch)
    session, window = build_window(tmp_path)
    # Rows offer controls only on a live session; this tier has no compositor to connect.
    monkeypatch.setattr(type(session), "live", property(lambda _self: True))
    session.model.entities.binds.append(exec_bind("SUPER + notakey", "kitty", enabled=False))
    window.binds_page.refresh()

    captures: list[Any] = []

    class FakeCapture:
        def __init__(self, *, on_done: Any, **_kwargs: Any) -> None:
            captures.append(on_done)

        def present(self, _parent: Any) -> None:
            pass

    replaced: list[tuple[int, Any]] = []
    monkeypatch.setattr(window_module, "CaptureDialog", FakeCapture)
    monkeypatch.setattr(
        session, "replace_bind", lambda index, bind: replaced.append((index, bind)) or True
    )

    window.binds_page.rows[0].enable_button.emit("clicked")
    captures[0]("SUPER + F")

    [(index, bind)] = replaced
    assert (index, bind.keys, bind.enabled) == (0, "SUPER + F", True)


def test_a_declared_empty_submap_gets_a_group_flagged_unreachable(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Submap
    from hyprtweaker.ui.pages.binds import UNREACHABLE

    session, window = build_window(tmp_path)

    session.model.entities.submaps.append(Submap(name="resize"))
    window.binds_page.refresh()

    groups = window.binds_page.groups
    assert [g.get_title() for g in groups] == ["Keybinds", "Submap: resize"]
    assert UNREACHABLE in groups[1].get_description()


def test_an_entered_submap_is_not_flagged(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Bind, DispatcherCall, Submap
    from hyprtweaker.ui.pages.binds import UNREACHABLE

    session, window = build_window(tmp_path)

    session.model.entities.submaps.append(Submap(name="resize"))
    session.model.entities.binds.extend(
        [
            Bind(
                keys="SUPER + R",
                dispatcher=DispatcherCall(path="submap", positional=("resize",)),
            ),
            exec_bind("right", "grow", submap="resize"),
        ]
    )
    window.binds_page.refresh()

    groups = window.binds_page.groups
    assert UNREACHABLE not in groups[1].get_description()


def test_reveal_focuses_the_named_row(tmp_path: Path) -> None:
    """The conflict popover's jump: reveal(index) must address the model index."""
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "first"), exec_bind("SUPER + Q", "second")]
    )
    window.binds_page.refresh()

    # No display focus in the smoke tier; what is assertable is that the lookup finds
    # the right row rather than raising or walking off the list.
    window.binds_page.reveal(1)
    window.binds_page.reveal(99)  # out of range must be a no-op, not an error


def test_an_ampersand_in_a_trigger_or_command_is_shown_as_written(tmp_path: Path) -> None:
    """`A&B` and `a && b` are text, not Pango markup: parsed as markup they render blank."""
    from gi.repository import Gtk

    row, _calls = editable_row(exec_bind("SUPER + A&B", "make && run"))

    def texts(widget: Any) -> list[str]:
        found = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
        child = widget.get_first_child()
        while child is not None:
            found += texts(child)
            child = child.get_next_sibling()
        return found

    shown = texts(row.widget)
    assert "SUPER + A&B" in shown
    assert "make && run" in shown
