"""Reading and writing `data/schema/hyprland-<ver>.json`, the Generated schema.

The file is committed, never produced at install time (ADR-0011): generating it needs a
running Hyprland of that exact version, and the Overlay has to be curated against a known
option list anyway. Serialisation is deterministic -- same inputs, byte-identical file --
so a release check's diff shows Hyprland's changes and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import (
    CurationFlag,
    GeneratedOption,
    GetOptionKey,
    OptionType,
    Vec2Range,
    Widget,
)

SCHEMA_FORMAT_VERSION = 1
"""Bumped when the record shape changes, so a stale file fails loudly instead of oddly."""


@dataclass(frozen=True, slots=True)
class GeneratedSchema:
    """Every Option Hyprland `hyprland_version` exposes, machine-derived."""

    hyprland_version: str
    options: tuple[GeneratedOption, ...]
    provenance: dict[str, Any]
    """What the generator consumed, including any degradation, for the release-check PR."""

    def __post_init__(self) -> None:
        names = [option.name for option in self.options]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"duplicate options in generated schema: {sorted(duplicates)}")

    def __len__(self) -> int:
        return len(self.options)


def _option_to_json(option: GeneratedOption) -> dict[str, Any]:
    """One record, keys in a fixed order so diffs stay readable."""
    payload: dict[str, Any] = {
        "name": option.name,
        "lua_key": option.lua_key,
        "section": option.section,
        "path": list(option.path),
        "order": option.order,
        "type": option.type.value,
        "widget": option.widget.value,
        "description": option.description,
        "default": option.default,
        "default_raw": option.default_raw,
        "sentinel_default": option.sentinel_default,
        "getoption_key": option.getoption_key.value,
    }

    # Optional keys are omitted rather than emitted as null: a 353-record file where every
    # record carries nine `null`s buries the fields that do say something.
    if option.min is not None:
        payload["min"] = option.min
    if option.max is not None:
        payload["max"] = option.max
    if option.map is not None:
        payload["map"] = option.map
    if option.choices is not None:
        payload["choices"] = list(option.choices)
    if option.vec2_range is not None:
        payload["vec2_range"] = {
            "min_x": option.vec2_range.min_x,
            "min_y": option.vec2_range.min_y,
            "max_x": option.vec2_range.max_x,
            "max_y": option.vec2_range.max_y,
        }
    if option.device_overridable:
        payload["device_overridable"] = True
    if option.refresh:
        payload["refresh"] = list(option.refresh)
    if option.curation_flags:
        payload["curation_flags"] = [flag.value for flag in option.curation_flags]

    return payload


def _option_from_json(payload: dict[str, Any]) -> GeneratedOption:
    vec2_raw = payload.get("vec2_range")
    vec2_range = (
        Vec2Range(
            min_x=float(vec2_raw["min_x"]),
            min_y=float(vec2_raw["min_y"]),
            max_x=float(vec2_raw["max_x"]),
            max_y=float(vec2_raw["max_y"]),
        )
        if isinstance(vec2_raw, dict)
        else None
    )

    choices = payload.get("choices")
    enum_map = payload.get("map")

    return GeneratedOption(
        name=str(payload["name"]),
        lua_key=str(payload["lua_key"]),
        section=str(payload["section"]),
        path=tuple(str(part) for part in payload["path"]),
        order=int(payload["order"]),
        type=OptionType(payload["type"]),
        widget=Widget(payload["widget"]),
        description=str(payload["description"]),
        default=payload["default"],
        default_raw=payload["default_raw"],
        sentinel_default=bool(payload["sentinel_default"]),
        getoption_key=GetOptionKey(payload["getoption_key"]),
        min=payload.get("min"),
        max=payload.get("max"),
        map={str(key): int(value) for key, value in enum_map.items()} if enum_map else None,
        choices=tuple(str(choice) for choice in choices) if choices else None,
        vec2_range=vec2_range,
        device_overridable=bool(payload.get("device_overridable", False)),
        refresh=tuple(str(bit) for bit in payload.get("refresh", ())),
        curation_flags=tuple(CurationFlag(flag) for flag in payload.get("curation_flags", ())),
    )


def dumps(schema: GeneratedSchema) -> str:
    """Serialise deterministically, options in declaration order."""
    payload = {
        "format_version": SCHEMA_FORMAT_VERSION,
        "hyprland_version": schema.hyprland_version,
        "provenance": schema.provenance,
        "options": [
            _option_to_json(option)
            for option in sorted(schema.options, key=lambda option: option.order)
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def loads(text: str) -> GeneratedSchema:
    payload = json.loads(text)

    found = payload.get("format_version")
    if found != SCHEMA_FORMAT_VERSION:
        raise ValueError(
            f"generated schema format version {found!r}, "
            f"but this build reads {SCHEMA_FORMAT_VERSION}"
        )

    return GeneratedSchema(
        hyprland_version=str(payload["hyprland_version"]),
        options=tuple(_option_from_json(record) for record in payload["options"]),
        provenance=dict(payload.get("provenance", {})),
    )


def load(path: Path) -> GeneratedSchema:
    return loads(path.read_text(encoding="utf-8"))
