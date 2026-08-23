"""Rendering a nested Lua table, deterministically.

The writer's whole output is one shape -- `hl.config({ ... })` around a tree of tables --
so this module is small on purpose: build a tree whose leaves are *already* Lua source
(the value types render their own literals, `model/values.py`), then print it.

Determinism is the requirement that shapes everything here. The Manifest stores a content
hash per Module and the Apply transaction skips a write whose bytes did not change, so a
renderer that reordered keys or spelled a number two ways would produce phantom writes,
phantom reloads and phantom hand-edit warnings.
"""

from __future__ import annotations

import re
from typing import TypeAlias

from ..model.values import lua_string

INDENT = "  "

LUA_KEYWORDS = frozenset(
    {
        "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
        "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
        "then", "true", "until", "while",
    }
)  # fmt: skip

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

LuaTree: TypeAlias = dict[str, "str | LuaTree"]
"""A table: keys to either a rendered Lua literal or a nested table.

Leaves are *already* Lua source. Keeping the tree that way means this module never has to
know what a gradient is -- the value types render themselves (`model/values.py`), and the
one place that could get a literal wrong stays one place.
"""


def table_key(key: str) -> str:
    """`foo = ` for a plain identifier, `["foo"] = ` for anything else.

    Hyprland's key vocabulary happens to be all identifiers today, but `end` and `repeat`
    are exactly the kind of name a future section could take, and a bare `end = 1` is a
    syntax error rather than a wrong value -- it would take the entire Module down.
    """
    if _IDENTIFIER.match(key) and key not in LUA_KEYWORDS:
        return f"{key} = "
    return f"[{lua_string(key)}] = "


def render_table(tree: LuaTree, depth: int = 0) -> str:
    """A nested Lua table literal, one entry per line, insertion order preserved.

    Insertion order is the caller's promise: the model hands over Options in Hyprland's own
    declaration order, so the generated file reads in the same order as the wiki and as
    `hyprctl descriptions`.
    """
    if not tree:
        return "{}"

    pad = INDENT * (depth + 1)
    lines = ["{"]
    for key, value in tree.items():
        rendered = render_table(value, depth + 1) if isinstance(value, dict) else value
        lines.append(f"{pad}{table_key(key)}{rendered},")
    lines.append(f"{INDENT * depth}}}")
    return "\n".join(lines)


def insert(tree: LuaTree, path: tuple[str, ...], literal: str) -> None:
    """Place a rendered literal at a dotted path, creating the intermediate tables.

    Raises rather than overwriting when a path would have to pass *through* a leaf: that
    means the Schema declared both `a.b` and `a.b.c` as values, which no `hl.config` table
    can express and which the generator should never produce.
    """
    node = tree
    for step in path[:-1]:
        branch = node.setdefault(step, {})
        if not isinstance(branch, dict):
            raise ValueError(f"{'.'.join(path)} passes through the leaf {step!r}")
        node = branch

    if isinstance(node.get(path[-1]), dict):
        raise ValueError(f"{'.'.join(path)} is already a table")
    node[path[-1]] = literal
