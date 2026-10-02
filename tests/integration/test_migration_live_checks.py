"""The Migration switch's live checks, against a real compositor (#101, ADR-0009).

The unit tier proves each check compares what it should. Only a compositor can say whether
the *expected* side is right: that the binds the Writer emits are the binds Hyprland
registers, that a workspace rule with one field is listed, that the arrangement a monitor
rule asks for is the one Hyprland ends up with. A check that is wrong here is the worst
failure this wizard has, a hard rollback of a migration that worked, so the corpus run below
is the proof that the bind count may be hard.

Each test writes a model the way the wizard does, boots a nested compositor on it, and runs
`MigrationFlow.verify_live` over the real IPC socket.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest \\
        tests/integration/test_migration_live_checks.py -m hyprland
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from harness import NestedHyprland, rices, write_determinism_preamble
from harness.corpus import rice as find_rice
from harness.corpus import stage
from harness.state import SCHEMA_DIR, SCHEMA_VERSION

from hyprtweaker.engine.importer import import_config
from hyprtweaker.engine.importer.loss import LossReport
from hyprtweaker.engine.importer.mapping import ImportResult
from hyprtweaker.engine.ipc import CommandClient
from hyprtweaker.engine.migration.flow import Check, MigrationFlow, Preview
from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.model.entities import (
    Bind,
    DispatcherCall,
    LayerRule,
    MonitorRule,
    WindowRule,
    WorkspaceRule,
)
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"


def verify(nested: NestedHyprland, paths: ConfigPaths, model: ConfigModel) -> list[Check]:
    """Run the switch's live checks over the nested compositor's own socket."""
    flow = MigrationFlow(
        paths=paths,
        schema=load_schema(SCHEMA_VERSION, SCHEMA_DIR),
        app_version=APP_VERSION,
        client=CommandClient(nested.instance),
    )
    result = ImportResult(model=model, entities=model.entities, loss=LossReport())
    preview = Preview(detection=flow.detect(), result=result)
    return asyncio.run(flow.verify_live(preview))


def test_every_kind_of_entity_the_wizard_writes_is_found_live(
    harness_home: Path, artifacts: Path
) -> None:
    """One of each, written by the Writer, read back by the checks over the socket."""
    paths = ConfigPaths.rooted_at(harness_home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)

    model = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    entities = model.entities
    entities.binds.extend(
        [
            Bind(keys="SUPER + Q", dispatcher=DispatcherCall(path="window.close")),
            Bind(keys="SUPER + W", dispatcher=DispatcherCall(path="window.kill")),
            # Never registers: a comment in the Module. The check must not expect it.
            Bind(
                keys="SUPER + E",
                dispatcher=DispatcherCall(path="window.close"),
                enabled=False,
            ),
            # Never emitted at all: a function-valued action belongs to user.lua.
            Bind(keys="SUPER + R", dispatcher=None),
        ]
    )
    entities.window_rules.append(WindowRule(match={"class": "^(a)$"}, effects={"float": True}))
    entities.layer_rules.append(LayerRule(match={"namespace": "^(x)$"}, effects={"blur": True}))
    entities.workspace_rules.extend(
        [
            WorkspaceRule(workspace="1", fields={"gaps_in": 3}),
            WorkspaceRule(workspace="2", fields={"gaps_in": 4}),
            WorkspaceRule(workspace="3"),  # a selector with nothing else to say
        ]
    )
    entities.monitors.append(MonitorRule(output="", fields={"scale": 1}))
    Writer(paths, app_version=APP_VERSION).write(model)

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested.log"
    ) as nested:
        checks = verify(nested, paths, model)

    by_name = {check.name: check for check in checks}
    assert by_name["configerrors"].ok, by_name["configerrors"].detail
    assert by_name["binds"].ok, by_name["binds"].detail
    assert by_name["workspace rules"].ok, by_name["workspace rules"].detail
    assert [check.detail for check in checks if check.name == "monitors"] == [""]
    assert {check.name for check in checks} == {
        "configerrors",
        "binds",
        "workspace rules",
        "monitors",
    }, "window and layer rules have no listing to verify against"


def test_a_missing_bind_is_caught_live(harness_home: Path, artifacts: Path) -> None:
    """The negative: a config that loads with fewer binds than the model has fails."""
    paths = ConfigPaths.rooted_at(harness_home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)

    written = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    written.entities.binds.append(
        Bind(keys="SUPER + Q", dispatcher=DispatcherCall(path="window.close"))
    )
    Writer(paths, app_version=APP_VERSION).write(written)

    expected = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    expected.entities.binds.extend(
        [
            Bind(keys="SUPER + Q", dispatcher=DispatcherCall(path="window.close")),
            Bind(keys="SUPER + W", dispatcher=DispatcherCall(path="window.kill")),
        ]
    )
    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested.log"
    ) as nested:
        checks = verify(nested, paths, expected)

    binds = next(check for check in checks if check.name == "binds")
    assert not binds.ok
    assert binds.hard
    assert binds.detail == "Only 1 of the 2 keybinds in the new configuration are active."


@pytest.mark.parametrize("rice", [candidate.name for candidate in rices()])
def test_no_corpus_rice_is_rolled_back_by_a_bind_count_that_is_not_the_switchs_fault(
    rice: str, tmp_path: Path, artifacts: Path
) -> None:
    """The proof the bind check may be hard: every pinned rice, imported, written and booted.

    A hard check that fails here for a rice that loaded cleanly would roll back a real
    user's migration over a count, so the assertion is exactly "no hard failure among the
    entity checks". The soft checks are written to the artifacts for reading.
    """
    staged = stage(find_rice(rice), tmp_path / "home")
    schema = load_schema(SCHEMA_VERSION, SCHEMA_DIR)
    result = import_config(
        staged.entrypoint,
        schema,
        env={"HOME": str(staged.home), "XDG_CONFIG_HOME": str(staged.home / ".config")},
    )
    paths = ConfigPaths(hypr_dir=staged.hypr_dir, state_dir=tmp_path / "state")
    result.model.adopt_entities(result.entities)  # what the wizard's write step does
    Writer(paths, app_version=APP_VERSION).write(result.model)

    with NestedHyprland(
        paths.entrypoint, home=staged.home, log=artifacts / f"{rice}.log"
    ) as nested:
        checks = verify(nested, paths, result.model)

    report = "\n".join(
        f"{'ok  ' if check.ok else 'FAIL'} {'hard' if check.hard else 'soft'} "
        f"{check.name}: {check.detail}"
        for check in checks
    )
    (artifacts / f"{rice}-checks.txt").write_text(report + "\n")
    print(f"\n[{rice}]\n{report}")

    entity_failures = [
        check
        for check in checks
        if check.hard and not check.ok and check.name != "configerrors"
    ]
    assert not entity_failures, f"{rice}: {entity_failures}"
