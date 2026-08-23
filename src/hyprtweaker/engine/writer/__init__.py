"""Writer: deterministic Module rendering, the Entrypoint, and the syntax gate.

Model in, App dir out (ADR-0005, ADR-0010). Four pieces:

- `lua.py` -- nested-table rendering, deterministic to the byte;
- `modules.py` -- one `hl.config` Module per Section, and the Entrypoint's require order;
- `syntax.py` -- the `luac -p` gate every rendered file passes before it reaches disk;
- `writer.py` -- `Writer`, which renders, gates, writes atomically, prunes stale Modules
  and updates the Manifest.

The Apply transaction (debounce, reload, read-back, auto-revert) is #54 and wraps this.

Typical use::

    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.writer import Writer

    writer = Writer(ConfigPaths.default(), app_version="0.1.0")
    result = writer.write(model)
    result.written        # ('options/decoration.lua',)
    result.skipped        # left alone rather than overwritten
    result.hand_edited    # app-owned files the app cannot show it wrote (ADR-0016)
"""

from __future__ import annotations

from .lua import LuaTree, insert, render_table, render_table_inline, table_key
from .modules import (
    is_option_module,
    module_relpath,
    module_stem,
    render_entrypoint,
    render_module,
)
from .syntax import LuaSyntaxError, gate, gate_available
from .writer import ModuleSet, ProtectedFile, Writer, WriteResult

__all__ = [
    "LuaSyntaxError",
    "LuaTree",
    "ModuleSet",
    "ProtectedFile",
    "WriteResult",
    "Writer",
    "gate",
    "gate_available",
    "insert",
    "is_option_module",
    "module_relpath",
    "module_stem",
    "render_entrypoint",
    "render_module",
    "render_table",
    "render_table_inline",
    "table_key",
]
