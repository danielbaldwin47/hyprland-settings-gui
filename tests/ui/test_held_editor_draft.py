"""UI smoke tier: a held bind editor whose save is refused keeps what the user typed (#225).

The session half (the refusal, the file left alone, the stale index) is in
`tests/unit/test_session_held_editor.py`. This tier checks the user's side of a refused
save: the toast says why, and the dialog with the draft in it is still there to act on.
"""

from __future__ import annotations

from pathlib import Path

from _live_window import APP_VERSION, live_entity_window

BINDS = "binds.lua"


def test_a_refused_save_from_a_held_editor_keeps_the_dialog_and_the_draft(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.model import Bind, DispatcherCall
    from hyprtweaker.engine.model.entities import EntitySet
    from hyprtweaker.engine.state.manifest import Manifest, ModuleRecord
    from hyprtweaker.engine.writer.binds import render_binds_module

    def exec_bind(keys: str, command: str) -> Bind:
        return Bind(
            keys=keys, dispatcher=DispatcherCall(path="exec_cmd", positional=(command,))
        )

    a, b, c = (
        exec_bind("SUPER + A", "alpha"),
        exec_bind("SUPER + B", "bravo"),
        exec_bind("SUPER + C", "charlie"),
    )
    session, window, _applier = live_entity_window(tmp_path)
    session.on_state_changed = window.sync
    session.on_refused = window.show_refused
    session.model.entities.binds.extend([a, b, c])
    app_text = render_binds_module(EntitySet(binds=[a, b, c]), app_version=APP_VERSION)
    assert app_text is not None
    module = session.paths.app_dir / BINDS
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(app_text, encoding="utf-8")
    session.paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    session.paths.manifest.write_text(
        Manifest(
            app_version=APP_VERSION,
            schema_version="0.56.0",
            modules={BINDS: ModuleRecord.of(app_text)},
        ).render(),
        encoding="utf-8",
    )
    window.binds_page.refresh()

    window._edit_bind(1)
    dialog = window.get_visible_dialog()
    assert dialog is not None
    dialog._description.set_text("my draft")

    # Somebody else moves bravo to the end of binds.lua and reloads Hyprland.
    hand = render_binds_module(EntitySet(binds=[a, c, b]), app_version="by-hand")
    assert hand is not None
    module.write_text(hand, encoding="utf-8")
    session._reread_binds()
    session._changed()

    dialog._save()

    assert window._banner.get_title() == (
        "binds.lua was edited outside this app, so changes to it are not saved."
    )
    assert module.read_text(encoding="utf-8") == hand
    assert window.get_visible_dialog() is dialog
    assert dialog._description.get_text() == "my draft"
    assert dialog._error.get_visible()
    assert dialog._error.get_text() == (
        "Keybind changed was not saved: binds.lua was edited outside this app. "
        "Cancel, then choose Details on the banner."
    )
