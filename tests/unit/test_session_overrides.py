"""Reading `user.lua` to find out what it takes out of the app's hands.

`user.lua` is the one config file the app must never write, and it is required last -- so
anything it sets wins over every generated Module. The Row has to say so, and the only way
to know what it sets is to run it: it is a program, not a list.

That makes this the drift badge's reader, deferred out of #57 until the Lua importer of
this ticket existed to be it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _fake_hyprland import FakeHyprland
from _support import Runner, sample_schema, session_for

from hyprtweaker.engine.importer.lua import Consent, lua_binary, overridden_options
from hyprtweaker.engine.paths import ConfigPaths

pytestmark = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter installed")

SCHEMA = sample_schema()
GRANTED = Consent(evaluate=True)


def write_user_lua(root: Path, body: str) -> Path:
    path = ConfigPaths.rooted_at(root).user_lua
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# --- the reader ---------------------------------------------------------------------------


def test_it_reports_the_options_user_lua_sets(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = write_user_lua(
        tmp_path,
        "hl.config({ decoration = { rounding = 20 }, general = { border_size = 4 } })\n",
    )

    assert overridden_options(path, SCHEMA, consent=GRANTED) == {
        "decoration:rounding",
        "general:border_size",
    }


def test_a_user_lua_that_only_binds_keys_overrides_no_options(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Binds are appended, not overridden -- the badge is about Options winning."""
    path = write_user_lua(tmp_path, 'hl.bind("SUPER + P", hl.dsp.exec_cmd("rofi"))\n')

    assert overridden_options(path, SCHEMA, consent=GRANTED) == frozenset()


def test_a_missing_user_lua_overrides_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert overridden_options(tmp_path / "nope.lua", SCHEMA, consent=GRANTED) == frozenset()


def test_a_broken_user_lua_is_not_an_error_the_user_has_to_see(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A badge that cannot be computed is a missing badge, never a broken window: the
    config being unreadable is the Banner's business, not the Row's."""
    path = write_user_lua(tmp_path, "this is not lua ===\n")

    assert overridden_options(path, SCHEMA, consent=GRANTED) == frozenset()


def test_reading_user_lua_cannot_run_what_it_shells_out_to(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """It is read on every reload, so it must be at least as safe as an import."""
    target = tmp_path / "touched"
    path = write_user_lua(
        tmp_path,
        f'os.execute("touch {target}")\nhl.config({{ decoration = {{ rounding = 3 }} }})\n',
    )

    assert overridden_options(path, SCHEMA, consent=GRANTED) == {"decoration:rounding"}
    assert not target.exists()


# --- the session ---------------------------------------------------------------------------


def test_a_session_reports_what_user_lua_overrides(tmp_path) -> None:  # type: ignore[no-untyped-def]
    write_user_lua(tmp_path, "hl.config({ decoration = { rounding = 20 } })\n")
    session = session_for(FakeHyprland(), tmp_path, Runner())

    session.refresh_overrides()

    assert session.overridden == {"decoration:rounding"}


def test_a_session_with_no_user_lua_reports_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    session = session_for(FakeHyprland(), tmp_path, Runner())

    session.refresh_overrides()

    assert session.overridden == frozenset()


def test_an_unchanged_user_lua_is_not_re_evaluated(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Every reload funnels through the refresh, and evaluation spawns an interpreter.

    Paying that on each of a slider's transactions would make dragging it stutter for a
    file that has not changed since the app started.
    """
    write_user_lua(tmp_path, "hl.config({ decoration = { rounding = 20 } })\n")
    session = session_for(FakeHyprland(), tmp_path, Runner())

    calls = 0
    real = overridden_options

    def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr("hyprtweaker.session.overridden_options", counting)

    session.refresh_overrides()
    session.refresh_overrides()
    session.refresh_overrides()

    assert calls == 1, "an unchanged user.lua was evaluated more than once"
    assert session.overridden == {"decoration:rounding"}


def test_editing_user_lua_changes_what_the_session_reports(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The other half of the cache: it has to notice when the file really does change."""
    session = session_for(FakeHyprland(), tmp_path, Runner())
    write_user_lua(tmp_path, "hl.config({ decoration = { rounding = 20 } })\n")
    session.refresh_overrides()
    assert session.overridden == {"decoration:rounding"}

    write_user_lua(tmp_path, "hl.config({ general = { border_size = 9 } })\n")
    session.refresh_overrides()

    assert session.overridden == {"general:border_size"}
