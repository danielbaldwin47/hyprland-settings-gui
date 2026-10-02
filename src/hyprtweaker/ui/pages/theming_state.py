"""What the Theming page says, decided without a toolkit (#164, ADR-0014).

The page draws; this module decides. Every function takes what detection found
(`ToolDetection`), the Manifest's Bridge entries and the files present, and the Color source
read off the Entrypoint, and answers with the words and the state the user sees. Nothing
here is cached: the page asks again on every refresh, because the Entrypoint, a tool's
config and its output file can all change outside the app.
"""

from __future__ import annotations

import enum
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from hyprtweaker.engine.bridge import (
    REGISTRY,
    Active,
    BridgeEntry,
    ColorSource,
    ManualColors,
    Off,
    PresetColors,
    Several,
    Waiting,
    Wallpaper,
    source_name,
)
from hyprtweaker.engine.bridge.wire import ChangedFile, ToolDetection

BACKENDS: tuple[str, ...] = tuple(tool for tool, spec in REGISTRY.items() if spec.color_source)
"""The Wallpaper backends, one tab each (matugen, wallust), in the registry's order."""

OTHER_TOOLS: tuple[str, ...] = tuple(
    tool for tool, spec in REGISTRY.items() if not spec.color_source
)
"""The tools listed under "Other tools" (noctalia, DMS, shell-switch)."""


def source_line(source: ColorSource) -> str:
    """The Color source as ADR-0014 names it: `Wallpaper (matugen)`, `Preset`, `Manual`."""
    match source:
        case Wallpaper():
            return f"Wallpaper ({source_name(source)})"
        case Several(tools):
            return f"{_and(_title(tool) for tool in tools)} both load; the last one wins"
        case _:
            return source_name(source)


def source_detail(source: ColorSource, entries: Sequence[BridgeEntry]) -> str:
    """One sentence under the line: where the colours come from, and any other tool that
    still sets some of them (a shell that is on while the source is Preset or Manual)."""
    match source:
        case Wallpaper(tool):
            return f"{_title(tool)} sets your border and group colors from your wallpaper."
        case Several():
            return "Switch to one of them to choose which sets your colors."
        case PresetColors():
            lead = "The colors of the Preset you applied"
        case ManualColors():
            lead = "Your own color settings apply"
    setters = _color_setters(entries)
    if setters:
        return f"{lead}, except where {_and(setters)} sets them."
    return f"{lead}. No theming tool overrides them."


def _color_setters(entries: Sequence[BridgeEntry]) -> list[str]:
    """Tools other than the backends whose colour-setting module loads."""
    tools: list[str] = []
    for entry in entries:
        spec = REGISTRY.get(entry.tool)
        if spec is None or spec.color_source or not spec.sets_colors:
            continue
        if isinstance(entry.state, Active) and spec.title not in tools:
            tools.append(spec.title)
    return tools


# --- backend tabs --------------------------------------------------------------------------


class TabState(enum.Enum):
    IN_USE = "in use"
    """The Color source: its module loads."""

    WAITING = "waiting"
    """The Color source, but the tool has not written its file yet."""

    LOAD_NOW = "load now"
    """The Color source, and the file is there but not loaded yet: "Load now"."""

    NOT_IN_USE = "not in use"
    """Found, and not the Color source: "Switch to <tool>"."""

    NOT_INSTALLED = "not installed"
    """Nothing of it is here: the tab says so and offers "Check again"."""


IN_USE_STATES = frozenset({TabState.IN_USE, TabState.WAITING, TabState.LOAD_NOW})


@dataclass(frozen=True, slots=True)
class Tab:
    tool: str
    title: str
    state: TabState
    wired: bool

    @property
    def label(self) -> str:
        """The tab's text: the in-use tab says so, so "one active" is visible at a glance."""
        return f"{self.title} · in use" if self.state in IN_USE_STATES else self.title


