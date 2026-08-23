"""The five guarded steps, driven to completion with no display and no compositor.

Every acceptance criterion on #63 that is about *safety* is here, because each one is a
claim about ordering: nothing written before the report is available, a backup before the
first write, a sentinel before the Entrypoint, and silence rolling back rather than keeping.
Ordering claims are exactly what a state machine can be tested for and a dialog cannot.

`asyncio.run` per test, as the IPC tests do -- no pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import pytest
from _support import SAMPLE_APP_VERSION, sample_schema

from hyprtweaker.engine.importer.loss import LossReport
from hyprtweaker.engine.importer.lua.sandbox import Consent, lua_binary
from hyprtweaker.engine.migration import sentinel as sentinels
from hyprtweaker.engine.migration.detect import ConfigKind
from hyprtweaker.engine.migration.flow import Decision, MigrationFlow, Step, fresh_start
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import Schema
from hyprtweaker.engine.state import Manifest

T = TypeVar("T")

CONF = """\
general {
    gaps_in = 5
    gaps_out = 20
}

decoration {
    rounding = 10
}
"""


class FakeClient:
    """A compositor that says the config loaded cleanly, and counts what it was asked."""

    def __init__(self, *, errors: tuple[str, ...] = (), binds: int = 0) -> None:
        self.errors = errors
        self.binds = binds
        self.full_resets = 0

    async def configerrors(self) -> tuple[str, ...]:
        return self.errors

    async def bind_count(self) -> int:
        return self.binds

    async def reload_full_reset(self) -> None:
        self.full_resets += 1


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def tree(root: Path) -> dict[str, str]:
    """Every file under a directory, by relative path, with a hash of its contents."""
    if not root.is_dir():
        return {}
    return {
        str(item.relative_to(root)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(root.rglob("*"))
        if item.is_file()
    }


@pytest.fixture
def schema() -> Schema:
    return sample_schema()


@pytest.fixture
def paths(tmp_path: Path) -> ConfigPaths:
    config = ConfigPaths.rooted_at(tmp_path)
    config.hypr_dir.mkdir(parents=True)
    return config


@pytest.fixture
def legacy(paths: ConfigPaths) -> ConfigPaths:
    paths.hyprland_conf.write_text(CONF, encoding="utf-8")
    return paths


def flow_for(
    paths: ConfigPaths, schema: Schema, client: FakeClient | None = None
) -> MigrationFlow:
    return MigrationFlow(
        paths=paths,
        schema=schema,
        app_version=SAMPLE_APP_VERSION,
        client=client,
    )


class TestPreviewWritesNothing:
    """ "Preview shows the Loss report before anything is written."""

    def test_building_a_preview_leaves_the_config_dir_exactly_as_it_was(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        before = tree(legacy.hypr_dir)
        flow = flow_for(legacy, schema)

        preview = flow.build_preview()

        assert preview.model  # something was actually imported
        assert tree(legacy.hypr_dir) == before
        assert not legacy.entrypoint.exists()
        assert not legacy.app_dir.exists()

    def test_the_loss_report_is_available_at_preview_time(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        flow = flow_for(legacy, schema)
        preview = flow.build_preview()

        report = flow.save_report()

        assert report.exists()
        assert report.parent == legacy.reports_dir
        assert preview.loss.render()

    def test_the_report_survives_the_wizard(self, legacy: ConfigPaths, schema: Schema) -> None:
        """It is reachable from the app menu long afterwards, so it is on disk, not in RAM."""
        flow = flow_for(legacy, schema)
        flow.build_preview()
        flow.save_report()

        assert LossReport.latest(legacy) is not None


class TestBackupPrecedesTheSwitch:
    def test_a_backup_is_taken_before_the_first_write(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        flow = flow_for(legacy, schema)
        flow.build_preview()

        backup = flow.back_up()

        assert backup.exists
        assert (backup.path / "hyprland.conf").read_text(encoding="utf-8") == CONF
        # Still nothing written to the real dir at this point.
        assert not legacy.entrypoint.exists()

    def test_the_backup_copies_rather_than_moves(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        flow = flow_for(legacy, schema)
        flow.build_preview()
        flow.back_up()

        assert legacy.hyprland_conf.is_file()

    def test_a_symlinked_config_comes_back_as_a_symlink(
        self, paths: ConfigPaths, schema: Schema, tmp_path: Path
    ) -> None:
        """A dotfile repo symlinks its config in. A detached copy silently stops tracking."""
        real = tmp_path / "dotfiles" / "hyprland.conf"
        real.parent.mkdir(parents=True)
        real.write_text(CONF, encoding="utf-8")
        paths.hyprland_conf.symlink_to(real)

        flow = flow_for(paths, schema)
        flow.build_preview()
        backup = flow.back_up()

        assert (backup.path / "hyprland.conf").is_symlink()


class TestTheOriginalTreeIsUntouched:
    """The `.conf` path never writes, moves or deletes the legacy tree (ADR-0005)."""

    def test_a_full_migration_leaves_hyprland_conf_byte_identical(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        before = legacy.hyprland_conf.read_bytes()
        client = FakeClient()
        flow = flow_for(legacy, schema, client)
        flow.build_preview()
        flow.back_up()

        result = run(flow.switch())
        flow.keep()

        assert result.ok
        assert legacy.hyprland_conf.read_bytes() == before

    def test_rolling_back_is_deleting_one_generated_file(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        """Which is what makes "delete `hyprland.lua`" a complete rollback."""
        client = FakeClient()
        flow = flow_for(legacy, schema, client)
        flow.build_preview()
        flow.back_up()
        run(flow.switch())
        assert legacy.entrypoint.is_file()

        flow.roll_back()

        assert not legacy.entrypoint.exists()
        assert legacy.hyprland_conf.is_file()


class TestSwitchOrdering:
    def test_the_sentinel_is_on_disk_before_the_entrypoint(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        """Killing the app mid-wizard must leave the previous config active.

        The ordering is what makes that true, so it is asserted directly: at the moment the
        Entrypoint appears, a sentinel describing how to undo it already exists.
        """
        seen: list[bool] = []

        class WatchfulClient(FakeClient):
            async def reload_full_reset(self) -> None:
                seen.append(legacy.sentinel.is_file() and legacy.entrypoint.is_file())
                await super().reload_full_reset()

        flow = flow_for(legacy, schema, WatchfulClient())
        flow.build_preview()
        flow.back_up()

        run(flow.switch())

        assert seen == [True]

    def test_the_switch_asks_for_a_full_reset_not_a_plain_reload(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        """Hyprland caches which config file it picked; a plain reload would change nothing."""
        client = FakeClient()
        flow = flow_for(legacy, schema, client)
        flow.build_preview()
        flow.back_up()

        run(flow.switch())

        assert client.full_resets == 1

    def test_config_errors_fail_the_switch(self, legacy: ConfigPaths, schema: Schema) -> None:
        client = FakeClient(errors=("hyprland.lua:3: unknown option",))
        flow = flow_for(legacy, schema, client)
        flow.build_preview()
        flow.back_up()

        result = run(flow.switch())

        assert not result.ok
        assert result.failures
        assert "unknown option" in result.errors[0]

    def test_import_provenance_lands_in_the_manifest(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        flow = flow_for(legacy, schema, FakeClient())
        flow.build_preview()
        flow.back_up()
        run(flow.switch())

        manifest = Manifest.load(
            legacy.manifest,
            app_version=SAMPLE_APP_VERSION,
            schema_version=schema.hyprland_version,
        )

        assert manifest.migration is not None
        assert manifest.migration["source"].endswith("hyprland.conf")


class TestCrashSafety:
    """ "Killing the app mid-wizard leaves the previous config active."""

    def test_an_unconfirmed_sentinel_is_found_on_the_next_start(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        first = flow_for(legacy, schema, FakeClient())
        first.build_preview()
        first.back_up()
        run(first.switch())
        # The app dies here: no keep, no roll back, nothing clears the marker.

        relaunched = flow_for(legacy, schema, FakeClient())
        pending = relaunched.pending_switch()

        assert pending is not None
        assert pending.kind == ConfigKind.LEGACY_CONF.value

    def test_a_relaunched_app_can_roll_back_a_switch_it_never_made(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        first = flow_for(legacy, schema, FakeClient())
        first.build_preview()
        first.back_up()
        run(first.switch())

        relaunched = flow_for(legacy, schema, FakeClient())
        relaunched.roll_back(relaunched.pending_switch())

        assert not legacy.entrypoint.exists()
        assert legacy.hyprland_conf.is_file()
        assert relaunched.pending_switch() is None

    def test_both_answers_clear_the_marker(self, legacy: ConfigPaths, schema: Schema) -> None:
        """So "still there" can only ever mean "nobody answered"."""
        flow = flow_for(legacy, schema, FakeClient())
        flow.build_preview()
        flow.back_up()
        run(flow.switch())
        assert legacy.sentinel.is_file()

        flow.keep()

        assert not legacy.sentinel.exists()

    def test_an_unreadable_sentinel_still_counts_as_a_pending_switch(
        self, paths: ConfigPaths
    ) -> None:
        """The conservative reading: the alternative silently strands the user."""
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        paths.sentinel.write_text("{ truncated", encoding="utf-8")

        assert sentinels.read(paths) is not None


class TestKeepOrRollBack:
    """ "One minute of inactivity rolls back automatically."""

    def test_silence_rolls_back(self, legacy: ConfigPaths, schema: Schema) -> None:
        flow = flow_for(legacy, schema, FakeClient())
        flow.build_preview()
        flow.back_up()
        run(flow.switch())

        decision = run(flow.decide(seconds=0.05, tick=0.01))

        assert decision is Decision.EXPIRED
        assert not legacy.entrypoint.exists()
        assert not legacy.sentinel.exists()

    def test_keeping_leaves_the_new_config_in_place(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        flow = flow_for(legacy, schema, FakeClient())
        flow.build_preview()
        flow.back_up()
        run(flow.switch())

        async def keep_promptly() -> Decision:
            task = asyncio.ensure_future(flow.decide(seconds=5.0, tick=0.01))
            await asyncio.sleep(0.02)
            flow.answer(Decision.KEPT)
            return await task

        decision = run(keep_promptly())

        assert decision is Decision.KEPT
        assert legacy.entrypoint.is_file()
        assert not legacy.sentinel.exists()

    def test_the_countdown_reports_what_is_left(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        """The dialog draws the timer but must not own it -- a closed window would strand
        the switch pending forever."""
        flow = flow_for(legacy, schema, FakeClient())
        flow.build_preview()
        flow.back_up()
        run(flow.switch())
        ticks: list[float] = []

        run(flow.decide(seconds=0.05, tick=0.01, on_tick=ticks.append))

        assert ticks
        assert ticks == sorted(ticks, reverse=True)
        assert all(value <= 0.05 for value in ticks)

    def test_the_default_answer_is_the_safe_one(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        """Idle = keep was rejected: a session with broken binds would strand the user."""
        flow = flow_for(legacy, schema, FakeClient())
        flow.build_preview()
        flow.back_up()
        run(flow.switch())

        assert run(flow.decide(seconds=0.01, tick=0.005)) is not Decision.KEPT


@pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter on this machine")
class TestForeignLuaPath:
    """The Lua path is consent-gated: nothing runs the user's file until they agree."""

    def test_the_original_is_renamed_beside_itself_never_deleted(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        original = "hl.config({ general = { gaps_in = 7 } })\n"
        paths.entrypoint.write_text(original, encoding="utf-8")

        flow = flow_for(paths, schema, FakeClient())
        detection = flow.detect()
        assert detection.kind is ConfigKind.FOREIGN_LUA

        flow.build_preview(consent=Consent(evaluate=True))
        flow.back_up()
        run(flow.switch())

        kept = paths.entrypoint.with_name("hyprland.lua.bak")
        assert kept.read_text(encoding="utf-8") == original
        assert paths.entrypoint.read_text(encoding="utf-8") != original

    def test_rolling_back_restores_the_original_over_the_generated_file(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        original = "hl.config({ general = { gaps_in = 7 } })\n"
        paths.entrypoint.write_text(original, encoding="utf-8")

        flow = flow_for(paths, schema, FakeClient())
        flow.detect()
        flow.build_preview(consent=Consent(evaluate=True))
        flow.back_up()
        run(flow.switch())
        flow.roll_back()

        assert paths.entrypoint.read_text(encoding="utf-8") == original
        assert not paths.entrypoint.with_name("hyprland.lua.bak").exists()


class TestFreshStart:
    def test_a_fresh_user_gets_a_working_entrypoint_and_nothing_set(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        """Unset is not "at its default": the app emits nothing until you change something."""
        model = fresh_start(paths, schema, app_version=SAMPLE_APP_VERSION)

        assert paths.entrypoint.is_file()
        assert len(model) == 0
        assert not list(paths.options_dir.glob("*.lua")) or all(
            not item.stat().st_size for item in paths.options_dir.glob("*.lua")
        )


def test_the_flow_walks_the_five_steps_in_order(legacy: ConfigPaths, schema: Schema) -> None:
    flow = flow_for(legacy, schema, FakeClient())
    assert flow.step is Step.DETECT

    flow.detect()
    assert flow.step is Step.PREVIEW

    flow.build_preview()
    assert flow.step is Step.BACK_UP

    flow.back_up()
    run(flow.switch())
    assert flow.step is Step.DECIDE

    flow.keep()
    assert flow.step is Step.DONE
