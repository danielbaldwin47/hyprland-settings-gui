"""Monitor profiles, end to end, against a real compositor (#69, ADR-0015).

The acceptance run the headless tiers cannot finish: capture then activate must
round-trip *exactly* -- and the activated files must be config a compositor accepts and
obeys, not merely bytes that equal other bytes. The witness is the nested instance
itself: the catch-all rule is the one rule guaranteed to bind whatever output the
harness's headless backend invents, so the profile's scale must show up in
`hyprctl monitors`, and the workspace pin in `hyprctl workspacerules`.

    pytest tests/integration/test_monitor_profiles_live.py -m hyprland
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION

from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.model.entities import MonitorRule, WorkspaceRule
from hyprtweaker.engine.paths import MONITORS_MODULE, WORKSPACE_RULES_MODULE, ConfigPaths
from hyprtweaker.engine.profiles import activated, capture
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"

DOCKED_MONITORS = [
    # The catch-all is the live witness: whatever output the headless backend creates,
    # this rule binds it, so the profile's scale is observable in `hyprctl monitors`.
    MonitorRule(output="", fields={"scale": 2}),
    # A dormant rule for an absent display rides along: activation must carry it into
    # the file even though nothing on the box can apply it today (ADR-0015).
    MonitorRule(output="desc:Dell U2720Q", fields={"mode": "2560x1440@60", "position": "0x0"}),
]

DOCKED_WORKSPACES = [
    WorkspaceRule(workspace="1", fields={"monitor": "desc:Dell U2720Q", "default": True}),
    WorkspaceRule(workspace="2", fields={"persistent": True}),
]


def build(home: Path) -> tuple[ConfigPaths, ConfigModel, Writer]:
    paths = ConfigPaths.rooted_at(home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)

    model = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    model.entities.monitors.extend(DOCKED_MONITORS)
    model.entities.workspace_rules.extend(DOCKED_WORKSPACES)
    return paths, model, Writer(paths, app_version=APP_VERSION)


def test_capture_then_activate_round_trips_on_a_live_box(
    harness_home: Path, artifacts: Path
) -> None:
    """Capture docked, drift away, activate: same bytes, and a compositor that obeys them."""
    paths, model, writer = build(harness_home)
    writer.write(model)
    docked_bytes = (
        (paths.app_dir / MONITORS_MODULE).read_bytes(),
        (paths.app_dir / WORKSPACE_RULES_MODULE).read_bytes(),
    )

    profile = capture(
        "Docked",
        monitors=model.entities.monitors,
        workspace_rules=model.entities.workspace_rules,
    )

    # Drift: the scale changes, the dormant rule goes, a pin flips and a foreign one lands.
    model.entities.monitors[:] = [MonitorRule(output="", fields={"scale": 1})]
    model.entities.workspace_rules[:] = [
        WorkspaceRule(workspace="1", fields={"monitor": "eDP-1", "default": True}),
        WorkspaceRule(workspace="2", fields={"persistent": True, "monitor": "eDP-1"}),
    ]
    writer.write(model)
    assert (paths.app_dir / MONITORS_MODULE).read_bytes() != docked_bytes[0]

    # Activation, as the session performs it: the pure function's state, one write.
    monitors, workspaces = activated(profile, workspace_rules=model.entities.workspace_rules)
    model.entities.monitors[:] = list(monitors)
    model.entities.workspace_rules[:] = list(workspaces)
    writer.write(model)

    # The golden half, against the real files: byte for byte what capture saw.
    assert (paths.app_dir / MONITORS_MODULE).read_bytes() == docked_bytes[0]
    assert (paths.app_dir / WORKSPACE_RULES_MODULE).read_bytes() == docked_bytes[1]

    # The live half: the activated config loads cleanly and the compositor obeys it.
    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested-profiles.log"
    ) as nested:
        assert nested.config_errors() == (), (
            f"the activated profile did not load cleanly: {nested.config_errors()}"
        )

        monitors_state = nested.hyprctl("monitors")
        assert isinstance(monitors_state, list) and monitors_state, monitors_state
        scales = {float(entry.get("scale", 0)) for entry in monitors_state}
        assert scales == {2.0}, f"the catch-all scale did not apply: {monitors_state!r}"

        rules_state = nested.hyprctl("workspacerules")
        assert isinstance(rules_state, list), rules_state
        by_selector = {
            str(entry.get("workspaceString", "")): entry
            for entry in rules_state
            if isinstance(entry, dict)
        }
        assert by_selector["1"].get("monitor") == "desc:Dell U2720Q", by_selector
        assert "monitor" not in by_selector["2"] or not by_selector["2"].get("monitor"), (
            f"the cleared pin survived activation: {by_selector['2']!r}"
        )
