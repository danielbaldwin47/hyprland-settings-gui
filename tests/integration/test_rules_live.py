"""Rules, end to end, against a real compositor (#67, ADR-0008).

The acceptance criterion no headless tier can decide: a disabled Rule *stays in the
Module* but *does not apply*. The unit tier proves the renderer emits `enabled = false`
and the round-trip keeps it; only a compositor can prove that Hyprland reads that spelling
as "registered but inert" rather than ignoring it -- or worse, applying it anyway.

The witness is a probe window per rule: a `float = true` rule matching the enabled
probe's class must leave it floating, and the identical-but-disabled rule must leave its
probe tiled. `hyprctl clients` is the independent reporter -- helper data used exactly the
way ADR-0008 allows, to observe, never to reconstruct rule state.

    pytest tests/integration/test_rules_live.py -m hyprland
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION
from harness.visual import PROBE_SCRIPT

from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.model.entities import LayerRule, WindowRule
from hyprtweaker.engine.paths import LAYER_RULES_MODULE, WINDOW_RULES_MODULE, ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer
from hyprtweaker.engine.writer.rules import parse_rules_module

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"

FLOATED = "probe.rules.floated"
TILED = "probe.rules.tiled"

WINDOW_SETTLE_SECONDS = 2.2

WINDOW_RULES = [
    # The enabled rule: its probe window must come up floating. The match is the exact
    # escaped shape Pick-a-window prefills (ADR-0008).
    WindowRule(
        match={"class": f"^({re.escape(FLOATED)})$"},
        effects={"float": True},
        name="float-the-probe",
    ),
    # The identical rule, disabled: in the file, registered, inert (ADR-0008).
    WindowRule(
        match={"class": f"^({re.escape(TILED)})$"},
        effects={"float": True},
        enabled=False,
    ),
]

LAYER_RULES = [
    LayerRule(match={"namespace": "^(probe-namespace)$"}, effects={"blur": True}),
]


def build(home: Path) -> tuple[ConfigPaths, ConfigModel, Writer]:
    paths = ConfigPaths.rooted_at(home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)

    model = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    model.entities.window_rules.extend(WINDOW_RULES)
    model.entities.layer_rules.extend(LAYER_RULES)
    return paths, model, Writer(paths, app_version=APP_VERSION)


def _open_probe(nested: NestedHyprland, app_id: str) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [sys.executable, str(PROBE_SCRIPT), app_id, "0.2,0.4,0.6,1.0"],
        env=nested.env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(WINDOW_SETTLE_SECONDS)
    return process


def _client(nested: NestedHyprland, app_id: str) -> dict[str, object]:
    clients = nested.hyprctl("clients")
    assert isinstance(clients, list)
    for entry in clients:
        if isinstance(entry, dict) and entry.get("class") == app_id:
            return entry
    raise AssertionError(f"probe window {app_id} never appeared in {clients!r}")


def test_a_disabled_rule_stays_in_the_module_but_does_not_apply(
    harness_home: Path, artifacts: Path
) -> None:
    """The acceptance run: model -> rule Modules -> a compositor that obeys `enabled`."""
    paths, model, writer = build(harness_home)
    result = writer.write(model)

    assert WINDOW_RULES_MODULE in result.written, (
        f"the Writer did not produce {WINDOW_RULES_MODULE}: {sorted(result.written)}"
    )
    assert LAYER_RULES_MODULE in result.written

    # The file half of the criterion, checked against the bytes on disk: the disabled
    # rule is *in* the Module, as a real call with `enabled = false`, not a comment.
    module_text = (paths.app_dir / WINDOW_RULES_MODULE).read_text(encoding="utf-8")
    assert module_text.count("hl.window_rule(") == len(WINDOW_RULES)
    assert "enabled = false" in module_text

    probes: list[subprocess.Popen[bytes]] = []
    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested-rules.log"
    ) as nested:
        try:
            assert nested.config_errors() == (), (
                f"the written rules did not load cleanly: {nested.config_errors()}"
            )

            probes.append(_open_probe(nested, FLOATED))
            probes.append(_open_probe(nested, TILED))

            floated = _client(nested, FLOATED)
            tiled = _client(nested, TILED)

            assert floated.get("floating") is True, (
                f"the enabled float rule did not apply: {floated!r}"
            )
            assert tiled.get("floating") is False, (
                f"the disabled rule applied anyway: {tiled!r}"
            )
        finally:
            for probe in probes:
                probe.terminate()


def test_the_written_modules_read_back_as_what_was_written(harness_home: Path) -> None:
    """The hand-edit adoption path agrees with the writer, disabled rule included."""
    paths, model, writer = build(harness_home)
    writer.write(model)

    parsed = parse_rules_module(paths.app_dir / WINDOW_RULES_MODULE)
    assert parsed.ok, parsed.errors
    assert [
        (dict(rule.match), dict(rule.effects), rule.name, rule.enabled)
        for rule in parsed.window_rules
    ] == [
        (dict(rule.match), dict(rule.effects), rule.name, rule.enabled) for rule in WINDOW_RULES
    ]

    layers = parse_rules_module(paths.app_dir / LAYER_RULES_MODULE, module=LAYER_RULES_MODULE)
    assert layers.ok, layers.errors
    assert len(layers.layer_rules) == len(LAYER_RULES)
