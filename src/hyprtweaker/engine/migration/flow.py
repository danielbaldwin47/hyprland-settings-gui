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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from ..importer.loss import RESCUE_LINE, LossReport
from ..importer.lua.mapping import import_lua
from ..importer.lua.sandbox import Consent
from ..importer.mapping import ImportResult, import_config
from ..model import ConfigModel
from ..model.values import lua_string
from ..paths import ConfigPaths
from ..schema import Schema
from ..state.manifest import Manifest
from ..writer import Writer
from ..writer.lua import table_key
from . import backup as backups
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
    and so it is obvious at a glance that migration talks to Hyprland in exactly three ways.
    """

    async def configerrors(self) -> tuple[str, ...]: ...

    async def bind_count(self) -> int: ...

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

    Entity counts are soft for now: the Entity Modules (`binds.lua`, `monitors.lua`, ...)
    are #64 and are not written yet, so a mismatch here reports a known gap in what the app
    can emit rather than evidence that the switch went wrong.
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

    step: Step = Step.DETECT
    detection: Detection | None = None
    preview: Preview | None = None
    backup: backups.Backup | None = None
    report_path: Path | None = None
    _answer: asyncio.Event | None = field(default=None, repr=False)
    _decision: Decision | None = field(default=None, repr=False)

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
        detection = self.detection or self.detect()
        path = source or detection.source
        if path is None:
            raise ValueError("nothing to import: no source file was detected or given")

        if path.suffix == ".lua":
            result = import_lua(path, self.schema, consent=consent or Consent())
        else:
            result = import_config(path, self.schema)

        preview = Preview(detection=replace(detection, source=path), result=result)
        self.preview = preview
        self.step = Step.BACK_UP
        return preview

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

    @contextmanager
    def _staged(self, preview: Preview) -> Iterator[ConfigPaths]:
        """The converted tree, rendered somewhere harmless.

        Staged rather than written in place, because the real Entrypoint *is* the switch:
        writing it to run the gate would leave a live session one manual reload away from a
        config nobody has approved yet. The staged tree is byte-identical to what Switch
        will write, so a verdict on it transfers -- and so does an export of it.
        """
        with tempfile.TemporaryDirectory(prefix="hyprtweaker-staged-") as raw:
            staging = ConfigPaths.rooted_at(Path(raw))
            staging.hypr_dir.mkdir(parents=True, exist_ok=True)
            self._write_tree(preview, staging)
            yield staging

    def stage_and_gate(self) -> VerifyGate:
        """Let Hyprland judge the converted tree before the live engine is touched."""
        preview = self._require_preview()
        if shutil.which("Hyprland") is None:
            return VerifyGate(ran=False, ok=True, output="no Hyprland binary on this machine")

        with self._staged(preview) as staging:
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
        sentinels.write(
            self.paths,
            kind=preview.detection.kind.value,
            source=preview.detection.source,
            backup=self.backup.path if self.backup else None,
            restore=restore,
            now=self.now(),
        )

        self._write_tree(preview, self.paths)
        self._record_provenance(preview)

        await self.client.reload_full_reset()
        checks = await self._verify_live(preview)
        ok = all(check.ok for check in checks if check.hard)
        errors = tuple(
            check.detail for check in checks if not check.ok and check.name == "configerrors"
        )

        self.step = Step.DECIDE
        return SwitchResult(ok=ok, checks=tuple(checks), errors=errors, notes=_SWITCH_NOTES)

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

    async def _verify_live(self, preview: Preview) -> list[Check]:
        """ADR-0009's live checks, spoken over the IPC socket rather than by spawning."""
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

        expected = len(preview.result.entities.binds)
        if expected:
            live = await self.client.bind_count()
            checks.append(
                Check(
                    name="binds",
                    ok=live >= expected,
                    detail=f"{live} live, {expected} imported",
                    hard=False,
                )
            )
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
        """
        record = marker or sentinels.read(self.paths)
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
        if self.client is not None:
            await self.client.reload_full_reset()

    # --- shared -------------------------------------------------------------------------

    @property
    def rescue_line(self) -> str:
        """The TTY escape hatch, printed in every report (ADR-0009)."""
        return RESCUE_LINE

    def _require_preview(self) -> Preview:
        if self.preview is None:
            raise RuntimeError("no preview yet: call build_preview() first")
        return self.preview

    def _preserve_foreign_entrypoint(self, preview: Preview) -> Path | None:
        """Rename a foreign `hyprland.lua` aside, since the new one contests its name.

        A rename, never a delete (ADR-0009), and it happens before the sentinel records it
        so the marker can never name a backup that was not made.
        """
        if preview.detection.kind is not ConfigKind.FOREIGN_LUA:
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

    def _write_tree(self, preview: Preview, paths: ConfigPaths) -> None:
        """Render the imported config into an App dir: `vars`, `legacy`, then the Modules.

        `vars.lua` and `legacy.lua` are written first because the Entrypoint's require list
        is discovered from what is on disk -- write them after, and the file that requires
        them would not mention them.
        """
        result = preview.result
        paths.app_dir.mkdir(parents=True, exist_ok=True)

        if result.variables:
            paths.vars_lua.write_text(_render_vars(result.variables), encoding="utf-8")
        if result.legacy:
            paths.legacy_lua.write_text(result.legacy, encoding="utf-8")

        Writer(paths, app_version=self.app_version).write(result.model)

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
        self.paths.manifest.write_text(stamped.render(), encoding="utf-8")


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


def _verify_config(entrypoint: Path, runtime_dir: Path) -> subprocess.CompletedProcess[str]:
    """`Hyprland --verify-config`, with the caller's own session out of reach.

    `--verify-config` *executes* the config with live bindings, so a run that inherited
    `HYPRLAND_INSTANCE_SIGNATURE` could reach the session the user is sitting in -- the
    static test tier hit exactly this (prototype #30).
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY")
    }
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
