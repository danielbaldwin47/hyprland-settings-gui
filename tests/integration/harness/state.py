"""What the compositor thinks the config said, and how two of those differ.

The Writer's own tests prove the bytes are what we meant to write; `Hyprland --verify-config`
proves Hyprland will accept them. Neither proves the values *landed* -- that a gradient
written as `{ colors = {...}, angle = 45 }` produces the gradient the user asked for rather
than a silently-defaulted one. Reading state back out of a running compositor is the only
check that closes that gap, and it is the one that caught two real converter bugs in
prototype #9 §6 that reading the spec had not.

**Values come from `getoption`, never from `descriptions`.** `hyprctl -j descriptions`
reports each option's *default* rather than its current value (issue #3); a diff built on it
would be a diff of the schema against itself and would pass no matter what was written.

Two comparison shapes, because compositor state has two shapes:

- **keyed** (options): compare per name, report the names that differ;
- **ordered lists** (binds, monitors, animations, ...): compare canonicalised records,
  report which side has records the other lacks, and separately whether order matches.

Both are lossy on purpose. Records carry live fields no config can control -- a monitor's
`activeWorkspace`, a client's `address` -- so each list names the keys worth comparing.
Comparing everything would make every diff non-empty and the check useless.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .nested import STATE_SURFACES, NestedHyprland

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.schema import load_schema  # noqa: E402

SCHEMA_DIR = ROOT / "data" / "schema"

#: `hyprctl binds` cannot report a Lua bind's action -- the dispatcher is always `__lua` and
#: the argument a registry index -- so those two fields are left out rather than compared as
#: constants. `key`/`keycode` are kept even though `code:N` binds report both empty
#: (prototype #9 §4.2): the emptiness is itself stable and a change in it is worth seeing.
BIND_KEYS = (
    "locked",
    "mouse",
    "release",
    "repeat",
    "longPress",
    "non_consuming",
    "auto_consuming",
    "has_description",
    "modmask",
    "submap",
    "submap_universal",
    "key",
    "keycode",
    "catch_all",
    "description",
    "allow_input_capture",
)

#: `reserved` is included deliberately. It is how the on-screen config-error banner shows up
#: in state -- it grows with the number of errors (prototype #9 §3) -- so a config that
#: started erroring is visible here even when every option value still matches.
MONITOR_KEYS = (
    "name",
    "width",
    "height",
    "refreshRate",
    "x",
    "y",
    "scale",
    "transform",
    "vrr",
    "disabled",
    "currentFormat",
    "mirrorOf",
    "reserved",
)

ANIMATION_KEYS = ("name", "overridden", "bezier", "enabled", "speed", "style")

#: Surfaces compared as ordered lists, and the keys that matter in each.
LIST_SURFACES: dict[str, tuple[str, ...] | None] = {
    "monitors": MONITOR_KEYS,
    "workspacerules": None,
    "devices": None,
    "layers": None,
}


def option_names(version: str = "0.56.2") -> tuple[str, ...]:
    """Every option the shipped schema knows, which is what makes the sweep exhaustive.

    Deliberately the whole schema rather than the options a test touched: a writer bug that
    corrupts a neighbouring section is exactly the kind this tier exists to catch, and it is
    invisible to a diff scoped to what the test set.
    """
    return tuple(option.name for option in load_schema(version, SCHEMA_DIR))


def option_value(record: Any) -> Any:
    """The value out of a `getoption` reply, whatever key it arrived under.

    `getoption` returns the value under a type-dependent key (`int`, `float`, `str`, `css`,
    `gradient`, ...) beside the `option`/`set` envelope, and the key is *engine*-dependent
    too -- the same option answers under `custom` on hyprlang and `gradient` on Lua
    (prototype #9 §4.3). Taking whichever key is not the envelope is what lets one comparison
    span both engines. Strings are whitespace-collapsed because `css` shorthands round-trip
    with varying spacing.
    """
    if not isinstance(record, dict):
        return record
    for key, value in record.items():
        if key in ("option", "set"):
            continue
        if isinstance(value, str):
            return " ".join(value.split())
        return value
    return None


@dataclass(frozen=True, slots=True)
class OptionDelta:
    """One option whose value differs between two captures."""

    name: str
    before: Any
    after: Any

    def __str__(self) -> str:
        return f"{self.name}: {self.before!r} -> {self.after!r}"


@dataclass(frozen=True, slots=True)
class ListDelta:
    """One surface's list comparison."""

    surface: str
    before_total: int
    after_total: int
    only_before: tuple[Any, ...]
    only_after: tuple[Any, ...]
    order_identical: bool

    @property
    def empty(self) -> bool:
        return not self.only_before and not self.only_after

    def __str__(self) -> str:
        return (
            f"{self.surface}: {self.before_total} -> {self.after_total} "
            f"(-{len(self.only_before)}/+{len(self.only_after)})"
        )


@dataclass(frozen=True, slots=True)
class CompositorState:
    """One capture of everything a reload rebuilds."""

    options: dict[str, Any] = field(default_factory=dict)
    surfaces: dict[str, Any] = field(default_factory=dict)

    @property
    def config_errors(self) -> tuple[str, ...]:
        raw = self.surfaces.get("configerrors")
        if not isinstance(raw, list):
            return ()
        return tuple(line for line in raw if isinstance(line, str) and line.strip())

    def option(self, name: str) -> Any:
        """One option's live value, already unwrapped from its `getoption` envelope."""
        return option_value(self.options.get(name))

    def write(self, path: Path) -> None:
        """Persist a capture beside the test's other artefacts, for post-mortem reading."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"options": self.options, "surfaces": self.surfaces}
        path.write_text(json.dumps(payload, indent=1, sort_keys=True))


def capture(nested: NestedHyprland, *, options: Sequence[str] | None = None) -> CompositorState:
    """Read every state surface, plus every named option, out of a nested compositor."""
    names = tuple(options) if options is not None else option_names()
    surfaces = {surface: nested.hyprctl(surface) for surface in STATE_SURFACES}
    return CompositorState(options=nested.getoptions(names), surfaces=surfaces)


@dataclass(frozen=True, slots=True)
class StateDiff:
    """Everything that differs between two captures."""

    options: tuple[OptionDelta, ...]
    binds: ListDelta
    lists: tuple[ListDelta, ...]
    animations: ListDelta
    beziers: ListDelta
    config_errors_before: tuple[str, ...]
    config_errors_after: tuple[str, ...]

    @property
    def empty(self) -> bool:
        """True when nothing config-derived changed between the two captures."""
        return (
            not self.options
            and self.binds.empty
            and self.animations.empty
            and self.beziers.empty
            and all(delta.empty for delta in self.lists)
        )

    def summary(self) -> str:
        parts = [f"options±{len(self.options)}", str(self.binds), str(self.animations)]
        parts.extend(str(delta) for delta in self.lists if not delta.empty)
        return " ".join(parts)

    def describe(self, limit: int = 20) -> str:
        """A readable failure message: what differs, capped so an assert stays legible."""
        lines = [self.summary()]
        lines.extend(f"  {delta}" for delta in self.options[:limit])
        if len(self.options) > limit:
            lines.append(f"  ... and {len(self.options) - limit} more options")
        if self.config_errors_before != self.config_errors_after:
            lines.append(f"  config errors before: {self.config_errors_before}")
            lines.append(f"  config errors after:  {self.config_errors_after}")
        return "\n".join(lines)


def diff(before: CompositorState, after: CompositorState) -> StateDiff:
    """Compare two captures. Empty means the compositor ended up in the same place."""
    return StateDiff(
        options=_diff_options(before.options, after.options),
        binds=_diff_list(
            "binds", before.surfaces.get("binds"), after.surfaces.get("binds"), BIND_KEYS
        ),
        lists=tuple(
            _diff_list(surface, before.surfaces.get(surface), after.surfaces.get(surface), keys)
            for surface, keys in LIST_SURFACES.items()
        ),
        animations=_diff_list(
            "animations",
            _split_animations(before.surfaces.get("animations"))[0],
            _split_animations(after.surfaces.get("animations"))[0],
            ANIMATION_KEYS,
        ),
        beziers=_diff_list(
            "beziers",
            _split_animations(before.surfaces.get("animations"))[1],
            _split_animations(after.surfaces.get("animations"))[1],
            None,
        ),
        config_errors_before=before.config_errors,
        config_errors_after=after.config_errors,
    )


def _diff_options(before: dict[str, Any], after: dict[str, Any]) -> tuple[OptionDelta, ...]:
    deltas = []
    for name in sorted(set(before) | set(after)):
        old, new = option_value(before.get(name)), option_value(after.get(name))
        if isinstance(old, float) and isinstance(new, float) and abs(old - new) < 1e-6:
            continue
        if old != new:
            deltas.append(OptionDelta(name=name, before=old, after=new))
    return tuple(deltas)


def _canonical(record: Any, keys: tuple[str, ...] | None) -> Any:
    """A record reduced to the keys worth comparing, in a form that compares by value."""
    if keys is not None and isinstance(record, dict):
        record = {key: record.get(key) for key in keys}
    return json.loads(json.dumps(record, sort_keys=True))


def _diff_list(
    surface: str, before: Any, after: Any, keys: tuple[str, ...] | None
) -> ListDelta:
    old = [_canonical(record, keys) for record in (before or [])]
    new = [_canonical(record, keys) for record in (after or [])]
    return ListDelta(
        surface=surface,
        before_total=len(old),
        after_total=len(new),
        only_before=tuple(record for record in old if record not in new),
        only_after=tuple(record for record in new if record not in old),
        order_identical=old == new,
    )


def _split_animations(payload: Any) -> tuple[list[Any], list[Any]]:
    """`hyprctl -j animations` answers with `[[animation nodes], [beziers]]`."""
    two_lists = isinstance(payload, list) and len(payload) == 2
    if two_lists and all(isinstance(part, list) for part in payload):
        return payload[0], payload[1]
    return (payload or []), []
