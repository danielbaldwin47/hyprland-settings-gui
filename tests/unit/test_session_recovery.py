"""Error surfacing and recovery, driven through a whole `Session` (ADR-0016, #60).

The matrix itself is `test_apply_recovery.py`'s -- lines in, actions out, no I/O. What is
tested here is everything that only exists once the policy has to *happen*: a Banner that
raises and clears on the right reloads, a restore that puts both the file and the model back,
a Quarantine that survives the next write, and the one case where the app overwrites a hand
edit without asking.

Deliberately from outside. Every assertion is about what a user would see or what is on disk,
because an implementation that got the bytes right and the model wrong -- or the Banner right
and the Entrypoint wrong -- would look perfectly healthy from inside.
"""

from __future__ import annotations

from pathlib import Path

from _fake_hyprland import NO_BINDS, FakeHyprland, run_with_fake
from _support import Runner, drain_events, section_conversation, session_for

from hyprtweaker.engine.apply import Action, Ownership
from hyprtweaker.engine.model import UNSET
from hyprtweaker.engine.state import Manifest
from hyprtweaker.session import Session

BORDER_SIZE = "general:border_size"
ROUNDING = "decoration:rounding"
GENERAL_MODULE = "options/general.lua"

USER_ERROR = "[\n\t\"/home/user/.config/hypr/user.lua:12: unexpected symbol near '}'\"\n]\n"
"""A foreign file's error. Absolute and suffix-matched, like every other path Hyprland
prints -- see `ownership.py` on why the app never compares against its own idea of the path."""

APP_ERROR = (
    '[\n\t"/home/user/.config/hypr/hyprtweaker/options/general.lua:4: '
    "unknown config key 'general.nope'\"\n]\n"
)

ENTRYPOINT_ERROR = (
    "[\n\t\"/home/user/.config/hypr/hyprland.lua:2: unexpected symbol near 'require'\"\n]\n"
)


def conversation(**set_values: object) -> dict[str, str]:
    return section_conversation("general", "decoration", **set_values)


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


async def settle(session: Session, runner: Runner) -> None:
    """Wait out the queue, then whatever the finished transactions spawned, then the queue."""
    await session.drain()
    await runner.settle()
    await session.drain()
    await runner.settle()


def break_once(fake: FakeHyprland, errors: str, binds: str) -> None:
    """Report a broken config for exactly one `configerrors` read, then be healthy again.

    What a recovery that *works* looks like from outside: the reload the app is reacting to
    is broken, and the reload its restore performs is not. A fake that stayed broken would
    measure the escalation path instead.
    """
    clean_errors = fake.conversation["j/configerrors"]
    clean_binds = fake.conversation["j/binds"]
    armed = [True]

    def hook(request: str, _seen: int) -> None:
        if request != "j/configerrors":
            return
        fake.conversation["j/configerrors"] = errors if armed[0] else clean_errors
        fake.conversation["j/binds"] = binds if armed[0] else clean_binds
        armed[0] = False

    fake.on_request = hook


async def foreign_reload(fake: FakeHyprland, session: Session, runner: Runner) -> None:
    """Push a `configreloaded` nobody asked for, and wait for the app to finish reacting.

    The sleep is the event stream's: a pushed line has to cross a socket and be dispatched
    before the session has spawned anything to wait on, so draining immediately would return
    while the interesting half had not started.
    """
    await fake.emit("configreloaded")
    await drain_events(runner)
    await settle(session, runner)


def app_dir(root: Path) -> Path:
    return root / "hypr" / "hyprtweaker"


def entrypoint(root: Path) -> str:
    return (root / "hypr" / "hyprland.lua").read_text()


def manifest_of(root: Path) -> Manifest:
    return Manifest.load(app_dir(root) / "manifest.json", app_version="x", schema_version="y")


# --- the Banner raises and clears -------------------------------------------------------------


