"""A display countdown's revert, end to end, against a real compositor (#192, ADR-0008).

The UI tier proves which rule list a revert writes; this proves the written list is
config a compositor obeys. A breaking scale edit applies, a benign vrr edit lands while
the countdown runs, and the revert (`revert_breaking`) puts the scale back: the nested
instance must show the old scale, and a revert with no benign edit must write the
pre-batch `monitors.lua` byte for byte. The witness is the catch-all rule, the one rule
guaranteed to bind whatever output the harness's headless backend invents.

    pytest tests/integration/test_display_revert_live.py -m hyprland
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from harness import HEADLESS_OUTPUT, NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION

from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.model.entities import MonitorRule
from hyprtweaker.engine.monitors_catalog import revert_breaking
from hyprtweaker.engine.paths import MONITORS_MODULE, ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"


def test_revert_puts_the_scale_back_and_keeps_the_benign_edit(
    harness_home: Path, artifacts: Path
) -> None:
    paths = ConfigPaths.rooted_at(harness_home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)
    model = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    writer = Writer(paths, app_version=APP_VERSION)
    monitors = model.entities.monitors
    module = paths.app_dir / MONITORS_MODULE

    monitors[:] = [MonitorRule(output="", fields={"scale": 1})]
    writer.write(model)
    before_bytes = module.read_bytes()
    snapshot = tuple(monitors)

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested-display-revert.log"
    ) as nested:

        def witness_scales() -> set[float]:
            state = nested.hyprctl("monitors")
            assert isinstance(state, list) and state, state
            return {
                float(entry.get("scale", 0))
                for entry in state
                if entry.get("name") != HEADLESS_OUTPUT
            }

        def reload_and_read(expected: float) -> set[float]:
            nested.hyprctl_text("reload")
            deadline = time.monotonic() + 10
            scales = witness_scales()
            while scales != {expected} and time.monotonic() < deadline:
                time.sleep(0.1)
                scales = witness_scales()
            return scales

        if not witness_scales():
            nested.hyprctl_text("output", "create", "headless")
        assert reload_and_read(1.0) == {1.0}

        # The batch: a breaking scale edit, then a benign vrr edit mid-countdown.
        monitors[:] = [MonitorRule(output="", fields={"scale": 2})]
        writer.write(model)
        assert reload_and_read(2.0) == {2.0}, "the breaking edit did not apply"
        monitors[:] = [MonitorRule(output="", fields={"scale": 2, "vrr": 1})]
        writer.write(model)

        # Revert: the scale goes back, the vrr edit stays, the compositor obeys.
        reverted = revert_breaking(snapshot, monitors)
        assert reverted == [MonitorRule(output="", fields={"scale": 1, "vrr": 1})]
        monitors[:] = reverted
        writer.write(model)
        assert reload_and_read(1.0) == {1.0}, "the revert did not put the scale back"
        errors: tuple[Any, ...] = nested.config_errors()
        assert errors == (), f"the reverted config did not load cleanly: {errors}"

    # With no benign edit to keep, the revert writes the pre-batch file exactly.
    monitors[:] = revert_breaking(snapshot, [MonitorRule(output="", fields={"scale": 2})])
    writer.write(model)
    assert module.read_bytes() == before_bytes
