"""Golden-file machinery for the unit tier: one compare-or-regenerate, one stream renderer.

Three test modules keep goldens (schema, hyprlang grammar, corpus rice) and each had its own
copy of the same `UPDATE_GOLDEN` dance. One copy means the regeneration contract -- skip on
regenerate, demand the file otherwise, and always tell the reader to look at the diff -- is
stated once and cannot drift between them.

Kept apart from `_support.py`, which parses `meson.build` file lists and builds schema
fixtures: golden handling is a different reason to edit a file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hyprtweaker.engine.importer import (
    Assignment,
    Handler,
    ParseResult,
    SourceEnter,
    SourceLeave,
    SpecialCategory,
    UnparsedLine,
    VariableDefinition,
)


def assert_matches_golden(actual: str, golden: Path, subject: str) -> None:
    """Compare against a golden file, or rewrite it when `UPDATE_GOLDEN` is set.

    Regenerate deliberately, never reflexively -- a regenerated golden nobody read turns a
    failing test into a silent behaviour change.
    """
    if os.environ.get("UPDATE_GOLDEN"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
        pytest.skip(f"regenerated {golden.name}")

    assert golden.is_file(), (
        f"missing golden file {golden}; create it with UPDATE_GOLDEN=1 and read the diff"
    )
    assert actual == golden.read_text(encoding="utf-8"), (
        f"{subject} no longer matches {golden.name}. If the change is intended, regenerate "
        "with UPDATE_GOLDEN=1 and review the diff in the PR."
    )


def _relative(path: Path, root: Path) -> str:
    """Tree-relative path, so a golden does not encode where the checkout lives."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _keyword_row(keyword: object, root: Path) -> str:
    """One `kind | where | detail` row, mirroring the schema golden's column style."""
    if isinstance(keyword, SourceEnter):
        return f"enter | {_relative(keyword.file, root)} | -"
    if isinstance(keyword, SourceLeave):
        return f"leave | {_relative(keyword.file, root)} | -"

    where = f"{_relative(keyword.origin.file, root)}:{keyword.origin.line}"

    if isinstance(keyword, VariableDefinition):
        return f"var | {where} | ${keyword.name} = {keyword.value}"
    if isinstance(keyword, Assignment):
        suffix = " [orphan]" if keyword.orphan else ""
        return f"assign | {where} | {keyword.key} = {keyword.value}{suffix}"
    if isinstance(keyword, Handler):
        name = f"{keyword.name}({keyword.flags})" if keyword.flags else keyword.name
        return f"handler | {where} | {name} = {keyword.value}"
    if isinstance(keyword, SpecialCategory):
        key = (
            f" {keyword.key_field}={keyword.key_value}"
            if keyword.key_field is not None and keyword.key_value is not None
            else ""
        )
        shape = "inline" if keyword.inline else "block"
        fields = "; ".join(f"{f.key} = {f.value}" for f in keyword.fields)
        return f"special | {where} | {keyword.category}{key} [{shape}] {{ {fields} }}"
    if isinstance(keyword, UnparsedLine):
        return f"unparsed | {where} | {keyword.code.value}: {keyword.text.strip()}"
    raise AssertionError(f"unrendered keyword kind: {type(keyword).__name__}")


def render_keyword_stream(result: ParseResult, root: Path) -> str:
    """The full parse as reviewable text: the ordered stream, then every finding.

    A golden over this is the only way a change in the grammar shows up as a diff a human
    reads rather than as a rice that silently converts differently.
    """
    lines = ["# keyword stream", "kind | where | detail"]
    lines += [_keyword_row(keyword, root) for keyword in result.keywords]

    lines += ["", "# diagnostics", "severity | code | where | message"]
    for diagnostic in result.diagnostics:
        severity = diagnostic.severity.value
        if diagnostic.suppressed:
            severity += " (suppressed)"
        where = f"{_relative(diagnostic.origin.file, root)}:{diagnostic.origin.line}"
        lines.append(f"{severity} | {diagnostic.code.value} | {where} | {diagnostic.message}")

    return "\n".join(lines) + "\n"
