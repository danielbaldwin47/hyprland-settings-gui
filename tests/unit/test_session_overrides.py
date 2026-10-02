"""The drift badge: knowing that something later in the load order won.

`user.lua` is required last, so a key it sets beats the Module the app wrote. ADR-0005 fixes
how that is noticed -- "after each reload the app compares `get_config`/`getoption` against
its model and badges diverging options as *overridden in user.lua*" -- so it falls out of
the Read-back the transaction already performs.

Notably *not* by reading `user.lua`. ADR-0018 considered evaluating it under the importer's
recording stub and rejected it: "running user code on every app start for a read-only
listing is consent-and-safety weight the feature doesn't earn". A badge earns even less.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from _fake_hyprland import FakeHyprland, option_reply, run_with_fake
from _support import Runner, drain_events, sample_schema, section_conversation, session_for

from hyprtweaker.engine.apply.result import UNREADABLE, ApplyOutcome, ApplyResult, Mismatch
from hyprtweaker.engine.importer.lua.sandbox import lua_binary
from hyprtweaker.engine.model import UNSET
from hyprtweaker.engine.model.values import CssGaps
from hyprtweaker.session import Session

ROUNDING = "decoration:rounding"


def mismatch(expected: Any, actual: Any, *, live_set: bool = True) -> Mismatch:
    return Mismatch(name=ROUNDING, expected=expected, actual=actual, live_set=live_set)


# --- what counts as an override -----------------------------------------------------------


def test_a_live_value_that_disagrees_is_an_override() -> None:
    """The quiet shape: the Module ran, and something later set the key to its own value."""
    assert mismatch(10, 20).overridden is True


def test_a_live_value_that_agrees_is_not_an_override() -> None:
    """The finding this test exists for: a `user.lua` that happens to set what the GUI set
    is not drift, and badging it would tell the user an edit failed that landed perfectly.

    ADR-0005 says *diverging* options, and agreement is not divergence.
    """
    assert mismatch(10, 10).overridden is False


def test_a_float_that_survived_the_wire_is_not_an_override() -> None:
    """Hyprland stores config floats as 32-bit, so an exact comparison would call every
    fractional Option the app ever wrote an override. `values_match` is the arbiter."""
    assert mismatch(0.95, 0.949999988079071).overridden is False


def test_a_complex_value_compares_as_a_value_and_not_as_an_object() -> None:
    assert mismatch(CssGaps(4, 8, 4, 8), CssGaps(4, 8, 4, 8)).overridden is False
    assert mismatch(CssGaps(4, 8, 4, 8), CssGaps(5, 5, 5, 5)).overridden is True


def test_a_key_the_live_config_does_not_set_is_not_an_override() -> None:
    """That is the loud shape -- the Module never ran -- and it is `unapplied`'s to report.

    The two must never both fire on one key: one says "nothing is setting this", the other
    says "something else is setting this", and they cannot both be true.
    """
    unset_live = mismatch(10, None, live_set=False)

    assert unset_live.overridden is False
    assert unset_live.unapplied is True


def test_an_unreadable_value_is_not_an_override() -> None:
    """ADR-0010's Unconfirmed: no answer is not evidence of disagreement, and must never be
    badged as one."""
    assert mismatch(10, UNREADABLE).overridden is False


def test_a_key_the_model_does_not_set_is_not_an_override() -> None:
    """Nothing was asked for, so nothing can have overridden it."""
    assert mismatch(UNSET, 20).overridden is False


# --- what the session does with it ----------------------------------------------------------


def read_back(*mismatches: Mismatch, keys: tuple[str, ...]) -> ApplyResult:
    """A transaction over `keys` whose Read-back found `mismatches`."""
    outcome = ApplyOutcome.READ_BACK_MISMATCH if mismatches else ApplyOutcome.OK
    return ApplyResult(outcome, keys=keys, mismatches=mismatches)


def test_the_session_reports_only_the_diverging_keys() -> None:
    """Every observed reload passes through `_observe`, so the badge is computed in exactly
    one place for every transaction the app runs."""
    with TemporaryDirectory() as root:
        session = session_for(FakeHyprland(), Path(root), Runner())
        session._observe(
            read_back(
                Mismatch(name=ROUNDING, expected=10, actual=20, live_set=True),
                Mismatch(name="general:border_size", expected=2, actual=2, live_set=True),
                Mismatch(name="general:gaps_in", expected=5, actual=None, live_set=False),
                keys=(ROUNDING, "general:border_size", "general:gaps_in"),
            )
        )

        assert session.overridden == {ROUNDING}, "only the key that actually diverged"
        assert session.unapplied == {"general:gaps_in"}, "the loud shape stays separate"


def test_a_clean_read_back_clears_that_keys_stale_badge() -> None:
    """A badge that outlived the reading that earned it would be describing a value that
    has since applied perfectly well."""
    with TemporaryDirectory() as root:
        session = session_for(FakeHyprland(), Path(root), Runner())
        session._observe(read_back(Mismatch(ROUNDING, 10, 20, live_set=True), keys=(ROUNDING,)))
        assert session.overridden == {ROUNDING}

        session._observe(read_back(keys=(ROUNDING,)))

        assert session.overridden == frozenset()


def test_a_read_back_leaves_the_badges_of_keys_it_did_not_read() -> None:
    """An edit to one Option says nothing about another: erasing its badge would hide an
    override that is still in force (#191, settled)."""
    with TemporaryDirectory() as root:
        session = session_for(FakeHyprland(), Path(root), Runner())
        session._observe(
            read_back(
                Mismatch(ROUNDING, 10, 20, live_set=True),
                Mismatch("general:gaps_in", 5, None, live_set=False),
                keys=(ROUNDING, "general:gaps_in"),
            )
        )

        session._observe(read_back(keys=("general:border_size",)))

        assert session.overridden == {ROUNDING}
        assert session.unapplied == {"general:gaps_in"}


# --- the drift scan: what the Row knows before the first edit (#191) ----------------------

GAPS_IN = "general:gaps_in"
WRITTEN = CssGaps(5, 5, 5, 5)
OVERRIDE = CssGaps(20, 20, 20, 20)

needs_lua = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter installed")


def live_says(fake: FakeHyprland, name: str, value: Any, *, live_set: bool = True) -> None:
    """What the running config answers about `name` from now on."""
    fake.conversation[f"j/getoption {name}"] = option_reply(
        sample_schema()[name], value, live_set=live_set
    )


async def launch(fake: FakeHyprland, root: Path) -> tuple[Session, Runner]:
    runner = Runner()
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live, session.offline_reason
    return session, runner


async def edit(session: Session, runner: Runner, name: str, value: Any) -> None:
    session.set_option(name, value)
    await session.drain()
    await runner.settle()


async def app_wrote(fake: FakeHyprland, root: Path, name: str, value: Any) -> None:
    """A previous run of the app wrote `value` into its own Module, and it applied."""
    live_says(fake, name, value)
    session, runner = await launch(fake, root)
    await edit(session, runner, name, value)
    assert name not in session.overridden
    await session.aclose()


async def foreign_reload(fake: FakeHyprland, session: Session, runner: Runner) -> None:
    """A `configreloaded` nobody asked for, and everything the session does about it."""
    await fake.emit("configreloaded")
    await drain_events(runner)
    await session.drain()
    await runner.settle()


def compositor() -> FakeHyprland:
    return FakeHyprland(section_conversation("general", "decoration"), reload_emits_event=True)


@needs_lua
def test_an_override_already_in_place_at_launch_is_badged_before_any_edit(
    tmp_path: Path,
) -> None:
    """The reason the scan exists: `user.lua` won before the app opened, and the Row has to
    say so on sight rather than after the user's first edit happens to read it back."""

    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        live_says(fake, GAPS_IN, OVERRIDE)

        session, _ = await launch(fake, tmp_path)

        assert session.overridden == {GAPS_IN}
        assert session.unapplied == frozenset()

    run_with_fake(scenario, compositor())


@needs_lua
def test_editing_another_option_keeps_the_launch_badge(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        live_says(fake, GAPS_IN, OVERRIDE)
        session, runner = await launch(fake, tmp_path)

        live_says(fake, ROUNDING, 12)
        await edit(session, runner, ROUNDING, 12)

        assert session.overridden == {GAPS_IN}

    run_with_fake(scenario, compositor())


@needs_lua
@pytest.mark.parametrize(
    ("live", "why"),
    [(WRITTEN, "user.lua sets what the app wrote"), ("not gaps", "an unreadable reply")],
)
def test_no_badge_without_a_disagreement(tmp_path: Path, live: Any, why: str) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        live_says(fake, GAPS_IN, live)

        session, _ = await launch(fake, tmp_path)

        assert session.overridden == frozenset(), why
        assert session.unapplied == frozenset(), why

    run_with_fake(scenario, compositor())


@needs_lua
def test_a_module_that_never_ran_is_unapplied_at_launch(tmp_path: Path) -> None:
    """The loud shape the same scan finds: a `require` that failed while the app was shut."""

    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        live_says(fake, GAPS_IN, CssGaps(0, 0, 0, 0), live_set=False)

        session, _ = await launch(fake, tmp_path)

        assert session.unapplied == {GAPS_IN}
        assert session.overridden == frozenset()

    run_with_fake(scenario, compositor())


@needs_lua
def test_a_hand_edited_module_is_not_read_as_what_the_app_asked_for(tmp_path: Path) -> None:
    """ADR-0016 class 2: bytes the Manifest does not recognise are somebody's edit, and say
    nothing about what the app wrote."""

    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        module = tmp_path / "hypr" / "hyprtweaker" / "options" / "general.lua"
        module.write_text(module.read_text() + "-- tweaked by hand\n")
        live_says(fake, GAPS_IN, OVERRIDE)

        session, _ = await launch(fake, tmp_path)

        assert session.overridden == frozenset()

    run_with_fake(scenario, compositor())


@needs_lua
def test_a_foreign_reload_badges_an_override_it_adds_and_clears_one_it_removes(
    tmp_path: Path,
) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        session, runner = await launch(fake, tmp_path)
        assert session.overridden == frozenset()

        live_says(fake, GAPS_IN, OVERRIDE)
        await foreign_reload(fake, session, runner)
        assert session.overridden == {GAPS_IN}, "user.lua gained the key"

        live_says(fake, GAPS_IN, WRITTEN)
        await foreign_reload(fake, session, runner)
        assert session.overridden == frozenset(), "user.lua lost it again"

    run_with_fake(scenario, compositor())


@needs_lua
def test_without_lua_there_is_no_scan_and_no_badge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No interpreter means the app cannot read its own Modules back: that is no scan, not
    "no overrides", and the session still opens live."""

    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        live_says(fake, GAPS_IN, OVERRIDE)
        monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))

        session, _ = await launch(fake, tmp_path)

        assert session.overridden == frozenset()

    run_with_fake(scenario, compositor())


@needs_lua
def test_a_read_back_that_lands_during_a_scan_keeps_its_newer_answer(tmp_path: Path) -> None:
    """The scan reads the Modules before it asks the compositor; a transaction confirmed in
    between knows better about its keys, and the scan must not overwrite that."""

    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        session, runner = await launch(fake, tmp_path)
        live_says(fake, GAPS_IN, OVERRIDE)

        def mid_scan(request: str, _seen: int) -> None:
            # The scan's own question about the key, not the re-read's before it.
            if request == f"j/getoption {GAPS_IN}" and session._drift_watches:
                fake.on_request = None
                session._observe(read_back(keys=(GAPS_IN,)))

        fake.on_request = mid_scan
        await foreign_reload(fake, session, runner)

        assert session.overridden == frozenset()

    run_with_fake(scenario, compositor())


@needs_lua
def test_an_override_is_never_adopted_as_the_users_own_value(tmp_path: Path) -> None:
    """Finding 11 of the #153 review: the launch and foreign-reload re-reads copied the
    value `user.lua` set into the model, so the next write put it in the app's own Module
    and the pill's "a value you set here is kept" was false after a relaunch."""

    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        live_says(fake, GAPS_IN, OVERRIDE)
        session, runner = await launch(fake, tmp_path)
        assert session.model.get(GAPS_IN) == WRITTEN

        live_says(fake, "general:border_size", 3)
        await edit(session, runner, "general:border_size", 3)

        module = tmp_path / "hypr" / "hyprtweaker" / "options" / "general.lua"
        assert session.model.get(GAPS_IN) == WRITTEN
        assert "gaps_in = { top = 5, right = 5, bottom = 5, left = 5 }," in module.read_text()
        await foreign_reload(fake, session, runner)
        assert session.model.get(GAPS_IN) == WRITTEN
        assert session.overridden == {GAPS_IN}

    run_with_fake(scenario, compositor())


@needs_lua
def test_a_timed_out_edit_reads_not_confirmed_until_a_reload_confirms_it(
    tmp_path: Path,
) -> None:
    """Owner call 3 of the #153 review: after a timeout the live value is the old one, and
    "Overridden" (blaming user.lua) would be false; the next reading settles it."""

    async def scenario(fake: FakeHyprland) -> None:
        await app_wrote(fake, tmp_path, GAPS_IN, WRITTEN)
        session, runner = await launch(fake, tmp_path)

        fake.reload_emits_event = False
        await edit(session, runner, GAPS_IN, CssGaps(10, 10, 10, 10))
        await session.drain()
        await runner.settle()

        assert GAPS_IN not in session.overridden
        assert session.unconfirmed == {GAPS_IN}

        fake.reload_emits_event = True
        live_says(fake, GAPS_IN, CssGaps(10, 10, 10, 10))
        await foreign_reload(fake, session, runner)

        assert session.unconfirmed == frozenset()
        assert session.overridden == frozenset()
        assert session.unapplied == frozenset()

    run_with_fake(scenario, compositor())
