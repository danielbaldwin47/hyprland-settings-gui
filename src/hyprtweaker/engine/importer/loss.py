"""The Loss report: what conversion changed, in three classes.

The wizard's account of an import (ADR-0009). Every finding is a `LossItem` carrying a
closed `LossCode`, one of three `LossClass`es, where it came from, and -- when there is one
-- what the Importer put in its place:

- **Info** -- converted faithfully, said out loud because the spelling changed. `on` became
  `true`; `SUPER_SHIFT` became `SUPER + SHIFT`.
- **Needs review** -- converted, but a human should look. An approximation, a value the new
  engine may reject, a construct baked to one branch.
- **Breakage** -- *not* converted, and the Importer cannot fix it. The class exists because
  the honest answer is sometimes "this stopped working"; hiding that in a warning is worse
  than saying it.

Codes `L1`-`L28` are the index of lossy cases in `docs/research/hyprlang-to-lua.md` §2.11,
kept at their published numbers so a finding here and a row there are the same thing.
`L29`-`L31` are added by this module for mapper-level cases that index does not cover --
it catalogues *translation* losses, and those three are about a keyword never reaching a
translation at all.

Deliberately distinct from `DiagnosticCode` in `keywords.py`: a Diagnostic is what hyprlang
itself would have complained about while the tree was still text, and says nothing about
conversion. A config can be full of Diagnostics and lose nothing, or parse perfectly and
lose plenty.

Reports persist to `$XDG_STATE_HOME/hyprtweaker/reports/<timestamp>.{json,md}` -- JSON as
the record the app reloads, Markdown as the copy a user can read without the app.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..paths import ConfigPaths

__all__ = [
    "FORMAT_VERSION",
    "LOSS_CODES",
    "LossClass",
    "LossCode",
    "LossContext",
    "LossItem",
    "LossReport",
    "describe",
]

FORMAT_VERSION = 1

RESCUE_LINE = (
    "> **If Hyprland will not start:** from a TTY, run "
    "`rm ~/.config/hypr/hyprland.lua` to go back to the previous config."
)
"""Printed in **every** report, including a clean one (ADR-0009).

Every report, because the reader who needs it is the one who cannot open the app to look
it up -- and a report that only carries the escape hatch when the Importer predicted
trouble is missing exactly the case where the prediction was wrong.
"""


class LossClass(StrEnum):
    """How much the user has to care. Ordered worst-last for display grouping."""

    INFO = "info"
    NEEDS_REVIEW = "needs-review"
    BREAKAGE = "breakage"


CLASS_ORDER: tuple[LossClass, ...] = (
    LossClass.BREAKAGE,
    LossClass.NEEDS_REVIEW,
    LossClass.INFO,
)

CLASS_TITLES: dict[LossClass, str] = {
    LossClass.BREAKAGE: "Breakage",
    LossClass.NEEDS_REVIEW: "Needs review",
    LossClass.INFO: "Info",
}


class LossCode(StrEnum):
    """Closed set of conversion findings. `L1`-`L28` are research §2.11's own numbering."""

    MODS_SPELLING = "L1"
    BARE_KEYCODE = "L2"
    UNKNOWN_KEYSYM = "L3"
    MULTIKEY_BIND = "L4"
    MOUSE_BIND = "L5"
    UNBIND_BY_STRING = "L6"
    DESCRIPTION_COMMAS = "L7"
    RESIZE_PERCENT = "L8"
    FULLSCREEN_STATE = "L9"
    TOGGLE_DEFAULT = "L10"
    DEAD_DISPATCHER = "L11"
    GESTURE_DISPATCHER = "L12"
    OLD_WINDOWRULE_SYNTAX = "L13"
    RULE_VALUE_TYPE = "L14"
    RULE_PRECEDENCE = "L15"
    WORKSPACE_INVERTED = "L16"
    LAYERRULE_DROPPED = "L17"
    MONITOR_SHAPE = "L18"
    ANIMATION_RANGE = "L19"
    DEVICE_FIELD = "L20"
    PLUGIN_GUARD = "L21"
    EXEC_TIMING = "L22"
    SOURCE_REQUIRE = "L23"
    VALUE_NORMALISED = "L24"
    REMOVED_OPTION = "L25"
    WIKI_DRIFT = "L26"
    CONDITIONAL_BAKED = "L27"
    VARIABLE_UNRESOLVED = "L28"
    # Added here: the index above catalogues translation losses, these three are about a
    # keyword that never got as far as a translation.
    LEGACY_DISPATCH_CALL = "L29"
    UNSUPPORTED_KEYWORD = "L30"
    UNPARSED_LINE = "L31"


