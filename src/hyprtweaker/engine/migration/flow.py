"""The five guarded steps of a migration, with no toolkit anywhere near them (ADR-0009).

**Detect -> Preview -> Back up -> Switch & verify -> Keep or roll back.** The wizard dialog
is a rendering of this object: every decision, every write and every way out lives here, so
the flow can be driven to completion -- including its failure paths -- by a test with no
display, and so a crashed app can finish a rollback the dialog never got to show.

The ordering rules are the safety story, and they are the reason this is a state machine
rather than one procedure:

- nothing is written until the user has seen the Loss report;
- the backup is taken before the first write, not before the switch;
- `Hyprland --verify-config` passes on the *staged* tree before the real one is touched;
- the sentinel is on disk before the Entrypoint is, so a switch is never in flight
  unrecorded;
- doing nothing rolls back. The countdown's default answer is the safe one, because the
  user who most needs the timer is the one whose keybinds just stopped working.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from ..bridge import REGISTRY, BridgeEntry
from ..bridge.wire import WireConsent
from ..files import write_atomic
from ..importer.loss import BACKUP_NAME, LossCode, LossReport, rescue_command, rescue_line
from ..importer.lua.mapping import import_lua
from ..importer.lua.sandbox import Consent, Policy
from ..importer.mapping import ImportResult, import_config
from ..model import ConfigModel
from ..model.values import lua_string
from ..monitors_catalog import arrangement_mismatches
from ..paths import ConfigPaths
from ..schema import Schema
from ..state.manifest import Manifest
from ..tools import detached_environment, find_tool
from ..writer import Writer, load_manifest
from ..writer.binds import live_bind_count
from ..writer.lua import table_key
from . import backup as backups
from . import bridge_setup
from . import sentinel as sentinels
from .detect import ConfigKind, Detection, detect
from .export import render as export_render

ROLLBACK_SECONDS = 60.0
"""How long Keep-or-roll-back waits before rolling back on its own (ADR-0009).

