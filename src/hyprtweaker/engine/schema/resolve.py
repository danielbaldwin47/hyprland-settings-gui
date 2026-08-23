"""Merging the Generated schema with the Overlay into the Schema the app actually uses.

Resolution is where the two halves stop being two halves. Above this module nothing knows
whether a title was curated or derived, whether a widget came from rule R3 or from a hand
override -- there is just a `ResolvedOption` with every field decided.

Version selection follows ADR-0012: exact match, else the nearest *lower* shipped schema.
Never a higher one -- degrading onto a newer schema would offer the user options their
compositor does not have, which is a config error on the next reload.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import generated as generated_module
from . import overlay as overlay_module
from .generated import GeneratedSchema
from .overlay import Overlay
from .types import (
    GeneratedOption,
    KnownValues,
    OverlayEntry,
    Range,
    ResolvedOption,
    SectionOverlay,
    Visibility,
)

SCHEMA_DIR_ENV = "HYPRTWEAKER_SCHEMA_DIR"
OVERLAY_FILENAME = "overlay.json"
_SCHEMA_FILENAME = re.compile(r"^hyprland-(\d+(?:\.\d+)*)\.json$")

_COLOUR_PARENT = "col"
"""The subsection whose leaves are colours without ever saying so."""

_ABBREVIATIONS = {
    "kb": "Keyboard",
    "vrr": "VRR",
    "hdr": "HDR",
    "cm": "Color management",
    "gl": "OpenGL",
    "vfr": "VFR",
    "anr": "ANR",
    "xkb": "XKB",
    "dpms": "DPMS",
}


def version_key(version: str) -> tuple[int, ...]:
    """Sortable form of a dotted version, so `0.56.10` beats `0.56.2`.

    Plain string comparison gets that pair backwards, which would silently pick an older
    schema than the one shipped for the running compositor.
    """
    return tuple(int(part) for part in re.findall(r"\d+", version))


def derive_title(option: GeneratedOption) -> str:
    """A last-resort human label for an Option with no curated title.

    Only reachable on the ADR-0012 supplement path -- an Option a *newer* Hyprland added
    that no shipped Overlay has seen, rendered in a *New in <version>* group. Every Option
    in a shipped schema carries a curated title instead, which the Overlay completeness
    test enforces, because prototype #8 measured that 126 of 126 curated options needed a
    human-written title to stop reading like a config key.
    """
    leaf = option.path[-1]
    parent = option.path[-2] if len(option.path) > 1 else ""

    words = [_ABBREVIATIONS.get(word, word) for word in leaf.split("_")]
    title = " ".join(words).strip()
    title = title[:1].upper() + title[1:]

    # `general:col.active_border` is a colour, and its leaf never says so.
    if parent == _COLOUR_PARENT:
        title = f"{title} color"

    return title


def _merge_range(generated: GeneratedOption, entry: OverlayEntry) -> Range | None:
    """Overlay bounds win; anything it leaves out falls back to the generated bounds."""
    curated = entry.range

    low = curated.min if curated is not None and curated.min is not None else generated.min
    high = curated.max if curated is not None and curated.max is not None else generated.max
    step = curated.step if curated is not None else None
    soft_max = curated.soft_max if curated is not None else None

    if low is None and high is None and step is None and soft_max is None:
        return None
    return Range(min=low, max=high, step=step, soft_max=soft_max)


def resolve_option(
    generated: GeneratedOption,
    entry: OverlayEntry | None,
    section: SectionOverlay | None,
) -> ResolvedOption:
    """Apply one Overlay entry (and its Section defaults) to one generated record.

    An absent entry is an empty one, so every field below reads as a plain override
    rather than repeating the same `is not None` guard twenty times. It is also what the
    ADR-0012 supplement path hands in: an Option a newer Hyprland added, which no shipped
    Overlay has an entry for, still has to resolve.
    """
    entry = entry or OverlayEntry()
    section = section or SectionOverlay()

    nullable = generated.sentinel_default if entry.nullable is None else entry.nullable

    # What the writer emits to mean "unset". Explicit curation wins; otherwise it is
    # whatever `descriptions` printed for the sentinel default.
    null_value: Any = entry.null_value
    if null_value is None and generated.sentinel_default:
        null_value = generated.default_raw

    # A Section sets the floor, a per-Option tier overrides it.
    visibility = entry.visibility or section.visibility or Visibility.DEFAULT

    # The Row subtitle is the description, curated `help` overriding it when the upstream
    # text is terse, wrong, or just repeats the enum list (ADR-0013).
    description = entry.help or generated.description

    known_values = entry.known_values
    if known_values is None and generated.choices:
        known_values = KnownValues(values=generated.choices)

    return ResolvedOption(
        name=generated.name,
        lua_key=generated.lua_key,
        section=generated.section,
        path=generated.path,
        order=generated.order,
        type=generated.type,
        widget=entry.widget or generated.widget,
        title=entry.title or derive_title(generated),
        description=description,
        default=generated.default,
        default_raw=generated.default_raw,
        nullable=nullable,
        null_label=entry.null_label,
        null_value=null_value,
        getoption_key=generated.getoption_key,
        visibility=visibility,
        range=_merge_range(generated, entry),
        map=generated.map,
        labels=entry.labels,
        known_values=known_values,
        vec2_range=generated.vec2_range,
        unit=entry.unit,
        depends_on=entry.depends_on,
        restart=entry.restart,
        help_url=entry.help_url or section.help_url,
        group=entry.group,
        group_order=entry.order,
        device_overridable=generated.device_overridable,
        refresh=generated.refresh,
        curation_flags=generated.curation_flags,
    )


@dataclass(frozen=True, slots=True)
class Schema:
    """The resolved Schema: every Option of one Hyprland version, fully decided."""

    hyprland_version: str
    options: tuple[ResolvedOption, ...]
    _by_name: dict[str, ResolvedOption] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_name", {option.name: option for option in self.options})

    @classmethod
    def merge(cls, schema: GeneratedSchema, overlay: Overlay) -> Schema:
        return cls(
            hyprland_version=schema.hyprland_version,
            options=tuple(
                resolve_option(
                    option,
                    overlay.entry(option.name),
                    overlay.section(option.section),
                )
                for option in schema.options
            ),
        )

    def __getitem__(self, name: str) -> ResolvedOption:
        return self._by_name[name]

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __iter__(self) -> Any:
        return iter(self.options)

    def __len__(self) -> int:
        return len(self.options)

    def get(self, name: str) -> ResolvedOption | None:
        return self._by_name.get(name)

    @property
    def section_names(self) -> tuple[str, ...]:
        """Sections in the order Hyprland declares them, not alphabetically."""
        seen: dict[str, None] = {}
        for option in sorted(self.options, key=lambda option: option.order):
            seen.setdefault(option.section, None)
        return tuple(seen)

    def section(self, name: str) -> tuple[ResolvedOption, ...]:
        return tuple(
            sorted(
                (option for option in self.options if option.section == name),
                key=lambda option: option.order,
            )
        )


def schema_dir() -> Path:
    """Where the committed schema files live.

    The env var comes first so tests and `meson devenv` can point at a checkout; then the
    checkout itself (this file is `src/hyprtweaker/engine/schema/resolve.py`, so the repo
    root is four parents up); then the installed data dirs.
    """
    override = os.environ.get(SCHEMA_DIR_ENV)
    if override:
        return Path(override)

    candidates = [Path(__file__).resolve().parents[4] / "data" / "schema"]
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    candidates += [
        Path(entry) / "hyprtweaker" / "schema" for entry in data_dirs.split(":") if entry
    ]

    for candidate in candidates:
        if (candidate / OVERLAY_FILENAME).is_file():
            return candidate

    raise FileNotFoundError(
        f"no schema directory found (looked in {[str(path) for path in candidates]}); "
        f"set {SCHEMA_DIR_ENV} to override"
    )


def available_versions(directory: Path | None = None) -> tuple[str, ...]:
    """Every Hyprland version a schema is shipped for, oldest first."""
    directory = directory or schema_dir()
    versions = [
        match.group(1)
        for path in directory.iterdir()
        if (match := _SCHEMA_FILENAME.match(path.name)) is not None
    ]
    return tuple(sorted(versions, key=version_key))


def select_version(wanted: str, available: tuple[str, ...]) -> str:
    """Exact match, else the nearest lower version (ADR-0012 degradation)."""
    if not available:
        raise FileNotFoundError("no generated schemas are shipped")
    if wanted in available:
        return wanted

    wanted_key = version_key(wanted)
    lower = [version for version in available if version_key(version) <= wanted_key]
    if not lower:
        # Older than every shipped schema. Hyprland < 0.56 has no Lua config at all, so
        # this is a misconfiguration rather than a degradation the app can absorb.
        raise ValueError(f"Hyprland {wanted} is older than every shipped schema {available}")
    return max(lower, key=version_key)


def load_schema(version: str | None = None, directory: Path | None = None) -> Schema:
    """Load the Schema for `version`, degrading to the nearest lower shipped one."""
    directory = directory or schema_dir()
    available = available_versions(directory)
    chosen = select_version(version, available) if version else max(available, key=version_key)

    schema = generated_module.load(directory / f"hyprland-{chosen}.json")
    curated = overlay_module.load(directory / OVERLAY_FILENAME)
    return Schema.merge(schema, curated)