@dataclass(frozen=True, slots=True)
class LossSpec:
    """What a code means, and how bad it is when nothing overrides that."""

    summary: str
    default_class: LossClass


LOSS_CODES: dict[LossCode, LossSpec] = {
    LossCode.MODS_SPELLING: LossSpec(
        "Modifier spelling rewritten to strict Lua tokens", LossClass.INFO
    ),
    LossCode.BARE_KEYCODE: LossSpec("Bare numeric keycode rewritten as code:N", LossClass.INFO),
    LossCode.UNKNOWN_KEYSYM: LossSpec(
        "Key name is not a known keysym -- silently dead in hyprlang, a config error in Lua",
        LossClass.NEEDS_REVIEW,
    ),
    LossCode.MULTIKEY_BIND: LossSpec(
        "Multi-key bind approximated as a combined key string; the matcher differs",
        LossClass.NEEDS_REVIEW,
    ),
    LossCode.MOUSE_BIND: LossSpec(
        "Mouse bind expressed as a drag/resize dispatcher -- Lua has no mouse flag",
        LossClass.INFO,
    ),
    LossCode.UNBIND_BY_STRING: LossSpec(
        "unbind matches by key string in Lua, not by modifier mask", LossClass.NEEDS_REVIEW
    ),
    LossCode.DESCRIPTION_COMMAS: LossSpec("Bind description kept verbatim", LossClass.INFO),
    LossCode.RESIZE_PERCENT: LossSpec(
        "Percentage resize/move arguments have no Lua equivalent", LossClass.BREAKAGE
    ),
    LossCode.FULLSCREEN_STATE: LossSpec(
        "fullscreenstate -1 (keep current) is not representable in Lua", LossClass.BREAKAGE
    ),
    LossCode.TOGGLE_DEFAULT: LossSpec(
        "Toggle action made explicit -- an omitted argument means off in hyprlang, "
        "toggle in Lua",
        LossClass.INFO,
    ),
    LossCode.DEAD_DISPATCHER: LossSpec(
        "Dispatcher, or one of its arguments, is dropped or deprecated in this Hyprland",
        LossClass.NEEDS_REVIEW,
    ),
    LossCode.GESTURE_DISPATCHER: LossSpec(
        "Gesture dispatcher action becomes a Lua callback", LossClass.NEEDS_REVIEW
    ),
    LossCode.OLD_WINDOWRULE_SYNTAX: LossSpec(
        "Pre-0.54 window rule syntax; this Hyprland rejects it and the rename table is "
        "not published",
        LossClass.BREAKAGE,
    ),
    LossCode.RULE_VALUE_TYPE: LossSpec("Rule value retyped for Lua", LossClass.INFO),
    LossCode.RULE_PRECEDENCE: LossSpec(
        "Named rules emitted before anonymous ones to preserve evaluation order",
        LossClass.INFO,
    ),
    LossCode.WORKSPACE_INVERTED: LossSpec(
        "Workspace rule inverted (border -> no_border)", LossClass.INFO
    ),
    LossCode.LAYERRULE_DROPPED: LossSpec(
        "Layer rule effect no longer exists in this Hyprland", LossClass.INFO
    ),
    LossCode.MONITOR_SHAPE: LossSpec("Monitor setting reshaped for Lua", LossClass.INFO),
    LossCode.ANIMATION_RANGE: LossSpec(
        "Animation or curve value is outside the range Lua accepts", LossClass.BREAKAGE
    ),
    LossCode.DEVICE_FIELD: LossSpec("Per-device setting changed or dropped", LossClass.INFO),
    LossCode.PLUGIN_GUARD: LossSpec(
        "Plugin option guarded -- Lua errors on keys of a plugin that is not loaded",
        LossClass.NEEDS_REVIEW,
    ),
    LossCode.EXEC_TIMING: LossSpec(
        "exec timing shifts: hyprlang defers the first launch, Lua spawns while parsing",
        LossClass.INFO,
    ),
    LossCode.SOURCE_REQUIRE: LossSpec("source became a require", LossClass.INFO),
    LossCode.VALUE_NORMALISED: LossSpec(
        "Value normalised for Lua's stricter parser", LossClass.INFO
    ),
    LossCode.REMOVED_OPTION: LossSpec(
        "Option does not exist in this Hyprland and was dropped", LossClass.NEEDS_REVIEW
    ),
    LossCode.WIKI_DRIFT: LossSpec(
        "Setting is documented but absent from this Hyprland build", LossClass.NEEDS_REVIEW
    ),
    LossCode.CONDITIONAL_BAKED: LossSpec(
        "hyprlang conditional baked to the branch taken at import", LossClass.NEEDS_REVIEW
    ),
    LossCode.VARIABLE_UNRESOLVED: LossSpec("Variable left unresolved", LossClass.NEEDS_REVIEW),
    LossCode.LEGACY_DISPATCH_CALL: LossSpec(
        "Command calls the legacy config engine (hyprctl dispatch/keyword) and will fail "
        "under Lua",
        LossClass.BREAKAGE,
    ),
    LossCode.UNSUPPORTED_KEYWORD: LossSpec(
        "Keyword has no model representation and was kept aside", LossClass.NEEDS_REVIEW
    ),
    LossCode.UNPARSED_LINE: LossSpec(
        "Line the parser could not read, carried over verbatim", LossClass.NEEDS_REVIEW
    ),
}