One minute, not five: the session this protects is one whose binds may have just stopped
working, and five minutes of that is a user reaching for the power button.
"""

VERIFY_TIMEOUT_SECONDS = 180.0
BACKUP_SUFFIX = ".bak"


def _displaces_entrypoint(detection: Detection) -> bool:
    """Whether this migration renames an existing `hyprland.lua` aside to make room.

    The single fact two otherwise-distant decisions both turn on: whether `back_up` makes a
    `hyprland.lua.bak`, and whether the rescue line restores one or deletes the Entrypoint.
    Keyed on the detected *kind* rather than the imported file's extension, because
    `build_preview(source=...)` replaces only the source -- importing a `.conf` while a
    foreign `hyprland.lua` is in place still displaces that file (#131).
    """
    return detection.kind is ConfigKind.FOREIGN_LUA


RELOAD_SETTLE_SECONDS = 0.25
"""How long to let a `reload full-reset` land before believing what the compositor says.

ADR-0010's settle window, for the same reason: the reply and the event both arrive before
the new config is readable, so an immediate read answers about the config being replaced.
"""

_SWITCH_NOTES = (
    "Environment variables and permissions apply at your next login, not now -- Hyprland "
    "does not re-read them on a reload.",
    "Anything set to run at startup may have been started again by the switch.",
)
"""ADR-0009's two "we cannot verify this here" caveats, said rather than assumed."""


class Step(StrEnum):
    """Where the flow has got to. The dialog's subpage is a function of this."""

    DETECT = "detect"
    PREVIEW = "preview"
    BACK_UP = "back-up"
    SWITCH = "switch"
    DECIDE = "decide"
    DONE = "done"


class Decision(StrEnum):
    """How Keep-or-roll-back ended."""

    KEPT = "kept"
    ROLLED_BACK = "rolled-back"
    EXPIRED = "expired"
    """Nobody answered in time, so it rolled back. Recorded apart from an explicit
    rollback because the two mean different things in a report: one is a user's judgement,
    the other is a user who could not act -- quite possibly because the switch broke their
    input."""


class Client(Protocol):
    """The slice of the IPC command client a migration needs.

    A Protocol rather than the concrete class so the flow's tests do not need a compositor,
    and so it is obvious at a glance what migration asks Hyprland: the one reload, and the
    four reads ADR-0009's live checks are made of.
    """

    async def configerrors(self) -> tuple[str, ...]: ...

    async def bind_count(self) -> int: ...

    async def workspace_rule_count(self) -> int: ...

    async def monitors(self) -> tuple[Mapping[str, Any], ...]: ...

    async def reload_full_reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Preview:
    """What an import would do, computed without writing anything."""

    detection: Detection
    result: ImportResult

    @property
    def loss(self) -> LossReport:
        return self.result.loss

    @property
    def model(self) -> ConfigModel:
        return self.result.model

    @property
    def imported(self) -> int:
        """How many settings this read got: its Options plus its Entities."""
        return len(self.model) + len(self.result.entities)

    @property
    def offered(self) -> tuple[Offered, ...]:
        """What the wizard's second offer would do for real, verbatim (#190).

        Non-empty only after a *blocked* read that came back empty (no Option, no Entity)
        or erroring, and that tried to run a command on the way: a config that builds itself
        from `io.popen` output reads as nothing, or as a Lua error, while its commands are
        faked. Anything else it read is a Preview worth showing as it is, and a read that
        already ran them for real has nothing left to offer.

        Running for real runs everything the blocked read faked, so the file operations are
        listed beside the commands, and a repeat is listed once with how many times it ran,
        in the order each first ran (#150 review, findings 6 and 19).
        """
        faked = [
            use
            for use in self.result.shell
            if use.kind in _OFFERED_KINDS and use.policy == Policy.BLOCK
        ]
        if not any(_OFFERED_KINDS[use.kind] == "command" for use in faked):
            return ()
        erroring = LossCode.EVAL_ERROR in self.loss.code_counts()
        if self.imported and not erroring:
            return ()
        times = Counter((_OFFERED_KINDS[use.kind], use.cmd) for use in faked)
        return tuple(Offered(text, kind, n) for (kind, text), n in times.items())


OfferedKind = Literal["command", "delete", "move"]


@dataclass(frozen=True, slots=True)
class Offered:
    """One thing the second offer would do for real, as the config wrote it."""

    text: str
    """The command line, the path deleted, or `old -> new` for a move."""
    kind: OfferedKind
    times: int = 1


_OFFERED_KINDS: dict[str, OfferedKind] = {
    "os.execute": "command",
    "io.popen": "command",
    "os.remove": "delete",
    "os.rename": "move",
}
"""The `ShellUse` kinds a config does itself. `importer.listdir` is the importer's own
listing (`runner.lua`), which runs under every policy and so is never offered."""


def asks_consent(source: Path) -> bool:
    """Whether reading `source` means running it, so the user is asked first (#190).

    Every `.lua` is read by evaluating it (the Lua importer); a `.conf` is only parsed.
    """
    return source.suffix == ".lua"


@dataclass(frozen=True, slots=True)
class VerifyGate:
    """The static `Hyprland --verify-config` gate over the staged tree."""

    ran: bool
    ok: bool
    output: str = ""

    @property
    def blocks(self) -> bool:
        """Whether the wizard must stop. A gate that could not run does not block.

        No Hyprland binary means a machine that cannot be migrated live anyway; refusing to
        preview or export there would be punishing the wrong user.
        """
        return self.ran and not self.ok


@dataclass(frozen=True, slots=True)
class Check:
    """One live verification of the switched-to config."""

    name: str
    ok: bool
    detail: str = ""

    hard: bool = True
    """Whether failing it rolls the migration back.

    Decided by what a false alarm costs. A hard check that misfires on a legitimate config
    rolls back a migration that worked, the worst outcome this wizard has, so only the two
    that mean "the user may be stranded" are hard: `configerrors`, and the bind count
    (a config that loads with no keybinds is ADR-0016's emergency). Workspace rules and
    monitors are compared with what Hyprland *did* with a request -- merged a selector,
    picked the closest mode -- so a difference there is reported, not acted on.
    """


@dataclass(frozen=True, slots=True)
class SwitchResult:
    """The outcome of going live."""

    ok: bool
    checks: tuple[Check, ...] = ()
    errors: tuple[str, ...] = ()
    detail: str = ""

    live: bool = True
    """Whether a running compositor was actually switched.

    False means the config was written but nothing changed yet, which is a different ending
    from a successful switch: there is nothing to keep or roll back, so no countdown runs.
    """

    notes: tuple[str, ...] = ()
    """What the switch could not verify, in the user's terms (ADR-0009).

    Not failures. `hl.env` and `hl.permission` do not take effect on a reload at all, and
    autostart entries may have been re-run by the switch -- a user judging "is everything
    still working?" needs both said out loud, because neither is visible in the session
    they are looking at.
    """

    bridges: tuple[str, ...] = ()
    """One sentence per theming tool the user chose to set up (#187): set up, or not and why.

    A tool that could not be wired never fails the switch; the user is told here instead.
    """

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.ok and check.hard)

    @property
    def warnings(self) -> tuple[Check, ...]:
        """Checks that did not pass but do not roll the migration back.

        Surfaced rather than swallowed: a soft check that fails silently is a comment in the
        source rather than a fact the user is told.
        """
        return tuple(check for check in self.checks if not check.ok and not check.hard)


