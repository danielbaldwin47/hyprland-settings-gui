"""The `plugins.lua` Module: the ordered plugin load list, written and read back (#174).

ADR-0018 §Plugins: one `hl.plugin.load` per entry, in list order, a disabled entry as the
same line behind `-- disabled: ` so its position survives. The read-back is the other half
of the contract: a hand-edited or imported `plugins.lua` must come back as the same list, or
the next write deletes what the user wrote.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _golden import assert_matches_golden
from _support import SAMPLE_APP_VERSION, SAMPLE_VERSION, SCHEMA_DIR, sample_model

from hyprtweaker.engine.importer import import_config
from hyprtweaker.engine.importer.lua import lua_binary
from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.model.entities import PluginLoad
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer
from hyprtweaker.engine.writer.declarations import parse_declarations_module
from hyprtweaker.engine.writer.plugins import render_plugins_module

VERSION = "0.1.0"
GOLDEN = Path(__file__).parent.parent / "golden" / "writer"

needs_lua = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter")

SAMPLE = [
    PluginLoad("/usr/lib/hyprland-plugins/libhyprbars.so"),
    PluginLoad("/home/me/.local/share/hyprpm/hyprexpo.so", enabled=False),
    PluginLoad('/opt/odd "quoted" & spaced/libborders-plus-plus.so'),
]


def entries(plugins: tuple[PluginLoad, ...] | list[PluginLoad]) -> list[tuple[str, bool]]:
    return [(plugin.path, plugin.enabled) for plugin in plugins]


class TestRender:
    def test_the_module_matches_its_golden(self) -> None:
        text = render_plugins_module(SAMPLE, app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "plugins.lua", "plugins.lua")

    def test_calls_come_in_list_order_and_a_disabled_entry_is_a_comment(self) -> None:
        text = render_plugins_module(SAMPLE, app_version=VERSION)
        assert text is not None

        body = [line for line in text.splitlines() if "plugin.load" in line]

        assert body == [
            'hl.plugin.load("/usr/lib/hyprland-plugins/libhyprbars.so")',
            '-- disabled: hl.plugin.load("/home/me/.local/share/hyprpm/hyprexpo.so")',
            'hl.plugin.load("/opt/odd \\"quoted\\" & spaced/libborders-plus-plus.so")',
        ]

    def test_an_empty_list_renders_no_module(self) -> None:
        """`None` makes the Writer prune: absence is how the model spells "no plugins"."""
        assert render_plugins_module([], app_version=VERSION) is None

    def test_a_list_of_only_disabled_entries_still_renders(self) -> None:
        """Pruning it would lose the entries the user switched off, not deleted."""
        text = render_plugins_module(
            [PluginLoad("/p/liba.so", enabled=False)], app_version=VERSION
        )

        assert text is not None
        assert '-- disabled: hl.plugin.load("/p/liba.so")' in text


@needs_lua
class TestReadBack:
    def test_the_written_module_reads_back_as_the_same_list(self) -> None:
        text = render_plugins_module(SAMPLE, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="plugins.lua")

        assert parsed.ok, parsed.errors
        assert entries(parsed.plugins) == [
            ("/usr/lib/hyprland-plugins/libhyprbars.so", True),
            ("/home/me/.local/share/hyprpm/hyprexpo.so", False),
            ('/opt/odd "quoted" & spaced/libborders-plus-plus.so', True),
        ]
        assert render_plugins_module(list(parsed.plugins), app_version=VERSION) == text

    def test_a_hand_edited_module_round_trips_without_loss(self) -> None:
        """What a person writes: no banner, single quotes, a plain comment, a re-enabled line."""
        text = (
            "-- my plugins\n"
            "hl.plugin.load('/p/libone.so')\n"
            "-- this one crashes on start, off for now\n"
            '-- disabled: hl.plugin.load("/p/two.so")\n'
            '  hl.plugin.load("/p/three.so")  -- trailing note\n'
        )

        parsed = parse_declarations_module(text, module="plugins.lua")

        assert parsed.ok, parsed.errors
        assert entries(parsed.plugins) == [
            ("/p/libone.so", True),
            ("/p/two.so", False),
            ("/p/three.so", True),
        ]
        assert [plugin.origin for plugin in parsed.plugins] == [
            "plugins.lua:2",
            "plugins.lua:4",
            "plugins.lua:5",
        ]

    def test_a_disabled_line_elsewhere_is_not_mistaken_for_a_plugin(self) -> None:
        """Only `-- disabled: hl.` revives; a plain comment naming a path stays a comment."""
        text = '-- disabled: see /p/old.so\nhl.plugin.load("/p/a.so")\n'

        parsed = parse_declarations_module(text, module="plugins.lua")

        assert entries(parsed.plugins) == [("/p/a.so", True)]

    def test_a_plugin_misfiled_into_another_module_still_comes_back(self) -> None:
        text = 'hl.env("A", "1")\nhl.plugin.load("/p/a.so")\n'

        parsed = parse_declarations_module(text, module="env.lua")

        assert entries(parsed.plugins) == [("/p/a.so", True)]
        assert [(v.name, v.value) for v in parsed.env] == [("A", "1")]


class TestTheModuleSet:
    """`plugins.lua` joins the canonical Module set like every Entity Module."""

    @pytest.fixture
    def paths(self, tmp_path: Path) -> ConfigPaths:
        config = ConfigPaths.rooted_at(tmp_path)
        config.hypr_dir.mkdir(parents=True)
        return config

    @pytest.fixture
    def model(self) -> ConfigModel:
        model = sample_model()
        model.entities.plugins[:] = [PluginLoad("/p/libhyprbars.so")]
        model.mark_entities_loaded()
        return model

    def test_it_is_required_by_the_entrypoint_before_user_lua(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        paths.user_lua.write_text("-- mine\n", encoding="utf-8")

        Writer(paths, app_version=SAMPLE_APP_VERSION).write(model)

        requires = [
            line.strip()
            for line in paths.entrypoint.read_text(encoding="utf-8").splitlines()
            if "require" in line and ("plugins" in line or "user" in line)
        ]
        assert [("plugins" in line, "user" in line) for line in requires] == [
            (True, False),
            (False, True),
        ]

    def test_it_is_hashed_in_the_manifest_and_snapshotted_by_the_journal(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        writer = Writer(paths, app_version=SAMPLE_APP_VERSION)

        result = writer.write(model)

        assert "plugins.lua" in result.written
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
        assert "plugins.lua" in manifest["modules"]
        assert "plugins.lua" in writer.candidate_files(model)

    def test_a_hand_edit_is_reported_not_overwritten(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        writer = Writer(paths, app_version=SAMPLE_APP_VERSION)
        writer.write(model)
        module = paths.app_dir / "plugins.lua"
        module.write_text('hl.plugin.load("/p/mine.so")\n', encoding="utf-8")

        model.entities.plugins.append(PluginLoad("/p/libother.so"))
        result = writer.write(model)

        assert result.hand_edited == ("plugins.lua",)
        assert module.read_text(encoding="utf-8") == 'hl.plugin.load("/p/mine.so")\n'

    def test_removing_the_last_entry_prunes_the_module(
        self, paths: ConfigPaths, model: ConfigModel
    ) -> None:
        writer = Writer(paths, app_version=SAMPLE_APP_VERSION)
        writer.write(model)

        model.entities.plugins.clear()
        result = writer.write(model)

        assert "plugins.lua" in result.removed
        assert not (paths.app_dir / "plugins.lua").exists()

    def test_an_imported_plugin_line_now_reaches_plugins_lua(
        self, paths: ConfigPaths, tmp_path: Path
    ) -> None:
        """Before #174 the Importer kept `plugin =` lines and the Writer dropped them."""
        conf = tmp_path / "hyprland.conf"
        conf.write_text(
            "plugin = /usr/lib/libhyprbars.so\nplugin = /p/hyprexpo.so\n", encoding="utf-8"
        )
        imported = import_config(conf, load_schema(SAMPLE_VERSION, SCHEMA_DIR))
        model = sample_model()
        model.adopt_entities(imported.entities)

        Writer(paths, app_version=SAMPLE_APP_VERSION).write(model)

        written = (paths.app_dir / "plugins.lua").read_text(encoding="utf-8")
        assert [line for line in written.splitlines() if "plugin.load" in line] == [
            'hl.plugin.load("/usr/lib/libhyprbars.so")',
            'hl.plugin.load("/p/hyprexpo.so")',
        ]


class TestLoadedName:
    @pytest.mark.parametrize(
        ("path", "name"),
        [
            ("/usr/lib/hyprland-plugins/libhyprbars.so", "hyprbars"),
            ("/p/hyprexpo.so", "hyprexpo"),
            ("/p/Hyprspace.so", "hyprspace"),
            ("relative-plugin", "relative-plugin"),
        ],
    )
    def test_the_name_a_loaded_plugin_is_matched_by(self, path: str, name: str) -> None:
        assert PluginLoad(path).name == name
