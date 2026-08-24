"""Monitor profiles: named captures of the display setup (ADR-0015).

A profile captures **both** halves of "docked": the full monitor rule set -- connected,
disconnected, and the catch-all, keyed by the same `output` strings so `desc:`-when-unique
identity is untouched -- and the `monitor` field of each workspace rule, as an overlay
patch rather than a copy of the file. Anything less makes "docked" half a profile.

Everything here is UI-free and file-format-shaped:

- **Activation is a pure function.** `activated()` computes the post-activation state from
  the current state plus the profile; the session renders that state through one normal
  Apply transaction (ADR-0010), so config on disk always shows exactly the active state.
- **Drift is "activating would change something".** The one definition that keeps the
  badge honest: it clears the moment the profile is re-activated or updated, and a
  workspace rule the user deleted does not drift a profile that could never bring it back
  (activation patches pins onto existing rules, never creates rules).
- **Matching is the connected-output set.** Capture records what was plugged in as
  `(connector, description)` pairs; the app-open toast compares the live set against
  those. A capture taken with nobody answering records nothing and never matches.

Profiles live at `monitor-profiles/<slug>.json` in the App dir. Never a Module: nothing
requires the files, the Manifest never claims them, and the writer's prune never touches
what the Manifest does not claim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model.entities import MonitorRule, WorkspaceRule

PIN_FIELD = "monitor"
"""The one workspace-rule field a profile overlays (ADR-0015)."""

ACTIVE_NAME = "active.json"
"""The pointer to the active profile, beside the profiles it points at.

In the App dir rather than the prefs file because "which profile is this config" is a
statement about the config on disk, not an app preference: it travels with the dotfiles
it describes.
"""

_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ConnectedOutput:
    """One plugged-in display at capture time -- the matching fingerprint's atom."""

    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class MonitorProfile:
    """A named, manually activated capture of the display setup (ADR-0015).

    `monitors` is the whole rule set, origins stripped -- a profile is data, not a file
    position. `pins` maps every workspace selector seen at capture to its `monitor`
    value, `None` spelling "had no pin", so activation knows to clear as well as set.
    """

    name: str
    monitors: tuple[MonitorRule, ...] = ()
    pins: Mapping[str, Any] = field(default_factory=dict)
    connected: tuple[ConnectedOutput, ...] = ()


@dataclass(frozen=True, slots=True)
class MonitorStateSnapshot:
    """Everything profile activation touches, frozen -- what its revert restores.

    Wider than the monitor list alone: activation patches workspace pins and moves the
    active pointer, so a revert that only put the monitor rules back would leave the
    refused profile's pins and pointer standing.
    """

    monitors: tuple[MonitorRule, ...]
    workspace_rules: tuple[WorkspaceRule, ...]
    active: str | None


# --- capture and activation --------------------------------------------------------------


def connected_outputs(monitors: Sequence[Mapping[str, Any]]) -> tuple[ConnectedOutput, ...]:
    """The matching fingerprint from one `hyprctl -j monitors` answer."""
    return tuple(
        ConnectedOutput(
            name=str(monitor.get("name", "")),
            description=str(monitor.get("description", "")).strip(),
        )
        for monitor in monitors
    )


def capture(
    name: str,
    *,
    monitors: Sequence[MonitorRule],
    workspace_rules: Sequence[WorkspaceRule],
    connected: Sequence[ConnectedOutput] = (),
) -> MonitorProfile:
    """The current display setup as a profile (ADR-0015's capture scope).

    Every workspace rule contributes a pin -- `None` when it has no `monitor` field --
    because "this workspace floats free" is as much a part of "docked" as "this one is
    on the TV".
    """
    return MonitorProfile(
        name=name,
        monitors=_stripped(monitors),
        pins={rule.workspace: rule.fields.get(PIN_FIELD) for rule in workspace_rules},
        connected=tuple(connected),
    )


def activated(
    profile: MonitorProfile,
    *,
    workspace_rules: Sequence[WorkspaceRule],
) -> tuple[tuple[MonitorRule, ...], tuple[WorkspaceRule, ...]]:
    """The state activation produces: the profile's rules, the patched workspace rules.

    The monitor list is replaced wholesale -- the profile *is* the rule set. Workspace
    rules are an overlay: a selector the profile knows gets its pin set or cleared; a
    selector it never saw keeps whatever it has; a selector that no longer exists is
    left unmade, because a pin is not a rule (ADR-0015: "an overlay patch on
    `workspace_rules.lua`, not a copy of the file").
    """
    monitors = _stripped(profile.monitors)
    patched: list[WorkspaceRule] = []
    for rule in workspace_rules:
        if rule.workspace in profile.pins:
            fields = dict(rule.fields)
            pin = profile.pins[rule.workspace]
            if pin is None:
                fields.pop(PIN_FIELD, None)
            else:
                # Assignment, not strip-and-append: updating an existing key in place
                # keeps its position, which is what keeps re-activation byte-identical.
                fields[PIN_FIELD] = pin
            rule = WorkspaceRule(workspace=rule.workspace, fields=fields, origin=rule.origin)
        patched.append(rule)
    return monitors, tuple(patched)


def matches(profile: MonitorProfile, connected: Sequence[ConnectedOutput]) -> bool:
    """Whether the live connected-output set is the one the profile was captured on."""
    if not profile.connected or not connected:
        return False
    key = lambda output: (output.name, output.description)  # noqa: E731
    return sorted(profile.connected, key=key) == sorted(connected, key=key)