@dataclass
class MigrationFlow:
    """One run of the wizard, from detection to a config the user decided to keep."""

    paths: ConfigPaths
    schema: Schema
    app_version: str
    client: Client | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    find: Callable[[str], Path | None] = field(default=find_tool, repr=False)
    """How a theming tool's program is looked up (`engine.tools`): never run, only found."""

    step: Step = Step.DETECT
    detection: Detection | None = None
    preview: Preview | None = None
    backup: backups.Backup | None = None
    report_path: Path | None = None
    _restore: Path | None = field(default=None, repr=False)
    """Where a displaced `hyprland.lua` was renamed to, once the switch has renamed it --
    the file the rescue line has to name (#131)."""
    _answer: asyncio.Event | None = field(default=None, repr=False)
    _decision: Decision | None = field(default=None, repr=False)
    _consents: dict[str, WireConsent] = field(default_factory=dict, repr=False)
    """The theming tools the user agreed to set up, by tool: wired at Switch, never before."""
    rollback_notes: tuple[str, ...] = ()
    """What the last rollback could not put back, one sentence per tool (#187)."""

    # --- 1. detect ----------------------------------------------------------------------

    def detect(self) -> Detection:
        """Which of ADR-0009's four cases holds, and what the wizard should offer."""
        found = detect(
            self.paths,
            app_version=self.app_version,
            schema_version=self.schema.hyprland_version,
        )
        self.detection = found
        self.step = Step.PREVIEW if found.offers_import else Step.DONE
        return found

    def pending_switch(self) -> sentinels.Sentinel | None:
        """An unconfirmed switch from a previous run -- the crash-safety entry point.

        Checked at startup, before anything else: finding one means the last switch was
        never answered for, so it is treated as failed and rollback is offered (ADR-0009).
        """
        return sentinels.read(self.paths)

    # --- 2. preview ---------------------------------------------------------------------

    def build_preview(
        self,
        source: Path | None = None,
        *,
        consent: Consent | None = None,
    ) -> Preview:
        """Run the right importer and hold the result. Writes nothing, anywhere.

        `source` overrides what detection found, which is what makes Import... at any later
        time the same wizard rather than a second one: point it at a file, get a preview.
        """
        preview = self.read_preview(source, consent=consent)
        self.hold(preview)
        return preview

    def read_preview(
        self,
        source: Path | None = None,
        *,
        consent: Consent | None = None,
        cancel: threading.Event | None = None,
    ) -> Preview:
        """`build_preview`'s read, holding nothing: the flow is unchanged once detection ran.

        The wizard runs this off the main loop and `hold`s the result back on it, so a read
        the user cancelled (`cancel` set: `Cancelled` is raised) leaves no Preview (#216).
        """
        detection = self.detection or self.detect()
        path = source or detection.source
        if path is None:
            raise ValueError("nothing to import: no source file was detected or given")

        if asks_consent(path):
            result = import_lua(path, self.schema, consent=consent or Consent(), cancel=cancel)
        else:
            result = import_config(path, self.schema)

        preview = Preview(detection=replace(detection, source=path), result=result)
        # The Importer is handed a file to read and never learns what will be renamed
        # around it, so the wizard stamps the answer on before the report is saved -- a
        # report read back months later still carries the rescue that fits it (#131).
        result.loss.restore_backup = _displaces_entrypoint(preview.detection)
        return preview

    def hold(self, preview: Preview) -> None:
        """Make `preview` the one the later steps back up, stage and switch."""
        self.preview = preview
        self.step = Step.BACK_UP

    def save_report(self) -> Path:
        """Persist the Loss report so it outlives the wizard (ADR-0009).

        Saved at the end of Preview rather than at the end of the migration: the report is
        the record of what conversion *would* do, and a user who reads it and backs out is
        exactly the user most likely to want it again later.
        """
        preview = self._require_preview()
        self.report_path = preview.loss.save(self.paths, now=self.now())
        return self.report_path

    # --- 3. back up ---------------------------------------------------------------------

    def back_up(self) -> backups.Backup:
        """Copy the whole hypr dir aside. Nothing has been written at this point."""
        self.backup = backups.create(self.paths, now=self.now())
        return self.backup

    def bridge_offers(self) -> tuple[bridge_setup.ToolOffer, ...]:
        """The theming tools the back-up step lists (ADR-0009 §Back up, bridge, static gate).

        None without an IPC socket: that path is Detect/Preview only, and a tool wired for a
        config that loads at next login would be set up behind a switch nobody verified.
        """
        if self.client is None:
            return ()
        return bridge_setup.offers(self.paths, self._manifest(), find=self.find)

    def consent(self, consent: WireConsent) -> None:
        """The user confirmed this tool's plan: it is wired at Switch, and only then.

        One wallpaper color tool at a time (finding 18 of the #153 review): confirming
        matugen withdraws wallust, and the switched tree makes the confirmed one the Color
        source, so the switch never ends with two backends loading at once."""
        spec = REGISTRY.get(consent.plan.tool)
        if spec is not None and spec.color_source:
            for tool in [t for t in self._consents if REGISTRY[t].color_source]:
                self._consents.pop(tool)
        self._consents[consent.plan.tool] = consent

    def withdraw(self, tool: str) -> None:
        """The user changed their mind before Switch: nothing of `tool`'s is touched."""
        self._consents.pop(tool, None)

    @property
    def consents(self) -> tuple[WireConsent, ...]:
        return tuple(self._consents.values())

    def _bridge_entries(self) -> tuple[BridgeEntry, ...]:
        """The Bridge entries the switched tree carries: those already in the Manifest (an
        already-wired tool keeps loading) plus one per consented tool."""
        return bridge_setup.with_consented(
            self._manifest(), self._consents.values(), self.paths.hypr_dir
        )

    @contextmanager
    def _staged(
        self, preview: Preview, bridges: Sequence[BridgeEntry] | None = None
    ) -> Iterator[ConfigPaths]:
        """The converted tree, rendered somewhere harmless.

        Staged rather than written in place, because the real Entrypoint *is* the switch:
        writing it to run the gate would leave a live session one manual reload away from a
        config nobody has approved yet. The staged tree is byte-identical to what Switch
        will write, so a verdict on it transfers -- and so does an export of it.
        """
        with tempfile.TemporaryDirectory(prefix="hyprtweaker-staged-") as raw:
            staging = ConfigPaths.rooted_at(Path(raw))
            staging.hypr_dir.mkdir(parents=True, exist_ok=True)
            for entry in bridges or ():
                # A tool's module that is already here loads in the real tree, so the gate
                # has to load it too; one that is not renders as waiting in both.
                source = self.paths.hypr_dir / entry.file
                if source.is_file():
                    (staging.hypr_dir / entry.file).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, staging.hypr_dir / entry.file)
            self._write_tree(preview, staging, bridges)
            yield staging

    def stage_and_gate(self) -> VerifyGate:
        """Let Hyprland judge the converted tree before the live engine is touched."""
        preview = self._require_preview()
        if not _hyprland_installed():
            return VerifyGate(ran=False, ok=True, output="no Hyprland binary on this machine")

        with self._staged(preview, self._bridge_entries()) as staging:
            runtime = staging.hypr_dir.parent / "run"
            runtime.mkdir(exist_ok=True)
            completed = _verify_config(staging.entrypoint, runtime)

        output = f"{completed.stdout}\n{completed.stderr}".strip()
        return VerifyGate(ran=True, ok=completed.returncode == 0, output=output)

    def export_text(self) -> str:
        """The converted config as one flattened file -- the "Copy the Lua instead" exit.

        Rendered from the *staged* tree, not from the model plus the live config dir. The
        difference is the whole correctness of this exit: `vars.lua` and `legacy.lua` exist
        only once the tree is written, so exporting from the model alone would silently drop
        the user's `$variables` and every construct the GUI cannot represent -- while
        inlining a `user.lua` and Bridge modules belonging to the config being replaced.
        """
        preview = self._require_preview()
        with self._staged(preview) as staging:
            return export_render(
                preview.model, staging, app_version=self.app_version, now=self.now()
            ).text

    # --- 4. switch & verify -------------------------------------------------------------

    async def switch(self) -> SwitchResult:
        """Write the new config, make the live session read it, and check that it did.

        The order is load-bearing and is the reason this is not three calls from the UI:
        sentinel, then files, then `reload full-reset`, then verification. A failure at any
        point after the sentinel leaves a marker that the next start knows how to undo.
        """
        preview = self._require_preview()
        self.step = Step.SWITCH

        if self.client is None:
            # ADR-0009: "Requires a live session; without an IPC socket the wizard runs
            # Detect/Preview only." The tree is still written -- it is what the user asked
            # for and it loads at next login -- but nothing switched, so there is nothing to
            # confirm: no sentinel, and no countdown to roll back a change that never
            # happened. Writing one anyway would offer to undo a migration on the next start.
            self._write_tree(preview, self.paths)
            self._record_provenance(preview)
            self.step = Step.DONE
            return SwitchResult(
                ok=True,
                live=False,
                detail=(
                    "There is no running Hyprland to switch, so nothing changed yet. Your "
                    "new configuration is written and loads the next time you log in."
                ),
            )

        restore = self._preserve_foreign_entrypoint(preview)
        self._restore = restore
        consents = self.consents
        sentinels.write(
            self.paths,
            kind=preview.detection.kind.value,
            source=preview.detection.source,
            backup=self.backup.path if self.backup else None,
            restore=restore,
            bridge_tools=tuple(consent.plan.tool for consent in consents),
            now=self.now(),
        )

        # The tree, Entrypoint included, lands before any tool file: noctalia, once its
        # template is on, appends its own `require` to a `hyprland.lua` that lacks one, and
        # the app would then read its own Entrypoint as hand-edited (#166 Left open). With
        # the line already there -- commented while the tool has not run (S4) -- there is no
        # window in which that can happen.
        self._write_tree(preview, self.paths, self._bridge_entries())
        bridges = bridge_setup.wire_consented(
            consents,
            register=self._carries_bridge,
            unregister=lambda tool: self._drop_bridge(preview, tool),
            hypr_dir=self.paths.hypr_dir,
        )
        self._record_provenance(preview)

        await self.client.reload_full_reset()
        checks = await self.verify_live(preview)
        ok = all(check.ok for check in checks if check.hard)
        errors = tuple(
            check.detail for check in checks if not check.ok and check.name == "configerrors"
        )

        self.step = Step.DECIDE
        return SwitchResult(
            ok=ok, checks=tuple(checks), errors=errors, notes=_SWITCH_NOTES, bridges=bridges
        )

    async def _settled_errors(self) -> tuple[str, ...]:
        """`configerrors`, read only once the reload has actually finished.

        `reload full-reset` answers `"ok"` as soon as it has been *asked*, not once the new
        config is parsed -- the same gap ADR-0010 documents for `configreloaded`, which
        fires ~11 ms before the new values are readable. Reading straight after the reply
        can therefore report the state of the config being replaced, which on this path
        would mean verifying the old config and keeping the new one.

        So: settle, read, and if that read is dirty, settle and read again. Errors that
        survive the second read are real; ones that do not were the reload still in flight.
        """
        assert self.client is not None
        await asyncio.sleep(RELOAD_SETTLE_SECONDS)
        errors = await self.client.configerrors()
        if errors:
            await asyncio.sleep(RELOAD_SETTLE_SECONDS)
            errors = await self.client.configerrors()
        return errors

    async def verify_live(self, preview: Preview) -> list[Check]:
        """ADR-0009's live checks, spoken over the IPC socket rather than by spawning.

        Public so the Harness can run them against a compositor it booted on a written
        config, which is the only place the false-alarm question has an answer.
        """
        assert self.client is not None
        checks: list[Check] = []

        errors = await self._settled_errors()
        checks.append(
            Check(
                name="configerrors",
                ok=not errors,
                detail="\n".join(errors),
                hard=True,
            )
        )

        entities = preview.result.entities

        # The count the Writer emits, not the count imported: disabled binds are comments and
        # function-valued ones never reach `binds.lua`. `>=`, because `legacy.lua` and a
        # preserved script can register binds the model never held.
        expected_binds = live_bind_count(entities)
        if expected_binds:
            live = await self.client.bind_count()
            ok = live >= expected_binds
            checks.append(
                Check(
                    name="binds",
                    ok=ok,
                    detail=""
                    if ok
                    else (
                        f"Only {live} of the {expected_binds} keybinds in the new "
                        "configuration are active."
                    ),
                    hard=True,
                )
            )

        # Window and layer rules have no IPC listing in Hyprland, so `configerrors` is all
        # that verifies them; saying so in a row the user cannot act on would be noise.
        expected_workspace_rules = len(entities.workspace_rules)
        if expected_workspace_rules:
            live = await self.client.workspace_rule_count()
            ok = live >= expected_workspace_rules
            checks.append(
                Check(
                    name="workspace rules",
                    ok=ok,
                    detail=""
                    if ok
                    else (
                        f"Only {live} of the {expected_workspace_rules} workspace rules in "
                        "the new configuration are active."
                    ),
                    hard=False,
                )
            )

        if entities.monitors:
            mismatches = arrangement_mismatches(entities.monitors, await self.client.monitors())
            checks.extend(
                Check(name="monitors", ok=False, detail=detail, hard=False)
                for detail in mismatches
            )
            if not mismatches:
                checks.append(Check(name="monitors", ok=True, hard=False))
        return checks

    # --- 5. keep or roll back -----------------------------------------------------------

    async def decide(
        self,
        *,
        seconds: float = ROLLBACK_SECONDS,
        on_tick: Callable[[float], None] | None = None,
        tick: float = 1.0,
    ) -> Decision:
        """Wait for Keep, for Roll back, or for the clock. Silence rolls back.

        `on_tick` is called with the seconds remaining so a dialog can draw a countdown
        without owning the timer -- the deadline has to be the engine's, or a wizard whose
        window was closed would leave the switch pending forever.
        """
        self.step = Step.DECIDE
        self._answer = asyncio.Event()
        self._decision = None
        deadline = asyncio.get_running_loop().time() + seconds

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            if on_tick is not None:
                on_tick(remaining)
            try:
                await asyncio.wait_for(self._answer.wait(), timeout=min(tick, remaining))
            except TimeoutError:
                continue
            break

        decision = self._decision or Decision.EXPIRED
        if decision is Decision.KEPT:
            self.keep()
        else:
            self.roll_back()
        self.step = Step.DONE
        return decision

    def answer(self, decision: Decision) -> None:
        """Called by the dialog's two buttons. Safe before `decide` is even waiting."""
        self._decision = decision
        if self._answer is not None:
            self._answer.set()

    def keep(self) -> None:
        """Confirm the switch: clear the sentinel and let the new config stand."""
        sentinels.clear(self.paths)
        self.step = Step.DONE

    def roll_back(self, marker: sentinels.Sentinel | None = None) -> None:
        """Put the previous engine back.

        Deleting the Entrypoint is the whole rollback on the `.conf` path -- `hyprland.conf`
        was never touched, so Hyprland picks it up again by itself. On the `.lua` path the
        original is moved back from `hyprland.lua.bak` over the generated file.

        Takes an optional sentinel so a *relaunched* app can roll back a switch this object
        never made: after a crash the marker on disk is the only thing that remembers what
        the previous config was.

        Each theming tool the switch wired is unwired first, while the Entrypoint still has
        its line (#187). One that cannot be put back is said in `rollback_notes` and does
        not stop the rest: the user is never stranded on the new config for a tool's sake.
        """
        record = marker or sentinels.read(self.paths)
        self.rollback_notes = bridge_setup.unwire_all(
            record.bridge_tools if record else (),
            paths=self.paths,
            manifest=self._manifest,
            unregister=self._forget_bridge,
        )
        restore = Path(record.restore) if record and record.restore else None

        if restore and restore.is_file():
            os.replace(restore, self.paths.entrypoint)
        else:
            self.paths.entrypoint.unlink(missing_ok=True)

        sentinels.clear(self.paths)
        self.step = Step.DONE

    async def roll_back_live(self, marker: sentinels.Sentinel | None = None) -> None:
        """Roll back and make the running session read the restored config."""
        self.roll_back(marker)
        await self.reload_restored()

    async def reload_restored(self) -> None:
        """Make the running session read the config a rollback put back."""
        if self.client is not None:
            await self.client.reload_full_reset()

    # --- shared -------------------------------------------------------------------------

    @property
    def rescue_line(self) -> str:
        """The TTY escape hatch, printed in every report (ADR-0009).

        Path-specific, because the two paths rescue in opposite directions: the legacy path
        removes the Entrypoint this app generated, the Lua path restores the backup it
        renamed the user's own file into. Read off the file being imported rather than the
        detected kind, so an Import... of a `.conf` while a foreign `hyprland.lua` is in
        place gets the line for the file it is actually replacing (#131).
        """
        return rescue_line(self._restores_backup(), backup=self._backup_name())

    @property
    def rescue_command(self) -> str:
        """The same escape hatch as a bare command, for the wizard's own rescue rows.

        The dialog puts this in a Row where the report's Markdown would render as literal
        asterisks and backticks, and a rescue instruction the user has to mentally strip
        punctuation out of is one they can mistype at the worst possible moment.
        """
        return rescue_command(self._restores_backup(), backup=self._backup_name())

    def _backup_name(self) -> str:
        """The file the rescue restores *from*, exact once the switch has renamed it.

        A second migration finds `hyprland.lua.bak` already taken and stamps the new one, so
        naming the plain `.bak` after that would restore a config two migrations old. Before
        the switch there is nothing renamed and nothing to be locked out of, so the generic
        name is the honest answer there (#131).
        """
        return self._restore.name if self._restore is not None else BACKUP_NAME

    def _restores_backup(self) -> bool | None:
        """Whether rolling back means restoring `hyprland.lua.bak` rather than deleting.

        The same predicate `_preserve_foreign_entrypoint` renames by, deliberately: the
        rescue is the manual spelling of that rollback, so reading it off anything else --
        the imported file's extension, say -- lets the two disagree about a file the user
        only has one copy of (#131).
        """
        detection = self.preview.detection if self.preview is not None else self.detection
        if detection is None:
            return None
        return _displaces_entrypoint(detection)

    def _manifest(self) -> Manifest:
        return load_manifest(
            self.paths,
            app_version=self.app_version,
            schema_version=self.schema.hyprland_version,
        )

    def _carries_bridge(self, tool: str) -> bool:
        """`wire`'s `register`: the entry is already in the tree the switch just wrote."""
        return any(entry.tool == tool for entry in self._manifest().bridges)

    def _drop_bridge(self, preview: Preview, tool: str) -> bool:
        """Take a tool that could not be wired back out of the Manifest and the Entrypoint,
        before the reload: a line for a tool nobody set up would wait forever."""
        manifest = self._manifest().remove_bridge(tool)
        writer = Writer(self.paths, app_version=self.app_version)
        writer.set_bridges(preview.result.model, manifest.bridges)
        return True

    def _forget_bridge(self, tool: str) -> bool:
        """`unwire`'s `unregister` on Roll back: the Manifest only, since the Entrypoint that
        carries the line is about to be deleted or replaced by the user's own."""
        if self.paths.manifest.is_file():
            stripped = self._manifest().remove_bridge(tool)
            write_atomic(self.paths.manifest, stripped.render())
        return True

    def _require_preview(self) -> Preview:
        if self.preview is None:
            raise RuntimeError("no preview yet: call build_preview() first")
        return self.preview

    def _preserve_foreign_entrypoint(self, preview: Preview) -> Path | None:
        """Rename a foreign `hyprland.lua` aside, since the new one contests its name.

        A rename, never a delete (ADR-0009), and it happens before the sentinel records it
        so the marker can never name a backup that was not made.
        """
        if not _displaces_entrypoint(preview.detection):
            return None
        entrypoint = self.paths.entrypoint
        if not entrypoint.is_file():
            return None

        target = entrypoint.with_name(entrypoint.name + BACKUP_SUFFIX)
        stamp = self.now().strftime(backups.STAMP_FORMAT)
        if target.exists():
            # An earlier migration already claimed the name. Keep both: the older one may
            # be the user's only copy of a config from before that migration.
            target = entrypoint.with_name(f"{entrypoint.name}{BACKUP_SUFFIX}.{stamp}")
        os.replace(entrypoint, target)
        return target

    def _write_tree(
        self,
        preview: Preview,
        paths: ConfigPaths,
        bridges: Sequence[BridgeEntry] | None = None,
    ) -> None:
        """Render the imported config into an App dir: `vars`, `legacy`, then the Modules.

        `vars.lua` and `legacy.lua` are written first because the Entrypoint's require list
        is discovered from what is on disk -- write them after, and the file that requires
        them would not mention them. `bridges`, when given, are recorded in the Manifest
        first for the same reason: the Entrypoint renders its Bridge lines from it.
        """
        result = preview.result
        paths.app_dir.mkdir(parents=True, exist_ok=True)

        if result.variables:
            write_atomic(paths.vars_lua, _render_vars(result.variables))
        if result.legacy:
            write_atomic(paths.legacy_lua, result.legacy)

        # The Writer renders `model.entities`, and an Importer returns its Entities beside the
        # model rather than in it: without this the tree carries the Options and none of the
        # binds, rules or monitors the Preview promised (#101).
        result.model.adopt_entities(result.entities)
        writer = Writer(paths, app_version=self.app_version)
        if bridges is not None:
            writer.record_bridges(result.model, bridges)
        writer.write(result.model)

    def _record_provenance(self, preview: Preview) -> None:
        """Stamp the Manifest with where this config came from (ADR-0009).

        Written after the Writer rather than through it: the Writer owns the Module records
        and rewrites the Manifest on every save, and provenance is the one field it carries
        forward untouched rather than computes.
        """
        manifest = Manifest.load(
            self.paths.manifest,
            app_version=self.app_version,
            schema_version=self.schema.hyprland_version,
        )
        stamped = replace(manifest, migration=preview.result.provenance(now=self.now()))
        write_atomic(self.paths.manifest, stamped.render())


