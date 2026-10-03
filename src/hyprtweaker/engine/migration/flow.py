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
import hashlib
import logging
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
from ..bridge.wire import WireConsent, shown
from ..files import failure_reason, free_stamped_folder, keep_edited_copy, write_atomic
from ..importer.loss import (
    APP_DIR_BACKUP_NAME,
    BACKUP_NAME,
    IMPORTED_NAME,
    LossCode,
    LossReport,
    rescue_command,
    rescue_line,
)
from ..importer.lua.mapping import import_lua
from ..importer.lua.sandbox import Consent, Policy
from ..importer.mapping import ImportResult, import_config
from ..model import ConfigModel
from ..model.values import lua_string
from ..monitors_catalog import arrangement_mismatches
from ..paths import MONITOR_PROFILES_DIR, PRESETS_DIR, ConfigPaths
from ..profiles import ACTIVE_NAME
from ..schema import Schema
from ..state import kept_import
from ..state.manifest import Manifest
from ..tools import detached_environment, find_tool
from ..writer import Writer, load_manifest
from ..writer.binds import live_bind_count
from ..writer.lua import GENERATED_BANNER, table_key
from . import backup as backups
from . import bridge_setup
from . import sentinel as sentinels
from .detect import ConfigKind, Detection, detect
from .export import render as export_render

_log = logging.getLogger(__name__)

ROLLBACK_SECONDS = 60.0
"""How long Keep-or-roll-back waits before rolling back on its own (ADR-0009).

One minute, not five: the session this protects is one whose binds may have just stopped
working, and five minutes of that is a user reaching for the power button.
"""

VERIFY_TIMEOUT_SECONDS = 180.0
BACKUP_SUFFIX = ".bak"


def _displaces_entrypoint(detection: Detection) -> bool:
    """Whether this migration renames an existing `hyprland.lua` aside to make room.

    A foreign one, and the app's own on a menu Import over an app-generated config: deleting
    that one on Roll back left the user on `hyprland.conf` or Hyprland's defaults (#148
    review R1).

    The single fact two otherwise-distant decisions both turn on: whether `back_up` makes a
    `hyprland.lua.bak`, and whether the rescue line restores one or deletes the Entrypoint.
    Keyed on the detected *kind* rather than the imported file's extension, because
    `build_preview(source=...)` replaces only the source -- importing a `.conf` while a
    foreign `hyprland.lua` is in place still displaces that file (#131).
    """
    return detection.kind in (ConfigKind.FOREIGN_LUA, ConfigKind.APP_GENERATED)


RELOAD_SETTLE_SECONDS = 0.25
"""How long to let a `reload full-reset` land before believing what the compositor says.

ADR-0010's settle window, for the same reason: the reply and the event both arrive before
the new config is readable, so an immediate read answers about the config being replaced.
"""

