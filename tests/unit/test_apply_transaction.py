"""The Apply transaction end to end, against a scripted compositor (ADR-0010).

Nothing is monkeypatched below the transaction: these run a real `Writer` over a real temp
config dir and a real `CommandClient`/`EventStream` over real unix sockets, with only the
compositor faked. The point is that the *ordering* -- gate before disk, one reload, errors
before values -- is exercised rather than asserted about.

`fake.requests` is the wire-level record, and most of these tests read it. It is the only
place the transaction's shape is visible from outside: "exactly one reload" and "errors read
before any value" are both statements about that list.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _fake_hyprland import (
    CONFIG_ERRORS,
    FakeHyprland,
    model_conversation,
    option_reply,
    run_with_fake,
)
from _support import SAMPLE_APP_VERSION, SAMPLE_VERSION, SCHEMA_DIR, sample_model

from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult, ApplyTransaction
from hyprtweaker.engine.ipc import CommandClient, EventStream
from hyprtweaker.engine.model import UNSET, ConfigModel
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer, syntax

RELOAD = "reload"
CONFIGERRORS = "j/configerrors"


def getoption(name: str) -> str:
    return f"j/getoption {name}"


def model_with(**values: object) -> ConfigModel:
    """A model holding exactly `values`, keyed by Option name with `:` written as `__`."""
    model = ConfigModel(load_schema(SAMPLE_VERSION, SCHEMA_DIR))
    for key, value in values.items():
        model.set(key.replace("__", ":"), value)
    return model


def run_apply(
    tmp_path: Path,
    model: ConfigModel,
    *keys: str,
    fake: FakeHyprland | None = None,
    reload_timeout: float = 0.5,
    settle_timeout: float = 0.05,
) -> tuple[ApplyResult, FakeHyprland]:
    """One transaction over a fresh temp config dir, returning the result and the fake.

    `settle_timeout` is squeezed to 50 ms: the Read-back re-read window exists for an 11 ms
    race on a real compositor, and a fake answers instantly, so the full 250 ms would only
    ever be spent making the mismatch tests slow.
    """
    compositor = (
        fake
        if fake is not None
        else FakeHyprland(model_conversation(model), reload_emits_event=True)
    )

    async def scenario(started: FakeHyprland) -> ApplyResult:
        async with EventStream(started.instance) as events:
            # The reload reply is written from inside the command handler, so the listener
            # has to be registered on the server side before a reload can push to it.
            await started.wait_for_listeners(1)
            transaction = ApplyTransaction(
                model=model,
                writer=Writer(ConfigPaths.rooted_at(tmp_path), SAMPLE_APP_VERSION),
                client=CommandClient(started.instance),
                events=events,
                reload_timeout=reload_timeout,
                settle_timeout=settle_timeout,
            )
            return await transaction.run(keys)

    return run_with_fake(scenario, compositor), compositor


def app_dir(tmp_path: Path) -> Path:
    return ConfigPaths.rooted_at(tmp_path).app_dir


# --- the happy path -------------------------------------------------------------------------


class TestOneTransaction:
    """AC: a model edit lands on disk *and* in the live compositor via one transaction."""

    def test_the_edit_reaches_disk(self, tmp_path: Path) -> None:
        model = model_with(decoration__rounding=10)
        result, _ = run_apply(tmp_path, model, "decoration:rounding")

        assert result.outcome is ApplyOutcome.OK
        assert result.ok
        assert result.written == ("options/decoration.lua",)
        assert "rounding = 10" in (app_dir(tmp_path) / "options" / "decoration.lua").read_text()

    def test_the_entrypoint_requires_the_new_module(self, tmp_path: Path) -> None:
        """On disk is not applied: nothing runs a Module the Entrypoint does not require."""
        model = model_with(decoration__rounding=10)
        run_apply(tmp_path, model, "decoration:rounding")

        entrypoint = ConfigPaths.rooted_at(tmp_path).entrypoint.read_text()
        assert 'require("hyprtweaker/options/decoration")' in entrypoint

    def test_the_compositor_is_told_exactly_once(self, tmp_path: Path) -> None:
        """Five dirty Modules, one reload. A reload is a full teardown of the config state."""
        model = sample_model()
        _, fake = run_apply(tmp_path, model, "decoration:rounding", "general:gaps_in")

        assert fake.requests.count(RELOAD) == 1

    def test_errors_are_read_before_any_value(self, tmp_path: Path) -> None:
        """`configerrors` is cleared by the next reload and by any `eval` -- read it first."""
        model = model_with(decoration__rounding=10)
        _, fake = run_apply(tmp_path, model, "decoration:rounding")

        assert fake.requests == [RELOAD, CONFIGERRORS, getoption("decoration:rounding")]

    def test_only_the_touched_keys_are_read_back(self, tmp_path: Path) -> None:
        """The write is the whole model; the Read-back is what the user actually changed."""
        model = sample_model()
        _, fake = run_apply(tmp_path, model, "decoration:rounding")

        assert [line for line in fake.requests if line.startswith("j/getoption")] == [
            getoption("decoration:rounding")
        ]

    def test_every_value_type_survives_the_round_trip(self, tmp_path: Path) -> None:
        """Read-back compares model values to `getoption` replies, which spell them nothing
        like the Lua the writer emitted. A colour is an ARGB word here and `rgba(...)` there.
        """
        model = sample_model()
        keys = [option.name for option, _ in model.set_options()]
        result, _ = run_apply(tmp_path, model, *keys)

        assert result.mismatches == ()
        assert result.outcome is ApplyOutcome.OK


class TestNothingToDo:
    """A second identical apply must not buy a second reload."""

    def test_unchanged_bytes_skip_the_compositor_entirely(self, tmp_path: Path) -> None:
        model = model_with(decoration__rounding=10)
        run_apply(tmp_path, model, "decoration:rounding")

        result, fake = run_apply(tmp_path, model, "decoration:rounding")

        assert result.outcome is ApplyOutcome.NOTHING_TO_DO
        assert result.ok
        assert not result.reached_disk
        assert fake.requests == []


# --- the three failure branches ADR-0010 names ----------------------------------------------


class TestConfigErrors:
    """AC: config errors yield their own ApplyResult."""

    def test_hyprlands_own_lines_come_back_verbatim(self, tmp_path: Path) -> None:
        """ADR-0016 attributes ownership by the `file:line` prefix; reformatting loses it."""
        model = model_with(decoration__rounding=10)
        fake = FakeHyprland(
            model_conversation(model, **{CONFIGERRORS: CONFIG_ERRORS}),
            reload_emits_event=True,
        )
        result, _ = run_apply(tmp_path, model, "decoration:rounding", fake=fake)

        assert result.outcome is ApplyOutcome.CONFIG_ERRORS
        assert not result.ok
        assert len(result.errors) == 2
        assert result.errors[0].startswith(
            "/home/user/.config/hypr/hyprtweaker/options/general.lua:3:"
        )

    def test_a_rejected_config_is_not_also_read_back(self, tmp_path: Path) -> None:
        """The values are whatever survived the failed parse; comparing them says nothing."""
        model = model_with(decoration__rounding=10)
        fake = FakeHyprland(
            model_conversation(model, **{CONFIGERRORS: CONFIG_ERRORS}),
            reload_emits_event=True,
        )
        _, compositor = run_apply(tmp_path, model, "decoration:rounding", fake=fake)

        assert not [line for line in compositor.requests if line.startswith("j/getoption")]

    def test_the_files_stay_written(self, tmp_path: Path) -> None:
        """Rolling back is ADR-0016's decision, not the transaction's -- it only reports."""
        model = model_with(decoration__rounding=10)
        fake = FakeHyprland(
            model_conversation(model, **{CONFIGERRORS: CONFIG_ERRORS}),
            reload_emits_event=True,
        )
        result, _ = run_apply(tmp_path, model, "decoration:rounding", fake=fake)

        assert result.reached_disk
        assert (app_dir(tmp_path) / "options" / "decoration.lua").is_file()


class TestReadBackMismatch:
    """AC: a Read-back mismatch yields its own ApplyResult."""

    def test_a_wrong_live_value_names_both_sides(self, tmp_path: Path) -> None:
        """`user.lua` or a Bridge winning the override order looks exactly like this."""
        model = model_with(decoration__rounding=10)
        option = model.option("decoration:rounding")
        fake = FakeHyprland(
            model_conversation(model, **{getoption(option.name): option_reply(option, 8)}),
            reload_emits_event=True,
        )
        result, _ = run_apply(tmp_path, model, option.name, fake=fake)

        assert result.outcome is ApplyOutcome.READ_BACK_MISMATCH
        assert len(result.mismatches) == 1
        mismatch = result.mismatches[0]
        assert (mismatch.name, mismatch.expected, mismatch.actual) == (option.name, 10, 8)
        assert mismatch.live_set
        assert not mismatch.unapplied

    def test_a_key_the_live_config_never_set_reads_as_unapplied(self, tmp_path: Path) -> None:
        """The loud case: the Module did not run at all -- a failed `require`, or a skip."""
        model = model_with(decoration__rounding=10)
        option = model.option("decoration:rounding")
        fake = FakeHyprland(
            model_conversation(
                model, **{getoption(option.name): option_reply(option, 10, live_set=False)}
            ),
            reload_emits_event=True,
        )
        result, _ = run_apply(tmp_path, model, option.name, fake=fake)

        assert result.outcome is ApplyOutcome.READ_BACK_MISMATCH
        assert result.mismatches[0].unapplied

    def test_a_reset_option_the_live_config_still_sets_is_drift(self, tmp_path: Path) -> None:
        """Read-back doubles as the ADR-0005 drift scan: unset here, still set there."""
        model = model_with(decoration__rounding=10)
        option = model.option("decoration:rounding")
        conversation = model_conversation(model)
        model.unset(option.name)  # the user hit reset; the model now emits nothing
        fake = FakeHyprland(conversation, reload_emits_event=True)
        result, _ = run_apply(tmp_path, model, option.name, fake=fake)

        assert result.outcome is ApplyOutcome.READ_BACK_MISMATCH
        assert result.mismatches[0].expected is UNSET
        assert result.mismatches[0].live_set
        assert not result.mismatches[0].unapplied

    def test_a_reset_option_the_live_config_dropped_is_clean(self, tmp_path: Path) -> None:
        model = model_with(decoration__rounding=10)
        option = model.option("decoration:rounding")
        conversation = model_conversation(model)
        conversation[getoption(option.name)] = option_reply(option, 0, live_set=False)
        model.unset(option.name)
        result, _ = run_apply(
            tmp_path,
            model,
            option.name,
            fake=FakeHyprland(conversation, reload_emits_event=True),
        )

        assert result.outcome is ApplyOutcome.OK

    def test_float32_rounding_is_not_a_mismatch(self, tmp_path: Path) -> None:
        """Hyprland holds config floats as 32-bit; exact `==` would fail every fractional
        Option the app has ever written correctly."""
        model = model_with(decoration__active_opacity=0.95)
        option = model.option("decoration:active_opacity")
        fake = FakeHyprland(
            model_conversation(
                model, **{getoption(option.name): option_reply(option, 0.949999988079071)}
            ),
            reload_emits_event=True,
        )
        result, _ = run_apply(tmp_path, model, option.name, fake=fake)

        assert result.outcome is ApplyOutcome.OK

    def test_a_disagreeing_key_is_re_read_before_it_counts(self, tmp_path: Path) -> None:
        """`configreloaded` fires ~11 ms before the new values are readable, so the first
        `getoption` after it can honestly still answer with the old value."""
        model = model_with(decoration__rounding=10)
        option = model.option("decoration:rounding")
        fake = FakeHyprland(
            model_conversation(model, **{getoption(option.name): option_reply(option, 8)}),
            reload_emits_event=True,
        )
        run_apply(tmp_path, model, option.name, fake=fake, settle_timeout=0.05)

        assert fake.requests.count(getoption(option.name)) > 1

    def test_an_agreeing_key_is_read_exactly_once(self, tmp_path: Path) -> None:
        """The settle window must not cost the common path a second round trip per key."""
        model = model_with(decoration__rounding=10)
        _, fake = run_apply(tmp_path, model, "decoration:rounding")

        assert fake.requests.count(getoption("decoration:rounding")) == 1


class TestTimeout:
    """AC: a timeout yields its own ApplyResult -- an unknown outcome, not a failed one."""

    def test_no_configreloaded_is_a_timeout(self, tmp_path: Path) -> None:
        model = model_with(decoration__rounding=10)
        fake = FakeHyprland(model_conversation(model), reload_emits_event=False)
        result, _ = run_apply(
            tmp_path, model, "decoration:rounding", fake=fake, reload_timeout=0.05
        )

        assert result.outcome is ApplyOutcome.TIMEOUT
        assert not result.ok
        assert "configreloaded" in result.detail

    def test_a_timeout_does_not_read_back(self, tmp_path: Path) -> None:
        """Nothing is known to have reloaded, so a value read would confirm nothing."""
        model = model_with(decoration__rounding=10)
        fake = FakeHyprland(model_conversation(model), reload_emits_event=False)
        _, compositor = run_apply(
            tmp_path, model, "decoration:rounding", fake=fake, reload_timeout=0.05
        )

        assert compositor.requests == [RELOAD]

    def test_the_write_still_happened(self, tmp_path: Path) -> None:
        """The distinction the outcome exists for: durable on disk, fate unknown."""
        model = model_with(decoration__rounding=10)
        fake = FakeHyprland(model_conversation(model), reload_emits_event=False)
        result, _ = run_apply(
            tmp_path, model, "decoration:rounding", fake=fake, reload_timeout=0.05
        )

        assert result.reached_disk


class TestCompositorGone:
    """A socket that is not there is not the same as one that is slow."""

    def test_a_dead_command_socket_is_its_own_outcome(self, tmp_path: Path) -> None:
        model = model_with(decoration__rounding=10)

        async def scenario(started: FakeHyprland) -> ApplyResult:
            async with EventStream(started.instance) as events:
                await started.wait_for_listeners(1)
                transaction = ApplyTransaction(
                    model=model,
                    writer=Writer(ConfigPaths.rooted_at(tmp_path), SAMPLE_APP_VERSION),
                    client=CommandClient(started.instance),
                    events=events,
                    reload_timeout=0.2,
                )
                # Unlink the command socket after the stream is up: the compositor died
                # between the app deciding to apply and the reload going out.
                started.instance.command_socket.unlink()
                return await transaction.run(["decoration:rounding"])

        result = run_with_fake(scenario, FakeHyprland(model_conversation(model)))

        assert result.outcome is ApplyOutcome.COMPOSITOR_GONE
        assert result.reached_disk


# --- restart-flagged Options ------------------------------------------------------------------


class TestPendingRestart:
    """AC: restart-flagged writes skip Read-back and mark Pending restart."""

    RESTART_KEY = "xwayland:enabled"
    """Overlay-flagged `restart: hyprland`. The running session keeps reporting the old
    value however correct the write was, which is exactly why it cannot be confirmed."""

    def test_the_key_is_reported_pending(self, tmp_path: Path) -> None:
        model = model_with(xwayland__enabled=False)
        result, _ = run_apply(tmp_path, model, self.RESTART_KEY)

        assert result.pending_restart == (self.RESTART_KEY,)

    def test_it_is_never_read_back(self, tmp_path: Path) -> None:
        """Reading it back would manufacture a mismatch on every correct write."""
        model = model_with(xwayland__enabled=False)
        _, fake = run_apply(tmp_path, model, self.RESTART_KEY)

        assert getoption(self.RESTART_KEY) not in fake.requests

    def test_a_stale_live_value_is_not_a_mismatch(self, tmp_path: Path) -> None:
        """The compositor still reports the pre-write value, and that is correct of it."""
        model = model_with(xwayland__enabled=False)
        option = model.option(self.RESTART_KEY)
        fake = FakeHyprland(
            model_conversation(model, **{getoption(option.name): option_reply(option, True)}),
            reload_emits_event=True,
        )
        result, _ = run_apply(tmp_path, model, self.RESTART_KEY, fake=fake)

        assert result.outcome is ApplyOutcome.OK
        assert result.mismatches == ()

    def test_its_neighbours_are_still_read_back(self, tmp_path: Path) -> None:
        """Skipping Read-back is per Option, not per transaction."""
        model = model_with(xwayland__enabled=False, decoration__rounding=10)
        _, fake = run_apply(tmp_path, model, self.RESTART_KEY, "decoration:rounding")

        assert [line for line in fake.requests if line.startswith("j/getoption")] == [
            getoption("decoration:rounding")
        ]

    def test_pending_survives_a_failed_apply(self, tmp_path: Path) -> None:
        """Pending is about the file, and the file was written whatever the reload said."""
        model = model_with(xwayland__enabled=False)
        fake = FakeHyprland(
            model_conversation(model, **{CONFIGERRORS: CONFIG_ERRORS}),
            reload_emits_event=True,
        )
        result, _ = run_apply(tmp_path, model, self.RESTART_KEY, fake=fake)

        assert result.outcome is ApplyOutcome.CONFIG_ERRORS
        assert result.pending_restart == (self.RESTART_KEY,)


# --- the pre-disk guarantee ---------------------------------------------------------------


class TestAborted:
    """AC: a syntax-gate failure aborts before any file is replaced."""

    @pytest.fixture(autouse=True)
    def _require_luac(self) -> None:
        """Without `luac` the gate is a documented no-op, so there is nothing to assert.

        Skipping quietly on a machine that promised to have one would be the "no tests ran,
        therefore pass" failure this repo refuses elsewhere -- hence the env check.
        """
        if syntax.gate_available():
            return
        if os.environ.get("HYPRTWEAKER_REQUIRE_LUAC"):
            pytest.fail("HYPRTWEAKER_REQUIRE_LUAC is set but no luac was found")
        pytest.skip("no luac on this machine; the syntax gate degrades to a no-op")

    def break_the_renderer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make the Writer render Lua that does not parse -- the writer bug the gate is for.

        Patched at the renderer rather than at the gate, so what the test proves is that the
        gate is *wired into the write path*, not merely that it can raise when called.
        """
        monkeypatch.setattr(
            "hyprtweaker.engine.writer.writer.render_module",
            lambda *_args, **_kwargs: "hl.config({ = 1 })",
        )

    def test_broken_lua_never_reaches_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = model_with(decoration__rounding=10)
        run_apply(tmp_path, model, "decoration:rounding")
        before = (app_dir(tmp_path) / "options" / "decoration.lua").read_bytes()

        model.set("decoration:rounding", 12)
        self.break_the_renderer(monkeypatch)
        result, fake = run_apply(tmp_path, model, "decoration:rounding")

        assert result.outcome is ApplyOutcome.ABORTED
        assert not result.reached_disk
        assert (app_dir(tmp_path) / "options" / "decoration.lua").read_bytes() == before
        assert fake.requests == []

    def test_the_detail_names_the_module(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message ends up in a bug report; "it failed" helps nobody."""
        model = model_with(decoration__rounding=10)
        self.break_the_renderer(monkeypatch)
        result, _ = run_apply(tmp_path, model, "decoration:rounding")

        assert "options/decoration.lua" in result.detail

    def test_an_unknown_key_aborts_before_rendering(self, tmp_path: Path) -> None:
        """A typo'd key would write a Module that fails the *whole* reload."""
        model = model_with(decoration__rounding=10)
        result, fake = run_apply(tmp_path, model, "decoration:nope")

        assert result.outcome is ApplyOutcome.ABORTED
        assert "decoration:nope" in result.detail
        assert not app_dir(tmp_path).exists()
        assert fake.requests == []


# --- the in-flight flag ---------------------------------------------------------------------


def test_in_flight_is_false_outside_a_transaction(tmp_path: Path) -> None:
    """The flag foreign-reload correlation reads. A stuck `True` would deafen the app."""
    model = model_with(decoration__rounding=10)
    fake = FakeHyprland(model_conversation(model), reload_emits_event=True)

    async def scenario(started: FakeHyprland) -> tuple[bool, bool]:
        async with EventStream(started.instance) as events:
            await started.wait_for_listeners(1)
            transaction = ApplyTransaction(
                model=model,
                writer=Writer(ConfigPaths.rooted_at(tmp_path), SAMPLE_APP_VERSION),
                client=CommandClient(started.instance),
                events=events,
                reload_timeout=0.5,
                settle_timeout=0.05,
            )
            before = transaction.in_flight
            await transaction.run(["decoration:rounding"])
            return before, transaction.in_flight

    assert run_with_fake(scenario, fake) == (False, False)
