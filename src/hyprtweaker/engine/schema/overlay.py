"""Reading `data/schema/overlay.json`, the hand-curated half of the Schema.

The Overlay is version-independent and keyed by colon-form option name. It is a
first-class reviewed asset, not a patch file: prototype #8 showed that generation gets you
the page and curation gets you the settings app, and that the options generation gets
wrong are the ones a user reaches for first -- keyboard layout, acceleration profile,
touchpad drag lock.

Unknown keys are rejected rather than ignored. A typo in a hand-edited 353-entry file is
otherwise invisible: `nulllabel` would simply never apply, and the row would leak
`[[EMPTY]]` into its placeholder with every test still green.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .types import (
    Dependency,
    KnownValues,
    OverlayEntry,
    OverlayGroup,
    Range,
    Restart,
    SectionOverlay,
    Visibility,
    Widget,
)

OVERLAY_FORMAT_VERSION = 1

_ENTRY_KEYS = frozenset(field.name for field in fields(OverlayEntry))
_SECTION_KEYS = frozenset(field.name for field in fields(SectionOverlay))
_GROUP_KEYS = frozenset(field.name for field in fields(OverlayGroup))
_TOP_LEVEL_KEYS = frozenset({"format_version", "sections", "options"})


@dataclass(frozen=True, slots=True)
class Overlay:
    """The curated layer: per-Section defaults plus per-Option overrides."""

    sections: dict[str, SectionOverlay]
    options: dict[str, OverlayEntry]

    def entry(self, name: str) -> OverlayEntry | None:
        return self.options.get(name)

    def section(self, name: str) -> SectionOverlay | None:
        return self.sections.get(name)


def _reject_unknown(where: str, payload: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown overlay field(s) {sorted(unknown)}")


def _parse_range(payload: Any) -> Range | None:
    if payload is None:
        return None
    _reject_unknown("range", payload, frozenset({"min", "max", "step", "soft_max"}))
    return Range(
        min=payload.get("min"),
        max=payload.get("max"),
        step=payload.get("step"),
        soft_max=payload.get("soft_max"),
    )


def _parse_known_values(payload: Any) -> KnownValues | None:
    if payload is None:
        return None
    if isinstance(payload, list):
        return KnownValues(values=tuple(str(value) for value in payload))
    _reject_unknown("known_values", payload, frozenset({"values", "open"}))
    return KnownValues(
        values=tuple(str(value) for value in payload["values"]),
        open=bool(payload.get("open", False)),
    )


def _parse_depends_on(payload: Any) -> Dependency | None:
    if payload is None:
        return None
    _reject_unknown("depends_on", payload, frozenset({"option", "value"}))
    return Dependency(option=str(payload["option"]), value=payload["value"])


def _parse_entry(name: str, payload: dict[str, Any]) -> OverlayEntry:
    _reject_unknown(f"option {name!r}", payload, _ENTRY_KEYS)

    nullable = payload.get("nullable")
    null_label = payload.get("null_label")
    # A nullable Option without a label renders its sentinel: ADR-0013 makes `null_label`
    # load-bearing for every nullable string, since it becomes the entry's placeholder.
    if nullable and not null_label:
        raise ValueError(f"option {name!r}: nullable is set but null_label is missing")

    return OverlayEntry(
        title=payload.get("title"),
        help=payload.get("help"),
        help_url=payload.get("help_url"),
        widget=Widget(payload["widget"]) if "widget" in payload else None,
        labels=(
            {str(key): str(value) for key, value in payload["labels"].items()}
            if "labels" in payload
            else None
        ),
        known_values=_parse_known_values(payload.get("known_values")),
        nullable=nullable,
        null_label=null_label,
        null_value=payload.get("null_value"),
        range=_parse_range(payload.get("range")),
        unit=payload.get("unit"),
        depends_on=_parse_depends_on(payload.get("depends_on")),
        restart=Restart(payload["restart"]) if "restart" in payload else None,
        visibility=Visibility(payload["visibility"]) if "visibility" in payload else None,
        group=payload.get("group"),
        order=payload.get("order"),
        deprecated_in=payload.get("deprecated_in"),
        renamed_from=payload.get("renamed_from"),
    )


def _parse_groups(section: str, payload: Any) -> tuple[OverlayGroup, ...]:
    groups: list[OverlayGroup] = []
    for entry in payload or ():
        _reject_unknown(f"section {section!r} group", entry, _GROUP_KEYS)
        group = OverlayGroup(title=str(entry["title"]), description=entry.get("description"))
        if any(seen.title == group.title for seen in groups):
            raise ValueError(f"section {section!r}: duplicate group {group.title!r}")
        groups.append(group)
    return tuple(groups)


def _parse_section(name: str, payload: dict[str, Any]) -> SectionOverlay:
    _reject_unknown(f"section {name!r}", payload, _SECTION_KEYS)
    return SectionOverlay(
        title=payload.get("title"),
        help_url=payload.get("help_url"),
        visibility=Visibility(payload["visibility"]) if "visibility" in payload else None,
        groups=_parse_groups(name, payload.get("groups")),
    )


def _check_groups_declared(
    sections: dict[str, SectionOverlay], options: dict[str, OverlayEntry]
) -> None:
    """An Option's `group` must name a Group its own Section declares.

    A misspelt name would otherwise render as a stray heading of its own, after the curated
    ones, with every test that reads titles still green."""
    for name, entry in options.items():
        if entry.group is None:
            continue
        section = sections.get(name.split(":", 1)[0])
        declared = {group.title for group in section.groups} if section else set()
        if entry.group not in declared:
            raise ValueError(
                f"option {name!r}: group {entry.group!r} is not one its Section declares"
            )


def loads(text: str) -> Overlay:
    payload = json.loads(text)
    _reject_unknown("overlay", payload, _TOP_LEVEL_KEYS)

    found = payload.get("format_version")
    if found != OVERLAY_FORMAT_VERSION:
        raise ValueError(
            f"overlay format version {found!r}, but this build reads {OVERLAY_FORMAT_VERSION}"
        )

    sections = {
        name: _parse_section(name, entry) for name, entry in payload.get("sections", {}).items()
    }
    options = {
        name: _parse_entry(name, entry) for name, entry in payload.get("options", {}).items()
    }
    _check_groups_declared(sections, options)
    return Overlay(sections=sections, options=options)


def load(path: Path) -> Overlay:
    return loads(path.read_text(encoding="utf-8"))
