"""The static verify gate: `Hyprland --verify-config` over what the Writer produces.

**ADR-0011 tier 2** -- "Static (per-commit, when Hyprland present)". It is in `testpaths`
and carries no marker, so a plain `pytest` on a developer box with Hyprland runs it and one
without skips it. Deliberately *not* tier 3: `--verify-config` needs no compositor and
finishes in well under a second, so gating it behind `-m hyprland` would mean the check
that catches the most damaging class of writer bug never ran on the machine best able to
run it.

`luac -p` proves the output *parses*; only this proves Hyprland *accepts* it -- that every
value is spelled the way its own parser reads. The gap between the two is where the
expensive bugs live: a gradient written as `descriptions` text parses perfectly as Lua and
is rejected as a config.

It earned its place immediately. The first run found five Options whose curated "no value"
was `-1` -- valid Lua, valid hyprlang, and `invalid color "-1"` to the Lua engine. Nothing
in the unit tier could have seen that.

`--verify-config` *executes* the config with live bindings, so the compositor environment is
stripped first: without that, a verify run can reach the session the developer is sitting in
(prototype #30 FINDINGS §"sandbox").
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

pytestmark = pytest.mark.skipif(
    shutil.which("Hyprland") is None, reason="no Hyprland binary on this machine"
)


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
    _, emitted, total = write_everything(tmp_path, "0.56.2")

    assert (total - emitted) == 5, f"{total - emitted} Options were skipped, expected 5"


def write_declarations(root: Path, version: str) -> ConfigPaths:
    """Write a config holding one of every declarative Entity kind (#70).

    Separate from the maximal Option config above because the two prove different things.
    That one asks whether 353 *values* are spelled the way their parsers read; this one asks
    whether six *call shapes* are the ones `hl.curve`, `hl.animation`, `hl.gesture`,
    `hl.device`, `hl.env`, `hl.permission` and `hl.exec_cmd` accept.

    `luac -p` cannot answer that: every wrong shape here is valid Lua. A device field name
    that does not exist, a `curve =` key the wiki shows and the parser rejects, an animation
    naming a curve nothing declared -- each parses perfectly and takes the Module down.
    """
    from hyprtweaker.engine.model.entities import (
        Animation,
        Curve,
        Device,
        EnvVar,
        Gesture,
        Permission,
        StartupCommand,
    )

    paths = ConfigPaths.rooted_at(root)
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)

    model = ConfigModel(load_schema(version, SCHEMA_DIR))
    entities = model.entities
    entities.curves.extend(
        [
            Curve("easeOutQuint", {"type": "bezier", "points": [[0.23, 1], [0.32, 1]]}),
            Curve("easy", {"type": "spring", "mass": 1, "stiffness": 238.1, "dampening": 24.2}),
        ]
    )
    entities.animations.extend(
        [
            Animation(
                "windowsIn",
                {"enabled": True, "speed": 4.1, "bezier": "easeOutQuint", "style": "popin 87%"},
            ),
            Animation("windowsOut", {"enabled": True, "speed": 1.49, "spring": "easy"}),
            Animation("border", {"enabled": False}),
        ]
    )
    entities.gestures.extend(
        [
            Gesture({"fingers": 3, "direction": "horizontal", "action": "workspace"}),
            Gesture(
                {
                    "fingers": 4,
                    "direction": "up",
                    "action": "special",
                    "workspace_name": "magic",
                    "scale": 1.5,
                    "mods": "SUPER",
                    "disable_inhibit": True,
                }
            ),
        ]
    )
    entities.devices.extend(
        [
            Device("epic-mouse-v1", {"sensitivity": -0.5, "natural_scroll": True}),
            Device(
                "at-translated-set-2-keyboard",
                {"kb_layout": "us,de", "repeat_rate": 50, "repeat_delay": 300},
            ),
            Device("my-tablet", {"region_position": [10, 20], "transform": 1}),
        ]
    )
    entities.env.extend(
        [
            EnvVar("XCURSOR_SIZE", "24"),
            EnvVar("GDK_BACKEND", "wayland,x11,*"),
            EnvVar("XDG_CURRENT_DESKTOP", "Hyprland", dbus=True),
        ]
    )
    entities.permissions.extend(
        [
            Permission("/usr/(bin|local/bin)/grim", "screencopy", "allow"),
            Permission("/usr/bin/hyprlock", "keyboard", "deny"),
        ]
    )
    entities.startup.extend(
        [
            StartupCommand("true"),
            StartupCommand("[workspace 2 silent] true", raw=True),
            StartupCommand("true", event=""),
            StartupCommand("true", event="hyprland.shutdown"),
        ]
    )
    model.mark_entities_loaded()

    Writer(paths, app_version="0.0.0-test").write(model)
    return paths


def test_every_declarative_entity_kind_is_a_config_hyprland_accepts(tmp_path: Path) -> None:
    paths = write_declarations(tmp_path, "0.56.2")
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir()

    result = verify(paths.entrypoint, runtime_dir)

    assert result.returncode == 0, (
        f"Hyprland rejected the generated entity Modules:\n{result.stdout}\n{result.stderr}"
    )
    assert "config ok" in result.stdout


def test_the_entity_modules_were_actually_written(tmp_path: Path) -> None:
    """Guards the test above: "accepted" must not come from having written nothing."""
    paths = write_declarations(tmp_path, "0.56.2")

    written = {path.name for path in paths.app_dir.glob("*.lua")}

    assert {
        "animations.lua",
        "gestures.lua",
        "devices.lua",
        "env.lua",
        "permissions.lua",
        "autostart.lua",
    } <= written


def write_imported_conf(root: Path, version: str, conf: str) -> tuple[ConfigPaths, object]:
    """Import one `hyprland.conf` and write what came out, exactly as the wizard would."""
    from hyprtweaker.engine.importer import import_config

    paths = ConfigPaths.rooted_at(root)
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    legacy = root / "source" / "hyprland.conf"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(conf, encoding="utf-8")

    result = import_config(legacy, load_schema(version, SCHEMA_DIR))
    result.model.adopt_entities(result.entities)
    Writer(paths, app_version="0.0.0-test").write(result.model)
    return paths, result


DEAD_KEYSYM_CONF = """
bind = SUPER, Q, killactive
bind = SUPER, notakey, exec, kitty
bind = SUPER, alsonotakey, killactive
"""


def test_a_dead_keysym_bind_does_not_take_the_imported_config_down(tmp_path: Path) -> None:
    """The invariant #108 is gated on, proved where it actually bites (#131).

    hyprlang resolved key names at press time and let a typo sit inert for years; Lua
    resolves at bind time and refuses the *whole* config. So one imported dead keysym is
    not one dead bind -- it is a config that will not load, which is exactly what this tier
    exists to catch. The Importer disables such binds, and this asserts the disabling is
    the spelling Hyprland actually accepts rather than one that merely looks inert.
    """
    paths, result = write_imported_conf(tmp_path, "0.56.2", DEAD_KEYSYM_CONF)
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir()

    binds = result.entities.binds
    assert [bind.enabled for bind in binds] == [True, False, False], (
        "the fixture no longer exercises a dead keysym; xkb knows these names now"
    )

    verified = verify(paths.entrypoint, runtime_dir)

    assert verified.returncode == 0, (
        f"Hyprland rejected a config with an imported dead-keysym bind:"
        f"\n{verified.stdout}\n{verified.stderr}"
    )
    assert "config ok" in verified.stdout


def test_the_dead_keysym_binds_reached_the_file_as_comments(tmp_path: Path) -> None:
    """Guards the test above: "accepted" must not come from having dropped the binds.

    A dropped bind would also verify, and would lose the user's line -- the failure the
    Loss report exists to prevent.
    """
    paths, _ = write_imported_conf(tmp_path, "0.56.2", DEAD_KEYSYM_CONF)

    written = (paths.app_dir / "binds.lua").read_text(encoding="utf-8")

    assert "notakey" in written
    assert "alsonotakey" in written
    for line in written.splitlines():
        if "notakey" in line:
            assert line.strip().startswith("--"), line
