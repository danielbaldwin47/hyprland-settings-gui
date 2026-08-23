"""The Writer against a real directory: what lands, what is left alone, what is pruned.

Everything here is behaviour a user would notice -- a file appearing, a file surviving, a
reload not happening. The App dir is a throwaway `tmp_path`, so these run anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _support import SAMPLE_APP_VERSION, sample_model

from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.state import Manifest
from hyprtweaker.engine.writer import ProtectedFile, Writer


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
        (paths.options_dir / "general.lua").write_text("-- mine now\n", encoding="utf-8")

        result = writer.write(model)
        assert result.hand_edited == ("options/general.lua",)

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
