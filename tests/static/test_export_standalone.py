"""`Hyprland --verify-config` over an Export, alone in an empty directory.

The acceptance criterion is that an export "runs standalone", and the load-bearing word is
*standalone*: the file is put somewhere with no App dir, no `user.lua`, no hyprtweaker
anywhere, and Hyprland is asked to accept it. A `require` the flattening missed would
resolve on the exporting machine and fail here, which is exactly the bug this tier exists
to catch -- and exactly the one a unit test on the rendered text cannot see.

ADR-0011 tier 2, alongside `test_writer_verify_config.py`: no compositor needed, well under
a second, so it runs on any developer box with Hyprland and skips everywhere else.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from test_writer_verify_config import verify  # noqa: E402  (same tier, same rig)

from hyprtweaker.engine.migration.export import render  # noqa: E402
from hyprtweaker.engine.model import ConfigModel  # noqa: E402
from hyprtweaker.engine.paths import ConfigPaths  # noqa: E402
from hyprtweaker.engine.schema import load_schema  # noqa: E402
from hyprtweaker.engine.writer import Writer  # noqa: E402

SCHEMA_DIR = ROOT / "data" / "schema"
APP_VERSION = "0.0.0-test"
VERSION = "0.56.2"

pytestmark = pytest.mark.skipif(
    shutil.which("Hyprland") is None, reason="no Hyprland binary on this machine"
)


def export_of_everything(root: Path) -> str:
    """Export a config that sets every Option the schema has, plus a `user.lua`.

    Maximal for the same reason the writer's own gate is: a sampled fixture exercises the
    types the author thought of.
    """
    paths = ConfigPaths.rooted_at(root)
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)

    schema = load_schema(VERSION, SCHEMA_DIR)
    model = ConfigModel(schema)
    for option in schema:
        model.set(option.name, None if option.default is None else option.default)

    paths.user_lua.write_text(
        "-- the escape hatch travels with the export\n"
        'hl.bind("SUPER + Q", hl.dsp.window.close())\n',
        encoding="utf-8",
    )
    Writer(paths, app_version=APP_VERSION).write(model)
    return render(model, paths, app_version=APP_VERSION).text


def test_an_export_is_a_config_hyprland_accepts_with_nothing_else_present(
    tmp_path: Path,
) -> None:
    text = export_of_everything(tmp_path / "source")

    elsewhere = tmp_path / "usb-stick"
    elsewhere.mkdir()
    standalone = elsewhere / "hyprland.lua"
    standalone.write_text(text, encoding="utf-8")
    runtime = tmp_path / "run"
    runtime.mkdir()

    result = verify(standalone, runtime)

    assert result.returncode == 0, (
        f"Hyprland rejected a standalone export:\n{result.stdout}\n{result.stderr}"
    )
    assert "config ok" in result.stdout


def test_the_export_really_was_alone(tmp_path: Path) -> None:
    """Guards the test above: "accepted" must not come from files sitting next to it.

    Without this, moving the export next to its own App dir would make the check pass for
    the wrong reason and the self-containment claim would quietly stop being tested.
    """
    text = export_of_everything(tmp_path / "source")
    elsewhere = tmp_path / "usb-stick"
    elsewhere.mkdir()
    (elsewhere / "hyprland.lua").write_text(text, encoding="utf-8")

    assert [item.name for item in elsewhere.iterdir()] == ["hyprland.lua"]
