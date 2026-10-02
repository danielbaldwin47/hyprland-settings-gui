"""Colours and gradients round-trip with no false "Overridden" (#153 review, addendum 37).

The drift scan (`overrides.scan`) and a transaction's Read-back compare what the app's own
Module sets against `getoption`. Every unit-tier run of that comparison answers with the
fake compositor's replies; the real reply for a gradient was unverified, and most rices set
a gradient border, so a mismatch there would badge the Theming page's main keys
"Overridden" for nearly every user. Here a real nested Hyprland answers.

    HARNESS_DRM_CARD=/dev/dri/card0 \\
        pytest tests/integration/test_drift_gradients_live.py -m hyprland
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from harness import NestedHyprland
from harness.state import SCHEMA_DIR, SCHEMA_VERSION
from test_apply_end_to_end import APP_VERSION, apply_edit, build

from hyprtweaker.engine.apply import ApplyOutcome, overrides
from hyprtweaker.engine.ipc import CommandClient
from hyprtweaker.engine.model.values import parse_value
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.state import Manifest

pytestmark = pytest.mark.hyprland

COLOURS = {
    "general:col.active_border": "rgba(33ccffee) rgba(00ff99ee) 45deg",
    "general:col.inactive_border": "rgba(595959aa)",
    "group:col.border_active": "rgba(ffaa00ff) rgba(aa00ffff) 90deg",
}


def test_gradients_and_colours_read_back_and_scan_clean(
    harness_home: Path, artifacts: Path
) -> None:
    paths, model, writer = build(harness_home, {})
    writer.write(model)
    schema = load_schema(SCHEMA_VERSION, SCHEMA_DIR)

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested.log"
    ) as nested:
        for name, text in COLOURS.items():
            option = schema.get(name)
            assert option is not None, name
            model.set(name, parse_value(option.type, text))

        applied = asyncio.run(apply_edit(nested.instance, model, writer, list(COLOURS)))

        assert applied.outcome is ApplyOutcome.OK, (applied.outcome, applied.mismatches)
        assert applied.mismatches == ()
        manifest = Manifest.load(
            paths.manifest, app_version=APP_VERSION, schema_version=SCHEMA_VERSION
        )
        expected = overrides.reference(paths.app_dir, schema, manifest)
        assert {option.name for option, _value in expected} >= set(COLOURS)
        mismatches = asyncio.run(overrides.scan(CommandClient(nested.instance), expected))
        assert mismatches == ()
