"""Write the curated tables into `data/schema/overlay.json`.

    .venv/bin/python tools/curate_overlay.py          # rewrite the Overlay from the tables
    .venv/bin/python tools/curate_overlay.py --check  # exit 1 if they disagree

`tools/overlay_groups.toml` holds each Section's Groups in display order, each with a
`title`, an optional `description` and its member Options in display order. The script
writes `sections.<s>.groups` and each member's `group` and `order` (its 1-based position in
the list), and removes both from every Option no table row names, so the Overlay converges
on the table whatever state it was left in. It writes through `overlay_text`, which leaves
every entry whose values do not change byte-identical; running it twice changes nothing.

`tools/overlay_help.toml` holds each setting's `help` prose (or the reason it has none, see
`tools/overlay_help.py`); the script writes each `help` the same way, and removes it from
every Option the table gives none.

`tests/unit/test_curate_overlay.py` fails while the Overlay and the table disagree. The
conventions a table row follows are in `tests/unit/test_overlay_completeness.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import overlay_help
import overlay_text

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "data" / "schema" / "overlay.json"
GROUPS_TABLE = ROOT / "tools" / "overlay_groups.toml"

_GROUP_KEYS = frozenset({"title", "description", "options"})


@dataclass(frozen=True, slots=True)
class CuratedGroup:
    """One table row: a Group's heading, its optional description and its members."""

    title: str
    description: str | None
    options: tuple[str, ...]


Tables = Mapping[str, tuple[CuratedGroup, ...]]


def parse_groups(text: str) -> dict[str, tuple[CuratedGroup, ...]]:
    """The Groups table, by Section, rejecting what the Overlay's loader would not explain."""
    tables: dict[str, tuple[CuratedGroup, ...]] = {}
    placed: set[str] = set()
    for section, rows in tomllib.loads(text).items():
        groups: list[CuratedGroup] = []
        for row in rows:
            unknown = set(row) - _GROUP_KEYS
            if unknown:
                raise ValueError(f"section {section!r}: unknown key(s) {sorted(unknown)}")
            group = CuratedGroup(
                title=str(row["title"]),
                description=row.get("description"),
                options=tuple(str(name) for name in row["options"]),
            )
            for name in group.options:
                if name.split(":", 1)[0] != section:
                    raise ValueError(f"{name!r} is not an option of section {section!r}")
                if name in placed:
                    raise ValueError(f"{name!r} is placed twice")
                placed.add(name)
            groups.append(group)
        tables[section] = tuple(groups)
    return tables


def load_tables(path: Path = GROUPS_TABLE) -> dict[str, tuple[CuratedGroup, ...]]:
    return parse_groups(path.read_text(encoding="utf-8"))


def _group_entry(group: CuratedGroup) -> dict[str, str]:
    entry = {"title": group.title}
    if group.description is not None:
        entry["description"] = group.description
    return entry


def curate(text: str, tables: Tables, helps: Mapping[str, str] | None = None) -> str:
    """`text` with every Section's Groups and every Option's `group` and `order` set from
    `tables`: what the tables name is written, what they do not is removed.

    Given `helps` (the prose of `tools/overlay_help.toml`), each Option's `help` is set the
    same way: written where the table has it, removed where it does not. Without it, `help`
    is left as it is."""
    payload = json.loads(text)

    sections: dict[str, dict[str, Any]] = {
        name: {"groups": None} for name in payload["sections"]
    }
    for name, groups in tables.items():
        sections[name] = {"groups": [_group_entry(group) for group in groups] or None}

    options: dict[str, dict[str, Any]] = {
        name: {"group": None, "order": None} for name in payload["options"]
    }
    if helps is not None:
        for name in options:
            options[name]["help"] = None
        for name, prose in helps.items():
            options[name] = {**options.get(name, {}), "help": prose}
    for groups in tables.values():
        for group in groups:
            for order, name in enumerate(group.options, start=1):
                options[name] = {**options[name], "group": group.title, "order": order}

    text = overlay_text.set_fields(text, "sections", sections)
    return overlay_text.set_fields(text, "options", options)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the Overlay is stale")
    args = parser.parse_args(argv)

    current = OVERLAY.read_text(encoding="utf-8")
    curated = curate(current, load_tables(), overlay_help.helps(overlay_help.load_table()))
    if curated == current:
        return 0
    if args.check:
        print(f"{OVERLAY} disagrees with {GROUPS_TABLE}: run tools/curate_overlay.py")
        return 1
    OVERLAY.write_text(curated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
