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
import threading
from collections.abc import Coroutine
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

import pytest
from _support import SAMPLE_APP_VERSION, sample_schema

from hyprtweaker.engine.importer.loss import LossCode, LossReport
from hyprtweaker.engine.importer.lua.sandbox import Cancelled, Consent, lua_binary
from hyprtweaker.engine.migration import sentinel as sentinels
from hyprtweaker.engine.migration.detect import ConfigKind
from hyprtweaker.engine.migration.flow import (
    Decision,
    MigrationFlow,
    Offered,
    Step,
    fresh_start,
)
from hyprtweaker.engine.model.values import CssGaps
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

    def __init__(
        self,
        *,
        errors: tuple[str, ...] = (),
        binds: int = 0,
        workspace_rules: int = 0,
        monitors: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self.errors = errors
        self.binds = binds
        self.workspace_rules = workspace_rules
        self.outputs = monitors
        self.full_resets = 0
        self.reads: list[str] = []

    async def configerrors(self) -> tuple[str, ...]:
        self.reads.append("configerrors")
        return self.errors

    async def bind_count(self) -> int:
        self.reads.append("binds")
        return self.binds

    async def workspace_rule_count(self) -> int:
        self.reads.append("workspacerules")
        return self.workspace_rules

    async def monitors(self) -> tuple[dict[str, Any], ...]:
        self.reads.append("monitors")
        return self.outputs

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


ENTITY_CONF = """\
bind = SUPER, Q, exec, kitty
bind = SUPER, W, killactive
workspace = 1, gapsin:3
workspace = 2, gapsin:4
monitor = DP-1, 1920x1080@60, 0x0, 1.5
"""

DISPLAY = {
    "name": "DP-1",
    "description": "Dell U2720Q",
    "width": 1920,
    "height": 1080,
    "x": 0,
    "y": 0,
    "scale": 1.5,
    "transform": 0,
}


class TestTheSwitchWritesTheEntities:
    """Found while building the live checks: the wizard imported binds, rules and monitors and
    wrote none of them, because the Writer renders `model.entities` and nothing adopted the
    importer's. The count checks below compare against what is written, so this has to hold."""

    def test_the_converted_tree_carries_the_binds_the_workspace_rules_and_the_monitors(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        paths.hyprland_conf.write_text(ENTITY_CONF, encoding="utf-8")
        flow = flow_for(
            paths, schema, FakeClient(binds=2, workspace_rules=2, monitors=(DISPLAY,))
        )
        flow.build_preview()
        flow.back_up()

        run(flow.switch())

        written = {item.name for item in paths.app_dir.glob("*.lua")}
        assert {"binds.lua", "monitors.lua", "workspace_rules.lua"} <= written
        binds = (paths.app_dir / "binds.lua").read_text(encoding="utf-8")
        assert 'hl.bind("SUPER + Q", hl.dsp.exec_cmd("kitty"))' in binds


class TestLiveChecks:
    """ADR-0009's live checks: what the compositor registered against what was imported."""

    @pytest.fixture
    def entities_conf(self, paths: ConfigPaths) -> ConfigPaths:
        paths.hyprland_conf.write_text(ENTITY_CONF, encoding="utf-8")
        return paths

    def switch(self, paths: ConfigPaths, schema: Schema, client: FakeClient) -> Any:
        flow = flow_for(paths, schema, client)
        flow.build_preview()
        flow.back_up()
        return run(flow.switch())

    def test_a_switch_that_registered_everything_passes_every_check(
        self, entities_conf: ConfigPaths, schema: Schema
    ) -> None:
        client = FakeClient(binds=2, workspace_rules=2, monitors=(DISPLAY,))

        result = self.switch(entities_conf, schema, client)

        assert result.ok
        assert [check.name for check in result.checks] == [
            "configerrors",
            "binds",
            "workspace rules",
            "monitors",
        ]
        assert all(check.ok for check in result.checks)
        assert client.reads == ["configerrors", "binds", "workspacerules", "monitors"]

    def test_missing_binds_roll_the_switch_back(
        self, entities_conf: ConfigPaths, schema: Schema
    ) -> None:
        """A config that loads with no keybinds is the stranded user ADR-0016 exists for."""
        result = self.switch(entities_conf, schema, FakeClient(binds=1, workspace_rules=2))

        assert not result.ok
        assert [check.detail for check in result.failures] == [
            "Only 1 of the 2 keybinds in the new configuration are active."
        ]

    def test_extra_binds_from_a_script_or_legacy_file_are_not_a_failure(
        self, entities_conf: ConfigPaths, schema: Schema
    ) -> None:
        result = self.switch(entities_conf, schema, FakeClient(binds=9, workspace_rules=2))

        assert result.ok

    def test_a_bind_the_writer_never_emits_is_not_expected_live(
        self, entities_conf: ConfigPaths, schema: Schema
    ) -> None:
        """A disabled bind is a comment in `binds.lua`: counting it would roll back real
        migrations over a bind that was never going to fire."""
        flow = flow_for(entities_conf, schema, FakeClient(binds=1, workspace_rules=2))
        preview = flow.build_preview()
        binds = preview.result.entities.binds
        binds[0] = replace(binds[0], enabled=False)
        flow.back_up()

        result = run(flow.switch())

        assert result.ok

    def test_a_missing_workspace_rule_is_reported_without_rolling_back(
        self, entities_conf: ConfigPaths, schema: Schema
    ) -> None:
        result = self.switch(entities_conf, schema, FakeClient(binds=2, workspace_rules=1))

        assert result.ok
        assert [check.detail for check in result.warnings] == [
            "Only 1 of the 2 workspace rules in the new configuration are active."
        ]

    def test_a_display_that_differs_from_its_rule_is_reported_without_rolling_back(
        self, entities_conf: ConfigPaths, schema: Schema
    ) -> None:
        display = {**DISPLAY, "scale": 1.0}

        result = self.switch(
            entities_conf, schema, FakeClient(binds=2, workspace_rules=2, monitors=(display,))
        )

        assert result.ok
        assert [check.detail for check in result.warnings] == [
            "DP-1 is at scale 1, the configuration asks for 1.5"
        ]

    def test_a_config_with_no_entities_asks_only_for_configerrors(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        client = FakeClient()

        result = self.switch(legacy, schema, client)

        assert result.ok
        assert client.reads == ["configerrors"]

    def test_window_and_layer_rules_are_verified_by_configerrors_alone(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        """Hyprland offers no listing of either, so there is nothing to count and no row to
        show: a "could not confirm" line the user cannot act on is noise (ADR-0009)."""
        paths.hyprland_conf.write_text(
            "windowrule {\n    name = float-pavucontrol\n"
            "    match:class = ^(pavucontrol)$\n    float = on\n}\n"
            "layerrule {\n    name = blur-bar\n    match:namespace = bar\n    blur = on\n}\n",
            encoding="utf-8",
        )
        client = FakeClient()
        flow = flow_for(paths, schema, client)
        entities = flow.build_preview().result.entities
        assert entities.window_rules and entities.layer_rules  # the premise
        flow.back_up()

        result = run(flow.switch())

        assert result.ok
        assert [check.name for check in result.checks] == ["configerrors"]
        assert client.reads == ["configerrors"]


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

    def test_a_second_roll_back_leaves_the_restored_original_alone(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        """#148 hand-test 19: the relaunch offer's close response is Roll back, so closing it
        after the press rolled back twice; the second found no .bak and deleted the user's
        restored hyprland.lua, and Hyprland put its autogenerated default in its place."""
        original = "hl.config({ general = { gaps_in = 7 } })\n"
        paths.entrypoint.write_text(original, encoding="utf-8")
        first = flow_for(paths, schema, FakeClient())
        first.detect()
        first.build_preview(consent=Consent(evaluate=True))
        first.back_up()
        run(first.switch())

        relaunched = flow_for(paths, schema, FakeClient())
        pending = relaunched.pending_switch()
        relaunched.roll_back(pending)
        relaunched.roll_back(pending)

        assert paths.entrypoint.read_text(encoding="utf-8") == original

    def test_a_roll_back_leaves_no_app_dir_claiming_the_config(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        """#148 hand-tests 20 and 25: the Manifest and Modules stayed behind, and the next
        launch took the user's own hyprland.lua for the app's and wrote where nothing
        loads. They move into the state directory: kept, and claiming nothing."""
        from hyprtweaker.engine.migration.detect import detect

        original = "hl.config({ general = { gaps_in = 7 } })\n"
        paths.entrypoint.write_text(original, encoding="utf-8")
        flow = flow_for(paths, schema, FakeClient())
        flow.detect()
        flow.build_preview(consent=Consent(evaluate=True))
        flow.back_up()
        run(flow.switch())
        assert paths.manifest.is_file()

        flow.roll_back()

        assert not paths.app_dir.exists()
        kept = list((paths.state_dir / "rolled-back").glob("*/hyprtweaker/manifest.json"))
        assert len(kept) == 1
        assert detect(paths, app_version="x", schema_version="y").kind is ConfigKind.FOREIGN_LUA


class TestTheRescueLine:
    """The line a locked-out user types from a TTY has to match the path they came in on.

    One constant served both, and it deleted `hyprland.lua` -- which is the generated file
    on the legacy path and the user's only config on the Lua path (#131, ADR-0009).
    """

    def test_the_legacy_path_removes_the_generated_entrypoint(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        flow = flow_for(legacy, schema)
        flow.detect()
        assert "rm ~/.config/hypr/hyprland.lua" in flow.rescue_line

    def test_the_lua_path_restores_the_backup(self, paths: ConfigPaths, schema: Schema) -> None:
        paths.entrypoint.write_text("hl.config({})\n", encoding="utf-8")
        flow = flow_for(paths, schema)
        detection = flow.detect()

        assert detection.kind is ConfigKind.FOREIGN_LUA
        assert "hyprland.lua.bak" in flow.rescue_line
        assert "rm " not in flow.rescue_line

    def test_importing_a_conf_that_still_displaces_a_lua_restores_the_backup(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        """The case that makes this a property of the *migration*, not of the file read.

        `build_preview(source=...)` overrides only the source, so importing a `.conf` while
        a foreign `hyprland.lua` is in place still renames that file aside -- and a rescue
        keyed off the imported file's extension would print `rm` for it, deleting the
        Entrypoint and never restoring the backup the switch just made.
        """
        paths.entrypoint.write_text("hl.config({ general = {} })\n", encoding="utf-8")
        elsewhere = paths.hypr_dir / "other.conf"
        elsewhere.write_text(CONF, encoding="utf-8")

        flow = flow_for(paths, schema)
        flow.detect()
        flow.build_preview(source=elsewhere)

        assert flow.preview is not None
        assert flow.preview.detection.source == elsewhere
        assert "hyprland.lua.bak" in flow.rescue_command
        assert "rm " not in flow.rescue_command

    def test_the_rescue_names_the_stamped_backup_a_second_migration_made(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        """`.bak` is only free once. After that the switch stamps the new backup, and a
        rescue still naming the plain `.bak` restores a config two migrations old."""
        paths.entrypoint.write_text("hl.config({ general = {} })\n", encoding="utf-8")
        paths.entrypoint.with_name("hyprland.lua.bak").write_text("old\n", encoding="utf-8")

        flow = flow_for(paths, schema, FakeClient())
        flow.detect()
        flow.build_preview(consent=Consent(evaluate=True))
        flow.back_up()
        run(flow.switch())

        stamped = [
            path.name
            for path in paths.hypr_dir.iterdir()
            if path.name.startswith("hyprland.lua.bak.")
        ]
        assert len(stamped) == 1, f"expected one stamped backup, found {stamped}"
        assert stamped[0] in flow.rescue_command
        assert paths.entrypoint.with_name("hyprland.lua.bak").read_text() == "old\n"

    def test_the_report_carries_the_same_line_the_wizard_shows(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        """The report outlives the dialog, so a disagreement between the two is a rescue
        instruction the user reads after the wizard is gone."""
        paths.entrypoint.write_text("hl.config({})\n", encoding="utf-8")
        flow = flow_for(paths, schema)
        flow.detect()
        preview = flow.build_preview(consent=Consent(evaluate=True))

        assert preview.result.loss.rescue_line == flow.rescue_line


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


class TestTheDiyExit:
    """ "Copy the Lua instead" must hand over the *converted* config, whole."""

    def test_the_copy_carries_variables_and_legacy_constructs(
        self, paths: ConfigPaths, schema: Schema
    ) -> None:
        """Both live only in files the tree write creates, so exporting from the model
        alone would drop them silently -- the user would carry away a config missing
        everything the GUI could not represent."""
        paths.hyprland_conf.write_text(
            "$gap = 5\ngeneral {\n    gaps_in = $gap\n}\nexec-once = waybar\n",
            encoding="utf-8",
        )
        flow = flow_for(paths, schema)
        flow.build_preview()

        text = flow.export_text()

        assert "hl.config(" in text
        result = flow.preview.result  # type: ignore[union-attr]
        if result.variables:
            assert "hyprtweaker/vars" in text
        if result.legacy:
            assert "hyprtweaker/legacy" in text

    def test_the_copy_does_not_inline_the_config_being_replaced(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        """A `user.lua` belonging to the old setup is not part of what conversion produced."""
        legacy.user_lua.write_text("-- from the config being replaced\n", encoding="utf-8")
        flow = flow_for(legacy, schema)
        flow.build_preview()

        assert "from the config being replaced" not in flow.export_text()

    def test_copying_writes_nothing_to_the_real_config_dir(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        before = tree(legacy.hypr_dir)
        flow = flow_for(legacy, schema)
        flow.build_preview()

        flow.export_text()

        assert tree(legacy.hypr_dir) == before


class TestWithoutACompositor:
    """ADR-0009: "without an IPC socket the wizard runs Detect/Preview only"."""

    def test_nothing_is_switched_and_no_sentinel_is_left(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        """A sentinel here would offer, on the next start, to roll back a switch that never
        happened -- and the countdown would auto-roll-back a live config nothing replaced."""
        flow = flow_for(legacy, schema, client=None)
        flow.build_preview()
        flow.back_up()

        result = run(flow.switch())

        assert result.ok
        assert not result.live
        assert not legacy.sentinel.exists()
        assert flow.step is Step.DONE

    def test_the_config_is_still_written_for_next_login(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        flow = flow_for(legacy, schema, client=None)
        flow.build_preview()
        flow.back_up()

        run(flow.switch())

        assert legacy.entrypoint.is_file()


class TestReloadSettling:
    def test_config_errors_are_re_read_before_they_count(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        """`reload full-reset` answers before the new config is parsed, so the first read can
        describe the config being replaced. Believing it would roll back a good migration."""

        class SlowClient(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.reads = 0

            async def configerrors(self) -> tuple[str, ...]:
                self.reads += 1
                # Stale errors from the outgoing config on the first read only.
                return ("hyprland.conf:1: leftover",) if self.reads == 1 else ()

        client = SlowClient()
        flow = flow_for(legacy, schema, client)
        flow.build_preview()
        flow.back_up()

        result = run(flow.switch())

        assert client.reads == 2
        assert result.ok, "a transient first read must not fail the switch"

    def test_errors_that_survive_the_second_read_still_fail(
        self, legacy: ConfigPaths, schema: Schema
    ) -> None:
        client = FakeClient(errors=("hyprland.lua:3: real problem",))
        flow = flow_for(legacy, schema, client)
        flow.build_preview()
        flow.back_up()

        assert not run(flow.switch()).ok


def _foreign_lua(paths: ConfigPaths, source: str) -> MigrationFlow:
    paths.entrypoint.write_text(source, encoding="utf-8")
    flow = flow_for(paths, sample_schema())
    flow.detect()
    return flow


@pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter on this machine")
class TestAReadOffTheMainLoop:
    """The wizard reads in a worker and holds the result on the main loop (#216)."""

    def test_a_read_is_the_flow_s_preview_only_once_held(self, paths: ConfigPaths) -> None:
        flow = _foreign_lua(paths, "hl.config({ general = { gaps_in = 7 } })\n")

        preview = flow.read_preview(consent=Consent(evaluate=True))

        assert flow.preview is None
        flow.hold(preview)
        assert flow.preview is not None
        assert flow.preview.model.get("general:gaps_in") == CssGaps(7, 7, 7, 7)
        assert flow.step is Step.BACK_UP

    def test_a_cancelled_read_leaves_the_flow_where_it_was(self, paths: ConfigPaths) -> None:
        flow = _foreign_lua(paths, "hl.config({ general = { gaps_in = 7 } })\n")
        cancel = threading.Event()
        cancel.set()

        with pytest.raises(Cancelled):
            flow.read_preview(consent=Consent(evaluate=True), cancel=cancel)

        assert flow.preview is None
        assert flow.step is Step.PREVIEW


@pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter on this machine")
class TestTheSecondOffer:
    """After a blocked read that found nothing, the commands it would have run (#190).

    Offered only when the blocked run came back empty or erroring *and* it tried to run a
    command: a config that builds itself from shell output reads as nothing under `BLOCK`,
    and running those commands for real is the only way to read it at all.
    """

    def test_a_config_built_from_a_pipe_offers_its_command(self, paths: ConfigPaths) -> None:
        flow = _foreign_lua(
            paths,
            'local f = io.popen("echo 5")\n'
            'hl.config({ general = { gaps_in = tonumber(f:read("*a")) } })\n',
        )

        preview = flow.build_preview(consent=Consent(evaluate=True))

        assert preview.offered == (Offered("echo 5", "command"),)
        assert preview.imported == 0

    def test_a_blocked_run_that_errors_offers_what_it_ran_before_the_error(
        self, paths: ConfigPaths
    ) -> None:
        flow = _foreign_lua(
            paths,
            'os.execute("hyprctl version")\n'
            'local n = tonumber(io.popen("echo 5"):read("*a"))\n'
            "hl.config({ general = { gaps_in = n + 1 } })\n"
            'io.popen("never reached")\n',
        )

        preview = flow.build_preview(consent=Consent(evaluate=True))

        assert preview.offered == (
            Offered("hyprctl version", "command"),
            Offered("echo 5", "command"),
        )

    def test_file_operations_are_listed_and_repeats_counted(self, paths: ConfigPaths) -> None:
        """Running for real runs everything the blocked read faked, file operations and
        every repeat included, so the offer lists all of it: once each, with a count."""
        flow = _foreign_lua(
            paths,
            'os.remove("stale")\nio.popen("echo 5")\nos.execute("echo 5")\n'
            'os.rename("a.lua", "b.lua")\n',
        )

        preview = flow.build_preview(consent=Consent(evaluate=True))

        assert preview.offered == (
            Offered("stale", "delete"),
            Offered("echo 5", "command", times=2),
            Offered("a.lua -> b.lua", "move"),
        )

    def test_file_operations_alone_offer_nothing(self, paths: ConfigPaths) -> None:
        """Deleting a file never builds a setting: a config that only does that has
        nothing to gain from running for real."""
        flow = _foreign_lua(paths, 'os.remove("stale")\n')

        preview = flow.build_preview(consent=Consent(evaluate=True))

        assert preview.offered == ()

    def test_an_erroring_read_counts_what_it_imported_before_the_error(
        self, paths: ConfigPaths
    ) -> None:
        """The Commands page tells the user what they already have without running
        anything (#150 review, owner call 2): Options plus Entities of the blocked read."""
        flow = _foreign_lua(
            paths,
            "hl.config({ general = { gaps_in = 7, gaps_out = 9 } })\n"
            'hl.bind("SUPER + Q", hl.dsp.exec_cmd("kitty"))\n'
            'local n = tonumber(io.popen("echo 5"):read("*a"))\n'
            "hl.config({ general = { border_size = n + 1 } })\n",
        )

        preview = flow.build_preview(consent=Consent(evaluate=True))

        assert preview.offered == (Offered("echo 5", "command"),)
        assert preview.imported == 3

    def test_a_config_that_read_something_offers_nothing(self, paths: ConfigPaths) -> None:
        flow = _foreign_lua(
            paths,
            'io.popen("echo 5")\nhl.config({ general = { gaps_in = 7 } })\n',
        )

        preview = flow.build_preview(consent=Consent(evaluate=True))

        assert len(preview.model) == 1
        assert preview.offered == ()

    def test_an_error_with_no_command_offers_nothing(self, paths: ConfigPaths) -> None:
        flow = _foreign_lua(paths, 'error("broken")\n')

        preview = flow.build_preview(consent=Consent(evaluate=True))

        assert LossCode.EVAL_ERROR in preview.loss.code_counts()
        assert preview.offered == ()

    def test_running_them_for_real_reads_the_config_and_offers_nothing_more(
        self, paths: ConfigPaths, tmp_path: Path
    ) -> None:
        marker = tmp_path / "ran"
        flow = _foreign_lua(
            paths,
            f'local f = io.popen("touch {marker}; echo 5")\n'
            'hl.config({ general = { gaps_in = tonumber(f:read("*a")) } })\n',
        )
        flow.build_preview(consent=Consent(evaluate=True))
        assert not marker.exists()

        preview = flow.build_preview(consent=Consent(evaluate=True, passthrough=True))

        assert marker.exists()
        assert preview.model.get("general:gaps_in") == CssGaps(5, 5, 5, 5)
        assert preview.offered == ()
