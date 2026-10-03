"""The Manifest on its own: hashing, hand-edit detection, and reading a damaged file.

The Writer tests cover the happy path through a real write. What is worth isolating here
is the unhappy one -- a Manifest that is missing, truncated, or from a future format --
because "the app refuses to start" is the wrong answer to every one of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.state import (
    FORMAT_VERSION,
    Manifest,
    ModuleRecord,
    RetiredValue,
    RetireReason,
    content_hash,
    is_damaged,
)

VERSIONS = {"app_version": "0.1.0", "schema_version": "0.56.2"}


class TestModuleRecord:
    def test_a_record_matches_the_bytes_it_was_made_from(self, tmp_path: Path) -> None:
        text = "hl.config({})\n"
        path = tmp_path / "module.lua"
        path.write_text(text, encoding="utf-8")

        assert ModuleRecord.of(text).matches(path)

    def test_any_edit_breaks_the_match(self, tmp_path: Path) -> None:
        record = ModuleRecord.of("hl.config({})\n")
        path = tmp_path / "module.lua"
        path.write_text("hl.config({ })\n", encoding="utf-8")

        assert not record.matches(path)

    def test_a_missing_file_does_not_match(self, tmp_path: Path) -> None:
        assert not ModuleRecord.of("x").matches(tmp_path / "gone.lua")

    def test_the_hash_is_of_the_utf8_bytes(self) -> None:
        assert ModuleRecord.of("é").sha256 == content_hash("é".encode())


class TestRoundTrip:
    def test_render_then_load_preserves_everything(self, tmp_path: Path) -> None:
        manifest = Manifest(
            app_version="0.1.0",
            schema_version="0.56.2",
            entrypoint=ModuleRecord.of("-- entry\n"),
            modules={"options/general.lua": ModuleRecord.of("hl.config({})\n")},
            migration={"date": "2026-08-22"},
            unverified=("options/misc.lua",),
        )
        path = tmp_path / "manifest.json"
        path.write_text(manifest.render(), encoding="utf-8")

        assert Manifest.load(path, **VERSIONS) == manifest

    def test_it_renders_as_readable_json(self, tmp_path: Path) -> None:
        """A user's App dir may well be in a dotfile repo; a one-line blob is hostile there."""
        rendered = Manifest(**VERSIONS).render()

        assert rendered.startswith("{\n")
        assert rendered.endswith("\n")
        assert f'"format_version": {FORMAT_VERSION}' in rendered


class TestDamagedFiles:
    def test_a_missing_manifest_reads_as_empty(self, tmp_path: Path) -> None:
        manifest = Manifest.load(tmp_path / "nope.json", **VERSIONS)

        assert manifest.modules == {}
        assert manifest.app_version == "0.1.0"

    def test_a_truncated_manifest_reads_as_empty_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        """An unrelated crash mid-write must not brick the app on next launch."""
        path = tmp_path / "manifest.json"
        path.write_text('{"format_version": 2, "modu', encoding="utf-8")

        assert Manifest.load(path, **VERSIONS).modules == {}

    def test_an_unknown_format_version_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text('{"format_version": 99, "modules": {"a.lua": {}}}', encoding="utf-8")

        assert Manifest.load(path, **VERSIONS).modules == {}

    def test_a_malformed_module_entry_is_dropped_not_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            f'{{"format_version": {FORMAT_VERSION}, "modules": {{"a.lua": {{"sha256": 5}}, '
            '"b.lua": {"sha256": "ab", "size": 2}}}',
            encoding="utf-8",
        )

        assert set(Manifest.load(path, **VERSIONS).modules) == {"b.lua"}