def _render_vars(variables: dict[str, str]) -> str:
    """The imported `$variable` table, as a module returning it.

    A module rather than a set of globals: `vars` is required first so that `legacy.lua` can
    read the variables its constructs referenced, and a returned table is what `require`
    hands back.
    """
    lines = [
        "-- Variables imported from hyprland.conf. Rewritten only by a new import.",
        "return {",
    ]
    for name in sorted(variables):
        lines.append(f"  {table_key(name)}{lua_string(variables[name])},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _hyprland_installed() -> bool:
    return shutil.which("Hyprland") is not None


def _verify_config(entrypoint: Path, runtime_dir: Path) -> subprocess.CompletedProcess[str]:
    """`Hyprland --verify-config`, with the caller's own session out of reach.

    `--verify-config` *executes* the config with live bindings, so a run that inherited
    `HYPRLAND_INSTANCE_SIGNATURE` could reach the session the user is sitting in -- the
    static test tier hit exactly this (prototype #30).
    """
    environment = detached_environment()
    environment["XDG_RUNTIME_DIR"] = str(runtime_dir)
    return subprocess.run(
        ["Hyprland", "--verify-config", "-c", str(entrypoint)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=VERIFY_TIMEOUT_SECONDS,
    )


def fresh_start(paths: ConfigPaths, schema: Schema, *, app_version: str) -> ConfigModel:
    """ADR-0009 case 4: no config at all. Give the user a working Entrypoint.

    Every Option Unset, which is not the same as every Option at its default: an Unset
    Option is one this app does not emit, so Hyprland's own default applies and the
    generated tree stays empty until the user actually changes something.
    """
    model = ConfigModel(schema)
    paths.app_dir.mkdir(parents=True, exist_ok=True)
    Writer(paths, app_version=app_version).write(model)
    return model


__all__ = [
    "ROLLBACK_SECONDS",
    "Check",
    "Client",
    "Decision",
    "MigrationFlow",
    "Preview",
    "Step",
    "SwitchResult",
    "VerifyGate",
    "fresh_start",
]