_SWITCH_NOTES = (
    "Environment variables and permissions apply at your next login, not now — Hyprland "
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


@dataclass(frozen=True, slots=True)
class RollBackOutcome:
    """What a Roll back did, and when it could not finish, what remains (#268).

    Incomplete means the switch is still unfinished: its marker stays, so the next start
    offers Roll back again, and nothing says "you are on the configuration you had before".
    """

    complete: bool
    edited_copy: Path | None = None
    """Where a changed Entrypoint was copied before Roll back replaced or removed it."""
    notes: tuple[str, ...] = ()
    """One sentence each: the copy kept, where the switch's own files went, what a theming
    tool could not put back (#187)."""
    rescue: str = ""
    """Why it stopped, what is still in place and how to recover; empty when complete."""


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
    """Where the switch moves a displaced `hyprland.lua`, chosen before its marker is
    written -- the file the rescue line has to name (#131, #268)."""
    _restore_app_dir: Path | None = field(default=None, repr=False)
    """Where the switch moves the App dir it found, chosen with `_restore`."""
    _imported: Path | None = field(default=None, repr=False)
    """Where the rescue moves the imported App dir out of the way: a name free at switch
    time, so a repeated migration's rescue never moves into an earlier one (#268)."""
    _answer: asyncio.Event | None = field(default=None, repr=False)
    _decision: Decision | None = field(default=None, repr=False)
    _consents: dict[str, WireConsent] = field(default_factory=dict, repr=False)
    """The theming tools the user agreed to set up, by tool: wired at Switch, never before."""
    rollback: RollBackOutcome | None = None
    """What the last Roll back did, for the page that reports it."""

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
        result.loss.restore_app_dir = self.paths.app_dir.is_dir()
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
        the backup names are chosen, the sentinel records them with the original
        Entrypoint's hash, the originals move, the tree is written, the sentinel is
        rewritten with the generated Entrypoint's hash, then `reload full-reset`, then
        verification. Nothing moves before the marker is on disk, so a failure at any point
        leaves either nothing changed or a marker the next start knows how to undo (#268).
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
            try:
                # Kept by being written: there is no countdown to answer (#259).
                kept_import.write(self.paths, self._manifest())
            except OSError as error:
                _log.warning("could not record the kept import: %s", error)
            self.step = Step.DONE
            return SwitchResult(
                ok=True,
                live=False,
                detail=(
                    "There is no running Hyprland to switch, so nothing changed yet. Your "
                    "new configuration is written and loads the next time you log in."
                ),
            )

        # Read while the Manifest and Entrypoint that record them are still in place.
        entries = self._bridge_entries()
        self._choose_backup_names(preview)
        consents = self.consents
        started = self.now()

        def record(generated: str | None = None) -> None:
            sentinels.write(
                self.paths,
                kind=preview.detection.kind.value,
                source=preview.detection.source,
                backup=self.backup.path if self.backup else None,
                restore=self._restore,
                restore_app_dir=self._restore_app_dir,
                bridge_tools=tuple(consent.plan.tool for consent in consents),
                original_sha256=original,
                generated_sha256=generated,
                now=started,
            )

        original = _sha256(self.paths.entrypoint)
        record()
        self._preserve_entrypoint()
        self._preserve_app_dir(entries)
        self._resave_report(preview)

        # The tree, Entrypoint included, lands before any tool file: noctalia, once its
        # template is on, appends its own `require` to a `hyprland.lua` that lacks one, and
        # the app would then read its own Entrypoint as hand-edited (#166 Left open). With
        # the line already there -- commented while the tool has not run (S4) -- there is no
        # window in which that can happen.
        self._write_tree(preview, self.paths, entries)
        bridges = bridge_setup.wire_consented(
            consents,
            register=self._carries_bridge,
            unregister=lambda tool: self._drop_bridge(preview, tool),
            hypr_dir=self.paths.hypr_dir,
        )
        self._record_provenance(preview)
        try:
            record(_sha256(self.paths.entrypoint))
        except OSError as error:
            # The first marker stands: Roll back then copies any Entrypoint that is not the
            # original before replacing it, which costs a needless copy, never a file.
            _log.warning("could not record the generated Entrypoint's hash: %s", error)

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
        """Confirm the switch: record the import, clear the sentinel, let it stand.

        The record (`state/kept_import.py`) is what makes the import the user's restore
        boundary at the Session's next read-back (#259). Written first, so a Keep that dies
        in between leaves the switch unanswered rather than kept with no record; a record
        that cannot be written raises with the sentinel still in place.
        """
        kept_import.write(self.paths, self._manifest())
        sentinels.clear(self.paths)
        self.step = Step.DONE

    def roll_back(self, marker: sentinels.Sentinel | None = None) -> RollBackOutcome:
        """Put the previous engine back, or say exactly what is still in place (#268).

        Deleting the Entrypoint is the whole rollback on the `.conf` path -- `hyprland.conf`
        was never touched, so Hyprland picks it up again by itself. On the `.lua` path the
        original is moved back from `hyprland.lua.bak` over the generated file, or copied
        from the full-tree backup when that file is gone.

        Takes an optional sentinel so a *relaunched* app can roll back a switch this object
        never made: after a crash the marker on disk is the only thing that remembers what
        the previous config was. Decided by bytes, not by which step the switch reached: an
        Entrypoint that hashes as the original stays, a named backup is trusted only once it
        exists.

        Idempotent: a second call finds the original in place and leaves it. Nothing the user
        wrote is lost: an Entrypoint that is neither the original nor what the switch wrote
        is copied to `edited-copies` first, and a copy that fails stops Roll back before any
        byte changes. The marker is cleared only when Roll back completes.

        Each theming tool the switch wired is unwired first, while the Entrypoint still has
        its line (#187). One that cannot be put back is said in the notes and does not stop
        the rest: the user is never stranded on the new config for a tool's sake.
        """
        record = marker or sentinels.read(self.paths)
        outcome = self._roll_back(record)
        self.rollback = outcome
        if outcome.complete:
            sentinels.clear(self.paths)
            # A Keep that died between its record and the sentinel: never a boundary (#259).
            try:
                kept_import.clear(self.paths)
            except OSError as error:
                _log.warning("could not drop the unanswered kept-import record: %s", error)
        self.step = Step.DONE
        return outcome

    def _roll_back(self, record: sentinels.Sentinel | None) -> RollBackOutcome:
        if record is not None and not record.known:
            return RollBackOutcome(complete=False, rescue=self._unreadable_rescue())
        entrypoint = self.paths.entrypoint
        how, source = self._entrypoint_plan(record)
        if how is _Put.STUCK:
            assert record is not None and record.restore is not None
            return RollBackOutcome(complete=False, rescue=self._stuck_rescue(record))

        edited: Path | None = None
        if how is not _Put.LEAVE and self._changed_since_switch(record):
            try:
                edited = keep_edited_copy(self.paths, entrypoint, entrypoint.name)
            except OSError as error:
                return RollBackOutcome(
                    complete=False, rescue=self._no_copy_rescue(record, error)
                )

        notes: list[str] = []
        if edited is not None:
            notes.append(
                f"{entrypoint.name} had changed since the switch, so that version is kept "
                f"as {shown(edited, self.paths)}."
            )
        notes.extend(
            bridge_setup.unwire_all(
                record.bridge_tools if record else (),
                paths=self.paths,
                manifest=self._manifest,
                unregister=self._forget_bridge,
            )
        )

        if how is _Put.MOVE_BACK:
            assert source is not None
            os.replace(source, entrypoint)
        elif how is _Put.FROM_BACKUP:
            assert source is not None and record is not None and record.backup is not None
            _copy_into_place(source, entrypoint)
            notes.append(
                f"{Path(record.restore or BACKUP_NAME).name} was missing, so your "
                f"{entrypoint.name} was put back from the backup made before the switch."
            )
        elif how is _Put.DELETE:
            entrypoint.unlink()

        disowned: Path | None = None
        if record and record.restore_app_dir:
            # The App dir the user had is moved back, the switch's own moved out of its way
            # (#148 review R1). Missing: the switch stopped before moving it, or an earlier
            # call already moved it back; either way the App dir in place is the user's.
            kept = Path(record.restore_app_dir)
            if kept.is_dir():
                disowned = self._disown_app_dir()
                os.replace(kept, self.paths.app_dir)
        elif not _generated_by_this_app(entrypoint):
            disowned = self._disown_app_dir()

        if disowned is not None:
            notes.append(
                "What the switch wrote, with the presets and display profiles in it, is "
                f"kept in {shown(disowned.parent, self.paths)}."
            )
        if record and record.backup and Path(record.backup).is_dir():
            notes.append(
                "A full copy of your config from before the switch is kept in "
                f"{shown(Path(record.backup), self.paths)}."
            )
        return RollBackOutcome(complete=True, edited_copy=edited, notes=tuple(notes))

    def _entrypoint_plan(self, record: sentinels.Sentinel | None) -> tuple[_Put, Path | None]:
        """What Roll back does to the Entrypoint, decided before any byte changes."""
        entrypoint = self.paths.entrypoint
        if record is None or record.restore is None:
            # The `.conf` path: nothing was displaced, so whatever is there the switch wrote.
            # Its hash says so even after a hand edit dropped the banner, which is copied
            # first (review m1 F1). Only a marker without the hash falls back to the banner:
            # a file without it is the user's own and stays.
            if record is not None and record.generated_sha256 is not None:
                present = entrypoint.is_file() or entrypoint.is_symlink()
                return (_Put.DELETE if present else _Put.LEAVE), None
            return (_Put.DELETE if _generated_by_this_app(entrypoint) else _Put.LEAVE), None
        restore = Path(record.restore)
        in_backup = Path(record.backup) / entrypoint.name if record.backup else None
        original = record.original_sha256 or (
            _sha256(in_backup) if in_backup is not None else None
        )
        if original is not None and _sha256(entrypoint) == original:
            # The switch stopped before moving it, or an earlier Roll back put it back
            # (#148 hand-test 19): the user's own file is in place, even with the app's
            # banner, as an app user's carries (#148 review R1).
            return _Put.LEAVE, None
        if restore.is_file() or restore.is_symlink():
            return _Put.MOVE_BACK, restore
        if in_backup is not None and (in_backup.is_file() or in_backup.is_symlink()):
            return _Put.FROM_BACKUP, in_backup
        return _Put.STUCK, None

    def _changed_since_switch(self, record: sentinels.Sentinel | None) -> bool:
        """Whether the Entrypoint holds bytes the switch did not write: a hand edit during
        the countdown, or a switch that stopped before recording what it wrote."""
        current = _sha256(self.paths.entrypoint)
        if current is None:
            return False
        if record is not None and record.generated_sha256 is not None:
            return current != record.generated_sha256
        return current != (record.original_sha256 if record else None)

    def _unreadable_rescue(self) -> str:
        latest = backups.latest(self.paths)
        where = (
            f" The backup made before the switch is in {shown(latest.path, self.paths)}."
            if latest is not None
            else ""
        )
        return (
            "The record of this switch could not be read, so nothing was rolled back and "
            f"the new configuration is still in place.{where} If {BACKUP_NAME} is in "
            "~/.config/hypr, this puts it back from a TTY:\n"
            f"{rescue_command(True)}"
        )

    def _stuck_rescue(self, record: sentinels.Sentinel) -> str:
        name = self.paths.entrypoint.name
        backup = Path(record.backup) if record.backup else None
        where = (
            f" The backup made before the switch is in {shown(backup, self.paths)}, without "
            f"a {name}."
            if backup is not None and backup.is_dir()
            else " There is no backup from before the switch to put it back from."
        )
        return (
            f"Your {name} could not be put back: {Path(record.restore or BACKUP_NAME).name} "
            f"is missing.{where} Nothing was changed, so the new configuration is still in "
            "place, and the app offers this again at its next start. If you are locked out, "
            "this moves the new file aside from a TTY, so Hyprland starts on its own "
            f"default:\nmv ~/.config/hypr/{name} ~/.config/hypr/{name}.switched"
        )

    def _no_copy_rescue(self, record: sentinels.Sentinel | None, error: OSError) -> str:
        name = self.paths.entrypoint.name
        reason = failure_reason(error)
        # The edited file is moved aside first, never removed or written over: there is no
        # copy of it anywhere else.
        command = f"mv ~/.config/hypr/{name} ~/.config/hypr/{name}.switched"
        if record is not None and record.restore is not None:
            command += f" && {marker_rescue_command(self.paths, record)}"
        return (
            f"Nothing was rolled back: {name} has changed since the switch, and a copy of "
            f"it could not be kept ({reason}). The new configuration is still in place, and "
            "the app offers this again at its next start. If you are locked out, this keeps "
            f"your edited file and puts the previous configuration back from a TTY:\n{command}"
        )

    def _disown_app_dir(self) -> Path | None:
        """Move the App dir the rolled-back switch wrote into the state directory.

        Nothing loads it now, and left in place its Manifest claimed the user's own config
        for the app: the next launch wrote edits into Modules nothing loads (#148 hand-tests
        20, 25). Moved, never deleted: presets or profiles in it stay in reach. A folder of
        its own per Roll back, so two in one second never meet (#268). Returns where it went.
        """
        app_dir = self.paths.app_dir
        if not app_dir.is_dir():
            return None
        base = free_stamped_folder(
            self.paths.state_dir / ROLLED_BACK_DIR, app_dir.name, now=self.now()
        )
        base.mkdir(parents=True, exist_ok=True)
        target = base / app_dir.name
        shutil.move(str(app_dir), str(target))
        return target

    async def roll_back_live(self, marker: sentinels.Sentinel | None = None) -> RollBackOutcome:
        """Roll back and make the running session read the restored config."""
        outcome = self.roll_back(marker)
        await self.reload_restored()
        return outcome

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
        return rescue_line(
            self._restores_backup(),
            backup=self._backup_name(),
            app_dir_backup=self._app_dir_backup_name(),
            imported=self._imported.name if self._imported is not None else IMPORTED_NAME,
        )

    @property
    def rescue_command(self) -> str:
        """The same escape hatch as a bare command, for the wizard's own rescue rows.

        The dialog puts this in a Row where the report's Markdown would render as literal
        asterisks and backticks, and a rescue instruction the user has to mentally strip
        punctuation out of is one they can mistype at the worst possible moment.
        """
        return rescue_command(
            self._restores_backup(),
            backup=self._backup_name(),
            app_dir_backup=self._app_dir_backup_name(),
            imported=self._imported.name if self._imported is not None else IMPORTED_NAME,
        )

    def _backup_name(self) -> str:
        """The file the rescue restores *from*, exact once the switch has renamed it.

        A second migration finds `hyprland.lua.bak` already taken and stamps the new one, so
        naming the plain `.bak` after that would restore a config two migrations old. Before
        the switch there is nothing renamed and nothing to be locked out of, so the generic
        name is the honest answer there (#131).
        """
        return self._restore.name if self._restore is not None else BACKUP_NAME

    @property
    def moved_aside(self) -> str | None:
        """Where this switch moved what it displaced, by the exact names it made (#268).

        `None` before the switch, or when it displaced nothing. A repeated migration's
        names are stamped, so the Done page names them rather than the generic ones the
        Preview could only promise.
        """
        kept = [
            f"{what} as {shown(path, self.paths)}"
            for what, path in (
                (f"your previous {self.paths.entrypoint.name}", self._restore),
                ("the app's previous folder", self._restore_app_dir),
            )
            if path is not None and path.exists()
        ]
        return f"Kept {' and '.join(kept)}." if kept else None

    @property
    def app_data_note(self) -> str | None:
        """What the preview says of the user's Presets and Monitor profiles, which carry
        over into the imported config (R8), and of the active-profile pointer, which does
        not; `None` with nothing of theirs in the App dir."""
        presets = self._count(self.paths.presets_dir)
        profiles = self._count(self.paths.monitor_profiles_dir)
        if not presets and not profiles:
            return None
        kept = " and ".join(
            f"{count} {noun}{'' if count == 1 else 's'}"
            for count, noun in ((presets, "preset"), (profiles, "display profile"))
            if count
        )
        said = [f"Kept from the app: {kept}."]
        if (self.paths.monitor_profiles_dir / ACTIVE_NAME).is_file():
            # The pointer never carries over; the reason is only true of an import that
            # sets the displays itself (#268 comment 2).
            sets_displays = self.preview is not None and bool(
                self.preview.result.entities.monitors
            )
            said.append(
                "None of the profiles stays marked active, because the imported config "
                "sets your displays."
                if sets_displays
                else "None of the profiles stays marked active; choose one on the Displays "
                "page to use it again."
            )
        if (self.paths.hypr_dir / APP_DIR_BACKUP_NAME).exists():
            # The switch stamps the name then; the Done page and the re-saved report give
            # the exact one, which is not known until the switch picks its stamp.
            said.append(
                "The app's previous folder is kept in ~/.config/hypr under a dated name, "
                f"since {APP_DIR_BACKUP_NAME} is taken by an earlier import."
            )
        else:
            said.append(
                f"The app's previous folder is kept as ~/.config/hypr/{APP_DIR_BACKUP_NAME}."
            )
        return " ".join(said)

    @staticmethod
    def _count(directory: Path) -> int:
        if not directory.is_dir():
            return 0
        return sum(1 for path in directory.glob("*.json") if path.name != ACTIVE_NAME)

    def _app_dir_backup_name(self) -> str | None:
        """The App dir the rescue moves back, or `None` when the switch displaces none."""
        if self._restore_app_dir is not None:
            return self._restore_app_dir.name
        displaced = (
            self.preview.result.loss.restore_app_dir
            if self.preview is not None
            else self.paths.app_dir.is_dir()
        )
        return APP_DIR_BACKUP_NAME if displaced else None

    def _restores_backup(self) -> bool | None:
        """Whether rolling back means restoring `hyprland.lua.bak` rather than deleting.

        The same predicate `_preserve_entrypoint` renames by, deliberately: the
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

    def _choose_backup_names(self, preview: Preview) -> None:
        """Name, once, where the switch moves what it displaces (#268).

        Chosen before the sentinel is written so the marker can name them before anything
        moves; a reader checks a named backup exists before trusting it. A name an earlier
        migration already claimed gets the switch's stamp: keep both, the older one may be
        the user's only copy of a config from before that migration.
        """
        stamp = self.now().strftime(backups.STAMP_FORMAT)
        entrypoint = self.paths.entrypoint
        self._restore = (
            _free_beside(entrypoint, entrypoint.name + BACKUP_SUFFIX, stamp)
            if _displaces_entrypoint(preview.detection) and entrypoint.is_file()
            else None
        )
        app_dir = self.paths.app_dir
        self._restore_app_dir = (
            _free_beside(app_dir, APP_DIR_BACKUP_NAME, stamp) if app_dir.is_dir() else None
        )
        self._imported = _free_beside(app_dir, IMPORTED_NAME, stamp)

    def _preserve_entrypoint(self) -> None:
        """Rename an existing `hyprland.lua` aside, since the new one contests its name.

        A rename, never a delete (ADR-0009), to the name the sentinel already recorded.
        """
        if self._restore is not None:
            os.replace(self.paths.entrypoint, self._restore)

    def _resave_report(self, preview: Preview) -> None:
        """Re-save the Preview's report with the backups this switch made (#268, ADR-0009).

        Same file, so a report read back later names the files of *this* switch rather than
        the generic ones. A report that cannot be re-saved never fails the switch: the
        wizard's pages show the exact names regardless.
        """
        loss = preview.loss
        loss.backup_name = self._restore.name if self._restore is not None else None
        loss.app_dir_backup_name = (
            self._restore_app_dir.name if self._restore_app_dir is not None else None
        )
        loss.imported_name = self._imported.name if self._imported is not None else None
        if self.report_path is None:
            return
        try:
            loss.save(self.paths, path=self.report_path)
        except OSError as error:
            _log.warning("could not re-save the loss report %s: %s", self.report_path, error)

    def _preserve_app_dir(self, bridges: Sequence[BridgeEntry]) -> None:
        """Rename an existing App dir aside, presets and Monitor profiles in it, before the
        switch writes the imported one: written over in place, the user's Modules had no
        copy Roll back could put back (#148 review R1). Named like the Entrypoint's.

        A wired theming tool's output is copied into the new App dir, so its colors keep
        loading after the switch, as they did when the tree was written in place.
        """
        app_dir = self.paths.app_dir
        target = self._restore_app_dir
        if target is None:
            return
        os.replace(app_dir, target)
        # What the user saved in the app is theirs, not the replaced config's (#148 fix
        # review R8). Copied, so the moved-aside dir stays whole for Roll back.
        for name in (PRESETS_DIR, MONITOR_PROFILES_DIR):
            if (target / name).is_dir():
                shutil.copytree(
                    target / name,
                    app_dir / name,
                    symlinks=True,
                    ignore=shutil.ignore_patterns(ACTIVE_NAME),
                )
        for entry in bridges:
            output = self.paths.hypr_dir / entry.file
            kept = (
                target / output.relative_to(app_dir) if output.is_relative_to(app_dir) else None
            )
            if kept is not None and kept.is_file():
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(kept, output)

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
    "RollBackOutcome",
    "Step",
    "SwitchResult",
    "VerifyGate",
    "fresh_start",
]


class _Put(StrEnum):
    """What Roll back does to the Entrypoint (`_entrypoint_plan`)."""

    LEAVE = "leave"
    """The user's own file is in place already."""
    MOVE_BACK = "move-back"
    """The displaced original is moved back from `hyprland.lua.bak`."""
    FROM_BACKUP = "from-backup"
    """That file is gone; the full-tree backup's copy is copied into place."""
    DELETE = "delete"
    """The `.conf` path: the generated Entrypoint goes, `hyprland.conf` takes over."""
    STUCK = "stuck"
    """Neither copy exists: Roll back changes nothing and says so."""


def marker_rescue_command(paths: ConfigPaths, marker: sentinels.Sentinel | None) -> str:
    """The TTY rescue for the switch `marker` records, with the names it made (#268).

    What a relaunched app, which has no Preview, shows beside a Roll back that failed. The
    imported App dir's destination is a name free now, so the rescue never moves into one.
    """
    if marker is None or not marker.known:
        return rescue_command(None)
    stamp = datetime.now(UTC).strftime(backups.STAMP_FORMAT)
    return rescue_command(
        marker.restore is not None,
        backup=Path(marker.restore).name if marker.restore else BACKUP_NAME,
        app_dir_backup=Path(marker.restore_app_dir).name if marker.restore_app_dir else None,
        imported=_free_beside(paths.app_dir, IMPORTED_NAME, stamp).name,
    )


def _sha256(path: Path) -> str | None:
    """The file's `sha256`, or `None` when there is no file to hash."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _free_beside(path: Path, name: str, stamp: str) -> Path:
    """`name` beside `path` if nothing holds it, else `name.<stamp>[-n]`."""
    target = path.with_name(name)
    suffix = 1
    while target.exists() or target.is_symlink():
        target = path.with_name(f"{name}.{stamp}" + (f"-{suffix}" if suffix > 1 else ""))
        suffix += 1
    return target


def _copy_into_place(source: Path, target: Path) -> None:
    """Copy `source` over `target` in one rename; a symlink is recreated as one, as the
    backup took it (`backup.create`)."""
    if source.is_symlink():
        temporary = target.with_name(f".{target.name}.restoring")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(source.readlink())
        os.replace(temporary, target)
        return
    write_atomic(target, source.read_bytes())
    shutil.copymode(source, target)


ROLLED_BACK_DIR = "rolled-back"
"""Where a Roll back keeps the App dir it disowns, one `<timestamp>/` each."""


def _generated_by_this_app(path: Path) -> bool:
    """Whether `path` is a file this app generated: it opens with the app's banner."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return text.startswith(GENERATED_BANNER.split("{", 1)[0])
