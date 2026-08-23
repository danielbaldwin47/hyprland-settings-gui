"""Export: the whole config flattened into one file that runs without the app.

Flattening is not concatenation, and the two tests that matter here are about the
difference. A file inlined by pasting its body loses its scope and, if it ends in a
`return`, silently truncates everything after it -- so an export would look fine and be
missing half the config. Each chunk therefore gets its own function, and the requires
between them still resolve.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _support import SAMPLE_APP_VERSION, sample_model

from hyprtweaker.engine.migration.export import render
from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.writer import Writer, gate_available
from hyprtweaker.engine.writer.syntax import check


@pytest.fixture
def paths(tmp_path: Path) -> ConfigPaths:
    config = ConfigPaths.rooted_at(tmp_path)
    config.hypr_dir.mkdir(parents=True)
    return config


@pytest.fixture
def model() -> ConfigModel:
    return sample_model()


def written(paths: ConfigPaths, model: ConfigModel) -> None:
    Writer(paths, app_version=SAMPLE_APP_VERSION).write(model)


class TestSelfContained:
    def test_every_generated_module_is_inlined(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert result.inlined
        for require_path in result.inlined:
            assert f'__inlined["{require_path}"]' in result.text

    def test_nothing_is_required_from_outside_the_file(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """The point of an export: it runs on a machine with no hyprtweaker and no App dir."""
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        for require_path in result.inlined:
            assert f'require("{require_path}")' not in result.text

    def test_the_settings_themselves_are_in_there(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert "hl.config(" in result.text

    def test_the_users_own_lua_travels_with_it(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        paths.user_lua.write_text(
            'hl.bind("SUPER + Q", hl.dsp.window.close())\n', encoding="utf-8"
        )
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert "hl.dsp.window.close()" in result.text
        assert paths.require_path(paths.user_lua) in result.inlined

    def test_the_user_module_is_inlined_last_so_it_still_wins(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """Require order is the app's whole override story; flattening must preserve it."""
        paths.user_lua.write_text("-- mine\n", encoding="utf-8")
        paths.legacy_lua.parent.mkdir(parents=True, exist_ok=True)
        paths.legacy_lua.write_text("-- imported\n", encoding="utf-8")
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert result.inlined[-1] == paths.require_path(paths.user_lua)
        assert result.text.index("-- imported") < result.text.index("-- mine")


class TestFlatteningIsNotConcatenation:
    def test_a_module_that_returns_does_not_truncate_the_export(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """`vars.lua` is a module that returns a table. Pasted in, it would end the file.

        Everything after it would stop being config, and nothing would say so.
        """
        paths.app_dir.mkdir(parents=True, exist_ok=True)
        paths.vars_lua.write_text('return { wallpaper = "/tmp/a.png" }\n', encoding="utf-8")
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert "hl.config(" in result.text.split("wallpaper", 1)[1]

    def test_a_local_in_one_chunk_cannot_collide_with_another(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        paths.user_lua.write_text("local gaps = 5\n", encoding="utf-8")
        paths.legacy_lua.parent.mkdir(parents=True, exist_ok=True)
        paths.legacy_lua.write_text("local gaps = 9\n", encoding="utf-8")
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert not gate_available() or check(result.text, "export.lua").ok

    def test_requires_between_inlined_files_still_resolve(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """A `legacy.lua` reading `vars` must find the inlined copy, not a path from the
        machine the export came from."""
        paths.app_dir.mkdir(parents=True, exist_ok=True)
        paths.vars_lua.write_text("return { gap = 5 }\n", encoding="utf-8")
        paths.legacy_lua.write_text(
            'local vars = require("hyprtweaker/vars")\n', encoding="utf-8"
        )
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert "__host_require" in result.text
        assert result.text.index('__inlined["hyprtweaker/vars"]') < result.text.index(
            'local vars = require("hyprtweaker/vars")'
        )


class TestItIsValidLua:
    @pytest.mark.skipif(not gate_available(), reason="no luac on this machine")
    def test_the_export_parses(self, paths: ConfigPaths, model: ConfigModel) -> None:
        paths.user_lua.write_text("-- nothing much\n", encoding="utf-8")
        written(paths, model)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert check(result.text, "hyprland.lua").ok

    @pytest.mark.skipif(not gate_available(), reason="no luac on this machine")
    def test_an_export_of_an_empty_config_parses(self, paths: ConfigPaths) -> None:
        """A fresh user exporting immediately is a real thing to do."""
        from _support import sample_schema

        empty = ConfigModel(sample_schema())
        written(paths, empty)

        result = render(empty, paths, app_version=SAMPLE_APP_VERSION)

        assert check(result.text, "hyprland.lua").ok


class TestHonestAboutGaps:
    def test_a_bridge_module_is_inlined_like_any_other_require(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        written(paths, model)
        bridge = paths.bridge_dir / "matugen.lua"
        bridge.parent.mkdir(parents=True, exist_ok=True)
        bridge.write_text("-- colors\n", encoding="utf-8")

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert paths.require_path(bridge) in result.inlined
        assert not result.missing

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads unreadable files anyway")
    def test_a_require_that_cannot_be_read_is_reported_not_dropped_silently(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        """An export missing part of the config must say so, in the file itself.

        Otherwise the user carries away something they believe is complete.
        """
        written(paths, model)
        bridge = paths.bridge_dir / "matugen.lua"
        bridge.parent.mkdir(parents=True, exist_ok=True)
        bridge.write_text("-- colors\n", encoding="utf-8")
        bridge.chmod(0o000)

        result = render(model, paths, app_version=SAMPLE_APP_VERSION)

        assert paths.require_path(bridge) in result.missing
        assert "could not be read" in result.text

    def test_writing_it_out_lands_where_asked(
        self, paths: ConfigPaths, model: ConfigModel, tmp_path: Path
    ) -> None:
        written(paths, model)
        target = tmp_path / "somewhere" / "hyprland.lua"

        render(model, paths, app_version=SAMPLE_APP_VERSION).write(target)

        assert target.read_text(encoding="utf-8").startswith("-- Hyprland config exported")
