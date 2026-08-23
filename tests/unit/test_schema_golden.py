"""Golden file over the resolved Schema: every Option, every decided field.

The Schema is the input to every generated Row in the app, so a change in how one Option
resolves is a change in what a user sees. A golden file makes that diff-shaped and
reviewable -- retyping an Option, losing a `depends_on`, or flipping a visibility tier
shows up as a line in a code review instead of as a bug report.

Regenerate deliberately, never reflexively::

    UPDATE_GOLDEN=1 pytest tests/unit/test_schema_golden.py

A regenerated golden that nobody read is worse than no golden at all: it converts a failing
test into a silent behaviour change. Read the diff first.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hyprtweaker.engine.schema import ResolvedOption, Schema
from hyprtweaker.engine.schema import generated as generated_module
from hyprtweaker.engine.schema import overlay as overlay_module
from hyprtweaker.engine.schema.resolve import available_versions

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "data" / "schema"
GOLDEN_DIR = ROOT / "tests" / "golden"

COLUMNS = ("name", "type", "widget", "nullable", "visibility", "depends_on", "title")


def _row(option: ResolvedOption) -> str:
    dependency = (
        f"{option.depends_on.option}={option.depends_on.value}"
        if option.depends_on is not None
        else "-"
    )
    return " | ".join(
        (
            option.name,
            option.type.value,
            option.widget.value,
            "null" if option.nullable else "-",
            option.visibility.value,
            dependency,
            option.title,
        )
    )


def render(schema: Schema) -> str:
    header = " | ".join(COLUMNS)
    rows = [_row(option) for option in sorted(schema.options, key=lambda o: o.order)]
    return "\n".join([header, *rows]) + "\n"


def load_schema_for(version: str) -> Schema:
    return Schema.merge(
        generated_module.load(SCHEMA_DIR / f"hyprland-{version}.json"),
        overlay_module.load(SCHEMA_DIR / "overlay.json"),
    )


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_resolved_schema_matches_golden(version: str) -> None:
    schema = load_schema_for(version)
    actual = render(schema)
    golden = GOLDEN_DIR / f"schema-{version}.resolved.txt"

    if os.environ.get("UPDATE_GOLDEN"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
        pytest.skip(f"regenerated {golden.name}")

    assert golden.is_file(), (
        f"missing golden file {golden}; create it with UPDATE_GOLDEN=1 and read the diff"
    )
    assert actual == golden.read_text(encoding="utf-8"), (
        f"the resolved Schema no longer matches {golden.name}. If the change is intended, "
        "regenerate with UPDATE_GOLDEN=1 and review the diff in the PR."
    )


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_golden_covers_every_option(version: str) -> None:
    """Guards the golden itself: a truncated file must not read as a passing comparison."""
    schema = load_schema_for(version)
    golden = GOLDEN_DIR / f"schema-{version}.resolved.txt"
    lines = golden.read_text(encoding="utf-8").splitlines()

    assert len(lines) == len(schema.options) + 1, (
        f"{golden.name} has {len(lines) - 1} rows but the schema has {len(schema.options)}"
    )
    assert lines[0] == " | ".join(COLUMNS)