def describe(code: LossCode) -> str:
    """The one-line meaning of a code."""
    return LOSS_CODES[code].summary


@dataclass(frozen=True, slots=True)
class LossItem:
    """One finding.

    `source` is the hyprlang the Importer read and `replacement` what it produced, so the
    wizard can show the pair without re-deriving either. Both are free text: a replacement
    is sometimes a Lua fragment, sometimes a sentence ("dropped").
    """

    code: LossCode
    message: str
    origin: str = ""
    source: str = ""
    replacement: str = ""
    loss_class: LossClass | None = None

    @property
    def severity(self) -> LossClass:
        """The item's class -- its own override, else the code's default."""
        return (
            self.loss_class
            if self.loss_class is not None
            else LOSS_CODES[self.code].default_class
        )

    def as_json(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "code": str(self.code),
            "class": str(self.severity),
            "message": self.message,
        }
        for name in ("origin", "source", "replacement"):
            value = getattr(self, name)
            if value:
                record[name] = value
        return record

    @classmethod
    def from_json(cls, record: dict[str, Any]) -> LossItem:
        return cls(
            code=LossCode(record["code"]),
            message=record.get("message", ""),
            origin=record.get("origin", ""),
            source=record.get("source", ""),
            replacement=record.get("replacement", ""),
            loss_class=LossClass(record["class"]) if "class" in record else None,
        )