class TestRetiredValues:
    """ADR-0012 §Retirement: a removed Option's value persists in the Manifest."""

    def test_retired_values_survive_a_round_trip(self, tmp_path: Path) -> None:
        gradient = {"colors": ["rgba(33ccffee)", "rgba(00ff99ee)"], "angle": 45}
        manifest = Manifest(
            **VERSIONS,
            retired={
                "general:col.active_border": RetiredValue("0.57.0", gradient),
                "decoration:rounding": RetiredValue("0.58.1", 10),
                "general:new_size": RetiredValue("0.59.0", 7, RetireReason.NOT_IN_SCHEMA),
            },
        )
        path = tmp_path / "manifest.json"
        path.write_text(manifest.render(), encoding="utf-8")

        assert Manifest.load(path, **VERSIONS) == manifest

    def test_they_render_as_a_sorted_name_keyed_table(self) -> None:
        """The on-disk shape #178 and a dotfile-repo diff both read."""
        manifest = Manifest(
            **VERSIONS,
            retired={
                "misc:z_last": RetiredValue("0.57.0", "text"),
                "decoration:rounding": RetiredValue("0.58.1", 10),
            },
        )

        assert manifest.as_json()["retired"] == {
            "decoration:rounding": {"retired_in": "0.58.1", "value": 10, "reason": "removed"},
            "misc:z_last": {"retired_in": "0.57.0", "value": "text", "reason": "removed"},
        }
        assert list(manifest.as_json()["retired"]) == ["decoration:rounding", "misc:z_last"]

    def test_a_manifest_written_before_retirement_reads_with_none(self, tmp_path: Path) -> None:
        """Additive key, no format bump: an existing install is neither damaged nor empty."""
        path = tmp_path / "manifest.json"
        path.write_text(
            f'{{"format_version": {FORMAT_VERSION}, "modules": '
            '{"options/general.lua": {"sha256": "ab", "size": 2}}}',
            encoding="utf-8",
        )

        manifest = Manifest.load(path, **VERSIONS)

        assert not is_damaged(path)
        assert manifest.retired == {}
        assert set(manifest.modules) == {"options/general.lua"}

    def test_a_malformed_retired_entry_is_dropped_not_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            f'{{"format_version": {FORMAT_VERSION}, "retired": {{'
            '"a:no_version": {"value": 1}, '
            '"a:no_value": {"retired_in": "0.57.0"}, '
            '"a:not_a_table": 5, '
            '"a:kept": {"retired_in": "0.57.0", "value": false}}}',
            encoding="utf-8",
        )

        assert Manifest.load(path, **VERSIONS).retired == {
            "a:kept": RetiredValue("0.57.0", False)
        }

    def test_a_missing_or_unknown_reason_reads_as_removed(self, tmp_path: Path) -> None:
        """Additive key (#214): an entry written before it, or by a newer build with a reason
        this one does not know, is the announced kind it always was."""
        path = tmp_path / "manifest.json"
        path.write_text(
            f'{{"format_version": {FORMAT_VERSION}, "retired": {{'
            '"a:before": {"retired_in": "0.57.0", "value": 1}, '
            '"a:unknown": {"retired_in": "0.57.0", "value": 2, "reason": "eclipsed"}, '
            '"a:quiet": {"retired_in": "0.59.0", "value": 3, "reason": "not_in_schema"}}}',
            encoding="utf-8",
        )

        assert Manifest.load(path, **VERSIONS).retired == {
            "a:before": RetiredValue("0.57.0", 1),
            "a:unknown": RetiredValue("0.57.0", 2),
            "a:quiet": RetiredValue("0.59.0", 3, RetireReason.NOT_IN_SCHEMA),
        }

    def test_a_retired_table_of_the_wrong_type_reads_as_none(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            f'{{"format_version": {FORMAT_VERSION}, "retired": ["a:b"]}}', encoding="utf-8"
        )

        assert Manifest.load(path, **VERSIONS).retired == {}


