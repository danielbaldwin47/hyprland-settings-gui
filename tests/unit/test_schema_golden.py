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

import pytest
from _golden import assert_matches_golden
from _support import ROOT, SCHEMA_DIR

from hyprtweaker.engine.schema import ResolvedOption, Schema, load_schema
from hyprtweaker.engine.schema.resolve import available_versions

GOLDEN_DIR = ROOT / "tests" / "golden"

# Every curated field a Row reads, so dropping a `null_label`, a `labels` map or a
# clamped `range` shows up as a diff rather than as a wrong widget in the app.
COLUMNS = (
    "name",
    "type",
    "widget",
    "nullable",
    "null_label",
    "visibility",
    "depends_on",
    "range",
    "labels",
    "unit",
    "title",
)


def _row(option: ResolvedOption) -> str:
    dependency = (
        f"{option.depends_on.option}={option.depends_on.value}"
        if option.depends_on is not None
        else "-"
    )
    bounds = "-"
    if option.range is not None:
        parts = [
            f"{key}={value}"
            for key, value in (
                ("min", option.range.min),
                ("max", option.range.max),
                ("step", option.range.step),
                ("soft_max", option.range.soft_max),
            )
            if value is not None
        ]
        bounds = ",".join(parts) or "-"

    labels = (
        "/".join(f"{key}={value}" for key, value in option.labels.items())
        if option.labels
        else "-"
    )

    return " | ".join(
        (
            option.name,
            option.type.value,
            option.widget.value,
            "null" if option.nullable else "-",
            option.null_label or "-",
            option.visibility.value,
            dependency,
            bounds,
            labels,
            option.unit or "-",
            option.title,
        )
    )


def render(schema: Schema) -> str:
    header = " | ".join(COLUMNS)
    rows = [_row(option) for option in sorted(schema.options, key=lambda o: o.order)]
    return "\n".join([header, *rows]) + "\n"


def load_schema_for(version: str) -> Schema:
    """Through `load_schema` -- the Engine API the acceptance criterion names."""
    return load_schema(version, SCHEMA_DIR)


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_resolved_schema_matches_golden(version: str) -> None:
    schema = load_schema_for(version)
    actual = render(schema)
    golden = GOLDEN_DIR / f"schema-{version}.resolved.txt"

    assert_matches_golden(actual, golden, "the resolved Schema")


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
