"""`debug:manual_crash`'s two labels, against Hyprland's own words and its parser.

A wrong label here crashes the user's compositor: Hyprland arms the crash when the setting
becomes 1 and fires it when the setting goes back to 0 (`lua/ConfigManager.cpp:832-839` in
0.56.2), so "Armed" must be 1 and the default 0 must read as the harmless state. Checked
only through `--verify-config`, which never runs a compositor: this setting is never set on
a running Hyprland, nested or not.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_writer_verify_config import SCHEMA_DIR, pytestmark, verify  # noqa: F401

from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

MANUAL_CRASH = "debug:manual_crash"


def test_armed_is_the_value_hyprlands_description_says_arms_the_crash() -> None:
    generated = json.loads((SCHEMA_DIR / "hyprland-0.56.2.json").read_text(encoding="utf-8"))
    (upstream,) = [o for o in generated["options"] if o["name"] == MANUAL_CRASH]
    option = load_schema("0.56.2", SCHEMA_DIR)[MANUAL_CRASH]

    assert upstream["description"] == "set to 1 and then back to 0 to crash Hyprland."
    assert option.labels == {"0": "Off", "1": "Armed"}
    assert option.default == 0


def test_each_labelled_value_is_a_config_hyprland_accepts(tmp_path: Path) -> None:
    schema = load_schema("0.56.2", SCHEMA_DIR)
    for value in (0, 1):
        paths = ConfigPaths.rooted_at(tmp_path / str(value))
        paths.hypr_dir.mkdir(parents=True)
        model = ConfigModel(schema)
        model.set(MANUAL_CRASH, value)
        Writer(paths, app_version="0.0.0-test").write(model)
        runtime_dir = tmp_path / f"run{value}"
        runtime_dir.mkdir()

        result = verify(paths.entrypoint, runtime_dir)

        assert result.returncode == 0, f"{value}:\n{result.stdout}\n{result.stderr}"
        assert "config ok" in result.stdout, value