class TestRetiredNotices:
    """ADR-0012: one notice per release; the Manifest remembers which releases were shown."""

    def test_a_shown_release_survives_a_round_trip(self, tmp_path: Path) -> None:
        manifest = Manifest(**VERSIONS).with_retired_notice("0.57.0")
        path = tmp_path / "manifest.json"
        path.write_text(manifest.render(), encoding="utf-8")

        assert Manifest.load(path, **VERSIONS).retired_notices == ("0.57.0",)

    def test_releases_are_kept_once_each_in_release_order(self) -> None:
        manifest = (
            Manifest(**VERSIONS)
            .with_retired_notice("0.58.0")
            .with_retired_notice("0.57.10")
            .with_retired_notice("0.57.2")
            .with_retired_notice("0.58.0")
        )

        assert manifest.as_json()["retired_notices"] == ["0.57.2", "0.57.10", "0.58.0"]

    def test_a_manifest_written_before_notices_reads_with_none(self, tmp_path: Path) -> None:
        """Additive key, no format bump (S4): an existing install shows what it never saw."""
        path = tmp_path / "manifest.json"
        path.write_text(f'{{"format_version": {FORMAT_VERSION}}}', encoding="utf-8")

        assert Manifest.load(path, **VERSIONS).retired_notices == ()
        assert not is_damaged(path)

    def test_a_malformed_record_keeps_only_its_versions(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            f'{{"format_version": {FORMAT_VERSION}, "retired_notices": ["0.57.0", 5, null]}}',
            encoding="utf-8",
        )

        assert Manifest.load(path, **VERSIONS).retired_notices == ("0.57.0",)

    def test_a_record_of_the_wrong_type_reads_as_none(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            f'{{"format_version": {FORMAT_VERSION}, "retired_notices": "0.57.0"}}',
            encoding="utf-8",
        )

        assert Manifest.load(path, **VERSIONS).retired_notices == ()


