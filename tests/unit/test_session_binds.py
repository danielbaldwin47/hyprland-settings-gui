"""The Session never stores an enabled Bind whose Trigger Hyprland cannot load (#199).

The rule lives here as well as on the Binds row and in the editor because later callers
(the scripting surface, spec #152) reach the Session directly. A refusal is `False` with no
model change and no commit, so nothing is written either.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, section_conversation, session_for
from test_session_monitors import live_session

from hyprtweaker.engine.model.entities import Bind, DispatcherCall

DEAD = Bind(
    keys="SUPER + notakey", dispatcher=DispatcherCall("exec_cmd", positional=("kitty",))
)
AMP = Bind(keys="SUPER + A&B", dispatcher=DispatcherCall("exec_cmd", positional=("foot",)))
PLAIN = Bind(keys="SUPER + Q", dispatcher=DispatcherCall("window.close"))


@pytest.fixture(autouse=True)
def xkb_knows_all_but_notakey(monkeypatch: pytest.MonkeyPatch) -> None:
    def known(name: str) -> bool:
        return name.lower() not in ("notakey", "a&b")

    monkeypatch.setattr("hyprtweaker.engine.importer.binds.known_keysym", known)
    monkeypatch.setattr("hyprtweaker.engine.triggers.known_keysym", known)


def disabled(bind: Bind) -> Bind:
    return replace(bind, enabled=False)


class TestSetBindEnabled:
    @pytest.mark.parametrize("bind", [DEAD, AMP], ids=["dead-keysym", "ampersand"])
    def test_enabling_an_unloadable_bind_is_refused(self, tmp_path: Path, bind: Bind) -> None:
        session, applier = live_session(tmp_path)
        assert session.add_bind(disabled(bind))

        assert session.set_bind_enabled(0, True) is False

        assert session.model.entities.binds == [disabled(bind)]
        assert applier.commits == 1, "only the add wrote"

    @pytest.mark.parametrize("bind", [DEAD, AMP], ids=["dead-keysym", "ampersand"])
    def test_disabling_is_never_refused(self, tmp_path: Path, bind: Bind) -> None:
        session, applier = live_session(tmp_path)
        assert session.add_bind(disabled(bind))

        assert session.set_bind_enabled(0, False) is True

        assert session.model.entities.binds == [disabled(bind)]
        assert applier.commits == 2

    def test_enabling_a_loadable_bind_still_works(self, tmp_path: Path) -> None:
        session, applier = live_session(tmp_path)
        assert session.add_bind(disabled(PLAIN))

        assert session.set_bind_enabled(0, True) is True

        assert session.model.entities.binds == [PLAIN]
        assert applier.commits == 2


class TestAddAndReplace:
    @pytest.mark.parametrize("bind", [DEAD, AMP], ids=["dead-keysym", "ampersand"])
    def test_an_enabled_unloadable_bind_is_not_added(self, tmp_path: Path, bind: Bind) -> None:
        session, applier = live_session(tmp_path)

        assert session.add_bind(bind) is False
        assert session.add_bind(disabled(bind)) is True

        assert session.model.entities.binds == [disabled(bind)]
        assert applier.commits == 1

    @pytest.mark.parametrize("bind", [DEAD, AMP], ids=["dead-keysym", "ampersand"])
    def test_an_enabled_unloadable_bind_does_not_replace(
        self, tmp_path: Path, bind: Bind
    ) -> None:
        session, applier = live_session(tmp_path)
        assert session.add_bind(PLAIN)

        assert session.replace_bind(0, bind, expected=PLAIN) is False
        assert session.model.entities.binds == [PLAIN]

        assert session.replace_bind(0, disabled(bind), expected=PLAIN) is True
        assert session.model.entities.binds == [disabled(bind)]
        assert applier.commits == 2


class TestEditBinds:
    """The refusal lives in `edit_binds`, so every Bind edit, wrapped or not, meets it."""

    @pytest.mark.parametrize("bind", [DEAD, AMP], ids=["dead-keysym", "ampersand"])
    def test_an_edit_that_stores_a_new_unloadable_bind_is_refused(
        self, tmp_path: Path, bind: Bind
    ) -> None:
        session, applier = live_session(tmp_path)
        assert session.add_bind(PLAIN)

        assert session.edit_binds(lambda binds: binds.insert(0, bind)) is False

        assert session.model.entities.binds == [PLAIN]
        assert applier.commits == 1, "only the add wrote"

    def test_an_edit_that_carries_an_unloadable_bind_along_is_not(self, tmp_path: Path) -> None:
        """Enabled and unloadable already: the edit did not put it there, so it stands."""
        session, applier = live_session(tmp_path)
        session.model.entities.binds[:] = [DEAD, PLAIN]

        assert session.edit_binds(lambda binds: binds.reverse()) is True

        assert session.model.entities.binds == [PLAIN, DEAD]
        assert applier.commits == 1


def test_a_refused_enable_leaves_the_written_binds_lua_alone(tmp_path: Path) -> None:
    """Against a scripted compositor, so the file the Writer produced is what is compared."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()
        assert session.live

        assert session.add_bind(disabled(DEAD))
        await session.drain()
        written = binds_lua(tmp_path)
        assert b"notakey" in written

        assert session.set_bind_enabled(0, True) is False
        await session.drain()
        await runner.settle()

        assert binds_lua(tmp_path) == written

    run_with_fake(scenario, FakeHyprland(section_conversation("general")))


def binds_lua(root: Path) -> bytes:
    return (root / "hypr" / "hyprtweaker" / "binds.lua").read_bytes()
