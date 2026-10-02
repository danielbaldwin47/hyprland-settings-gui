"""Seed `tools/overlay_help.toml`: one entry per setting, each waiting for a decision (#129).

    .venv/bin/python tools/seed_overlay_help.py           # add what the table lacks
    .venv/bin/python tools/seed_overlay_help.py --check   # exit 1 if a setting is undecided

For every setting in the newest shipped schema the table gets an entry, and above it a
comment with what a writer needs to decide it: the upstream description Hyprland ships, the
wiki's text (from `docs/research/option-schema.coverage.json`, so no network), what the
combo already names, the restart note, and why the seed flags the setting as a candidate
(the upstream line is short, lists values the combo shows, is shorter than the wiki's, or
is too long for a Row). The writer then gives each entry `help = "..."` (prose that
replaces the upstream line) or `skip = "<reason the upstream line stands>"`.

The seed never overwrites a decision: a rerun keeps every `help` and `skip` and refreshes
only the comments, and an entry for a setting no shipped version has any more is dropped.
Wiki text tracks Hyprland's main branch, so it is a starting point: carry no behaviour,
key or value over that the installed version lacks.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import overlay_help

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.schema import load_schema  # noqa: E402
from hyprtweaker.engine.schema.resolve import available_versions  # noqa: E402

SCHEMA_DIR = ROOT / "data" / "schema"
COVERAGE = ROOT / "docs" / "research" / "option-schema.coverage.json"

SHORT = 40
"""An upstream description shorter than this is a candidate: too terse to say what it does."""
WIKI_LONGER = 15
"""The wiki's text longer than the upstream's by more than this is a candidate."""

HEADER = """\
# What each setting's Row says (#129).
#
# One `["<setting>"]` entry per setting, with exactly one of:
#   help = "..."   replaces the upstream line in the Row subtitle, the Help popover and the
#                  search text: what the setting does, then when you would change it. At
#                  most 160 characters and two sentences, ending with a period.
#   skip = "..."   why the upstream line stands as it is.
# Say "setting", "Hyprland", "this version"; never "option", "schema" or "wiki". Do not list
# values the combo already names, and do not restate restart needs (the restart pill does).
# The rules are code: tools/overlay_help.py, run by tests/unit/test_overlay_help.py.
#
# After editing, write the `help` entries into data/schema/overlay.json:
#     .venv/bin/python tools/curate_overlay.py
# The comment above each entry is generated: tools/seed_overlay_help.py rewrites it and
# keeps your decisions. Never edit `help` in overlay.json by hand.
"""


@dataclass(frozen=True, slots=True)
class Facts:
    """What the comment above one entry carries."""

    name: str
    section: str
    title: str
    group: str | None
    kind: str
    upstream: str
    wiki: str | None
    named: str | None
    restart_note: bool
    flags: tuple[str, ...]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def flags_for(upstream: str, wiki: str | None, values: Iterable[str]) -> tuple[str, ...]:
    """Why a setting is a candidate for prose of its own."""
    flags = []
    if len(upstream) < SHORT:
        flags.append(f"short ({len(upstream)} chars)")
    if len(upstream) > overlay_help.MAX_CHARS:
        flags.append(f"too long for a Row ({len(upstream)} chars)")
    shown = [value for value in values if value.lower() in _words(upstream)]
    if len(shown) >= 2:
        flags.append("lists values the combo shows: " + ", ".join(shown))
    if wiki is not None and len(wiki) > len(upstream) + WIKI_LONGER:
        flags.append(f"wiki is longer ({len(wiki)} chars)")
    return tuple(flags)


def gather(version: str) -> list[Facts]:
    schema = load_schema(version, SCHEMA_DIR)
    coverage: dict[str, Any] = {
        row["name"]: row for row in json.loads(COVERAGE.read_text(encoding="utf-8"))["options"]
    }
    generated = {
        row["name"]: row["description"]
        for row in json.loads((SCHEMA_DIR / f"hyprland-{version}.json").read_text("utf-8"))[
            "options"
        ]
    }
    facts = []
    for option in schema:
        row = coverage.get(option.name, {})
        wiki = row.get("wiki_desc")
        values: list[str] = []
        named = None
        if option.labels:
            values, named = list(option.labels), "labels"
        elif option.map:
            values, named = list(option.map), "map"
        elif option.known_values:
            values, named = list(option.known_values.values), "known values"
        facts.append(
            Facts(
                name=option.name,
                section=option.section,
                title=option.title,
                group=option.group,
                kind=f"{option.type.value}, default {option.default_raw!r}",
                upstream=generated[option.name],
                wiki=wiki,
                named=named if values else None,
                restart_note=bool(row.get("restart_note")),
                flags=flags_for(generated[option.name], wiki, values),
            )
        )
    return facts


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def render(facts: list[Facts], decided: dict[str, overlay_help.HelpEntry]) -> str:
    """The whole table: the header, then each setting's comment, header and decision."""
    out = [HEADER]
    section = None
    for fact in facts:
        if fact.section != section:
            section = fact.section
            out.append(f"\n# --- {section} " + "-" * max(4, 90 - len(section)) + "\n")
        entry = decided.get(fact.name, overlay_help.HelpEntry(None, None))
        out.append(f"# {fact.name}: {fact.title} [{fact.group}] ({fact.kind})")
        out.append(f"#   upstream: {fact.upstream}")
        out.append(f"#   wiki:     {fact.wiki or '(none)'}")
        if fact.named:
            out.append(f"#   the combo already names its values ({fact.named})")
        if fact.restart_note:
            out.append("#   the wiki notes a restart is needed")
        if fact.flags:
            out.append("#   candidate: " + "; ".join(fact.flags))
        out.append(f"[{_quote(fact.name)}]")
        if entry.help is not None:
            out.append(f"help = {_quote(entry.help)}")
        if entry.skip is not None:
            out.append(f"skip = {_quote(entry.skip)}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a setting is undecided")
    args = parser.parse_args(argv)

    version = list(available_versions(SCHEMA_DIR))[-1]
    decided = overlay_help.load_table() if overlay_help.TABLE.exists() else {}
    facts = gather(version)
    undecided = [
        fact.name
        for fact in facts
        if (entry := decided.get(fact.name)) is None
        or (entry.help is None) == (entry.skip is None)
    ]
    if args.check:
        if undecided:
            print(f"{len(undecided)} setting(s) undecided in {overlay_help.TABLE}")
        return 1 if undecided else 0

    overlay_help.TABLE.write_text(render(facts, decided), encoding="utf-8")
    print(f"{len(facts)} settings; {len(undecided)} undecided; {overlay_help.TABLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
