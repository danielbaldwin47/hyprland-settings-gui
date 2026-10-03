"""Bridge entries: which tool modules the Entrypoint loads, and why one does not (ADR-0006).

An entry is one Manifest record per Bridge module. Its state is one of three, and each
renders as exactly one Entrypoint line, so the Entrypoint alone says what every bridge is
doing:

- `Active` -- the line, loading the module;
- `Waiting` -- the line commented, `-- waiting for <tool>'s first run`: the tool is set up
  but has not written its file yet, and requiring a missing file errors on every reload;
- `Off(source)` -- the line commented, `-- off: Color source is <source>`: ADR-0014's gate.

Quarantine is not a state here. It stays in `Manifest.quarantined`, wins over all three, and
keeps its own wording, because "this file stopped the config from loading" is a different
fact from "you chose another Color source".

The **Color source** is derived from those lines, never stored (ADR-0014): which Wallpaper
backend's require loads, else whether the gate was closed for a Preset or for Manual colours.

Everything here is pure: entries and facts in, entries or text out.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .registry import REGISTRY, Mechanism, ToolSpec, spec_for_module

# --- Color source (ADR-0014) ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Wallpaper:
    """Colours come from a wallpaper through `tool`: matugen or wallust."""

    tool: str


@dataclass(frozen=True, slots=True)
class PresetColors:
    """Colours a Preset applied, as ordinary GUI Options with nothing overriding them."""


@dataclass(frozen=True, slots=True)
class ManualColors:
    """Colours set by hand, as ordinary GUI Options with nothing overriding them."""


@dataclass(frozen=True, slots=True)
class Several:
    """More than one Wallpaper backend loads; the last required wins.

    Reachable only through a hand edit or a hand-placed Bridge module: the app never writes
    it. The Theming page says so ("matugen and wallust both set your colors; wallust wins").
    """

    tools: tuple[str, ...]


ChosenSource = Wallpaper | PresetColors | ManualColors
"""A Color source a user can choose: what `set_color_source` takes."""

ColorSource = ChosenSource | Several


def source_name(source: ChosenSource, registry: Mapping[str, ToolSpec] = REGISTRY) -> str:
    """How a source reads in the Entrypoint and on screen: `Preset`, `Manual`, `matugen`."""
    match source:
        case Wallpaper(tool):
            spec = registry.get(tool)
            return spec.title if spec is not None else tool
        case PresetColors():
            return "Preset"
        case ManualColors():
            return "Manual"


# --- entry states -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Active:
    """The Entrypoint loads the module."""


@dataclass(frozen=True, slots=True)
class Waiting:
    """Set up, but the tool has not written its file yet. The line is commented."""


@dataclass(frozen=True, slots=True)
class Off:
    """Gated off by the Color source `source` (ADR-0014). The line is commented."""

    source: ChosenSource


BridgeState = Active | Waiting | Off

ACTIVE = Active()
WAITING = Waiting()


def _state_json(state: BridgeState) -> dict[str, str]:
    match state:
        case Active():
            return {"state": "active"}
        case Waiting():
            return {"state": "waiting"}
        case Off(source):
            return {"state": "off", "off_for": _source_token(source)}


def _source_token(source: ChosenSource) -> str:
    match source:
        case Wallpaper(tool):
            return f"wallpaper:{tool}"
        case PresetColors():
            return "preset"
        case ManualColors():
            return "manual"


def _state_from_json(payload: Mapping[str, Any]) -> BridgeState | None:
    state, off_for = payload.get("state"), payload.get("off_for")
    if state == "active":
        return ACTIVE
    if state == "waiting":
        return WAITING
    if state != "off" or not isinstance(off_for, str):
        return None
    if off_for == "preset":
        return Off(PresetColors())
    if off_for == "manual":
        return Off(ManualColors())
    if off_for.startswith("wallpaper:"):
        return Off(Wallpaper(off_for.removeprefix("wallpaper:")))
    return None


@dataclass(frozen=True, slots=True)
class BridgeEntry:
    """One Bridge module the Manifest records, and what the Entrypoint does with it.

    Stored whole -- line, file, mechanism -- rather than looked up by tool, so an entry a
    newer app wrote for a tool this build does not know still renders the line it had.
    Kept out of `Manifest.modules`: a Bridge module is the tool's, never hashed, never
    pruned, never hand-edit-checked (ADR-0006).
    """

    tool: str
    module: str
    """The `require` name, in the tool's one spelling. Quarantine's key for this file."""

    line: str
    """The exact Entrypoint text that loads it."""

    file: str
    """Where the tool writes it, relative to the hypr dir."""

    mechanism: Mechanism
    state: BridgeState

    def with_state(self, state: BridgeState) -> BridgeEntry:
        return replace(self, state=state)

    def as_json(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "module": self.module,
            "line": self.line,
            "file": self.file,
            "mechanism": self.mechanism.value,
            **_state_json(self.state),
        }

    @classmethod
    def from_json(cls, payload: Any) -> BridgeEntry | None:
        """One entry, or `None` for a malformed one: never fatal, like every Manifest key."""
        if not isinstance(payload, dict):
            return None
        fields = [payload.get(key) for key in ("tool", "module", "line", "file", "mechanism")]
        if not all(isinstance(value, str) and value for value in fields):
            return None
        tool, module, line, file, mechanism = (str(value) for value in fields)
        state = _state_from_json(payload)
        if state is None or mechanism not in {member.value for member in Mechanism}:
            return None
        return cls(tool, module, line, file, Mechanism(mechanism), state)


def files_present(hypr_dir: Path, files: Iterable[str]) -> frozenset[str]:
    """Which of `files` (Bridge module files, relative to `hypr_dir`) a tool has written.

    The one answer to "has this tool run": what entry states, the Theming page and the
    wizard read (finding 28 of the #153 review). A tool writes every module of its own in
    one run, but a Bridge counts as run only when all of them are here.
    """
    return frozenset(name for name in files if (hypr_dir / name).is_file())


def has_run(spec: ToolSpec, hypr_dir: Path) -> bool:
    """Whether `spec`'s tool has written every Bridge module of its own."""
    files = [module.file for module in spec.modules]
    return files_present(hypr_dir, files) == frozenset(files)


def entries_for(spec: ToolSpec, *, present: Collection[str]) -> tuple[BridgeEntry, ...]:
    """New entries for wiring `spec`: active where its file exists, waiting where not.

    Never gated: setting a tool up is not a Color source choice. A switch is
    `bridge_states_for`, after this.
    """
    return tuple(
        BridgeEntry(
            tool=spec.tool,
            module=each.module,
            line=each.line,
            file=each.file,
            mechanism=spec.mechanism,
            state=ACTIVE if each.file in present else WAITING,
        )
        for each in spec.modules
    )


def in_require_order(
    entries: Iterable[BridgeEntry], registry: Mapping[str, ToolSpec] = REGISTRY
) -> tuple[BridgeEntry, ...]:
    """Registered modules in the registry's order, then any it does not know, by module."""
    rank = {
        each.module: index
        for index, each in enumerate(
            each for spec in registry.values() for each in spec.modules
        )
    }
    return tuple(
        sorted(entries, key=lambda entry: (rank.get(entry.module, len(rank)), entry.module))
    )


# --- the gate ---------------------------------------------------------------------------------


def bridge_states_for(
    source: ChosenSource,
    entries: Sequence[BridgeEntry],
    *,
    present: Collection[str],
    registry: Mapping[str, ToolSpec] = REGISTRY,
) -> tuple[BridgeEntry, ...]:
    """Every entry's state once `source` is the Color source. Raises `ValueError` for a
    Wallpaper backend that has no entry, since there is no require to enable.

    - `Wallpaper(tool)`: that backend loads, the other backend is off for it, and every
      other bridge loads.
    - `PresetColors`, `ManualColors`: both backends are off, and so is every other bridge
      whose Options include a colour -- ADR-0014's "nothing overriding them". A module that
      sets colour and other keys is gated whole: the app cannot load half a file.

    A bridge that loads is `Active` when `present` names its file, else `Waiting`.
    """
    if isinstance(source, Wallpaper) and not any(e.tool == source.tool for e in entries):
        raise ValueError(f"{source_name(source, registry)} is not set up")

    def state(entry: BridgeEntry) -> BridgeState:
        spec = registry.get(entry.tool)
        if spec is None:
            return entry.state
        if isinstance(source, Wallpaper):
            gated = spec.color_source and spec.tool != source.tool
        else:
            gated = spec.color_source or spec.sets_colors
        if gated:
            return Off(source)
        return ACTIVE if entry.file in present else WAITING

    return tuple(entry.with_state(state(entry)) for entry in entries)


def with_presence(
    entries: Sequence[BridgeEntry], *, present: Collection[str]
) -> tuple[BridgeEntry, ...]:
    """Entries brought into step with the files on disk: a waiting module whose file now
    exists loads, a loading one whose file is gone waits. `Off` is never touched."""

    def state(entry: BridgeEntry) -> BridgeState:
        if isinstance(entry.state, Off):
            return entry.state
        return ACTIVE if entry.file in present else WAITING

    return tuple(entry.with_state(state(entry)) for entry in entries)


def owners(
    entries: Iterable[BridgeEntry],
    *,
    quarantined: Collection[str] = (),
    registry: Mapping[str, ToolSpec] = REGISTRY,
) -> dict[str, str]:
    """Option name to the tool that sets it, for every loading, unquarantined entry.

    From the registry's static key lists (ADR-0018: the app never evaluates tool Lua). Two
    tools setting one key: the later in require order, which is the one that wins.
    """
    owned: dict[str, str] = {}
    for entry in in_require_order(entries, registry):
        spec = registry.get(entry.tool)
        if spec is None or entry.state != ACTIVE or entry.module in quarantined:
            continue
        owned.update(dict.fromkeys(spec.owned_keys, spec.tool))
    return owned


# --- the Entrypoint line ----------------------------------------------------------------------

OFF_NOTE = "off: Color source is {source}"
WAITING_NOTE = "waiting for {tool}'s first run"


def render_line(
    entry: BridgeEntry, *, present: bool, registry: Mapping[str, ToolSpec] = REGISTRY
) -> str:
    """The entry's Entrypoint line: the require, or the require commented with its reason.

    An `Active` entry whose file is missing renders as waiting: the Manifest may not have
    caught up with a deleted file yet, and a require of a missing file errors every reload.
    """
    spec = registry.get(entry.tool)
    title = spec.title if spec is not None else entry.tool
    match entry.state:
        case Off(source):
            note = OFF_NOTE.format(source=source_name(source, registry))
        case Active() if present:
            return entry.line
        case Active() | Waiting():
            note = WAITING_NOTE.format(tool=title)
    return f"-- {entry.line}  -- {note}"


_REQUIRE_LINE = re.compile(
    r"^(?P<comment>--\s*)?require\(\s*(?P<q>[\"'])(?P<module>[^\"']+)(?P=q)\s*\)[^\s-]*"
    r"\s*(?:--\s*(?P<note>.*?))?\s*$"
)
"""A line that requires a module, perhaps commented, perhaps with a trailing reason."""


def _parsed(
    text: str, registry: Mapping[str, ToolSpec]
) -> list[tuple[ToolSpec, str, BridgeState | None]]:
    """Each registered module the text requires: its spec, its module, and its state.

    `None` for a commented line with no reason this app writes -- the Quarantine block's form.
    """
    found: list[tuple[ToolSpec, str, BridgeState | None]] = []
    for raw in text.splitlines():
        match = _REQUIRE_LINE.match(raw.strip())
        if match is None:
            continue
        module = match.group("module")
        spec = spec_for_module(module, registry)
        if spec is None:
            continue
        canonical = next(
            each.module
            for each in spec.modules
            if each.module.replace(".", "/") == module.replace(".", "/")
        )
        if match.group("comment") is None:
            found.append((spec, canonical, ACTIVE))
        else:
            found.append((spec, canonical, _note_state(match.group("note") or "", registry)))
    return found


def _note_state(note: str, registry: Mapping[str, ToolSpec]) -> BridgeState | None:
    if note.startswith("waiting for"):
        return WAITING
    prefix = OFF_NOTE.format(source="")
    if not note.startswith(prefix):
        return None
    name = note.removeprefix(prefix).strip()
    if name == "Preset":
        return Off(PresetColors())
    for spec in registry.values():
        if spec.color_source and spec.title == name:
            return Off(Wallpaper(spec.tool))
    return Off(ManualColors())


def bridges_from_entrypoint(
    text: str, registry: Mapping[str, ToolSpec] = REGISTRY
) -> tuple[BridgeEntry, ...]:
    """The entries an Entrypoint's lines describe, for a Manifest that was lost (S4).

    Matches each registered module in either spelling, commented or not, and restores the
    state its line states. A commented line with no reason of ours (Quarantine's, or a hand
    edit) comes back `Active`: Quarantine was recorded in the lost Manifest too, and the line
    renders as waiting anyway while its file is missing.
    """
    entries: dict[str, BridgeEntry] = {}
    for spec, module, state in _parsed(text, registry):
        if module in entries:
            continue
        bridge = next(each for each in spec.modules if each.module == module)
        entries[module] = BridgeEntry(
            tool=spec.tool,
            module=module,
            line=bridge.line,
            file=bridge.file,
            mechanism=spec.mechanism,
            state=ACTIVE if state is None else state,
        )
    return in_require_order(entries.values(), registry)


def color_source_of(text: str, registry: Mapping[str, ToolSpec] = REGISTRY) -> ColorSource:
    """The Color source an Entrypoint's text puts in force (ADR-0014). Never stored.

    The Wallpaper backend whose line loads -- or waits for its first run, which is still
    that backend's choice. Several, when more than one does. Otherwise, the source a gated
    line names: Preset if any says so, else Manual.
    """
    lines = _parsed(text, registry)
    backends: list[str] = []
    for spec, _module, state in lines:
        if spec.color_source and state in (ACTIVE, WAITING) and spec.tool not in backends:
            backends.append(spec.tool)
    if len(backends) == 1:
        return Wallpaper(backends[0])
    if backends:
        return Several(tuple(backends))
    if any(state == Off(PresetColors()) for _spec, _module, state in lines):
        return PresetColors()
    return ManualColors()
