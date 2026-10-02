"""Harness tier: a running Hyprland loads every font weight the app offers (#194).

`tests/static/test_font_weight_names.py` proves `--verify-config` accepts each one. This
loads the same values into a nested compositor and reads `configerrors`, in case the
parser that runs at load passes a value the verifier would not, or the other way round.
`getoption` cannot read either setting back on 0.56.2 ("invalid type", ADR-0010).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import NestedHyprland

from hyprtweaker.engine.model import FONT_WEIGHT_NAMES, FontWeight

pytestmark = pytest.mark.hyprland

SETTINGS = ("font_weight_active", "font_weight_inactive")
NUMBERS = (99, 100, 550, 1000, 1001)


def setting(key: str, weight: FontWeight) -> str:
    return f"hl.config({{ group = {{ groupbar = {{ {key} = {weight.lua()} }} }} }})\n"


def test_every_offered_weight_loads_without_a_config_error(
    harness_home: Path, artifacts: Path
) -> None:
    weights = [FontWeight(weight) for weight in (*FONT_WEIGHT_NAMES, *NUMBERS)]
    lines = [setting(key, weight) for weight in weights for key in SETTINGS]
    # The negative, last: proves a refused weight does reach `configerrors`.
    lines += [setting(key, FontWeight("extrabold")) for key in SETTINGS]
    config = harness_home / "font-weights.lua"
    config.write_text("".join(lines), encoding="utf-8")

    with NestedHyprland(config, home=harness_home, log=artifacts / "nested.log") as nested:
        errors = nested.config_errors()

    assert [error.split(": ", 1)[1] for error in errors] == [
        f"error setting 'group.groupbar.{key}': font weight \"extrabold\" was not found"
        for key in SETTINGS
    ]