def backend_tab(
    detection: ToolDetection,
    entries: Sequence[BridgeEntry],
    source: ColorSource,
    *,
    present: Collection[str],
) -> Tab:
    tool = detection.tool
    title = _title(tool)
    if not detection.found:
        return Tab(tool, title, TabState.NOT_INSTALLED, wired=False)
    if not _in_use(tool, source):
        return Tab(tool, title, TabState.NOT_IN_USE, wired=detection.wired)
    own = [entry for entry in entries if entry.tool == tool]
    waiting = [entry for entry in own if isinstance(entry.state, Waiting)]
    if not waiting:
        return Tab(tool, title, TabState.IN_USE, wired=True)
    if all(entry.file in present for entry in waiting):
        return Tab(tool, title, TabState.LOAD_NOW, wired=True)
    return Tab(tool, title, TabState.WAITING, wired=True)


def _in_use(tool: str, source: ColorSource) -> bool:
    match source:
        case Wallpaper(chosen):
            return chosen == tool
        case Several(tools):
            return tool in tools
        case _:
            return False


def has_backend(detections: Iterable[ToolDetection]) -> bool:
    """Whether to show the tabs at all; else the one-sentence empty state."""
    return any(each.found for each in detections)


def resume_target(
    source: ColorSource, entries: Sequence[BridgeEntry], *, shown: str
) -> str | None:
    """The backend "Resume wallpaper colors" switches back to, or `None` when it has none.

    Only under Preset or Manual. Of the backends set up, the one whose tab is on screen if
    it is one of them, else the first: the user is looking at what they will get back.
    """
    if not isinstance(source, PresetColors | ManualColors):
        return None
    wired = [tool for tool in BACKENDS if any(entry.tool == tool for entry in entries)]
    if not wired:
        return None
    return shown if shown in wired else wired[0]


# --- other tools ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolState:
    """One "Other tools" row: what it says and which actions it offers."""

    tool: str
    title: str
    status: str
    setup: bool = False
    remove: bool = False
    load: bool = False
    patch: bool = False
    """shell-switch's "Copy patch": the change the user makes to their own script."""


def other_tool(
    detection: ToolDetection, entries: Sequence[BridgeEntry], *, present: Collection[str]
) -> ToolState:
    tool = detection.tool
    spec = REGISTRY[tool]
    title = spec.title
    if detection.needs_update is not None:
        return ToolState(tool, title, detection.needs_update)
    own = [entry for entry in entries if entry.tool == tool]
    if not own:
        return ToolState(tool, title, "Not set up", setup=True)
    for entry in own:
        if isinstance(entry.state, Off):
            return ToolState(
                tool,
                title,
                f"Off while the Color source is {source_name(entry.state.source)}",
                remove=True,
            )
    waiting = [entry for entry in own if isinstance(entry.state, Waiting)]
    if waiting and all(entry.file in present for entry in waiting):
        return ToolState(tool, title, "Its files are ready to load", remove=True, load=True)
    if waiting:
        return ToolState(
            tool,
            title,
            f"Waiting for {title}'s first run",
            remove=True,
            patch=bool(spec.template_pack and spec.template_pack.patch),
        )
    if spec.sets_colors:
        return ToolState(tool, title, "On: sets your border and group colors", remove=True)
    return ToolState(tool, title, "On", remove=True)


def changed_since_setup(title: str, files: Sequence[ChangedFile]) -> str:
    """The body of Remove's question when files changed since setup: each file as it is now
    beside the copy "Restore the copy" would put back, so the user sees both before
    choosing. Nothing has changed when this is asked."""
    listed = []
    for each in files:
        if each.copy is None:
            listed.append(f"{each.shown}\nSetup created this file, so restoring deletes it.")
        else:
            listed.append(f"{each.shown}\nCopy from before setup: {each.copy}")
    these = "These files were" if len(files) > 1 else "This file was"
    return (
        f"{these} changed after {title} was set up:\n\n"
        + "\n\n".join(listed)
        + "\n\nRestore the copy (the file as it is now is kept beside it), or leave it as "
        f"it is and only stop loading {title}."
    )


def _title(tool: str) -> str:
    spec = REGISTRY.get(tool)
    return spec.title if spec is not None else tool


def _and(names: Iterable[str]) -> str:
    items = list(names)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


__all__ = [
    "BACKENDS",
    "IN_USE_STATES",
    "OTHER_TOOLS",
    "Tab",
    "TabState",
    "ToolState",
    "backend_tab",
    "changed_since_setup",
    "has_backend",
    "other_tool",
    "resume_target",
    "source_detail",
    "source_line",
]
