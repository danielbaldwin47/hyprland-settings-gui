"""Bridge setup in the Migration wizard's back-up step (ADR-0009, ADR-0006, #187).

ADR-0009 §Back up, bridge, static gate: "detected tools offered per-tool, each flip behind
explicit confirmation". What is offered, and how the wizard's Switch and Roll back wire and
unwire through #166's `wire`/`unwire`; `MigrationFlow` decides when.

- **Offered**: a tool whose program is installed, that is not already wired, and that
  `plan_wire` can plan. A wired tool is listed as set up, with no offer (it keeps loading
  after the switch, its entry carried in the Manifest); a tool `plan_wire` refuses is listed
  with the reason. A tool that is not installed is not listed: there is nothing of it to keep
  working, and a user who only wants to migrate reads no row about it.
- **Consent** is a `WireConsent` per tool, made by the confirm from the plan it showed (S3).
  Nothing is written before Switch.
- **Notes** are what the user reads afterwards, one sentence per tool.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ..bridge import (
    REGISTRY,
    BridgeEntry,
    ToolSpec,
    Wallpaper,
    bridge_states_for,
    files_present,
    has_run,
)
from ..bridge.wire import (
    IfChanged,
    NeedsChoice,
    NotDone,
    Unwired,
    WireConsent,
    WirePlan,
    detect,
    plan_wire,
    shown,
    unwire,
    wire,
)
from ..paths import ConfigPaths
from ..state.manifest import Manifest
from ..tools import find_tool

Find = Callable[[str], Path | None]
Register = Callable[[str], bool]

LATER = "You can set it up on the Theming page."
"""Where a tool the wizard did not set up is set up later (#164)."""


@dataclass(frozen=True, slots=True)
class Offer:
    """An installed tool the user can set up; `plan` is what its confirm shows."""

    plan: WirePlan

    @property
    def tool(self) -> str:
        return self.plan.tool

    @property
    def title(self) -> str:
        return self.plan.title


@dataclass(frozen=True, slots=True)
class SetUp:
    """Already wired: its Manifest entry is carried through the switch. Nothing to offer."""

    tool: str
    title: str


@dataclass(frozen=True, slots=True)
class CannotSetUp:
    """Installed, but `plan_wire` refused it (a version it cannot bridge, a config it cannot
    edit safely): listed with the reason, never offered."""

    tool: str
    title: str
    reason: str


ToolOffer = Offer | SetUp | CannotSetUp


def offers(
    paths: ConfigPaths, manifest: Manifest, *, find: Find = find_tool
) -> tuple[ToolOffer, ...]:
    """Every tool the back-up step lists, in the registry's order. Reads; runs nothing."""
    found: list[ToolOffer] = []
    for tool, spec in REGISTRY.items():
        seen = detect(tool, paths=paths, manifest=manifest, find=find)
        if seen.wired:
            found.append(SetUp(tool, spec.title))
        elif seen.installed:
            plan = plan_wire(tool, paths=paths, find=find)
            if isinstance(plan, NotDone):
                found.append(CannotSetUp(tool, spec.title, plan.reason))
            else:
                found.append(Offer(plan))
    return tuple(found)


def with_consented(
    manifest: Manifest, consents: Iterable[WireConsent], hypr_dir: Path
) -> tuple[BridgeEntry, ...]:
    """`manifest`'s entries plus one per consented tool: what the switched tree carries.

    Each new entry is active where its file is already in `hypr_dir` and waiting otherwise
    (settled S4): a tool that has not run yet gets its line commented, never a require of a
    missing file.
    """
    backend: str | None = None
    for consent in consents:
        spec = REGISTRY[consent.plan.tool]
        manifest = manifest.add_bridge(
            spec, present=files_present(hypr_dir, (m.file for m in spec.modules))
        )
        if spec.color_source:
            backend = spec.tool
    if backend is None:
        return manifest.bridges
    # The confirmed wallpaper color tool is the Color source: one already set up is gated
    # off for it rather than loading beside it.
    present = files_present(hypr_dir, (entry.file for entry in manifest.bridges))
    return bridge_states_for(Wallpaper(backend), manifest.bridges, present=present)


def wire_consented(
    consents: Iterable[WireConsent], *, register: Register, unregister: Register, hypr_dir: Path
) -> tuple[str, ...]:
    """Wire each consented tool; one sentence each, said on the Keep-or-roll-back page.

    A tool that cannot be wired now has its entry taken out again (`wire` calls
    `unregister`, and puts back any file it wrote) and is reported: it costs the switch
    nothing, and the rest of the migration stands.
    """
    notes: list[str] = []
    for consent in consents:
        plan = consent.plan
        outcome = wire(plan, consent, register=register, unregister=unregister)
        if isinstance(outcome, NotDone):
            notes.append(f"{plan.title} was not set up: {outcome.reason} {LATER}")
        else:
            spec = REGISTRY[plan.tool]
            loads = has_run(spec, hypr_dir)
            notes.append(f"{plan.title} is set up." if loads else _waiting(spec))
    return tuple(notes)


def unwire_all(
    tools: Iterable[str],
    *,
    paths: ConfigPaths,
    manifest: Callable[[], Manifest],
    unregister: Register,
) -> tuple[str, ...]:
    """Unwire each tool a switch wired; what could not be put back, one sentence each.

    Never stops part-way: a tool that cannot be unwired is reported and the rollback goes on
    (settled #187). A tool config changed since setup is never overwritten (S3): it is left
    as the user has it, its line goes, and the note says where the copy is.
    """
    notes: list[str] = []
    backups = shown(paths.bridge_backups_dir, paths) + "/"
    for tool in tools:
        title = REGISTRY[tool].title if tool in REGISTRY else tool
        try:
            outcome = unwire(tool, paths=paths, manifest=manifest(), unregister=unregister)
            if isinstance(outcome, NeedsChoice):
                changed = ", ".join(each.shown for each in outcome.changed)
                outcome = unwire(
                    tool,
                    paths=paths,
                    manifest=manifest(),
                    unregister=unregister,
                    if_changed=IfChanged.LEAVE,
                )
                if isinstance(outcome, Unwired):
                    notes.append(
                        f"{changed} changed after {title} was set up, so it was left as it "
                        f"is. The copy from before setup is in {backups}."
                    )
                    continue
        except OSError as error:
            outcome = NotDone(tool, f"{error.strerror or error}.")
        if isinstance(outcome, NotDone):
            notes.append(
                f"{title}'s config could not be put back: {outcome.reason} The copy from "
                f"before setup is in {backups}."
            )
    return tuple(notes)


def _waiting(spec: ToolSpec) -> str:
    if spec.color_source:
        return f"{spec.title} is set up. Its colors load from {spec.title}'s next run."
    return f"{spec.title} is set up. It loads from {spec.title}'s next run."


__all__ = [
    "CannotSetUp",
    "Offer",
    "SetUp",
    "ToolOffer",
    "offers",
    "unwire_all",
    "wire_consented",
    "with_consented",
]
