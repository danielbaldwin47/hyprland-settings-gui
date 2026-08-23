"""The static verify gate: `Hyprland --verify-config` over what the Writer produces.

ADR-0011 tier 2. `luac -p` proves the output *parses*; only this proves Hyprland *accepts*
it -- that every value is spelled the way its own parser reads. The two are not the same
check, and the gap between them is where the expensive bugs live: a gradient written as
`descriptions` text parses perfectly as Lua and is rejected as a config.

It has already earned its place. The first run of this test found five Options whose
curated "no value" was `-1` -- valid Lua, valid hyprlang, and `invalid color "-1"` to the
Lua engine. Nothing in the unit tier could have seen that.

Run it explicitly::

    pytest tests/integration -m hyprland

`--verify-config` *executes* the config with live bindings, so the compositor environment
is stripped first: without that, a verify run can reach the session the developer is
sitting in (prototype #30 FINDINGS §"sandbox").
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.model import ConfigModel  # noqa: E402
from hyprtweaker.engine.paths import ConfigPaths  # noqa: E402
from hyprtweaker.engine.schema import load_schema  # noqa: E402
from hyprtweaker.engine.writer import Writer  # noqa: E402

SCHEMA_DIR = ROOT / "data" / "schema"
VERIFY_TIMEOUT_SECONDS = 180

pytestmark = pytest.mark.hyprland


def verify(entrypoint: Path, runtime_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run `Hyprland --verify-config` with the caller's compositor session out of reach."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY")
    }
    environment["XDG_RUNTIME_DIR"] = str(runtime_dir)

    return subprocess.run(
        ["Hyprland", "--verify-config", "-c", str(entrypoint)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=VERIFY_TIMEOUT_SECONDS,
    )


def write_everything(root: Path, version: str) -> tuple[ConfigPaths, int, int]:
    """Write a config that sets *every* Option in the schema, at its own default value.

    Deliberately maximal. A sampled fixture would exercise the types the author thought of;
    the whole schema exercises the ones they did not, which is where a wrong spelling for
    one of 353 options would otherwise sit until a user hit it.
    """
    paths = ConfigPaths.rooted_at(root)
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)

    schema = load_schema(version, SCHEMA_DIR)
    model = ConfigModel(schema)
    for option in schema:
        # A sentinel default is not a value; the nullable Options take their null instead.
        model.set(option.name, None if option.default is None else option.default)

    Writer(paths, app_version="0.0.0-test").write(model)
    return paths, len(model), len(schema)


def test_every_written_output_is_a_config_hyprland_accepts(tmp_path: Path) -> None:
    if shutil.which("Hyprland") is None:
        pytest.skip("no Hyprland binary on this machine")

    paths, emitted, total = write_everything(tmp_path, "0.56.2")
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir()

    result = verify(paths.entrypoint, runtime_dir)

    assert result.returncode == 0, (
        f"Hyprland rejected the generated config ({emitted} of {total} Options emitted):"
        f"\n{result.stdout}\n{result.stderr}"
    )
    assert "config ok" in result.stdout


def test_the_options_lua_cannot_express_are_the_only_ones_left_out(tmp_path: Path) -> None:
    """Guards the test above: "accepted" must not come from having emitted nothing.

    The five omissions are the colour and gradient fallbacks whose only Lua spelling is
    absence (`values.has_emittable_null`). Everything else is written.
    """
    if shutil.which("Hyprland") is None:
        pytest.skip("no Hyprland binary on this machine")

    _, emitted, total = write_everything(tmp_path, "0.56.2")

    assert (total - emitted) == 5, f"{total - emitted} Options were skipped, expected 5"
