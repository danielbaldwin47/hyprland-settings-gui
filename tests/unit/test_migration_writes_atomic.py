"""The migration flow writes the App dir's files whole or not at all (F16, ruling A15).

`engine/files.py` promises one helper, `write_atomic`, for every write outside the Writer
and the Journal: a crash during a plain `write_text` of `manifest.json`, `vars.lua` or
`legacy.lua` leaves a truncated file Hyprland or the app then reads.
"""

from __future__ import annotations

import ast
from pathlib import Path

FLOW = Path(__file__).resolve().parents[2] / "src/hyprtweaker/engine/migration/flow.py"


def plain_writes(source: str) -> list[int]:
    """Lines calling `.write_text(` or `.write_bytes(`."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"write_text", "write_bytes"}
    ]


def test_the_migration_flow_has_no_plain_write() -> None:
    assert plain_writes(FLOW.read_text()) == []


def test_the_scan_sees_a_plain_write() -> None:
    assert plain_writes("paths.manifest.write_text(text)") == [1]
