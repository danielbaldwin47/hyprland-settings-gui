"""An editor held open across a list change saves only onto the entry it opened on (#225).

Bind, rule and declaration editors capture a list index when they open, and their save
calls `replace_*(index, entity, expected=...)`. These drive a whole `Session` through the
sequences the diagnosis named: three distinguishable entries alpha, bravo, charlie, an
editor opened on bravo (index 1), the list changed underneath it by somebody else's edit
and `hyprctl reload`, then the editor's save. The draft changes bravo's identity (keys,
match, name), as a user's edit may. Before the fix the held index wrote over charlie.

The UI half (a refused save keeps the dialog and the draft) is
`tests/ui/test_held_editor_draft.py`; the nested run is
`tests/integration/test_held_editor_live.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, drain_events, section_conversation, session_for

from hyprtweaker.engine.model import Bind, DispatcherCall, WindowRule
from hyprtweaker.engine.model.entities import EntitySet, EnvVar
from hyprtweaker.engine.writer.binds import render_binds_module
from hyprtweaker.engine.writer.rules import render_window_rules_module
from hyprtweaker.engine.writer.session_scope import render_env_module
from hyprtweaker.session import Session


def exec_bind(keys: str, command: str) -> Bind:
    return Bind(keys=keys, dispatcher=DispatcherCall(path="exec_cmd", positional=(command,)))


@dataclass(frozen=True)
class Kind:
    """One editor kind: its Module, its three entries and draft, and its Session calls."""

    module: str
    alpha: Any
    bravo: Any
    charlie: Any
    draft: Any
    """What the user saved from the editor opened on bravo."""
    add: Callable[[Session, Any], bool]
    replace: Callable[[Session, int, Any, Any], bool]
    """`(session, index, entity, expected)`: the editor's save."""
    items: Callable[[Session], list[Any]]
    render: Callable[[list[Any]], str | None]
    refused_title: str
    label: Callable[[Any], str]
    """The entry's distinguishing field, lower-cased: "alpha", "bravo", "charlie"."""

    def names(self, session: Session) -> list[str]:
        return [self.label(item) for item in self.items(session)]

    def lists(self, *names: str) -> list[Any]:
        return [getattr(self, name) for name in names]


KINDS = {
    "bind": Kind(
        module="binds.lua",
        alpha=exec_bind("SUPER + A", "alpha"),
        bravo=exec_bind("SUPER + B", "bravo"),
        charlie=exec_bind("SUPER + C", "charlie"),
        draft=exec_bind("SUPER + D", "bravo-edited"),
        add=lambda s, e: s.add_bind(e),
        replace=lambda s, i, e, x: s.replace_bind(i, e, expected=x),
        items=lambda s: s.model.entities.binds,
        render=lambda items: render_binds_module(EntitySet(binds=items), app_version="by-hand"),
        refused_title="Keybind changed",
        label=lambda b: b.dispatcher.positional[0],
    ),
    "window rule": Kind(
        module="window_rules.lua",
        alpha=WindowRule(match={"class": "alpha"}, effects={"float": True}),
        bravo=WindowRule(match={"class": "bravo"}, effects={"float": True}),
        charlie=WindowRule(match={"class": "charlie"}, effects={"float": True}),
        draft=WindowRule(match={"class": "delta"}, effects={"float": True}),
        add=lambda s, e: s.add_rule("window", e),
        replace=lambda s, i, e, x: s.replace_rule("window", i, e, expected=x),
        items=lambda s: s.model.entities.window_rules,
        render=lambda items: render_window_rules_module(items, app_version="by-hand"),
        refused_title="Window rule changed",
        label=lambda r: r.match["class"],
    ),
    "env": Kind(
        module="env.lua",
        alpha=EnvVar("ALPHA", "1"),
        bravo=EnvVar("BRAVO", "2"),
        charlie=EnvVar("CHARLIE", "3"),
        draft=EnvVar("DELTA", "2"),
        add=lambda s, e: s.add_declaration("env", e),
        replace=lambda s, i, e, x: s.replace_declaration("env", i, e, expected=x),
        items=lambda s: s.model.entities.env,
        render=lambda items: render_env_module(items, app_version="by-hand"),
        refused_title="Variable changed",
        label=lambda v: v.name.lower(),
    ),
}

FOREIGN_CHANGES = {
    "reordered": ("alpha", "charlie", "bravo"),
    "removed": ("alpha", "charlie"),
}

HELD = 1
"""The index the editor opened on bravo at."""


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


async def settle(session: Session, runner: Runner) -> None:
    await session.drain()
    await runner.settle()
    await session.drain()


def module_file(root: Path, kind: Kind) -> Path:
    return root / "hypr" / "hyprtweaker" / kind.module


async def foreign_reload(fake: FakeHyprland, session: Session, runner: Runner) -> None:
    await fake.emit("configreloaded")
    await drain_events(runner)
    await settle(session, runner)


