"""User-visible text uses American spelling (#148 hand-test 10, the addendum's ruling).

Hyprland's own names say `color`, as GTK and the GNOME HIG do; one window said "colour" on
every Row and "color" on Theming. The scan covers what a user reads: the Overlay's and the
Tasks table's string values, the two curated TOML tables outside their comments, and every
string literal in `src/` that is not a docstring. Identifiers, comments and docs are not
copy and stay as written.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRITISH = re.compile(
    r"\b(colour|centre|behaviour|grey|favourite|recognis|customis|minimis|maximis|initialis)",
    re.IGNORECASE,
)
"""The British forms the ruling names (-ise verbs by stem)."""


def json_values(path: Path) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and BRITISH.search(node):
            found.append(node)

    walk(json.loads(path.read_text(encoding="utf-8")))
    return found


def toml_lines(path: Path) -> list[str]:
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#") and BRITISH.search(line)
    ]


def literal_hits(source: str) -> list[str]:
    """String literals that are not docstrings and carry a British form."""
    docstrings: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            docstrings.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    kinds = {tokenize.STRING}
    if hasattr(tokenize, "FSTRING_MIDDLE"):
        kinds.add(tokenize.FSTRING_MIDDLE)
    return [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type in kinds
        and token.start[0] not in docstrings
        and BRITISH.search(token.string)
    ]


def test_the_schema_tables_say_color() -> None:
    assert json_values(ROOT / "data/schema/overlay.json") == []
    assert json_values(ROOT / "data/schema/tasks.json") == []
    assert toml_lines(ROOT / "tools/overlay_help.toml") == []
    assert toml_lines(ROOT / "tools/overlay_groups.toml") == []


def test_no_string_the_app_shows_says_colour() -> None:
    found = [
        f"{path.relative_to(ROOT)}: {hit}"
        for path in sorted((ROOT / "src").rglob("*.py"))
        for hit in literal_hits(path.read_text(encoding="utf-8"))
    ]
    assert found == []


def test_the_scan_sees_a_literal_and_skips_a_docstring() -> None:
    source = 'def f():\n    """A colour."""\n    return "Pick a colour"\n'
    assert literal_hits(source) == ['"Pick a colour"']
