"""The Lua importer: a foreign `hyprland.lua`, evaluated rather than parsed (ADR-0009).

A hyprlang config can be read. A Lua config has to be *run* -- only Lua knows what
`for _, m in ipairs(discover()) do hl.monitor(m) end` declares. So the file runs, but
against a recording stub instead of the real API and with the world held away from it,
and what it declared while running becomes the model.

Three modules, one direction of flow:

* `sandbox` -- runs the file (through `runner.lua`) and returns a `Recording`. Owns
  consent and the block/passthrough policy; the only place the app executes user code.
* `scripts` -- lifts closures back out of their source files for `legacy.lua`, and
  detects the globals they read that lifting cannot carry.
* `mapping` -- turns a `Recording` into the model, the Entities, `legacy.lua` and the
  shared Loss report.
"""

from __future__ import annotations

from .mapping import import_lua, map_recording
from .sandbox import (
    Call,
    Consent,
    ConsentRequired,
    LuaUnavailable,
    Policy,
    Recording,
    Script,
    evaluate,
    lua_binary,
)
from .scripts import ScriptSource, render_legacy

__all__ = [
    "Call",
    "Consent",
    "ConsentRequired",
    "LuaUnavailable",
    "Policy",
    "Recording",
    "Script",
    "ScriptSource",
    "evaluate",
    "import_lua",
    "lua_binary",
    "map_recording",
    "render_legacy",
]