def test_a_foreign_error_raises_the_banner_without_touching_the_file(tmp_path: Path) -> None:
    """ADR-0016 class 3: `user.lua` gets a Banner and an offer, never a rewrite."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        user_lua = tmp_path / "hypr" / "user.lua"
        user_lua.parent.mkdir(parents=True, exist_ok=True)
        user_lua.write_text("-- broken }\n")
        before = user_lua.read_bytes()

        fake.conversation["j/configerrors"] = USER_ERROR
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        health = session.health
        assert health.unhealthy
        assert health.button == "Details"

        (problem,) = session.recovery.problems
        assert problem.ownership is Ownership.FOREIGN
        assert problem.actions == (Action.OPEN_FILE, Action.QUARANTINE)
        assert problem.line == 12
        assert user_lua.read_bytes() == before, "the app must never write user.lua"

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_clean_reload_clears_the_banner(tmp_path: Path) -> None:
    """A Banner that only ever raised would outlive the problem it named."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        fake.conversation["j/configerrors"] = USER_ERROR
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        assert session.recovery.unhealthy

        fake.conversation["j/configerrors"] = '[\n\t""\n]\n'
        session.set_option(ROUNDING, 14)
        await settle(session, runner)

        assert not session.recovery.unhealthy
        assert not session.health.unhealthy

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 14}), reload_emits_event=True)
    )