class TestIsDamaged:
    """Absent and unreadable both `load` as empty, and mean opposite things to a writer."""

    def test_absent_is_not_damaged(self, tmp_path: Path) -> None:
        """A fresh App dir: nothing was written here, so there is nothing to protect."""
        assert not is_damaged(tmp_path / "nope.json")

    def test_readable_is_not_damaged(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(Manifest(**VERSIONS).render(), encoding="utf-8")

        assert not is_damaged(path)

    def test_truncated_is_damaged(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text('{"format_version": 2, "modu', encoding="utf-8")

        assert is_damaged(path)

    def test_an_older_format_is_damaged_not_merely_empty(self, tmp_path: Path) -> None:
        """Why the `bytes` -> `size` rename bumped the version.

        Left at 1, an older file would parse to zero records and read as an empty App dir --
        indistinguishable from a first run, and so freely overwritable.
        """
        path = tmp_path / "manifest.json"
        path.write_text(
            '{"format_version": 1, "modules": {"a.lua": {"sha256": "ab", "bytes": 2}}}',
            encoding="utf-8",
        )

        assert is_damaged(path)


class TestHandEditDetection:
    @pytest.fixture
    def paths(self, tmp_path: Path) -> ConfigPaths:
        config = ConfigPaths.rooted_at(tmp_path)
        config.app_dir.mkdir(parents=True)
        return config

    def test_it_names_only_the_changed_modules(self, paths: ConfigPaths) -> None:
        untouched, edited = "hl.config({})\n", "hl.config({ a = 1 })\n"
        (paths.app_dir / "a.lua").write_text(untouched, encoding="utf-8")
        (paths.app_dir / "b.lua").write_text("-- someone else\n", encoding="utf-8")

        manifest = Manifest(
            **VERSIONS,
            modules={
                "a.lua": ModuleRecord.of(untouched),
                "b.lua": ModuleRecord.of(edited),
            },
        )

        assert manifest.hand_edited(paths) == ("b.lua",)

    def test_a_deleted_module_counts_as_edited(self, paths: ConfigPaths) -> None:
        """Deletion is a change the app should surface, not silently undo."""
        manifest = Manifest(**VERSIONS, modules={"gone.lua": ModuleRecord.of("x")})

        assert manifest.hand_edited(paths) == ("gone.lua",)

    def test_a_hand_edited_entrypoint_is_detected_too(self, paths: ConfigPaths) -> None:
        """ADR-0016's Entrypoint refusal: the stored hash has to be compared to something."""
        text = "-- generated\n"
        paths.entrypoint.write_text("-- mine now\n", encoding="utf-8")
        manifest = Manifest(**VERSIONS, entrypoint=ModuleRecord.of(text))

        assert manifest.hand_edited(paths) == ("hyprland.lua",)

    def test_an_untouched_entrypoint_is_not_reported(self, paths: ConfigPaths) -> None:
        text = "-- generated\n"
        paths.entrypoint.write_text(text, encoding="utf-8")
        manifest = Manifest(**VERSIONS, entrypoint=ModuleRecord.of(text))

        assert manifest.hand_edited(paths) == ()

    def test_an_unverified_name_counts_even_with_no_hash_to_compare(
        self, paths: ConfigPaths
    ) -> None:
        """After a lost record there is no hash; the name is the whole claim."""
        (paths.app_dir / "a.lua").write_text("anything\n", encoding="utf-8")
        paths.entrypoint.write_text("anything\n", encoding="utf-8")
        manifest = Manifest(**VERSIONS, unverified=("a.lua", "hyprland.lua"))

        assert manifest.hand_edited(paths) == ("a.lua", "hyprland.lua")

    def test_an_unverified_file_that_is_gone_is_not_reported(self, paths: ConfigPaths) -> None:
        """Nothing was ever claimed about it and it no longer exists -- nothing to protect."""
        manifest = Manifest(**VERSIONS, unverified=("a.lua",))

        assert manifest.hand_edited(paths) == ()


class TestBridges:
    """ADR-0006 §Placement: one entry per Bridge module, added without a format bump (#163)."""

    def test_wiring_records_each_module_and_survives_a_round_trip(self, tmp_path: Path) -> None:
        from hyprtweaker.engine.bridge import ACTIVE, DMS, SHELL_SWITCH, WAITING

        path = tmp_path / "manifest.json"
        manifest = (
            Manifest(**VERSIONS)
            .add_bridge(SHELL_SWITCH, present={"shell-switcher-binds.lua"})
            .add_bridge(DMS, present={"dms/colors.lua"})
        )
        path.write_text(manifest.render(), encoding="utf-8")

        loaded = Manifest.load(path, **VERSIONS)

        assert [(entry.module, entry.file, entry.state) for entry in loaded.bridges] == [
            ("dms.colors", "dms/colors.lua", ACTIVE),
            ("shell-switcher-startup", "shell-switcher-startup.lua", WAITING),
            ("shell-switcher-binds", "shell-switcher-binds.lua", ACTIVE),
        ]
        assert loaded.modules == {}, "a Bridge module is never one of the app's own"

    def test_state_changes_and_removal_are_per_tool(self) -> None:
        from hyprtweaker.engine.bridge import DMS, MATUGEN, WAITING, Off, PresetColors

        manifest = (
            Manifest(**VERSIONS)
            .add_bridge(MATUGEN, present=())
            .add_bridge(DMS, present=())
            .set_bridge_state("dms.colors", Off(PresetColors()))
        )
        assert [(entry.tool, entry.state) for entry in manifest.bridges] == [
            ("dms", Off(PresetColors())),
            ("matugen", WAITING),
        ]
        assert [entry.tool for entry in manifest.remove_bridge("dms").bridges] == ["matugen"]
        assert manifest.remove_bridge("dms").remove_bridge("dms") == manifest.remove_bridge(
            "dms"
        )

    def test_a_manifest_written_before_bridges_reads_with_none(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(f'{{"format_version": {FORMAT_VERSION}, "modules": {{}}}}')

        assert not is_damaged(path)
        assert Manifest.load(path, **VERSIONS).bridges == ()

    def test_a_malformed_entry_is_dropped_not_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            f'{{"format_version": {FORMAT_VERSION}, "bridges": ['
            '{"tool": "dms"}, 7, '
            '{"tool": "dms", "module": "dms.colors", "line": "require(\\"dms.colors\\")", '
            '"file": "dms/colors.lua", "mechanism": "adopt", "state": "active"}]}'
        )

        assert [entry.module for entry in Manifest.load(path, **VERSIONS).bridges] == [
            "dms.colors"
        ]