async def three_entries(fake: FakeHyprland, root: Path, runner: Runner, kind: Kind) -> Session:
    session = await live_session(fake, root, runner)
    for entity in kind.lists("alpha", "bravo", "charlie"):
        assert kind.add(session, entity)
        await settle(session, runner)
    assert kind.names(session) == ["alpha", "bravo", "charlie"]
    assert kind.names(session).index("bravo") == HELD
    return session


async def hand_edit(
    fake: FakeHyprland,
    root: Path,
    runner: Runner,
    session: Session,
    kind: Kind,
    names: tuple[str, ...],
) -> None:
    """Somebody else rewrites the Module to `names` and reloads Hyprland."""
    text = kind.render(kind.lists(*names))
    assert text is not None
    module_file(root, kind).write_text(text, encoding="utf-8")
    await foreign_reload(fake, session, runner)
    assert kind.names(session) == list(names)


def scenario_runner(scenario: Callable[[FakeHyprland], Any]) -> None:
    run_with_fake(scenario, FakeHyprland(section_conversation(), reload_emits_event=True))


@pytest.mark.parametrize("change", FOREIGN_CHANGES)
@pytest.mark.parametrize("kind_name", KINDS)
def test_a_held_editor_save_into_the_hand_edited_file_is_refused_and_writes_nothing(
    tmp_path: Path, kind_name: str, change: str
) -> None:
    """Half (a): the gate is closed after the adopted hand edit, so the stale index never
    reaches the file. The refusal itself is safe; the draft is the UI tier's question."""
    kind = KINDS[kind_name]
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await three_entries(fake, tmp_path, runner, kind)
        await hand_edit(fake, tmp_path, runner, session, kind, FOREIGN_CHANGES[change])
        session.on_refused = lambda what, file: refused.append((what, file))
        before = module_file(tmp_path, kind).read_bytes()

        assert kind.replace(session, HELD, kind.draft, kind.bravo) is False
        await settle(session, runner)

        assert module_file(tmp_path, kind).read_bytes() == before
        assert kind.names(session) == list(FOREIGN_CHANGES[change])
        assert refused == [(kind.refused_title, kind.module)]

    scenario_runner(scenario)


@pytest.mark.parametrize("change", FOREIGN_CHANGES)
@pytest.mark.parametrize("kind_name", KINDS)
def test_a_held_editor_save_after_replace_never_writes_over_another_entry(
    tmp_path: Path, kind_name: str, change: str
) -> None:
    """Half (b), at the Session: Replace opens the gate (the file is the app's again,
    holding the adopted list), and the held editor then saves. Not reachable from the
    window today, whose dialog blocks the Banner while it is open; this pins the index
    itself. Bravo is no longer at index 1, so the save is refused and says why."""
    kind = KINDS[kind_name]
    not_saved: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await three_entries(fake, tmp_path, runner, kind)
        await hand_edit(fake, tmp_path, runner, session, kind, FOREIGN_CHANGES[change])
        assert session.replace_edited_file(kind.module)
        await settle(session, runner)
        session.on_not_saved = lambda what, why: not_saved.append((what, why))
        written = module_file(tmp_path, kind).read_bytes()

        assert kind.replace(session, HELD, kind.draft, kind.bravo) is False
        await settle(session, runner)

        assert kind.names(session) == list(FOREIGN_CHANGES[change])
        assert module_file(tmp_path, kind).read_bytes() == written
        assert not_saved == [(kind.refused_title, "it changed outside this app")]

    scenario_runner(scenario)


async def reorder_then_revert(
    fake: FakeHyprland, root: Path, runner: Runner, kind: Kind
) -> Session:
    """Somebody reorders the Module to alpha, charlie, bravo and reloads, then puts the
    app's own bytes back (an editor's undo, `git checkout`) and reloads again."""
    session = await three_entries(fake, root, runner, kind)
    app_bytes = module_file(root, kind).read_bytes()
    await hand_edit(fake, root, runner, session, kind, ("alpha", "charlie", "bravo"))
    module_file(root, kind).write_bytes(app_bytes)
    await foreign_reload(fake, session, runner)
    return session


@pytest.mark.parametrize("kind_name", KINDS)
def test_a_hand_edit_put_back_to_the_apps_bytes_is_read_back_too(
    tmp_path: Path, kind_name: str
) -> None:
    kind = KINDS[kind_name]

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await reorder_then_revert(fake, tmp_path, runner, kind)

        assert kind.names(session) == ["alpha", "bravo", "charlie"]

    scenario_runner(scenario)


@pytest.mark.parametrize("kind_name", KINDS)
def test_a_held_editor_save_after_a_reverted_hand_edit_never_writes_over_another_entry(
    tmp_path: Path, kind_name: str
) -> None:
    """The external-only route to half (b): no Replace, no app gesture while the editor
    is held, and the file ends as the app wrote it before the save."""
    kind = KINDS[kind_name]

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await reorder_then_revert(fake, tmp_path, runner, kind)

        assert kind.replace(session, HELD, kind.draft, kind.bravo)
        await settle(session, runner)

        assert kind.names(session) == ["alpha", kind.label(kind.draft), "charlie"]

    scenario_runner(scenario)
