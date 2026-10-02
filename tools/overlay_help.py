"""The `help` table, and the rules every entry in it is held to (#129).

`tools/overlay_help.toml` has one entry per setting: `help = "..."` replaces the upstream
description in the Row subtitle, the Help popover and the search text; `skip = "<reason>"`
records that the upstream line is already good enough. `tools/curate_overlay.py` writes the
`help` entries into `data/schema/overlay.json`; `tools/seed_overlay_help.py` lists the
settings that have no decision yet.

The rules live here, as code, so the test and the seed apply the same ones:

- at most `MAX_CHARS` characters and `MAX_SENTENCES` sentences, ending with a period (the
  Row subtitle has no line cap, so this bound is what keeps every Row short);
- none of the words the app does not use (`BANNED`), except "XKB options", the term the
  user types;
- not a copy of the upstream description;
- on a control that shows labels (a combo or segmented control), no bare stored value: the
  user sees "Auto", never the `2` it is stored as, so the help names the label.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "tools" / "overlay_help.toml"

MAX_CHARS = 160
MAX_SENTENCES = 2

BANNED = re.compile(
    r"\b(schema|overlay|options?|wiki|release check|config variables?)\b", re.IGNORECASE
)
_XKB_OPTIONS = re.compile(r"\bXKB options?\b")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?![\w.]\d)")
_KEYS = frozenset({"help", "skip"})


@dataclass(frozen=True, slots=True)
class HelpEntry:
    """One setting's decision: prose for the Row, or the reason the upstream line stands."""

    help: str | None
    skip: str | None


def parse_table(text: str) -> dict[str, HelpEntry]:
    """The table by setting name. An entry with neither key is a seeded, undecided one."""
    table: dict[str, HelpEntry] = {}
    for name, row in tomllib.loads(text).items():
        unknown = set(row) - _KEYS
        if unknown:
            raise ValueError(f"{name!r}: unknown key(s) {sorted(unknown)}")
        table[name] = HelpEntry(help=row.get("help"), skip=row.get("skip"))
    return table


def load_table(path: Path = TABLE) -> dict[str, HelpEntry]:
    return parse_table(path.read_text(encoding="utf-8"))


def helps(table: Mapping[str, HelpEntry]) -> dict[str, str]:
    """The `help` strings the Overlay carries: every entry that wrote prose."""
    return {name: entry.help for name, entry in table.items() if entry.help is not None}


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def help_problems(
    text: str, upstream: str, labels: Mapping[str, str] | None = None
) -> list[str]:
    """Why `text` may not be a setting's help, each in a phrase; empty when it may.

    `labels` maps each stored value of a labelled control to the label it shows.
    """
    problems: list[str] = []
    if len(text) > MAX_CHARS:
        problems.append(f"{len(text)} characters is over the {MAX_CHARS}-character limit")
    sentences = len(_SENTENCE_END.findall(text))
    if sentences > MAX_SENTENCES:
        problems.append(f"{sentences} sentences; at most {MAX_SENTENCES}")
    if not text.endswith("."):
        problems.append("does not end with a period")
    for banned in dict.fromkeys(
        word.lower() for word in BANNED.findall(_XKB_OPTIONS.sub("", text))
    ):
        problems.append(f"says {banned!r}, a word the app does not use")
    if _plain(text) == _plain(upstream):
        problems.append("is the upstream description; write what it says in full")
    for number in dict.fromkeys(_NUMBER.findall(text)):
        if labels and number in labels:
            problems.append(
                f"quotes the stored value {number}, which the control shows as {labels[number]!r}"
            )
    return problems


def entry_problems(
    entry: HelpEntry, upstream: str, labels: Mapping[str, str] | None = None
) -> list[str]:
    """Why `entry` is not a finished decision for a setting with this upstream line."""
    if entry.help is None and entry.skip is None:
        return ["has neither `help` nor `skip`"]
    if entry.help is not None and entry.skip is not None:
        return ["has both `help` and `skip`"]
    if entry.skip is not None:
        return [] if entry.skip.strip() else ["has an empty `skip`"]
    assert entry.help is not None
    return help_problems(entry.help, upstream, labels)
