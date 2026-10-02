"""Every font-weight name the app offers is one Hyprland accepts (#194).

The two font-weight settings take a number or a name, and the names a Row offers come from
`FONT_WEIGHT_NAMES`. A name Hyprland does not know is a config error, so each one is written
through the Writer, as the Row writes it, and checked with `Hyprland --verify-config`.
`getoption` cannot answer this: it says "invalid type" for both settings on 0.56.2
(ADR-0010).

`tests/integration/test_font_weight_live.py` loads the same values into a nested Hyprland
and reads `configerrors`, in case `--verify-config` passes a value the parser then ignores.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_writer_verify_config import SCHEMA_DIR, verify

from hyprtweaker.engine.model import FONT_WEIGHT_NAMES, ConfigModel, FontWeight
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

SETTINGS = ("group:groupbar:font_weight_active", "group:groupbar:font_weight_inactive")

NUMBERS = (99, 100, 550, 1000, 1001)
"""The ends of the range the Row accepts, one step past each, and a weight with no name."""


def verdict(root: Path, weight: int | str) -> tuple[int, str]:
    """`--verify-config`'s exit code and output for a config setting both to `weight`."""
    paths = ConfigPaths.rooted_at(root)
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    model = ConfigModel(load_schema("0.56.2", SCHEMA_DIR))
    for name in SETTINGS:
        model.set(name, FontWeight(weight))
    Writer(paths, app_version="0.0.0-test").write(model)
    runtime_dir = root / "run"
    runtime_dir.mkdir()
    result = verify(paths.entrypoint, runtime_dir)
    return result.returncode, result.stdout + result.stderr


@pytest.mark.parametrize("weight", [*FONT_WEIGHT_NAMES, *NUMBERS])
def test_hyprland_accepts_every_offered_weight(tmp_path: Path, weight: int | str) -> None:
    code, output = verdict(tmp_path, weight)

    assert code == 0, output
    assert "config ok" in output


def test_hyprland_refuses_a_name_it_does_not_know(tmp_path: Path) -> None:
    """The gate can fail: without this, "accepted" could mean the value was never read."""
    code, output = verdict(tmp_path, "extrabold")

    assert code != 0
    assert 'font weight "extrabold" was not found' in output
