"""Presets: named look-and-feel bundles of Option values, saved by Capture scope (ADR-0014).

A Preset holds Option values and nothing else -- never a bind, rule or monitor (those are
Entities, and ADR-0014 keeps workflow out of a theme) -- so applying one is a burst of
ordinary Option edits through one Apply transaction, and undoing it is an Option undo.

Everything here is UI-free and file-format-shaped. Capture and apply are the Session's,
because both need the live compositor and the model.

The file, `presets/<slug>.json` in the App dir:

- **Keys are Option names** in colon form (`general:border_size`): the form the model, the
  Manifest and search use, and the one `getoption` takes.
- **Values are JSON-native**: booleans and numbers as themselves, strings as strings, the
  complex types (colors, gradients, css gaps, font weights) as their display text. Every value
  is read back through `parse_value` against the running Schema, so a file from another
  machine or a newer app cannot put a value of the wrong type into the model.
- **Only set values.** A Preset never stores "Unset", so applying one never unsets anything.
- **`format`** stamps the layout. A newer format is read for the keys this build knows
  (ADR-0014 §Sharing: unknown keys warn and skip, never fail); a file without one is not a
  Preset.

Never a Module: nothing requires the files, the Manifest never claims them, and the Writer's
prune never touches what the Manifest does not claim.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from .bridge import Several, Wallpaper
from .model.values import display_text
from .profiles import slugify
from .schema import OptionType, ResolvedOption, Schema

_log = logging.getLogger(__name__)

FORMAT = 1
"""The file layout this build writes. Bump it only for a change an older build would misread."""

WALLPAPERS_DIR = "wallpapers"
"""Under `presets/`: the images imported Theme archives carried (#169)."""

WALLPAPER_EXTENSIONS = ("png", "jpg", "jpeg", "webp")
"""The image types a Theme archive may carry, by the extension its wallpaper is saved under."""


class CaptureScope(StrEnum):
    """One checkbox of the Capture scope checklist: what a Preset saves and applies.

    The value is the file's spelling; `label` is the checklist's.
    """

    COLORS = "colors"
    GAPS_LAYOUT = "gaps-layout"
    ANIMATION_SWITCHES = "animation-switches"
    FONTS_CURSOR = "fonts-cursor"
    WALLPAPER = "wallpaper"

    @property
    def label(self) -> str:
        return _TITLES[self]


_TITLES = {
    CaptureScope.COLORS: "Colors",
    CaptureScope.GAPS_LAYOUT: "Gaps & layout",
    # Options-only in v1, so this scope is the two `animations:*` switches; curves and
    # leaves are Entities. It becomes "Animations" again if they join a later scope.
    CaptureScope.ANIMATION_SWITCHES: "Animation switches",
    CaptureScope.FONTS_CURSOR: "Fonts",
    CaptureScope.WALLPAPER: "Wallpaper",
}


def _in_scope(scope: CaptureScope, option: ResolvedOption) -> bool:
    """The scope-to-Option mapping, as rules over the Schema rather than a key list.

    Rules, so a Hyprland release that adds a colour or a gap Option is captured without a
    table to update. Disjoint by construction: the colour rule is by type, and no gap,
    rounding, animation switch or font Option is a colour.
    """
    leaf = option.name.rpartition(":")[2]
    match scope:
        case CaptureScope.COLORS:
            return option.type in (OptionType.COLOR, OptionType.GRADIENT)
        case CaptureScope.GAPS_LAYOUT:
            return option.name == "general:border_size" or option.name.startswith(
                ("general:gaps_", "decoration:rounding")
            )
        case CaptureScope.ANIMATION_SWITCHES:
            return option.name.startswith("animations:")
        case CaptureScope.FONTS_CURSOR:
            # Cursor theme and size are `env` Entities: Options-only in v1 (owner, 2026-10-01).
            return "font_" in leaf  # font_family, font_size, font_weight_*, splash_font_family
        case CaptureScope.WALLPAPER:
            return False  # an image path, not an Option (#170)


def scoped_options(schema: Schema, scope: CaptureScope) -> tuple[ResolvedOption, ...]:
    """Every Option of `schema` that `scope` captures, in the Schema's order."""
    return tuple(option for option in schema.options if _in_scope(scope, option))


def stored_value(value: Any) -> Any:
    """A model value as the file holds it: JSON-native, readable by `parse_value`."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return display_text(value)


@dataclass(frozen=True, slots=True)
class Preset:
    """One saved Preset, as the file holds it.

    `options` maps Option names to stored values (`stored_value`), not yet parsed: parsing
    needs the running Schema, and a key that Schema lacks is skipped at apply, not here.
    """

    name: str
    created: datetime
    scopes: frozenset[CaptureScope]
    options: Mapping[str, Any]
    app_version: str | None
    hyprland_version: str | None
    wallpaper: str | None = None
    """The image the Wallpaper scope captured; set and applied by #170."""


class PresetStore:
    """`presets/` as an object: list, load, write, delete.

    Tolerant on the read side, as `ProfileStore` is: a hand-broken file is left out of
    `list()` and is `None` from `load()`, never an exception. Atomic on the write side.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._revision = 0

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def revision(self) -> int:
        """Bumped by every `write` and `delete`: Presets are files, so nothing else says the
        list moved. Pulled by whoever needs to know (#172's index, #171's group)."""
        return self._revision

    @staticmethod
    def slug_for(name: str) -> str:
        return slugify(name, fallback="preset")

    def exists(self, slug: str) -> bool:
        return (self._dir / f"{slug}.json").is_file()

    def list(self) -> tuple[tuple[str, Preset], ...]:
        """Every readable Preset as `(slug, preset)`, sorted by name then slug."""
        found: list[tuple[str, Preset]] = []
        if self._dir.is_dir():
            for path in self._dir.glob("*.json"):
                preset = self.load(path.stem)
                if preset is not None:
                    found.append((path.stem, preset))
        return tuple(sorted(found, key=lambda pair: (pair[1].name.lower(), pair[0])))

    def load(self, slug: str) -> Preset | None:
        try:
            data = json.loads((self._dir / f"{slug}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as error:
            _log.debug("presets/%s.json is not readable: %s", slug, error)
            return None
        parsed = parse_preset(data)
        if parsed is None:
            _log.debug("presets/%s.json is not a preset", slug)
            return None
        return parsed.preset

    def write(self, slug: str, preset: Preset) -> None:
        """Write `preset` as `<slug>.json`, replacing any file of that slug.

        Whether replacing is wanted is the caller's question (`Session.save_preset`).
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{slug}.json"
        scratch = path.with_name(f".{path.name}.tmp")
        scratch.write_text(
            json.dumps(preset_to_json(preset), indent=2) + "\n", encoding="utf-8"
        )
        scratch.replace(path)
        self._revision += 1

    def delete(self, slug: str) -> None:
        """Remove the Preset, then its kept wallpaper. Raises `OSError` when the Preset's
        file cannot be removed; an image left behind is only disk space, so not fatal."""
        (self._dir / f"{slug}.json").unlink(missing_ok=True)
        self._revision += 1
        for extension in WALLPAPER_EXTENSIONS:
            with contextlib.suppress(OSError):
                (self.wallpaper_dir / f"{slug}.{extension}").unlink(missing_ok=True)

    @property
    def wallpaper_dir(self) -> Path:
        """Where an imported Preset's wallpaper is kept: `presets/wallpapers/<slug>.<ext>`.

        Only images an import wrote live here, named by the slug the store chose, so
        deleting a Preset deletes its image and nothing else.
        """
        return self._dir / WALLPAPERS_DIR

    def free_name(self, wanted: str) -> tuple[str, str]:
        """The name and slug `add` gives a Preset called `wanted`: itself, or the first of
        "<wanted> 2", "<wanted> 3", ... no Preset or wallpaper has."""
        name, slug, counter = wanted, self.slug_for(wanted), 2
        while self.exists(slug) or self._wallpaper_of(slug) is not None:
            name = f"{wanted} {counter}"
            slug, counter = self.slug_for(name), counter + 1
        return name, slug

    def add(
        self, preset: Preset, wallpaper: tuple[str, bytes] | None = None
    ) -> tuple[str, Preset]:
        """Write `preset` under a slug no Preset or wallpaper has, and return what was written.

        Never an overwrite: a name that is taken becomes "<name> 2", "<name> 3", ..., so the
        list shows two rows a user can tell apart. `wallpaper` is `(extension, bytes)`; it is
        written first and the Preset records where, so a Preset never names a missing image.
        If the Preset cannot be written, the image is removed again. Raises `OSError`.
        """
        name, slug = self.free_name(preset.name)
        image: Path | None = None
        if wallpaper is not None:
            extension, data = wallpaper
            image = self.wallpaper_dir / f"{slug}.{extension}"
            image.parent.mkdir(parents=True, exist_ok=True)
            scratch = image.with_name(f".{image.name}.tmp")
            scratch.write_bytes(data)
            scratch.replace(image)
        added = replace(preset, name=name, wallpaper=None if image is None else str(image))
        try:
            self.write(slug, added)
        except OSError:
            if image is not None:
                image.unlink(missing_ok=True)
            raise
        return slug, added

    def _wallpaper_of(self, slug: str) -> Path | None:
        for extension in WALLPAPER_EXTENSIONS:
            path = self.wallpaper_dir / f"{slug}.{extension}"
            if path.exists():
                return path
        return None


# --- JSON shape --------------------------------------------------------------------------


def preset_to_json(preset: Preset) -> dict[str, Any]:
    """The file's JSON object for `preset`: `presets/<slug>.json`, and a Theme archive's
    `preset.json`."""
    return {
        "format": FORMAT,
        "name": preset.name,
        "created": preset.created.isoformat(),
        "scopes": [scope.value for scope in CaptureScope if scope in preset.scopes],
        "options": dict(preset.options),
        "app_version": preset.app_version,
        "hyprland_version": preset.hyprland_version,
        "wallpaper": preset.wallpaper,
    }


MAX_NAME: Final = 120
"""Characters a Preset's name may have: its slug stays far under a file name's 255 bytes."""

MAX_OPTIONS: Final = 2000
"""Settings a Preset may hold: more than any Hyprland has, few enough that a preview of
every one is still a page, not a freeze (finding 8 of the #153 review)."""


@dataclass(frozen=True, slots=True)
class ParsedPreset:
    """A Preset read from JSON, and what reading it set aside."""

    preset: Preset
    newer: bool
    """Written by a newer format: only the keys this one knows were read."""
    dropped: tuple[str, ...] = ()
    """Option names whose value was not one a Preset stores (a list, an object)."""


def parse_preset(data: Any) -> ParsedPreset | None:
    """`preset_to_json` read back, from a file in the App dir or a theme file alike.

    Total: anything that is not a Preset -- a wrong type, a missing field, a name or a list
    of settings past `MAX_NAME` or `MAX_OPTIONS` -- is `None`, never an exception. A value
    no Option holds is set aside in `dropped`, not fatal.
    """
    if not isinstance(data, dict):
        return None
    fmt = data.get("format")
    if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt < 1:
        return None
    name, options, created = data.get("name"), data.get("options"), data.get("created")
    if not isinstance(name, str) or not name or len(name) > MAX_NAME:
        return None
    if not isinstance(options, dict) or len(options) > MAX_OPTIONS:
        return None
    if not isinstance(created, str):
        return None
    try:
        when = datetime.fromisoformat(created)
    except ValueError:
        return None
    if fmt > FORMAT:
        _log.warning(
            "preset %r is format %d; reading the keys format %d knows", name, fmt, FORMAT
        )
    known = {scope.value for scope in CaptureScope}
    scopes = data.get("scopes", ())
    kept = {str(key): value for key, value in options.items() if _storable(value)}
    preset = Preset(
        name=name,
        created=when,
        scopes=frozenset(
            CaptureScope(raw)
            for raw in (scopes if isinstance(scopes, list) else ())
            if isinstance(raw, str) and raw in known
        ),
        options=kept,
        app_version=_text(data.get("app_version")),
        hyprland_version=_text(data.get("hyprland_version")),
        wallpaper=_text(data.get("wallpaper")),
    )
    dropped = tuple(str(key) for key in options if str(key) not in kept)
    return ParsedPreset(preset, fmt > FORMAT, dropped)


def _storable(value: Any) -> bool:
    """What `stored_value` can have written: a JSON scalar, never a list or an object."""
    return value is None or isinstance(value, bool | int | float | str)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# --- what the Session answers -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PresetSaved:
    """The Preset was written as `presets/<slug>.json`."""

    slug: str
    preset: Preset


@dataclass(frozen=True, slots=True)
class PresetNameTaken:
    """A Preset already has this name's slug; nothing was written. Saving again with
    `replace=True` overwrites it (the dialog's "Replace <name>?")."""

    slug: str
    name: str
    """The existing Preset's name, as its row shows it."""


@dataclass(frozen=True, slots=True)
class PresetNotSaved:
    """Nothing was written, for `reason`: a sentence the dialog can show as it is."""

    reason: str


PresetSaveResult = PresetSaved | PresetNameTaken | PresetNotSaved


@dataclass(frozen=True, slots=True)
class PresetApplied:
    """The Preset's Options went into the model as one gesture, and are being applied.

    `skipped` names what the file holds that this session will not set: an Option the
    loaded Schema lacks, one the running Hyprland lacks, a Retired one, or a value that does
    not parse as the Option's type (ADR-0014 §Sharing: warn and skip, never fail).
    """

    applied: tuple[str, ...]
    skipped: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PresetNotApplied:
    """Nothing was applied, for `reason`: the session is read-only, or the file is gone."""

    reason: str


class ColorChoice(StrEnum):
    """What a Preset's Colors do while a wallpaper sets the colours (ADR-0014 §Color source).

    The value is what "remember my choice" stores, so it never changes spelling.
    """

    USE_PRESET = "use-preset"
    """"Use preset's colors": the Color source becomes Preset, in the same transaction."""
    KEEP_WALLPAPER = "keep-wallpaper"
    """"Keep wallpaper colors": everything but the Colors is applied; the source stays."""


@dataclass(frozen=True, slots=True)
class PresetColorConflict:
    """Nothing was applied: the Preset carries Colors and `source` sets the colours now.

    Apply again with a `ColorChoice`. `source` is a Wallpaper source, or `Several` when a
    hand edit loads more than one.
    """

    source: Wallpaper | Several


PresetApplyResult = PresetApplied | PresetNotApplied | PresetColorConflict


@dataclass(frozen=True, slots=True)
class PresetChange:
    """One Option a Preset would change: what it is now, and what the Preset sets.

    Both are model values, typed by the Option: `before` is the set value or Hyprland's
    default, and `None` when there is none (a nullable Option at "Device default").
    """

    option: ResolvedOption
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class PresetSection:
    """The changes within one Section, in the Schema's order, under its title."""

    name: str
    title: str
    changes: tuple[PresetChange, ...]


@dataclass(frozen=True, slots=True)
class PresetPreview:
    """What applying a Preset would do, before anything is done (ADR-0014 §Sharing).

    `sections` holds only real changes. `unchanged` names the Options already at the
    Preset's value. `unknown` names what this session cannot set: an Option the loaded
    Schema or the running Hyprland lacks, or a Retired one. `invalid` names a value that
    does not parse as its Option's type. Applying skips both, as `PresetApplied.skipped`.
    """

    sections: tuple[PresetSection, ...]
    unchanged: tuple[str, ...]
    unknown: tuple[str, ...]
    invalid: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PresetImported:
    """A Theme archive's Preset is in the store as `slug`; nothing has been applied."""

    slug: str
    preset: Preset


@dataclass(frozen=True, slots=True)
class PresetNotImported:
    """Nothing was written, for `reason`."""

    reason: str


PresetImportResult = PresetImported | PresetNotImported