def test_breakage_that_happened_while_the_app_was_closed_surfaces_at_startup(
    tmp_path: Path,
) -> None:
    """ADR-0016 §Surfacing: startup feeds the same pipeline as an apply."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        fake.conversation["j/configerrors"] = USER_ERROR

        session = await live_session(fake, tmp_path, runner)

        assert session.recovery.unhealthy, "the app has to open saying the config is broken"
        assert session.health.button == "Details"

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_an_entrypoint_refusal_reads_as_the_previous_config_still_running(
    tmp_path: Path,
) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        fake.conversation["j/configerrors"] = ENTRYPOINT_ERROR

        session = await live_session(fake, tmp_path, runner)

        assert session.recovery.entrypoint_refused
        assert session.health.severe
        assert "running the previous config" in session.health.title
        (problem,) = session.recovery.problems
        assert problem.offers(Action.REGENERATE)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- restore last good ---------------------------------------------------------------------


def test_restore_last_good_puts_the_file_and_the_model_back(tmp_path: Path) -> None:
    """The two halves ADR-0016 needs, and the half that is easy to forget is the model.

    A restore that only wrote bytes would be undone by the very next edit: Modules are
    rendered whole from the model, so a model still holding the broken value re-renders it.
    """

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        good = (app_dir(tmp_path) / GENERAL_MODULE).read_bytes()

        # A hand edit lands in the same Module. The app owns the file but did not write this.
        (app_dir(tmp_path) / GENERAL_MODULE).write_bytes(b"-- hand edited\n")
        fake.conversation["j/getoption " + BORDER_SIZE] = (
            '{"option": "general:border_size", "int": 3, "set": true }'
        )

        assert session.restore_last_good(GENERAL_MODULE)
        await settle(session, runner)

        assert (app_dir(tmp_path) / GENERAL_MODULE).read_bytes() == good
        assert session.model.get(BORDER_SIZE) == 3, "the model was re-read off the compositor"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_a_restored_module_is_the_apps_own_again(tmp_path: Path) -> None:
    """Otherwise the file the app just wrote reads as hand-edited and the next write stands
    down from it -- freezing the user's recovery in place."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)

        (app_dir(tmp_path) / GENERAL_MODULE).write_bytes(b"-- hand edited\n")
        assert GENERAL_MODULE in manifest_of(tmp_path).hand_edited(session.paths)

        session.restore_last_good(GENERAL_MODULE)
        await settle(session, runner)

        assert GENERAL_MODULE not in manifest_of(tmp_path).hand_edited(session.paths)

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_a_module_with_no_confirmed_write_has_nothing_to_restore(tmp_path: Path) -> None:
    """ "Restore whatever is newest" is exactly what ADR-0016 rules out: the newest may be
    what broke."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        assert session.last_good_for(GENERAL_MODULE) is None
        assert not session.restore_last_good(GENERAL_MODULE)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- the zero-binds emergency --------------------------------------------------------------


def test_zero_binds_restores_an_app_module_past_the_consent_gate(tmp_path: Path) -> None:
    """ADR-0016 §Zero-binds: "stranded-user beats hand-edit sanctity".

    The same hand edit that gets a Banner and a button when the user still has keybinds gets
    overwritten without a question when they do not -- because without binds they may not be
    able to open a terminal to fix it themselves.
    """

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        good = (app_dir(tmp_path) / GENERAL_MODULE).read_bytes()

        hand_edit = b"-- hand edited, and broken\n"
        (app_dir(tmp_path) / GENERAL_MODULE).write_bytes(hand_edit)
        fake.conversation["j/configerrors"] = APP_ERROR
        fake.conversation["j/binds"] = NO_BINDS

        # A foreign reload is how the app finds out, and it must act on it unprompted.
        await foreign_reload(fake, session, runner)

        assert (app_dir(tmp_path) / GENERAL_MODULE).read_bytes() == good

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_the_overwritten_hand_edit_is_preserved_in_the_journal(tmp_path: Path) -> None:
    """The ADR spends the hand edit only because it can promise it back."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)

        hand_edit = b"-- hand edited, and broken\n"
        (app_dir(tmp_path) / GENERAL_MODULE).write_bytes(hand_edit)
        fake.conversation["j/configerrors"] = APP_ERROR
        fake.conversation["j/binds"] = NO_BINDS
        await foreign_reload(fake, session, runner)

        stored = [
            session.journal.snapshot(change.before)
            for entry in session.journal.entries()
            for change in entry.changes
            if change.module == GENERAL_MODULE
        ]
        assert hand_edit in stored

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_the_banner_reports_what_the_emergency_overwrote(tmp_path: Path) -> None:
    """ADR-0016: the overwritten hand edit is "preserved in the Journal and reported in the
    Banner". Quietly keeping the edit and quietly taking it are different promises."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)

        (app_dir(tmp_path) / GENERAL_MODULE).write_bytes(b"-- hand edited, and broken\n")
        break_once(fake, APP_ERROR, NO_BINDS)
        await foreign_reload(fake, session, runner)

        title = session.health.title
        assert "general.lua" in title
        assert "history" in title, "the user has to be told their edit was kept"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_an_emergency_restore_that_does_not_help_stops_rather_than_looping(
    tmp_path: Path,
) -> None:
    """ADR-0016: "if the restore transaction itself errors ... stop auto-writing until the
    user acts".

    The emergency fires on a *state* -- errors plus zero binds -- and its own restore ends in
    a reload that re-observes that state. Without a gate the app would restore, find itself
    stranded again, and restore again, forever.
    """

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)

        (app_dir(tmp_path) / GENERAL_MODULE).write_bytes(b"-- hand edited, and broken\n")
        # Stays broken however many times the app tries: the error is somewhere else.
        fake.conversation["j/configerrors"] = APP_ERROR
        fake.conversation["j/binds"] = NO_BINDS
        await foreign_reload(fake, session, runner)

        reloads = fake.requests.count("reload")
        await foreign_reload(fake, session, runner)

        assert session.recovery_halted
        assert fake.requests.count("reload") - reloads <= 1, "it must not keep hammering"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


# --- read-back mismatch and timeout ----------------------------------------------------------


def test_a_value_that_did_not_take_joins_the_banner(tmp_path: Path) -> None:
    """ADR-0016: an unexplained read-back mismatch "joins the Banner".

    The loud shape: the model sets the key, the live config sets nothing, and no error says
    why -- which means the Module never ran.
    """

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        # The compositor accepts the reload but reports the key as never set.
        fake.conversation[f"j/getoption {ROUNDING}"] = (
            '{"option": "decoration:rounding", "int": 0, "set": false }'
        )
        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        assert session.health.unhealthy
        assert ROUNDING in session.health.unapplied
        assert "did not take effect" in session.health.title
        assert not session.recovery.unhealthy, "no configerrors -- this is the quiet failure"

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_timeout_re_polls_once_and_raises_the_banner_for_what_it_finds(
    tmp_path: Path,
) -> None:
    """ADR-0016 §Timeout: "re-poll once; if still unconfirmed, treat as a foreign-unknown
    state -- full re-read, Banner if errors"."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        # The reload never announces itself, so the transaction times out; by the time the
        # app looks again, the config is visibly broken.
        fake.reload_emits_event = False
        fake.conversation["j/configerrors"] = USER_ERROR
        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        assert session.recovery.unhealthy, "the re-poll found what the timeout could not"
        assert session.health.button == "Details"

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_binds_present_leaves_a_hand_edit_alone(tmp_path: Path) -> None:
    """The consent gate is up in every case but the emergency."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)

        hand_edit = b"-- hand edited, and broken\n"
        (app_dir(tmp_path) / GENERAL_MODULE).write_bytes(hand_edit)
        fake.conversation["j/configerrors"] = APP_ERROR
        await foreign_reload(fake, session, runner)

        assert (app_dir(tmp_path) / GENERAL_MODULE).read_bytes() == hand_edit
        (problem,) = session.recovery.problems
        assert problem.offers(Action.RESTORE_LAST_GOOD), "offered, not taken"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_a_stranded_banner_names_the_file_and_line(tmp_path: Path) -> None:
    """ADR-0016 spells this one out, because the user is reading it off a screen they cannot
    navigate away from."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        fake.conversation["j/configerrors"] = USER_ERROR
        fake.conversation["j/binds"] = NO_BINDS

        session = await live_session(fake, tmp_path, runner)

        assert session.health.title == "Your keybinds are not loaded — error in user.lua:12"
        assert session.health.severe

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- quarantine ---------------------------------------------------------------------------


