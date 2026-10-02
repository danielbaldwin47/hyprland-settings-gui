"""`plugins.lua` against a compositor (#174): what the Writer emits loads, and the loaded
state the app reads follows the list.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest tests/integration/test_plugins_live.py -m hyprland

Two claims the Plugins group rests on, asserted against the binary rather than the docs:

* A path whose file does not exist is **not** a config error on 0.56.2: Hyprland skips it,
  the rest of the config applies, and nothing is loaded. That is why the app writes such an
  entry as-is and marks its row "File not found" rather than refusing it.
* `hyprctl -j plugin list` is the read-back (`CommandClient.loaded_plugins`). `eval` of
  `hl.get_loaded_plugins()` cannot be: it answers `ok` whatever the Lua prints.

The second half needs a real plugin. Set `HYPRTWEAKER_TEST_PLUGIN` to a `.so` built for
the installed Hyprland and it runs; without it, it skips. A no-op plugin is enough (the
#174 report shows one built from `pluginInit` alone against `/usr/include/hyprland`).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from harness import NestedHyprland

from hyprtweaker.engine.ipc import CommandClient
from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.model.entities import PluginLoad
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

pytestmark = pytest.mark.hyprland

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "data" / "schema"
SCHEMA_VERSION = "0.56.2"
APP_VERSION = "0.0.0-harness"
TEST_PLUGIN = "HYPRTWEAKER_TEST_PLUGIN"


def write(home: Path, plugins: list[PluginLoad]) -> ConfigPaths:
    paths = ConfigPaths.rooted_at(home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    model = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    model.set("general:gaps_in", 7)
    model.entities.plugins.extend(plugins)
    model.mark_entities_loaded()
    Writer(paths, app_version=APP_VERSION).write(model)
    return paths


def loaded(nested: NestedHyprland) -> tuple[str, ...]:
    return asyncio.run(CommandClient(nested.instance).loaded_plugins())


def test_a_plugin_file_that_does_not_exist_is_skipped_not_an_error(
    harness_home: Path, artifacts: Path
) -> None:
    paths = write(
        harness_home,
        [PluginLoad("/nonexistent/libmissing.so"), PluginLoad("/nope/x.so", enabled=False)],
    )

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested-plugins.log"
    ) as nested:
        assert nested.config_errors() == ()
        assert nested.hyprctl("getoption", "general:gaps_in")["css"] == "7 7 7 7"
        assert loaded(nested) == ()
        # The reason the read-back is `plugin list`: eval answers `ok` and nothing else.
        assert nested.hyprctl_text("eval", "print('x')").strip() == "ok"


@pytest.mark.skipif(not os.environ.get(TEST_PLUGIN), reason=f"{TEST_PLUGIN} is not set")
def test_the_loaded_state_follows_the_list_across_reloads(
    harness_home: Path, artifacts: Path
) -> None:
    so = os.environ[TEST_PLUGIN]
    paths = write(harness_home, [PluginLoad(so)])
    name = PluginLoad(so).name

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested-plugins.log"
    ) as nested:
        assert nested.config_errors() == ()
        assert [each.lower() for each in loaded(nested)] == [name]

        write(harness_home, [PluginLoad(so, enabled=False)])
        nested.hyprctl_text("reload")
        assert loaded(nested) == ()

        write(harness_home, [PluginLoad(so)])
        nested.hyprctl_text("reload")
        assert [each.lower() for each in loaded(nested)] == [name]