def drift(
    profile: MonitorProfile,
    *,
    monitors: Sequence[MonitorRule],
    workspace_rules: Sequence[WorkspaceRule],
) -> bool:
    """Whether activating `profile` now would change anything.

    The self-consistent definition: no drift right after activation, drift the moment a
    hand edit or an app edit diverges, and the badge always clears on "Update profile"
    (recapture) or re-activation. Origins are ignored -- a profile is data, and the same
    rule read back from `monitors.lua:3` is not a different rule.
    """
    wanted_monitors, wanted_workspaces = activated(profile, workspace_rules=workspace_rules)
    return _shape(wanted_monitors, wanted_workspaces) != _shape(monitors, workspace_rules)


def _stripped(rules: Sequence[MonitorRule]) -> tuple[MonitorRule, ...]:
    """Fresh copies with origins dropped: a profile is data, not a file position."""
    return tuple(MonitorRule(output=rule.output, fields=dict(rule.fields)) for rule in rules)


def _shape(
    monitors: Sequence[MonitorRule], workspace_rules: Sequence[WorkspaceRule]
) -> tuple[Any, ...]:
    return (
        tuple((rule.output, dict(rule.fields)) for rule in monitors),
        tuple((rule.workspace, dict(rule.fields)) for rule in workspace_rules),
    )


# --- the store ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """The filename half of a profile name: lowered, dashed, never empty."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "profile"


class ProfileStore:
    """`monitor-profiles/` as an object: list, load, save, and the active pointer.

    Tolerant on the read side -- a hand-broken JSON file is skipped by `list()` and
    `None` from `load()`, never an exception, because the Monitors page must build
    whatever is in the directory. Strict on the write side: saving a new profile never
    silently replaces another one whose name happened to slug the same.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    @property
    def directory(self) -> Path:
        return self._dir

    def list(self) -> tuple[tuple[str, MonitorProfile], ...]:
        """Every readable profile as `(slug, profile)`, sorted by name then slug."""
        found: list[tuple[str, MonitorProfile]] = []
        if self._dir.is_dir():
            for path in self._dir.glob("*.json"):
                if path.name == ACTIVE_NAME:
                    continue
                profile = self.load(path.stem)
                if profile is not None:
                    found.append((path.stem, profile))
        return tuple(sorted(found, key=lambda pair: (pair[1].name.lower(), pair[0])))

    def load(self, slug: str) -> MonitorProfile | None:
        try:
            data = json.loads((self._dir / f"{slug}.json").read_text(encoding="utf-8"))
            return _from_json(data)
        except (OSError, ValueError, TypeError, LookupError, AttributeError):
            # LookupError covers the truncated-write shapes `_from_json` indexes into:
            # a dict missing "name" must read as "not a profile", never as a crash.
            return None

    def save(self, profile: MonitorProfile) -> str:
        """Write a new profile, returning its slug -- uniquified, never an overwrite."""
        base = slugify(profile.name)
        slug, counter = base, 2
        while (self._dir / f"{slug}.json").exists():
            slug = f"{base}-{counter}"
            counter += 1
        self.replace(slug, profile)
        return slug

    def replace(self, slug: str, profile: MonitorProfile) -> None:
        """Write `profile` under an existing slug -- the "Update profile" verb."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{slug}.json"
        scratch = path.with_name(f".{path.name}.tmp")
        scratch.write_text(
            json.dumps(_to_json(profile), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        scratch.replace(path)

    def delete(self, slug: str) -> None:
        """Remove a profile; an active pointer at it goes with it."""
        (self._dir / f"{slug}.json").unlink(missing_ok=True)
        if self.active_slug() == slug:
            self.set_active(None)

    # -- the active pointer --

    def active_slug(self) -> str | None:
        """The slug the pointer names, or `None` -- including when it names a ghost."""
        try:
            data = json.loads((self._dir / ACTIVE_NAME).read_text(encoding="utf-8"))
            slug = data.get("slug")
        except (OSError, ValueError, AttributeError):
            return None
        if not isinstance(slug, str) or not (self._dir / f"{slug}.json").is_file():
            return None
        return slug

    def set_active(self, slug: str | None) -> None:
        path = self._dir / ACTIVE_NAME
        if slug is None:
            path.unlink(missing_ok=True)
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"slug": slug}) + "\n", encoding="utf-8")


# --- JSON shape --------------------------------------------------------------------------


def _to_json(profile: MonitorProfile) -> dict[str, Any]:
    return {
        "version": _FORMAT_VERSION,
        "name": profile.name,
        "monitors": [
            {"output": rule.output, "fields": dict(rule.fields)} for rule in profile.monitors
        ],
        "pins": dict(profile.pins),
        "connected": [
            {"name": output.name, "description": output.description}
            for output in profile.connected
        ],
    }


def _from_json(data: Mapping[str, Any]) -> MonitorProfile:
    return MonitorProfile(
        name=str(data["name"]),
        monitors=tuple(
            MonitorRule(output=str(entry["output"]), fields=dict(entry.get("fields", {})))
            for entry in data.get("monitors", ())
        ),
        pins=dict(data.get("pins", {})),
        connected=tuple(
            ConnectedOutput(
                name=str(entry.get("name", "")),
                description=str(entry.get("description", "")),
            )
            for entry in data.get("connected", ())
        ),
    )


__all__ = [
    "ACTIVE_NAME",
    "PIN_FIELD",
    "ConnectedOutput",
    "MonitorProfile",
    "MonitorStateSnapshot",
    "ProfileStore",
    "activated",
    "capture",
    "connected_outputs",
    "drift",
    "matches",
    "slugify",
]