def test_quarantine_drops_the_require_and_states_it_in_the_file(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        user_lua = tmp_path / "hypr" / "user.lua"
        user_lua.parent.mkdir(parents=True, exist_ok=True)
        user_lua.write_text("-- broken }\n")

        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        assert 'require("user")' in entrypoint(tmp_path)

        assert session.quarantine("user")
        await settle(session, runner)

        text = entrypoint(tmp_path)
        assert 'require("user")' not in text.replace('-- require("user")', "")
        assert '-- require("user")' in text, "the file has to say what happened"
        assert session.quarantined == ("user",)
        assert user_lua.read_text() == "-- broken }\n", "still never written"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 12}), reload_emits_event=True)
    )


def test_quarantine_reverses_in_one_call(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        user_lua = tmp_path / "hypr" / "user.lua"
        user_lua.parent.mkdir(parents=True, exist_ok=True)
        user_lua.write_text("-- fixed now\n")

        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        session.quarantine("user")
        await settle(session, runner)
        assert session.quarantined == ("user",)

        assert session.release_quarantine("user")
        await settle(session, runner)

        assert session.quarantined == ()
        assert 'require("user")' in entrypoint(tmp_path).replace('-- require("user")', "")

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 12}), reload_emits_event=True)
    )


def test_a_quarantine_survives_the_next_ordinary_write(tmp_path: Path) -> None:
    """The failure this guards against is silent: an edit regenerating the require would put
    the broken file back without ever saying so."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        user_lua = tmp_path / "hypr" / "user.lua"
        user_lua.parent.mkdir(parents=True, exist_ok=True)
        user_lua.write_text("-- broken }\n")

        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        session.quarantine("user")
        await settle(session, runner)

        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)

        text = entrypoint(tmp_path)
        assert 'require("user")' not in text.replace('-- require("user")', "")

    run_with_fake(
        scenario,
        FakeHyprland(conversation(**{ROUNDING: 12, BORDER_SIZE: 3}), reload_emits_event=True),
    )


def test_an_active_quarantine_keeps_the_banner_up(tmp_path: Path) -> None:
    """A config that is not doing what its owner wrote has to keep saying so."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        user_lua = tmp_path / "hypr" / "user.lua"
        user_lua.parent.mkdir(parents=True, exist_ok=True)
        user_lua.write_text("-- broken }\n")

        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        session.quarantine("user")
        await settle(session, runner)

        health = session.health
        assert health.unhealthy
        assert "user.lua is disabled" in health.title
        assert health.button == "Re-enable"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 12}), reload_emits_event=True)
    )


