"""The Writer against a real directory: what lands, what is left alone, what is pruned.

Everything here is behaviour a user would notice -- a file appearing, a file surviving, a
reload not happening. The App dir is a throwaway `tmp_path`, so these run anywhere.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from _support import SAMPLE_APP_VERSION, sample_model

from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import Schema
from hyprtweaker.engine.state import Manifest
from hyprtweaker.engine.writer import ProtectedFile, Writer, gate_available


@pytest.fixture
def paths(tmp_path: Path) -> ConfigPaths:
    config = ConfigPaths.rooted_at(tmp_path)
    config.hypr_dir.mkdir(parents=True)
    return config


@pytest.fixture
def writer(paths: ConfigPaths) -> Writer:
    return Writer(paths, app_version=SAMPLE_APP_VERSION)


@pytest.fixture
def model() -> ConfigModel:
    return sample_model()


class TestWhatLands:
    def test_one_module_per_section_plus_an_entrypoint_and_a_manifest(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        result = writer.write(model)

        assert set(result.written) == {
            "options/general.lua",
            "options/decoration.lua",
            "options/group.lua",
            "options/misc.lua",
            "options/input_capture.lua",
        }
        assert paths.entrypoint.is_file()
        assert paths.manifest.is_file()

    def test_an_empty_model_writes_only_an_entrypoint(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """A fresh install with everything Unset still needs a config Hyprland can load."""
        result = writer.write(ConfigModel(model.schema))

        assert result.written == ()
        assert paths.entrypoint.is_file()

    def test_writing_the_same_model_twice_changes_nothing(
        self, writer: Writer, model: ConfigModel
    ) -> None:
        """Hyprland watches every required file: a no-op rewrite buys a reload for nothing."""
        writer.write(model)
        second = writer.write(model)

        assert second.written == ()
        assert not second.entrypoint_written
        assert not second.changed

    def test_resetting_the_last_option_in_a_section_removes_its_module(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """A stale Module keeps applying values the user already reset."""
        writer.write(model)
        model.unset("misc:force_default_wallpaper")
        result = writer.write(model)

        assert result.removed == ("options/misc.lua",)
        assert not (paths.app_dir / "options" / "misc.lua").exists()
        assert result.entrypoint_written, "the require list changed, so it must be rewritten"

    def test_two_sections_sharing_a_lua_root_is_refused_not_silently_dropped(
        self, writer: Writer, model: ConfigModel
    ) -> None:
        """Clean on 0.56.2 (21 Sections, 21 stems). A future release must not break it quietly.

        The failure mode without this guard is the worst kind: one Section's Module
        overwrites the other's in the dict, and a whole page of settings vanishes from the
        config with nothing written anywhere to say so.
        """
        general = model.schema["general:gaps_in"]
        impostor = replace(general, name="ghost:gaps_in", section="ghost")
        collided = Schema(
            hyprland_version=model.schema.hyprland_version, options=(general, impostor)
        )

        clash = ConfigModel(collided)
        clash.set("general:gaps_in", 5)
        clash.set("ghost:gaps_in", 5)

        with pytest.raises(ValueError, match="would be lost"):
            writer.render_modules(clash)


class TestSyntaxGateReporting:
    def test_a_write_says_whether_the_gate_actually_ran(
        self, writer: Writer, model: ConfigModel
    ) -> None:
        """The gate degrades without `luac`; a caller assuming otherwise would be worse off."""
        assert writer.write(model).syntax_gate_ran is gate_available()


class TestProtectedFiles:
    def test_user_lua_is_never_rewritten(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        paths.user_lua.write_text("-- mine\n", encoding="utf-8")
        writer.write(model)

        assert paths.user_lua.read_text(encoding="utf-8") == "-- mine\n"

    def test_legacy_lua_is_never_rewritten(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        paths.app_dir.mkdir(parents=True, exist_ok=True)
        paths.legacy_lua.write_text("-- imported\n", encoding="utf-8")
        writer.write(model)

        assert paths.legacy_lua.read_text(encoding="utf-8") == "-- imported\n"

    def test_asking_to_write_one_is_refused_rather_than_ignored(
        self, writer: Writer, paths: ConfigPaths
    ) -> None:
        """A check, not a convention: a future caller cannot lose the escape hatch."""
        with pytest.raises(ProtectedFile):
            writer._write_if_changed(paths.user_lua, "clobbered")

    def test_hyprland_conf_is_left_where_it_is(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """Rollback from a migration is "delete hyprland.lua", which needs the .conf intact."""
        paths.hyprland_conf.write_text("general { gaps_in = 5 }\n", encoding="utf-8")
        writer.write(model)

        assert paths.hyprland_conf.read_text(encoding="utf-8") == "general { gaps_in = 5 }\n"

    def test_a_module_the_app_never_wrote_is_not_pruned(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        writer.write(model)
        stray = paths.options_dir / "notes.lua"
        stray.write_text("-- not ours\n", encoding="utf-8")

        model.unset("misc:force_default_wallpaper")
        writer.write(model)

        assert stray.is_file()


class TestEntrypoint:
    def test_require_order_is_modules_then_legacy_then_bridge_then_user(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """The app's whole override story, in one list (ADR-0005, ADR-0006)."""
        paths.app_dir.mkdir(parents=True, exist_ok=True)
        paths.legacy_lua.write_text("-- legacy\n", encoding="utf-8")
        paths.bridge_dir.mkdir(parents=True, exist_ok=True)
        (paths.bridge_dir / "matugen.lua").write_text("-- matugen\n", encoding="utf-8")
        paths.user_lua.write_text("-- mine\n", encoding="utf-8")

        writer.write(model)
        requires = [
            line.split('"')[1]
            for line in paths.entrypoint.read_text(encoding="utf-8").splitlines()
            if line.startswith("require(")
        ]

        assert requires == [
            "hyprtweaker/options/decoration",
            "hyprtweaker/options/general",
            "hyprtweaker/options/group",
            "hyprtweaker/options/input_capture",
            "hyprtweaker/options/misc",
            "hyprtweaker/legacy",
            "hyprtweaker/bridge/matugen",
            "user",
        ]

    def test_vars_comes_before_the_option_modules(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """Imported `$variables` are a table the other Modules read (ADR-0005)."""
        paths.app_dir.mkdir(parents=True, exist_ok=True)
        paths.vars_lua.write_text("return {}\n", encoding="utf-8")
        writer.write(model)

        requires = [
            line.split('"')[1]
            for line in paths.entrypoint.read_text(encoding="utf-8").splitlines()
            if line.startswith("require(")
        ]
        assert requires[0] == "hyprtweaker/vars"

    def test_absent_files_are_not_required(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """Requiring a `user.lua` nobody created would add an error to every reload."""
        writer.write(model)
        entrypoint = paths.entrypoint.read_text(encoding="utf-8")

        assert 'require("user")' not in entrypoint
        assert "legacy" not in entrypoint

    def test_user_lua_appearing_later_regenerates_the_entrypoint(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """Directories are not watched: a new file means nothing until the list changes."""
        writer.write(model)
        paths.user_lua.write_text("-- mine\n", encoding="utf-8")
        result = writer.write(model)

        assert result.entrypoint_written
        assert 'require("user")' in paths.entrypoint.read_text(encoding="utf-8")


class TestManifest:
    def test_it_records_a_hash_per_module_and_the_entrypoint(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        writer.write(model)
        payload = json.loads(paths.manifest.read_text(encoding="utf-8"))

        assert payload["schema_version"] == model.schema.hyprland_version
        assert payload["app_version"] == SAMPLE_APP_VERSION
        assert payload["entrypoint"]["sha256"]
        assert set(payload["modules"]) == {
            "options/general.lua",
            "options/decoration.lua",
            "options/group.lua",
            "options/misc.lua",
            "options/input_capture.lua",
        }

    def test_a_hand_edited_module_is_reported_not_silently_overwritten(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """ADR-0016: the recovery is a banner offering restore-or-adopt, never a clobber."""
        writer.write(model)
        edited = paths.options_dir / "general.lua"
        edited.write_text("-- mine now\n", encoding="utf-8")

        model.set("general:gaps_in", 99)
        result = writer.write(model)

        assert result.hand_edited == ("options/general.lua",)
        assert result.skipped == ("options/general.lua",)
        assert edited.read_text(encoding="utf-8") == "-- mine now\n"

    def test_the_manifest_keeps_the_skipped_module_s_old_hash(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """Recording bytes nobody wrote would erase the very edit that was just detected."""
        writer.write(model)
        before = json.loads(paths.manifest.read_text(encoding="utf-8"))["modules"]
        (paths.options_dir / "general.lua").write_text("-- mine now\n", encoding="utf-8")

        model.set("general:gaps_in", 99)
        writer.write(model)

        after = json.loads(paths.manifest.read_text(encoding="utf-8"))["modules"]
        assert after["options/general.lua"] == before["options/general.lua"]
        assert writer.write(model).hand_edited == ("options/general.lua",)

    def test_the_caller_can_carry_the_user_s_overwrite_answer_back_in(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """ADR-0005 offers "adopt-into-legacy or overwrite" -- the Writer does not choose."""
        writer.write(model)
        edited = paths.options_dir / "general.lua"
        edited.write_text("-- mine now\n", encoding="utf-8")

        result = writer.write(model, overwrite_hand_edits=True)

        assert result.skipped == ()
        assert "hl.config(" in edited.read_text(encoding="utf-8")

    def test_a_hand_edited_module_is_not_pruned_either(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """Deleting somebody's edit is as wrong as overwriting it."""
        writer.write(model)
        edited = paths.options_dir / "misc.lua"
        edited.write_text("-- mine now\n", encoding="utf-8")

        model.unset("misc:force_default_wallpaper")
        result = writer.write(model)

        assert result.removed == ()
        assert edited.is_file()

    def test_a_spared_module_stays_in_the_manifest_and_stays_reportable(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """A record dropped here would leave an orphan no answer could ever reach.

        The Module is no longer rendered *and* hand-edited, so it is neither written nor
        pruned. If it also fell out of the Manifest, the next write would report nothing,
        and even an explicit "overwrite" could not find it to clean up.
        """
        writer.write(model)
        edited = paths.options_dir / "misc.lua"
        edited.write_text("-- mine now\n", encoding="utf-8")
        model.unset("misc:force_default_wallpaper")
        writer.write(model)

        assert (
            "options/misc.lua"
            in json.loads(paths.manifest.read_text(encoding="utf-8"))["modules"]
        )
        assert writer.write(model).hand_edited == ("options/misc.lua",)
        assert writer.write(model, overwrite_hand_edits=True).removed == ("options/misc.lua",)

    def test_a_damaged_manifest_protects_the_app_dir_instead_of_ignoring_it(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """A lost record is not the same as nothing having been written.

        Reading a corrupt Manifest as "no modules" would make every hand edit invisible and
        therefore silently overwritable -- the exact loss the hash exists to prevent.
        """
        writer.write(model)
        paths.manifest.write_text('{"format_version": 1, "modu', encoding="utf-8")
        edited = paths.options_dir / "general.lua"
        edited.write_text("-- mine now\n", encoding="utf-8")

        result = writer.write(model)

        assert "options/general.lua" in result.hand_edited
        assert edited.read_text(encoding="utf-8") == "-- mine now\n"

    def test_an_absent_manifest_protects_nothing(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """A fresh App dir has nothing to vouch for, so a first run must not stall."""
        result = writer.write(model)

        assert result.hand_edited == ()
        assert result.skipped == ()

    def test_a_hand_edited_entrypoint_is_left_alone(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """ADR-0016 Entrypoint refusal: a banner, never a silent regeneration."""
        writer.write(model)
        paths.entrypoint.write_text("-- mine now\n", encoding="utf-8")

        model.set("decoration:rounding", 12)
        result = writer.write(model)

        assert "hyprland.lua" in result.hand_edited
        assert not result.entrypoint_written
        assert paths.entrypoint.read_text(encoding="utf-8") == "-- mine now\n"

    def test_migration_provenance_survives_a_later_write(
        self, writer: Writer, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """The Importer stamps it once; every write after that has to carry it."""
        writer.write(model)
        manifest = Manifest.load(
            paths.manifest, app_version=SAMPLE_APP_VERSION, schema_version="0.56.2"
        )
        paths.manifest.write_text(
            Manifest(
                app_version=manifest.app_version,
                schema_version=manifest.schema_version,
                entrypoint=manifest.entrypoint,
                modules=manifest.modules,
                migration={"date": "2026-08-22", "source_sha256": "abc"},
            ).render(),
            encoding="utf-8",
        )

        model.set("decoration:rounding", 12)
        writer.write(model)

        payload = json.loads(paths.manifest.read_text(encoding="utf-8"))
        assert payload["migration"] == {"date": "2026-08-22", "source_sha256": "abc"}
