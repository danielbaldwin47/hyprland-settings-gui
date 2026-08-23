"""Binds, end to end, against a real compositor (#64, ADR-0007).

The acceptance criterion is that both add doors "create working binds on the live
compositor", and it is the one claim no headless tier can make. The unit tier proves the
renderer emits what it was told; the static tier proves `luac` would parse it. Neither
proves Hyprland *accepts* a bind.

It caught one on the way in. The dispatcher catalog had `exec_cmd` taking a table, and the
compositor's answer was `exec_cmd: bad argument 1: expected string, got table` -- so the
"Run command" door, the majority bind type in every corpus rice, would have written a
config that refused to load. Nothing headless could have found that: the file was valid
Lua, the round-trip was self-consistent, and the model agreed with itself throughout.

Nested, never the host. Binding keys on the developer's own session would be an unpleasant
surprise at best; `NestedHyprland` is a compositor of our own that cannot reach it.

Read-back here is `hyprctl binds`, which is *not* how the app verifies binds and must not
be mistaken for an endorsement of doing so -- it is blind to `code:N`, which is the whole
reason ADR-0007 makes binds write-only over IPC. It is used here for the one thing it is
good for: an independent witness that the compositor really took the bind, rather than the
app agreeing with itself about the file it just wrote.

    pytest tests/integration/test_binds_live.py -m hyprland
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION

from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.model.entities import Bind, BindOptions, DispatcherCall
from hyprtweaker.engine.paths import BINDS_MODULE, ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer
from hyprtweaker.engine.writer.binds import parse_binds_module

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"


def exec_bind(keys: str, command: str, **kwargs: Any) -> Bind:
    """The "Run command" door's output."""
    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path="exec_cmd", positional=(command,)),
        **kwargs,
    )


def action_bind(keys: str, path: str, **kwargs: Any) -> Bind:
    """The "Hyprland action" door's output."""
    return Bind(keys=keys, dispatcher=DispatcherCall(path=path), **kwargs)


BINDS = [
    # Door 1: exec, the majority bind type in every corpus rice.
    exec_bind("SUPER + Q", "true", options=BindOptions(description="Run something")),
    # Door 2: a typed dispatcher.
    action_bind("SUPER + C", "window.close"),
    # A flag, to prove the options table is accepted and not merely tolerated.
    action_bind("SUPER + K", "window.kill", options=BindOptions(locked=True)),
    # The bind IPC cannot see. It must still load without error -- that it does not come
    # back from `hyprctl binds` is the defect ADR-0007 is built around, asserted below.
    exec_bind("SUPER + code:10", "true"),
    # A duplicate of the first: legal, and both fire in order.
    exec_bind("SUPER + Q", "true"),
]


def build(home: Path) -> tuple[ConfigPaths, ConfigModel, Writer]:
    paths = ConfigPaths.rooted_at(home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)

    model = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    model.entities.binds.extend(BINDS)
    return paths, model, Writer(paths, app_version=APP_VERSION)


def test_written_binds_load_and_bind(harness_home: Path, artifacts: Path) -> None:
    """The acceptance run: model -> binds.lua -> a compositor that took the binds."""
    paths, model, writer = build(harness_home)
    result = writer.write(model)

    assert BINDS_MODULE in result.written, (
        f"the Writer did not produce {BINDS_MODULE}: {sorted(result.written)}"
    )

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested-binds.log"
    ) as nested:
        assert nested.config_errors() == (), (
            f"the written binds did not load cleanly: {nested.config_errors()}"
        )

        reported = nested.hyprctl("binds")
        assert isinstance(reported, list)

        assert len(reported) == len(BINDS), (
            f"every bind should register, including the duplicate: {reported}"
        )

        assert [entry["key"] for entry in reported] == ["Q", "C", "K", "", "Q"], (
            "binds must register in file order, duplicates included"
        )

        locked = [entry for entry in reported if entry["locked"]]
        assert len(locked) == 1, "the locked flag reached the compositor"

        assert not any(entry["mouse"] for entry in reported), (
            "the mouse flag is inert and must never be emitted (ADR-0007)"
        )

        assert any(entry["description"] == "Run something" for entry in reported)


def test_ipc_cannot_identify_a_lua_bind(harness_home: Path, artifacts: Path) -> None:
    """The premise ADR-0007 is built on, asserted against the compositor rather than cited.

    Two independent reasons `hyprctl binds` cannot be used to rebuild bind state, both
    visible in one reply:

    1. **Every Lua bind reports `dispatcher: "__lua"`** with an opaque callback id for its
       argument. What the bind *does* is not recoverable -- an exec and a window-close are
       indistinguishable from here.
    2. **A `code:N` bind reports `key: "", keycode: 0`.** It registered and it works, but
       the reply carries nothing that identifies which key it is.

    If a later Hyprland fixed either, this test fails and the ADR's write-only decision is
    worth revisiting -- which is the point of asserting it instead of quoting it.
    """
    paths, model, writer = build(harness_home)
    writer.write(model)

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested-ipc-blind.log"
    ) as nested:
        reported = nested.hyprctl("binds")

        assert {entry["dispatcher"] for entry in reported} == {"__lua"}, (
            "a Lua bind's action is opaque over IPC"
        )

        keycode_binds = [entry for entry in reported if not entry["key"]]
        assert len(keycode_binds) == 1, "the code:N bind registered but has no reportable key"
        assert keycode_binds[0]["keycode"] == 0, (
            "`parseKeyString` files keycodes under sMkKeys; IPC reports 0"
        )


def test_the_written_module_reads_back_as_what_was_written(harness_home: Path) -> None:
    """Hand-editability: the file the compositor loaded is one the app can re-read."""
    paths, model, writer = build(harness_home)
    writer.write(model)

    parsed = parse_binds_module(paths.app_dir / BINDS_MODULE)

    assert parsed.ok, parsed.errors
    assert [bind.keys for bind in parsed.binds] == [bind.keys for bind in BINDS]