@dataclass(slots=True)
class LossReport:
    """Every finding from one import, in the order they were found.

    Source order rather than grouped-by-class, because reading a report alongside the
    original config is how a user checks it; the grouping happens at render time.
    """

    items: list[LossItem] = field(default_factory=list)
    source: str = ""
    created: str = ""

    def add(
        self,
        code: LossCode,
        message: str,
        *,
        origin: str = "",
        source: str = "",
        replacement: str = "",
        loss_class: LossClass | None = None,
    ) -> LossItem:
        item = LossItem(
            code=code,
            message=message,
            origin=origin,
            source=source,
            replacement=replacement,
            loss_class=loss_class,
        )
        self.items.append(item)
        return item

    def extend(self, items: Iterable[LossItem]) -> None:
        self.items.extend(items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[LossItem]:
        return iter(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def of_class(self, loss_class: LossClass) -> list[LossItem]:
        return [item for item in self.items if item.severity is loss_class]

    def counts(self) -> dict[LossClass, int]:
        """How many of each class -- what the wizard's summary line counts."""
        tally = dict.fromkeys(CLASS_ORDER, 0)
        for item in self.items:
            tally[item.severity] += 1
        return tally

    def code_counts(self) -> dict[LossCode, int]:
        tally: dict[LossCode, int] = {}
        for item in self.items:
            tally[item.code] = tally.get(item.code, 0) + 1
        return tally

    @property
    def breakage(self) -> list[LossItem]:
        """The findings the Importer could not fix -- the ones that must be shown."""
        return self.of_class(LossClass.BREAKAGE)

    @property
    def clean(self) -> bool:
        """Nothing needs a human: no breakage, nothing to review. Info is fine."""
        return not self.of_class(LossClass.BREAKAGE) and not self.of_class(
            LossClass.NEEDS_REVIEW
        )

    # -- persistence --

    def as_json(self) -> dict[str, Any]:
        return {
            "format": FORMAT_VERSION,
            "created": self.created or _timestamp(),
            "source": self.source,
            "counts": {str(k): v for k, v in self.counts().items()},
            "items": [item.as_json() for item in self.items],
        }

    @classmethod
    def from_json(cls, record: dict[str, Any]) -> LossReport:
        version = record.get("format")
        if version != FORMAT_VERSION:
            raise ValueError(f"unsupported loss report format: {version!r}")
        return cls(
            items=[LossItem.from_json(item) for item in record.get("items", ())],
            source=record.get("source", ""),
            created=record.get("created", ""),
        )

    def render(self) -> str:
        """The Markdown copy: a summary line, the rescue line, then a section per class."""
        counts = self.counts()
        lines = ["# Import loss report", ""]
        if self.source:
            lines.append(f"Source: `{self.source}`")
        lines.append(f"Imported: {self.created or _timestamp()}")
        lines.append("")
        summary = ", ".join(f"{counts[c]} {CLASS_TITLES[c].lower()}" for c in CLASS_ORDER)
        lines.append(f"{len(self.items)} findings -- {summary}.")
        lines.extend(["", RESCUE_LINE])
        if not self.items:
            lines.append("")
            lines.append("Nothing was lost in conversion.")
            return "\n".join(lines) + "\n"
        for loss_class in CLASS_ORDER:
            group = self.of_class(loss_class)
            if not group:
                continue
            lines.extend(["", f"## {CLASS_TITLES[loss_class]} ({len(group)})", ""])
            for item in group:
                where = f" -- `{item.origin}`" if item.origin else ""
                lines.append(f"- **{item.code}** {item.message}{where}")
                if item.source:
                    lines.append(f"  - was: `{item.source}`")
                if item.replacement:
                    lines.append(f"  - now: `{item.replacement}`")
        return "\n".join(lines) + "\n"

    def save(self, paths: ConfigPaths, *, now: datetime | None = None) -> Path:
        """Write the pair to the reports dir; returns the JSON path.

        The stamp is the filename, so `latest()` needs no index and the two files of one
        report share a name.
        """
        if not self.created:
            self.created = _timestamp(now)
        stamp = _stamp(now)
        directory = paths.reports_dir
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / f"{stamp}.json"
        json_path.write_text(json.dumps(self.as_json(), indent=2) + "\n", encoding="utf-8")
        (directory / f"{stamp}.md").write_text(self.render(), encoding="utf-8")
        return json_path

    @classmethod
    def load(cls, path: Path) -> LossReport:
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def stored(cls, paths: ConfigPaths) -> list[Path]:
        """Every persisted report, oldest first. Empty when none were ever written."""
        directory = paths.reports_dir
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.json"))

    @classmethod
    def latest(cls, paths: ConfigPaths) -> LossReport | None:
        """The most recent report, or None -- what "view the last import" opens."""
        stored = cls.stored(paths)
        if not stored:
            return None
        return cls.load(stored[-1])


@dataclass(slots=True)
class LossContext:
    """One keyword's findings: the report, plus the origin and source text they share.

    Every mapping module was carrying its own copy of "call `report.add` with this origin
    and this source". Five copies of a wrapper is five chances for one of them to forget the
    source text -- and a finding without its original line is one a user cannot act on.
    """

    report: LossReport
    origin: str = ""
    source: str = ""

    def note(
        self,
        code: LossCode,
        message: str,
        *,
        replacement: str = "",
        loss_class: LossClass | None = None,
    ) -> LossItem:
        return self.report.add(
            code,
            message,
            origin=self.origin,
            source=self.source,
            replacement=replacement,
            loss_class=loss_class,
        )

    def at(self, *, origin: str | None = None, source: str | None = None) -> LossContext:
        """The same report, pointed at a different keyword."""
        return LossContext(
            report=self.report,
            origin=self.origin if origin is None else origin,
            source=self.source if source is None else source,
        )


def _now(now: datetime | None = None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _timestamp(now: datetime | None = None) -> str:
    return _now(now).replace(microsecond=0).isoformat()


def _stamp(now: datetime | None = None) -> str:
    return _now(now).strftime("%Y%m%d-%H%M%S")