def test_the_quarantine_target_is_the_require_the_entrypoint_emits(tmp_path: Path) -> None:
    """Matched against the generated require list, never derived from the printed path --
    which may have come through a symlink and would leave the Banner lying."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        user_lua = tmp_path / "hypr" / "user.lua"
        user_lua.parent.mkdir(parents=True, exist_ok=True)
        user_lua.write_text("-- broken }\n")

        fake.conversation["j/configerrors"] = USER_ERROR
        session = await live_session(fake, tmp_path, runner)

        (problem,) = session.recovery.problems
        assert session.quarantine_target(problem) == "user"

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_an_app_module_is_never_a_quarantine_target(tmp_path: Path) -> None:
    """Leaving out a Module the model still renders would put the two permanently at odds."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        fake.conversation["j/configerrors"] = APP_ERROR
        session = await live_session(fake, tmp_path, runner)

        (problem,) = session.recovery.problems
        assert session.quarantine_target(problem) is None

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- regenerate the entrypoint ---------------------------------------------------------------


def test_regenerate_rewrites_a_hand_edited_entrypoint(tmp_path: Path) -> None:
    """The one app-owned file whose hand edit is overwritten on request without ceremony: it
    holds no decisions, only a derived require list."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        good = entrypoint(tmp_path)

        (tmp_path / "hypr" / "hyprland.lua").write_text("this is not lua {\n")

        assert session.regenerate_entrypoint()
        await settle(session, runner)

        assert entrypoint(tmp_path) == good

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 12}), reload_emits_event=True)
    )


# --- errors never badge Rows ------------------------------------------------------------------


def test_config_errors_never_reach_a_rows_chrome(tmp_path: Path) -> None:
    """ADR-0016 §Surfacing: "Config errors are file-scoped and never appear on Rows".

    Asserted against `row_state`, which is where every pill a Row can wear is decided -- so a
    later change that tried to badge an error would have to come through here.
    """
    from hyprtweaker.ui.rows.state import row_state

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        fake.conversation["j/configerrors"] = APP_ERROR
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        assert session.recovery.unhealthy, "the precondition: something is wrong"

        option = session.schema[ROUNDING]
        state = row_state(option, session)

        assert state.pills == (), "an error is file-scoped; the Banner carries it"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 12}), reload_emits_event=True)
    )


def test_a_value_that_did_not_take_does_badge_its_row(tmp_path: Path) -> None:
    """The one carve-out ADR-0016 makes: an unexplained mismatch is *key*-scoped, unlike a
    config error, so it belongs on the Row as well as on the Banner."""
    from hyprtweaker.ui.rows.state import UNAPPLIED_PILL, row_state

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        fake.conversation[f"j/getoption {ROUNDING}"] = (
            '{"option": "decoration:rounding", "int": 0, "set": false }'
        )
        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        state = row_state(session.schema[ROUNDING], session)
        assert [pill.label for pill in state.pills] == [UNAPPLIED_PILL]

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 12}), reload_emits_event=True)
    )


def test_a_read_only_session_still_has_exactly_one_banner_line(tmp_path: Path) -> None:
    """Four unhealthy states, one Banner: something has to rank them, and it is `Health`."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.set_read_only("Hyprland is no longer running")

        health = session.health
        assert health.unhealthy
        assert health.title == "Hyprland is no longer running — settings are read-only."
        assert session.model.get(ROUNDING) is UNSET

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))
