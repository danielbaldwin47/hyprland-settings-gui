"""One run of the app: the Schema, the model, and the live connection behind them.

The window builds widgets; this holds everything they are a view of. Splitting it out is
what lets the whole edit-to-compositor path be tested without a display -- `tests/unit`
drives a `Session` against a scripted socket, and `tests/integration` drives the same object
against a nested Hyprland.

**Toolkit-free on purpose.** Nothing here imports `gi`. Coroutines are handed to a `spawn`
callable the caller supplies, and everything the UI must react to arrives as a plain Python
callback -- the same seam the engine already draws for `EventStream` (ADR-0011).

**Live or read-only, never a third thing.** Instant apply is the whole interaction model
(ADR-0003): there is no Apply button because a change *is* a write plus a reload. With no
compositor to reload there is nothing to make a change mean, and writing anyway would leave
values on disk the next launch cannot read back -- the app would open showing defaults over
a config that says otherwise. So a session with no Hyprland is read-only and says so.

**The transaction draws the gesture boundary, not the widget.** Undo steps are recorded when
a transaction *finishes*, from the values each Option held when its first edit since the last
transaction arrived. That is one decision doing three jobs. A slider drag -- fifty previews
and one commit -- becomes one step spanning press to release, because only the release ends
in a transaction. Four css-gaps spinners typed into in one breath become one step, because
the queue's debounce already coalesced them into one reload, and a gesture the *compositor*
saw as one change is one change. And a gesture that fails becomes no step at all: it is never
pushed, so ADR-0016's "drop the failed gesture from the undo stack (it never becomes a redo)"
holds by construction rather than by remembering to pop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from hyprtweaker.engine import binds_analysis
from hyprtweaker.engine.apply import (
    Action,
    Applier,
    ApplyOutcome,
    ApplyResult,
    Edit,
    Mismatch,
    Problem,
    Recovery,
    ReRead,
    UndoStack,
    UndoStep,
    app_owned_options,
    own_write_modules,
    plan,
    read_state,
)
from hyprtweaker.engine.entities_catalog import (
    IDENTITY_FIELD,
    device_field_bounds,
    overridden_options,
)
from hyprtweaker.engine.ipc import (
    MONITOR_ADDED,
    MONITOR_REMOVED,
    CommandClient,
    EventStream,
    Instance,
    IpcError,
    NoInstance,
)
from hyprtweaker.engine.model import UNSET, ConfigModel, OptionValue
from hyprtweaker.engine.model.entities import (
    Animation,
    Bind,
    Curve,
    Device,
    EnvVar,
    Gesture,
    LayerRule,
    MonitorRule,
    Permission,
    StartupCommand,
    WindowRule,
    WorkspaceRule,
)
from hyprtweaker.engine.paths import (
    ANIMATIONS_MODULE,
    AUTOSTART_MODULE,
    BINDS_MODULE,
    DEVICES_MODULE,
    ENV_MODULE,
    GESTURES_MODULE,
    LAYER_RULES_MODULE,
    MONITORS_MODULE,
    PERMISSIONS_MODULE,
    WINDOW_RULES_MODULE,
    WORKSPACE_RULES_MODULE,
    ConfigPaths,
)
from hyprtweaker.engine.profiles import (
    MonitorProfile,
    MonitorStateSnapshot,
    ProfileStore,
    activated,
    capture,
    connected_outputs,
    drift,
    matches,
)
from hyprtweaker.engine.schema import ResolvedOption, Schema, load_schema
from hyprtweaker.engine.state import Journal, LastKnownGood, Manifest, content_hash
from hyprtweaker.engine.writer import LuaSyntaxError, ModuleSet, ProtectedFile, Writer
from hyprtweaker.engine.writer.binds import parse_binds_module
from hyprtweaker.engine.writer.declarations import parse_declarations_module
from hyprtweaker.engine.writer.monitors import parse_monitors_module
from hyprtweaker.engine.writer.rules import parse_rules_module

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutoRevert:
    """One automatic recovery from the app's own rejected write (ADR-0016 §Auto-revert).

    Reported rather than merely logged, because instant apply has no cancel: the user watched
    a change land in the UI and it is being taken away again, so something has to say so. The
    error lines ride along verbatim so the toast's **Details** action has something to show
    without going back to the compositor -- `configerrors` is cleared by the next reload, and
    the restore transaction *is* the next reload.
    """

    keys: tuple[str, ...]
    """The Options whose values were put back."""

    modules: tuple[str, ...]
    """The App-dir Modules the errors blamed on this transaction's own write."""

    errors: tuple[str, ...]
    """The `configerrors` lines, `file:line` prefixes intact."""

    restored: bool = True
    """Whether the Modules came back byte-for-byte as their pre-write Snapshot.

    False is the escalation ADR-0016 names -- "if the restore transaction itself errors ...
    escalate to the Banner and stop auto-writing until the user acts". The toast still says
    what was attempted; what it must not do is claim a recovery that did not happen."""


@dataclass(frozen=True, slots=True)
class Health:
    """Everything the one Banner shows, decided once (ADR-0016 §Surfacing).

    ADR-0016 allows the app **one** persistent Banner for every unhealthy state there is --
    config errors, an Entrypoint refusal, an active Quarantine -- and this session has a
    fourth that predates it: no compositor, so nothing can be applied at all. One widget
    cannot show four sentences, so something has to rank them, and doing that in the window
    would put the ranking somewhere no test without a display can reach.

    So the judgement is made here and the window renders the answer. That is the same split
    `offline_reason` already drew ("why this session is read-only, in one line fit for the
    Banner"); this is that idea grown to cover the rest of the states.
    """

    offline_reason: str | None = None
    recovery: Recovery = field(default_factory=Recovery)
    quarantined: tuple[str, ...] = ()
    """Requires the Entrypoint is currently leaving out. Unhealthy on its own: a disabled
    `user.lua` is a config that is not doing what its owner wrote, and the Banner is the only
    thing standing between that and a very confusing afternoon."""

    halted: bool = False
    """Automatic recovery has failed and stopped (`Session.recovery_halted`)."""

    unapplied: tuple[str, ...] = ()
    """Keys written that the live config does not set, with nothing to explain it.

    ADR-0016: "An unexplained read-back mismatch (value didn't take, no error, no override)
    badges the Row 'didn't apply' **and joins the Banner**." This is the joining-the-Banner
    half, and it needs its own field because it is an unhealthy state with no `configerrors`
    behind it at all -- nothing in `recovery` could derive it."""

    rescued: tuple[str, ...] = ()
    """Modules the emergency restore overwrote without asking (ADR-0016 §Zero-binds).

    Reported because the ADR requires it: the overwritten hand edit "is preserved in the
    Journal **and reported in the Banner**". Quietly keeping a user's edit and quietly taking
    it are not the same promise, and only the second one needs announcing."""

    @property
    def unhealthy(self) -> bool:
        """Whether the Banner shows at all."""
        return bool(
            self.offline_reason
            or self.recovery.unhealthy
            or self.quarantined
            or self.halted
            or self.unapplied
            or self.rescued
        )

    @property
    def severe(self) -> bool:
        """Whether the Banner should read as an error rather than as a warning.

        Reserved for the three states where the config is not doing what the user believes
        it is doing: the compositor refused the Entrypoint and is running the *previous*
        config, the user has no keybinds, or the app has given up repairing things itself.
        An ordinary rejected value is loud enough as a plain Banner.
        """
        return self.recovery.entrypoint_refused or self.recovery.stranded or self.halted

    @property
    def title(self) -> str:
        """The Banner's one line. Empty when there is nothing wrong.

        Ordered by what the user most needs to know, not by which check ran first. Being
        stranded outranks everything because it is the state they cannot get themselves out
        of; read-only outranks the rest because nothing else can be acted on while it holds.
        """
        if self.recovery.stranded:
            return f"Your keybinds are not loaded — {self._first_error}"
        if self.offline_reason is not None:
            return f"{self.offline_reason} — settings are read-only."
        if self.recovery.entrypoint_refused:
            return "Hyprland rejected the last write and is running the previous config."
        if self.halted:
            return "Hyprland rejected a change, and the app could not put it back."
        if self.recovery.unhealthy:
            return "Hyprland reported a problem with your config."
        if self.rescued:
            # Ranked *below* the error line, not above it. A rescue that did not fix things
            # leaves both true at once, and in that case the live problem is what the user
            # needs -- the alternative pairs a reassuring title with a Details button opening
            # a dialog full of errors it never mentioned.
            files = ", ".join(name.rsplit("/", 1)[-1] for name in self.rescued)
            return (
                f"Restored {files} so your keybinds would load again. "
                f"Your edited version is saved in this app's history."
            )
        if self.unapplied:
            return f"{self._unapplied_summary} was written but did not take effect."
        if self.quarantined:
            disabled = ", ".join(f"{name}.lua" for name in self.quarantined)
            return f"{disabled} is disabled until you fix it."
        return ""

    @property
    def _unapplied_summary(self) -> str:
        """One key named, several counted -- the same rule the undo toast uses for gestures."""
        if len(self.unapplied) == 1:
            return self.unapplied[0]
        return f"{len(self.unapplied)} settings"

    @property
    def button(self) -> str | None:
        """What the Banner's button says, or `None` when it has nothing to open.

        A Banner with no errors behind it -- a Quarantine the user has already dealt with,
        or a session with no compositor -- gets no button rather than one opening an empty
        dialog.
        """
        if self.recovery.unhealthy:
            return "Details"
        if self.quarantined:
            return "Re-enable"
        return None

    @property
    def _first_error(self) -> str:
        """The line to name in a stranded Banner: `user.lua:12`, not the whole message.

        ADR-0016 spells this one out ("error in user.lua:12"), and the reason is that a
        stranded user is reading the Banner off a screen they cannot navigate away from.
        """
        for problem in self.recovery.problems:
            if not problem.path:
                continue
            name = problem.path.rsplit("/", 1)[-1]
            line = problem.line
            return f"error in {name}:{line}" if line is not None else f"error in {name}"
        return "the config could not be loaded."


Spawn = Callable[[Coroutine[Any, Any, None]], None]
"""How this session gets a coroutine running. The GTK app passes the main loop's own
scheduler, so engine callbacks land on the thread that owns the widgets."""

_NOT_CONNECTED_YET = "Connecting to Hyprland…"
"""The reason a session is read-only between construction and `start()` finishing.

A reason rather than a fourth state: the Rows are genuinely not editable yet, and a Banner
that says why is better than one that appears a moment later."""


class Session:
    """The app's state for one run, from schema load to a clean shutdown."""

    def __init__(
        self,
        *,
        spawn: Spawn,
        schema: Schema | None = None,
        paths: ConfigPaths | None = None,
        app_version: str,
        connect: Callable[[], Instance] = Instance.current,
    ) -> None:
        """`connect` names the compositor to talk to; by default, the one we run under.

        Injected for the same reason the Harness can drive the real `Applier`: an `Instance`
        is a frozen dataclass over a socket directory, so a nested Hyprland or a scripted
        pair of sockets is a first-class session and needs no monkeypatching.
        """
        self._spawn = spawn
        self._schema = schema if schema is not None else load_schema()
        self._paths = paths if paths is not None else ConfigPaths.default()
        self._app_version = app_version
        self._connect = connect

        self.on_state_changed: Callable[[], None] | None = None
        """Called when the model or the connection changed under the UI's feet -- the
        startup re-read, a foreign reload, a compositor that went away. The window's cue to
        make every control agree with the model again.

        Assigned after construction rather than injected, because the window it talks to
        needs this session to exist before *it* can."""

        self.on_applied: Callable[[ApplyResult], None] | None = None
        """Called once per finished Apply transaction, successful or not.

        Not called for a transaction that is being auto-reverted: that one is reported
        through `on_reverted` instead, so the user gets the one toast that says what happened
        rather than a failure toast chased by a recovery toast."""

        self.on_reverted: Callable[[AutoRevert], None] | None = None
        """Called when the app has just taken back its own rejected write (ADR-0016)."""

        self.on_recorded: Callable[[UndoStep], None] | None = None
        """Called with the gesture a finished transaction put on the undo stack.

        The step is handed over rather than left for the window to read off the stack top.
        Those are not the same thing: a transaction that recorded nothing -- an undo's own
        write, or one whose rendered bytes were already on disk -- would otherwise have the
        window offer to take back whatever gesture happened to be underneath, which is a
        gesture the user did not just make."""

        self._model = ConfigModel(self._schema)
        self._writer = Writer(self._paths, app_version=app_version)
        self._journal = Journal(self._paths)

        self._events: EventStream | None = None
        self._client: CommandClient | None = None
        self._applier: Applier | None = None
        self._offline_reason: str | None = _NOT_CONNECTED_YET
        self._closing = False
        self._pending_restart: set[str] = set()
        self._monitor_watchers: list[Callable[[], None]] = []
        """Who wants to hear about display hotplug -- the Monitors page's canvas (#68).

        A list of the session's own rather than a raw `EventStream.subscribe`, because
        watchers register before the stream exists (the window builds against a session
        that has not connected yet) and must survive it never existing at all."""

        self._profiles: ProfileStore | None = None
        """The Monitor-profile store, built lazily over `monitor-profiles/` (#69)."""

        self._undo = UndoStack()
        self._open_gestures: dict[str, OptionValue] = {}
        """Per Option, what it held when the current gesture's first edit arrived.

        Opened by any edit that finds no entry, closed by the transaction that carries the
        key. Between those two moments the Option is mid-gesture, however many model writes
        the widget makes -- which is what turns fifty slider ticks into one undo step."""

        self._reverting = False
        self._recovery_halted = False
        self._recovery = Recovery()
        """What the last reload said was wrong, attributed. The Banner is a view of this.

        Replaced wholesale by every reload the app hears about -- its own transactions, and
        the startup and foreign-reload re-reads -- because `configerrors` is itself replaced
        wholesale: it describes the *last* parse and nothing older. Accumulating would leave
        the Banner naming a file the user fixed two reloads ago."""

        self._restoring = False
        """Whether a Restore last good is in flight, so a second cannot start on top of it."""

        self._unapplied: tuple[str, ...] = ()
        """Keys the last transaction wrote that the live config does not set.

        ADR-0016's "unexplained read-back mismatch (value didn't take, no error, no
        override)", which "joins the Banner". A quiet value disagreement is not this: that is
        usually `user.lua` winning the override order on purpose, and the drift badge's
        business. `Mismatch.unapplied` is the loud shape -- the model sets the key and the
        live config sets nothing, which means the Module never ran."""

        self._overridden: tuple[str, ...] = ()
        """Keys whose live value disagrees with the model because something later won.

        The quiet counterpart of `_unapplied`: the value did not take, but for a reason the
        app can name. ADR-0005 fixes the mechanism -- "after each reload the app compares
        `get_config`/`getoption` against its model and badges diverging options" -- so this
        comes from the Read-back the transaction already does, never from reading
        `user.lua` itself. ADR-0018 rejects that: running the user's own code to answer a
        question about a badge is consent-and-safety weight no badge earns."""

        self._rescued: tuple[str, ...] = ()
        """Modules the emergency restore overwrote without asking, so the Banner can say so."""

        self._pending_rescue: tuple[str, ...] = ()
        """A rescue announced only once its own restore has been observed -- see
        `_emergency_restore`, which explains why it cannot be announced any earlier."""

        self._repolled = False
        """Whether the current timeout has already been re-polled once (ADR-0016 §Timeout).

        Once, not until it succeeds: a compositor that is not answering will not start
        because the app asked twice, and a retry loop against a busy Hyprland is how an app
        turns a slow reload into a hang."""

    # --- what the UI reads ------------------------------------------------------------------

    @property
    def schema(self) -> Schema:
        return self._schema

    @property
    def model(self) -> ConfigModel:
        return self._model

    @property
    def journal(self) -> Journal:
        """Snapshots and the change log for this install (ADR-0010 §Rollback, ADR-0016).

        Held by the session rather than built per transaction, so "what is this Module's Last
        known good?" has one answer whoever asks -- auto-revert now, the Banner's
        Restore last good now.
        """
        return self._journal

    @property
    def live(self) -> bool:
        """Whether edits reach a running compositor. False means the Rows are read-only."""
        return self._offline_reason is None

    @property
    def offline_reason(self) -> str | None:
        """Why this session is read-only, in one line fit for the Banner."""
        return self._offline_reason

    @property
    def unapplied(self) -> frozenset[str]:
        """Keys the last transaction wrote that the live config does not set.

        The Row badge ADR-0016 carves out of "errors never appear on Rows": an unexplained
        read-back mismatch is *key*-scoped, unlike a config error, so it is the one thing
        error surfacing has to say on the Row itself as well as on the Banner.

        Replaced per transaction rather than accumulated, for the same reason `configerrors`
        is: it describes the last write, and a badge that outlived the write that earned it
        would be telling the user about a value that has since applied perfectly well.
        """
        return frozenset(self._unapplied)

    @property
    def overridden(self) -> frozenset[str]:
        """Keys the live config sets to something other than what the model asked for.

        `user.lua` is required last (ADR-0005), so a key it sets beats the Module the app
        wrote -- the Row wears the "Overridden" pill rather than pretending the edit took.
        Replaced per transaction for the same reason `unapplied` is: it describes the last
        write, and a badge outliving the write that earned it would be a lie about a value
        that has since applied perfectly well.
        """
        return frozenset(self._overridden)

    @property
    def paths(self) -> ConfigPaths:
        """Where the config lives. What the Banner's Open file action needs."""
        return self._paths

    @property
    def app_version(self) -> str:
        """The running app's version, as it is stamped into generated files.

        Exposed because the Migration wizard builds its own Writer and Manifest reader: it
        runs before -- and sometimes instead of -- a live model, so it cannot take the one
        this Session made.
        """
        return self._app_version

    @property
    def recovery(self) -> Recovery:
        """The last reload's problems and what may be done about each (ADR-0016)."""
        return self._recovery

    @property
    def health(self) -> Health:
        """The whole of what the one Banner shows, right now.

        Assembled per call rather than cached: it is four cheap reads, and a cached copy
        would be a fifth piece of state to keep in step with the four it summarises.
        """
        return Health(
            offline_reason=self._offline_reason,
            recovery=self._recovery,
            quarantined=self.quarantined,
            halted=self._recovery_halted,
            unapplied=self._unapplied,
            rescued=self._rescued,
        )

    @property
    def quarantined(self) -> tuple[str, ...]:
        """Requires the Entrypoint is currently leaving out (ADR-0016 §Quarantine).

        Read off the Manifest rather than held, because the Manifest is where the decision
        lives -- and because the file is the thing the next write will consult, so a cached
        copy could disagree with what the Entrypoint actually says.
        """
        return self._manifest().quarantined

    @property
    def pending_restart(self) -> frozenset[str]:
        """Options this session wrote that need a restart before they take effect.

        "Applied to file, effective after Hyprland restart" (`CONTEXT.md`), which is a claim
        about a *file*: only keys an Apply transaction actually laid down get in here, never
        keys that were merely edited and refused. The Row badges from this (ADR-0013).

        Accumulated for the life of the session and never cleared, because the event that
        would clear it -- Hyprland restarting -- takes the session with it: the event stream
        drops and the session goes read-only. A `hyprctl reload` is *not* that event, and
        forgetting on one would tell the user a pending change had landed when it had not.
        """
        return frozenset(self._pending_restart)

    def value_of(self, option: ResolvedOption) -> OptionValue:
        """The model's value: a value, `None` for explicit null, or `UNSET`."""
        return self._model.get(option.name)

    def effective_value(self, option: ResolvedOption) -> Any:
        """What Hyprland is currently doing: the set value, else its own default.

        An Unset Option is not blank -- the compositor applies its default, and a control
        that renders empty would state that the setting has no value (prototype #8's
        blank-row defect).

        Not the same question as "what should the Row display", which is
        `ui/rows/state.shown_value` and differs in one deliberate way: this answers `None`
        or a sentinel-shaped value verbatim, because a dependency asking "is the controlling
        Option set to `custom`?" wants the raw comparison. A control asking what to *show*
        wants "Device default", and folding those two into one function would make one of
        them lie.
        """
        value = self._model.get(option.name)
        return option.default if value is UNSET else value

    def is_modified(self, option: ResolvedOption) -> bool:
        """Whether the model emits this Option at all -- ADR-0005's tri-state, not `!=`."""
        return self._model.is_set(option.name)

    # --- what the UI writes -----------------------------------------------------------------

    def set_option(self, name: str, value: Any) -> None:
        """A decided edit: into the model, then applied as soon as the queue is free."""
        if self._refuse(name):
            return
        self._begin_edit(name)
        self._model.set(name, value)
        self._applier.commit(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def unset_option(self, name: str) -> None:
        """Reset to Hyprland's default: stop emitting the Option (ADR-0013 §6)."""
        if self._refuse(name):
            return
        self._begin_edit(name)
        self._model.unset(name)
        self._applier.commit(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def touch_option(self, name: str, value: Any) -> None:
        """A mid-gesture edit that still writes -- a spin button being typed into.

        Applied once the changes stop (~150 ms). The discrete half of "mid-gesture": a
        keystroke burst is a handful of edits, so coalescing them into one transaction is
        enough and the value is durable the moment the user stops. A *continuous* gesture is
        `preview_option`, because fifty ticks a second is fifty reloads however well they
        coalesce.
        """
        if self._refuse(name):
            return
        self._begin_edit(name)
        self._model.set(name, value)
        self._applier.touch(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def preview_option(self, name: str, value: Any) -> None:
        """One tick of a continuous gesture: into the model, echoed over the socket, unwritten.

        The Eval preview tier (ADR-0010). Nothing reaches disk and no reload is issued -- the
        value is shown by `eval`, which is sub-frame and transient. What makes it durable is
        the single Apply transaction the gesture's release commits, through `set_option`.

        Between the tick and the release the model is deliberately ahead of the file, and
        that window is honest rather than a gap: the model is what the app is *about* to
        write, the Row shows it, and a reload arriving mid-drag wipes the preview and drags
        the model back to the truth through the foreign-reload re-read. Nothing else in the
        app has to know a gesture is in progress.
        """
        if self._refuse(name):
            return
        self._begin_edit(name)
        self._model.set(name, value)
        self._applier.preview(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def _begin_edit(self, name: str) -> None:
        """Remember what this Option held before the current gesture started.

        Idempotent per gesture, which is the whole mechanism: the *first* edit records the
        value at press, every later one finds an entry and leaves it alone, and the
        transaction that carries the key takes it (`_close`). So the delta an undo step is
        built from is press-to-release rather than tick-to-tick, with nothing in the widget
        layer having to know when a gesture began.
        """
        if name not in self._open_gestures:
            self._open_gestures[name] = self._model.get(name)

    def edit_binds(self, mutate: Callable[[list[Bind]], None]) -> bool:
        """Change the Bind list and write it, returning whether the edit was accepted.

        `mutate` is handed the live list because for Binds position *is* identity
        (ADR-0007): adding is an append at a chosen index, reordering is a move, and there
        is no key to address a bind by. Duplicates are legal, so nothing here de-duplicates.

        Returns `False` on a read-only session, for the same reason `_refuse` exists -- a
        model holding binds that were never written would show them in the list, survive a
        re-read, and get written later without the user asking again.

        Not on the undo stack: undo is keyed by Option name end to end (`_open_gestures`,
        `UndoStep`), and giving Entities a place on it is its own piece of work rather than
        a line here. Tracked as a leftover on the inbox issue.
        """
        if self._refuse("binds"):
            return False
        mutate(self._model.entities.binds)
        self._applier.commit_entities()  # type: ignore[union-attr]  # _refuse proved it is here
        return True

    def add_bind(self, bind: Bind) -> bool:
        """Append a Bind. `hl.bind` appends, so the end of the list is where a new one goes."""
        return self.edit_binds(lambda binds: binds.append(bind))

    def replace_bind(self, index: int, bind: Bind) -> bool:
        """Replace the Bind at `index`, keeping its position.

        In place rather than remove-and-append: position *is* identity, so a bind that
        jumped to the end of the list would change which of two duplicates fires first.
        """

        def swap(binds: list[Bind]) -> None:
            if 0 <= index < len(binds):
                binds[index] = bind

        return self.edit_binds(swap)

    def remove_bind(self, index: int) -> bool:
        """Delete the Bind at `index`."""

        def drop(binds: list[Bind]) -> None:
            if 0 <= index < len(binds):
                del binds[index]

        return self.edit_binds(drop)

    def set_bind_enabled(self, index: int, enabled: bool) -> bool:
        """Enable or disable the Bind at `index`, in place.

        The conflict surface's "disable it" (ADR-0007, #66). In place because the point of
        `enabled` over deletion is exactly that nothing moves: every other bind keeps its
        position, and re-enabling restores the world as it was.
        """

        def flip(binds: list[Bind]) -> None:
            if 0 <= index < len(binds):
                binds[index] = replace(binds[index], enabled=enabled)

        return self.edit_binds(flip)

    def swap_binds(self, first: int, second: int) -> bool:
        """Exchange the positions of two Binds -- which of two duplicates fires first.

        A swap rather than a general move because that is what fire order among duplicates
        *is* (ADR-0007): the conflict badge says "fires 1st", and this is the control that
        makes it say otherwise. Everything between the two stays put.
        """

        def exchange(binds: list[Bind]) -> None:
            if 0 <= first < len(binds) and 0 <= second < len(binds) and first != second:
                binds[first], binds[second] = binds[second], binds[first]

        return self.edit_binds(exchange)

    def save_submap(self, *, original: str | None, name: str, reset_target: str) -> bool:
        """Create a Submap, or rename one and retune its reset target (#66).

        The cascade semantics live in `engine.binds_analysis.save_submap`, where they are
        tested headless; this is only the write gate around them, shaped like `edit_binds`.
        """
        if self._refuse("submaps"):
            return False

        binds_analysis.save_submap(
            self._model.entities, original=original, name=name, reset_target=reset_target
        )
        self._applier.commit_entities()  # type: ignore[union-attr]  # _refuse proved it is here
        return True

    def rules(self, kind: str) -> list[WindowRule] | list[LayerRule]:
        """The live rule list for a kind -- `"window"` or `"layer"`.

        One accessor rather than two properties because every caller is already
        parameterised by kind: the two Pages are the same class twice (ADR-0008: "same
        list model and editor shell").
        """
        if kind == "window":
            return self._model.entities.window_rules
        if kind == "layer":
            return self._model.entities.layer_rules
        raise ValueError(f"unknown rule kind {kind!r}")

    def edit_rules(self, kind: str, mutate: Callable[[list[Any]], None]) -> bool:
        """Change a rule list and write it, returning whether the edit was accepted.

        `mutate` is handed the live list because for Rules position *is* identity
        (ADR-0008): later rules win per Effect, and there is no key to address an
        anonymous rule by. Shaped exactly like `edit_binds`, refusal and all, and like it
        deliberately not on the undo stack (the same leftover).
        """
        if self._refuse(f"{kind} rules"):
            return False
        mutate(self.rules(kind))
        self._applier.commit_entities()  # type: ignore[union-attr]  # _refuse proved it is here
        return True

    def add_rule(self, kind: str, rule: WindowRule | LayerRule) -> bool:
        """Append a Rule -- last, where it wins over everything it conflicts with."""
        return self.edit_rules(kind, lambda rules: rules.append(rule))

    def replace_rule(self, kind: str, index: int, rule: WindowRule | LayerRule) -> bool:
        """Replace the Rule at `index`, keeping its position."""

        def swap(rules: list[Any]) -> None:
            if 0 <= index < len(rules):
                rules[index] = rule

        return self.edit_rules(kind, swap)

    def remove_rule(self, kind: str, index: int) -> bool:
        """Delete the Rule at `index`."""

        def drop(rules: list[Any]) -> None:
            if 0 <= index < len(rules):
                del rules[index]

        return self.edit_rules(kind, drop)

    def set_rule_enabled(self, kind: str, index: int, enabled: bool) -> bool:
        """Enable or disable the Rule at `index`, in place.

        The point of `enabled` over deletion is that nothing moves (ADR-0008): the rule
        stays in the file at its position, and re-enabling restores the world as it was.
        """

        def flip(rules: list[Any]) -> None:
            if 0 <= index < len(rules):
                rules[index] = replace(rules[index], enabled=enabled)

        return self.edit_rules(kind, flip)

    def move_rule(self, kind: str, index: int, to: int) -> bool:
        """Move the Rule at `index` to position `to` -- the drag reorder (ADR-0008).

        A move rather than a swap because that is what dragging *is*: everything between
        the two positions shifts by one, which is exactly how the user read the gesture.
        """

        def shift(rules: list[Any]) -> None:
            if 0 <= index < len(rules) and 0 <= to < len(rules) and index != to:
                rules.insert(to, rules.pop(index))

        return self.edit_rules(kind, shift)

    # --- monitor rules ----------------------------------------------------------------------

    def _commit_entity_edit(self, what: str, mutate: Callable[[], None]) -> bool:
        """The write gate every monitor and workspace rule edit shares.

        Refuse on a read-only session (leaving the model alone, `_refuse`), run the
        mutation, commit one entity transaction. Extracted so the keyed edits below --
        which address rules by identity string rather than through a list -- do not each
        hand-copy the refuse/commit envelope. Like the bind and rule edits, deliberately
        not on the undo stack (the same Entity-undo leftover); undo matters least here,
        because display-breaking edits ride Confirm-or-revert, its own take-back.
        """
        if self._refuse(what):
            return False
        mutate()
        self._applier.commit_entities()  # type: ignore[union-attr]  # _refuse proved it is here
        return True

    @property
    def monitor_rules(self) -> list[MonitorRule]:
        """The live monitor rule list. Identity is the `output` string (ADR-0008)."""
        return self._model.entities.monitors

    def edit_monitor_rules(self, mutate: Callable[[list[MonitorRule]], None]) -> bool:
        """Change the monitor rule list and write it, returning whether it was accepted."""
        return self._commit_entity_edit(
            "monitor rules", lambda: mutate(self._model.entities.monitors)
        )

    def patch_monitor_rule(self, output: str, fields: Mapping[str, Any]) -> bool:
        """Merge `fields` into the rule for `output`, creating it if it has none.

        A merge because that is what `hl.monitor` itself does (`lua-api-surface.md` §3):
        the per-monitor rows each own one field, and a row that replaced the whole rule
        would erase every sibling's value on each toggle.
        """
        return self._commit_entity_edit(
            "monitor rules",
            lambda: self._model.entities.add_monitor_rule(
                MonitorRule(output=output, fields=dict(fields)), merge=True
            ),
        )

    def rename_monitor_rule(self, output: str, to: str) -> bool:
        """Change a rule's identity string, keeping its fields and position.

        The "Match by" toggle (ADR-0008): the same rule addressed as `desc:<description>`
        or as the port. Refused when `to` already names a rule -- silently fusing two
        rules the user meant as distinct would discard one of them -- and a no-op rename
        is accepted without a write.
        """
        if output == to:
            return True
        rules = self._model.entities.monitors
        if any(rule.output == to for rule in rules):
            return False
        index = next((i for i, rule in enumerate(rules) if rule.output == output), None)
        if index is None:
            return False

        def rename() -> None:
            rules[index] = replace(rules[index], output=to)

        return self._commit_entity_edit("monitor rules", rename)

    def remove_monitor_rule(self, output: str) -> bool:
        """Delete the rule whose identity is `output`."""

        def drop() -> None:
            rules = self._model.entities.monitors
            rules[:] = [rule for rule in rules if rule.output != output]

        return self._commit_entity_edit("monitor rules", drop)

    def monitor_snapshot(self) -> tuple[MonitorRule, ...]:
        """The monitor rule list as it stands -- what Confirm-or-revert restores to.

        A tuple of frozen dataclasses, so the snapshot cannot drift while the countdown
        runs however many edits land in between.
        """
        return tuple(self._model.entities.monitors)

    def restore_monitor_rules(self, snapshot: Sequence[MonitorRule]) -> bool:
        """Put the monitor rule list back to `snapshot`, through a normal transaction.

        The revert half of Confirm-or-revert (ADR-0008): a normal Apply rather than a file
        restore, because rendering the previous model produces the previous `monitors.lua`
        byte for byte -- same renderer, same input -- and a second way for bytes to reach
        the App dir would be a second place for bugs to live (ADR-0010 made the same call
        for undo).
        """

        def put_back(rules: list[MonitorRule]) -> None:
            rules[:] = list(snapshot)

        return self.edit_monitor_rules(put_back)

    def watch_monitors(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call `callback` on every display hotplug; returns the way to stop.

        The Monitors page's refresh cue (ADR-0008: "hotplug refreshes the canvas"). The
        callback runs on the main loop -- socket2 dispatch shares it under PyGObject's
        asyncio integration -- and carries no payload: the page re-fetches `monitors`
        wholesale, because added-vs-removed tells it nothing the fresh list does not.
        """
        self._monitor_watchers.append(callback)

        def unwatch() -> None:
            with suppress(ValueError):
                self._monitor_watchers.remove(callback)

        return unwatch

    def _on_monitor_hotplug(self, _event: Any) -> None:
        if self._closing:
            return
        for watcher in list(self._monitor_watchers):
            watcher()

    # --- workspace rules --------------------------------------------------------------------

    @property
    def workspace_rules(self) -> list[WorkspaceRule]:
        """The live workspace rule list. Identity is the selector string (ADR-0008)."""
        return self._model.entities.workspace_rules

    def edit_workspace_rules(self, mutate: Callable[[list[WorkspaceRule]], None]) -> bool:
        """Change the workspace rule list and write it. Shaped like `edit_monitor_rules`."""
        return self._commit_entity_edit(
            "workspace rules", lambda: mutate(self._model.entities.workspace_rules)
        )

    def save_workspace_rule(self, rule: WorkspaceRule, *, original: str | None = None) -> bool:
        """Add a workspace rule, or replace the one whose selector was `original`.

        One rule per selector, enforced here rather than trusted to the UI (ADR-0008:
        Hyprland merges duplicates, so a second row would be a lie). Saving onto a
        selector another rule already holds is refused -- the UI's move is to focus the
        existing row (ADR-0008), and silently fusing two rules would discard one. The
        session seam for the Workspaces page (its own ticket); the writer-level merge is
        gated separately by the golden tests.
        """
        rules = self._model.entities.workspace_rules
        if rule.workspace != original and any(
            existing.workspace == rule.workspace for existing in rules
        ):
            return False

        def save() -> None:
            for index, existing in enumerate(rules):
                if existing.workspace == original:
                    rules[index] = rule
                    return
            rules.append(rule)

        return self._commit_entity_edit("workspace rules", save)

    def remove_workspace_rule(self, selector: str) -> bool:
        """Delete the rule whose identity is `selector`."""

        def drop() -> None:
            rules = self._model.entities.workspace_rules
            rules[:] = [rule for rule in rules if rule.workspace != selector]

        return self._commit_entity_edit("workspace rules", drop)

    # --- declarative entities (#70) -----------------------------------------------------

    DECLARATION_KINDS: tuple[str, ...] = (
        "curves",
        "animations",
        "gestures",
        "devices",
        "env",
        "permissions",
        "startup",
    )
    """The Entity kinds the one generic list API below serves, named as `EntitySet` names.

    One parameterised API rather than seven copies of `edit_rules`, for the reason
    `rules(kind)` gives: every caller is already parameterised by kind, because the seven
    Pages are one Page class seven times over a field catalogue. The names are `EntitySet`'s
    own attribute names so this list and that dataclass cannot drift into disagreeing about
    what a kind is called.
    """

    def declarations(self, kind: str) -> list[Any]:
        """The live list for one declarative Entity kind."""
        if kind not in self.DECLARATION_KINDS:
            raise ValueError(f"unknown declaration kind {kind!r}")
        entities: list[Any] = getattr(self._model.entities, kind)
        return entities

    def edit_declarations(self, kind: str, mutate: Callable[[list[Any]], None]) -> bool:
        """Change one declarative list and write it, returning whether it was accepted.

        Shaped exactly like `edit_rules`, refusal and all, and like it deliberately not on
        the undo stack -- the same Entity-undo leftover.
        """
        return self._commit_entity_edit(kind, lambda: mutate(self.declarations(kind)))

    def identity_of(self, kind: str, entity: Any) -> str | None:
        """The identity string of one entity, or `None` for a kind that has no identity."""
        attribute = IDENTITY_FIELD.get(kind)
        return None if attribute is None else str(getattr(entity, attribute))

    def _identity_taken(self, kind: str, entity: Any, *, index: int | None) -> bool:
        """Whether saving `entity` would give two rows the same identity."""
        identity = self.identity_of(kind, entity)
        if identity is None:
            return False
        return any(
            position != index and self.identity_of(kind, existing) == identity
            for position, existing in enumerate(self.declarations(kind))
        )

    def add_declaration(self, kind: str, entity: Any) -> bool:
        """Append one entity, refusing an identity another row already holds.

        Refused rather than merged: merging is what Hyprland would do, and doing it
        silently would make the row the user just filled in vanish into one further up the
        list. The Page's move is to focus the existing row, exactly as `save_workspace_rule`
        expects of the Workspaces page.
        """
        if self._identity_taken(kind, entity, index=None):
            return False
        return self.edit_declarations(kind, lambda items: items.append(entity))

    def replace_declaration(self, kind: str, index: int, entity: Any) -> bool:
        """Replace the entity at `index`, keeping its position."""
        if self._identity_taken(kind, entity, index=index):
            return False

        def swap(items: list[Any]) -> None:
            if 0 <= index < len(items):
                items[index] = entity

        return self.edit_declarations(kind, swap)

    def remove_declaration(self, kind: str, index: int) -> bool:
        """Delete the entity at `index`."""

        def drop(items: list[Any]) -> None:
            if 0 <= index < len(items):
                del items[index]

        return self.edit_declarations(kind, drop)

    @property
    def curves(self) -> list[Curve]:
        """The live curve list. Identity is the name (`hl.curve` overwrites by it).

        The one declarative kind with a named accessor, because it has a caller that is not
        a Page: the animation editor's curve picker, which needs the curves while showing
        the animations. Everything else goes through `declarations(kind)` -- a property per
        kind would be seven more names for what one parameterised call already answers.
        """
        return self._model.entities.curves

    @property
    def device_overrides(self) -> dict[str, tuple[str, ...]]:
        """Which Options a per-device override shadows, and the devices that shadow them.

        The Row's `device-override` badge (ADR-0013, CONTEXT.md). Derived on each read
        rather than cached because the devices list is short, the Schema is fixed for the
        session, and a cache would need invalidating from every device edit -- a stale
        badge here says "your setting is being overridden" about a device the user just
        deleted, which is worse than recomputing a dictionary.
        """
        return overridden_options(
            self._model.entities.devices,
            (option.name for option in self._schema.options),
        )

    @property
    def device_field_bounds(self) -> dict[str, tuple[float | None, float | None]]:
        """The min/max each per-device field inherits from the Options it shadows.

        Read by the device editor so a per-device number is bounded by the same range as
        the global setting it overrides -- the "type-correct per the Schema" half of #70,
        and derived rather than curated so a Hyprland release moves both at once.
        """
        return device_field_bounds(
            {
                option.name: (option.range.min, option.range.max)
                for option in self._schema.options
                if option.range is not None
            }
        )

    # --- monitor profiles -------------------------------------------------------------------

    @property
    def _profile_store(self) -> ProfileStore:
        store = self._profiles
        if store is None:
            store = self._profiles = ProfileStore(self._paths.monitor_profiles_dir)
        return store

    def monitor_profiles(self) -> tuple[tuple[str, MonitorProfile], ...]:
        """Every saved profile as `(slug, profile)`, sorted by name (ADR-0015)."""
        return self._profile_store.list()

    def save_monitor_profile(
        self, name: str, connected: Sequence[Mapping[str, Any]] = ()
    ) -> str:
        """Capture the current display setup as a new profile, returning its slug.

        Allowed on a read-only session -- a capture is a JSON file in the App dir, not a
        config write, and "save what I have before experimenting" is most valuable
        exactly when things are fragile. `connected` is the live `hyprctl -j monitors`
        answer, helper data used as ADR-0008 allows: to fingerprint, never to
        reconstruct rule state.
        """
        return self._profile_store.save(
            capture(
                name,
                monitors=self._model.entities.monitors,
                workspace_rules=self._model.entities.workspace_rules,
                connected=connected_outputs(connected),
            )
        )

    def activate_monitor_profile(self, slug: str) -> bool:
        """Render a profile into the canonical Modules, in one Apply transaction.

        The monitor list is replaced wholesale and every pinned workspace rule patched,
        through the same `_commit_entity_edit` envelope as any other entity edit -- one
        mutation, one `commit_entities`, so `monitors.lua` and `workspace_rules.lua`
        change together or not at all (ADR-0015: "one normal Apply transaction").
        Confirm-or-revert is the caller's wrapper, exactly as for a breaking field edit.
        """
        profile = self._profile_store.load(slug)
        if profile is None:
            return False
        monitors, workspaces = activated(
            profile, workspace_rules=self._model.entities.workspace_rules
        )
        return self._put_monitor_state(monitors, workspaces, slug)

    def _put_monitor_state(
        self,
        monitors: Sequence[MonitorRule],
        workspaces: Sequence[WorkspaceRule],
        active: str | None,
    ) -> bool:
        """One transaction over both lists, then the pointer -- activation and its revert.

        The pointer moves only after the commit is accepted, so a refused write never
        claims a profile the files do not show.
        """

        def put() -> None:
            self._model.entities.monitors[:] = list(monitors)
            self._model.entities.workspace_rules[:] = list(workspaces)

        if not self._commit_entity_edit("monitor profile", put):
            return False
        self._profile_store.set_active(active)
        return True

    def active_monitor_profile(self) -> tuple[str, MonitorProfile] | None:
        """The profile the config on disk is, per the pointer -- or `None`."""
        slug = self._profile_store.active_slug()
        if slug is None:
            return None
        profile = self._profile_store.load(slug)
        return None if profile is None else (slug, profile)

    def monitor_profile_drift(self) -> bool:
        """Whether the active profile and reality disagree -- the drift badge's condition.

        True exactly when activating the profile again would change something, so the
        badge clears on re-activation and on "Update profile", and a hand edit to
        `monitors.lua` shows up the moment the file is re-read (ADR-0015).
        """
        active = self.active_monitor_profile()
        if active is None:
            return False
        _, profile = active
        return drift(
            profile,
            monitors=self._model.entities.monitors,
            workspace_rules=self._model.entities.workspace_rules,
        )

    def update_monitor_profile(
        self, slug: str, connected: Sequence[Mapping[str, Any]] = ()
    ) -> bool:
        """Recapture the current setup over an existing slug -- the drift badge's "Update"."""
        existing = self._profile_store.load(slug)
        if existing is None:
            return False
        self._profile_store.replace(
            slug,
            capture(
                existing.name,
                monitors=self._model.entities.monitors,
                workspace_rules=self._model.entities.workspace_rules,
                connected=connected_outputs(connected) or existing.connected,
            ),
        )
        return True

    def detach_monitor_profile(self) -> None:
        """Forget which profile is active; the config stays exactly as it is."""
        self._profile_store.set_active(None)

    def delete_monitor_profile(self, slug: str) -> None:
        self._profile_store.delete(slug)

    def matching_monitor_profile(
        self, connected: Sequence[Mapping[str, Any]]
    ) -> tuple[str, MonitorProfile] | None:
        """The profile the connected-output set matches, when activating it would change
        anything -- the app-open toast's condition (ADR-0018).

        A profile already in effect is excluded: offering to activate what the user is
        looking at would be noise, and the exclusion is what keeps the toast quiet on
        every ordinary launch of a stable setup.
        """
        live = connected_outputs(connected)
        for slug, profile in self._profile_store.list():
            if matches(profile, live) and drift(
                profile,
                monitors=self._model.entities.monitors,
                workspace_rules=self._model.entities.workspace_rules,
            ):
                return slug, profile
        return None

    def monitor_state_snapshot(self) -> MonitorStateSnapshot:
        """Both rule lists plus the active pointer -- what a profile revert restores.

        Wider than `monitor_snapshot` because activation touches workspace pins and the
        pointer too: reverting an activation that only put the monitor list back would
        leave the pins of the profile the user just refused.
        """
        return MonitorStateSnapshot(
            monitors=tuple(self._model.entities.monitors),
            workspace_rules=tuple(self._model.entities.workspace_rules),
            active=self._profile_store.active_slug(),
        )

    def restore_monitor_state(self, snapshot: MonitorStateSnapshot) -> bool:
        """Put both lists and the pointer back, through one normal transaction.

        The revert half of Confirm-or-revert for activation, shaped exactly like
        `restore_monitor_rules` and for the same reason: rendering the previous model
        produces the previous files byte for byte (ADR-0010).
        """
        return self._put_monitor_state(
            snapshot.monitors, snapshot.workspace_rules, snapshot.active
        )

    # --- helper data ------------------------------------------------------------------------

    def fetch_clients(
        self, done: Callable[[tuple[Mapping[str, Any], ...] | None], None]
    ) -> None:
        """Live open windows for the Pick-a-window helper, or `None` when unanswerable.

        Helper data only, never rule state (ADR-0008): the reply prefills a Match and is
        thrown away. `None` rather than `()` on a dead or absent compositor, because "no
        windows are open" and "nobody is there to ask" degrade differently -- the picker
        offers manual entry on the latter.
        """
        self._fetch_helper_data("clients", lambda client: client.clients(), done)

    def fetch_monitors(
        self, done: Callable[[tuple[Mapping[str, Any], ...] | None], None]
    ) -> None:
        """Live connected outputs for the Arrangement canvas, or `None` when unanswerable.

        Helper data only, never rule state (ADR-0008): the reply positions the canvas and
        fills the mode combos, and `None` degrades the page to its off-canvas lists.
        """
        self._fetch_helper_data("monitors", lambda client: client.monitors(), done)

    def fetch_layers(
        self, done: Callable[[tuple[Mapping[str, Any], ...] | None], None]
    ) -> None:
        """Live layer surfaces for the Pick-a-layer helper, or `None` when unanswerable."""
        self._fetch_helper_data("layers", lambda client: client.layers(), done)

    def _fetch_helper_data(
        self,
        what: str,
        query: Callable[[CommandClient], Coroutine[Any, Any, tuple[Mapping[str, Any], ...]]],
        done: Callable[[tuple[Mapping[str, Any], ...] | None], None],
    ) -> None:
        """The shared shape of a fire-and-callback helper query, failure spelled `None`."""
        client = self._client
        if client is None:
            done(None)
            return

        async def run() -> None:
            try:
                payload = await query(client)
            except IpcError as error:
                _log.debug("%s query failed: %s", what, error)
                done(None)
                return
            done(payload)

        self._spawn(run())

    def _refuse(self, name: str) -> bool:
        """Whether this session must decline an edit -- and leave the model alone doing it.

        The model is the app's claim about what the config says. On a read-only session
        nothing is written, so accepting the edit would leave the model holding a value that
        exists nowhere else: the Row would show it, a later re-read would not clear it (the
        compositor never had it), and a session that regained a compositor would write it
        without the user asking again. Declining keeps the model and the config in step.

        Unreachable from the UI, which makes every control insensitive while read-only. It
        is an invariant rather than a guard for that reason -- worth stating so no later
        caller has to rediscover it.
        """
        if self.live and self._applier is not None:
            return False
        _log.debug("read-only session: refusing the edit to %s", name)
        return True

    # --- undo -------------------------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        """Whether there is a gesture to take back, and a live session to take it back in."""
        return self.live and self._undo.can_undo

    @property
    def last_gesture(self) -> UndoStep | None:
        """The gesture `undo` would reverse, without reversing it.

        What the undo toast names its button after, so the toast and Ctrl+Z cannot come to
        disagree about which gesture "the last one" is.
        """
        return self._undo.top

    def undo(self) -> bool:
        """Take back the last gesture through a normal Apply transaction. `False` if none.

        A *normal* transaction, not a special path: the values go into the model exactly as a
        user edit would put them there and the queue renders, gates, writes and confirms them
        the same way (ADR-0010 §Undo). An undo that wrote files directly would be a second
        way for bytes to reach the App dir, with its own bugs and its own failure modes, for
        a gesture that is by definition already expressible as a model delta.

        Undoing does not push a step of its own. There is no redo tier in v1, and a stack
        that recorded its own reversals would turn Ctrl+Z pressed twice into a value
        oscillating between two states rather than walking back through history.
        """
        if not self.live or self._applier is None:
            return False
        step = self._undo.pop()
        if step is None:
            return False

        self._restore({edit.name: edit.before for edit in step.edits})
        self._applier.commit(*step.names)
        self._changed()
        return True

    def _restore(self, values: Mapping[str, OptionValue]) -> None:
        """Put the model back to `values`, and forget any gesture open on those Options.

        The forgetting matters: an Option mid-gesture has an entry in `_open_gestures` holding a
        pre-gesture value that the restore has just made wrong, and leaving it there would
        have the *next* transaction record an undo step spanning both.
        """
        for name, value in values.items():
            self._open_gestures.pop(name, None)
            if value is UNSET:
                self._model.unset(name)
            else:
                self._model.set(name, value)

    # --- lifecycle --------------------------------------------------------------------------

    def start(self) -> None:
        """Connect, recover the model, and begin applying. Returns immediately."""
        self._spawn(self._go_live())

    def set_read_only(self, reason: str) -> None:
        """Declare the session read-only for a reason it could not discover itself.

        The app knows one such reason: without PyGObject's asyncio integration there is no
        loop to run a transaction on, so no amount of connecting would help. Saying so up
        front beats leaving the Banner on "Connecting to Hyprland…" forever.
        """
        self._go_offline(reason)
        self._changed()

    async def _go_live(self) -> None:
        try:
            instance = self._connect()
        except NoInstance as error:
            self.set_read_only(str(error))
            return

        events = EventStream(instance, on_lost=self._on_stream_lost)
        client = CommandClient(instance)
        try:
            await events.start()
            await self._recover(client)
        except IpcError as error:
            await events.aclose()
            self.set_read_only(f"{instance.command_socket} is not answering: {error}")
            return

        self._events = events
        events.subscribe(self._on_monitor_hotplug, MONITOR_ADDED, MONITOR_REMOVED)
        self._client = client
        self._applier = Applier(
            model=self._model,
            writer=self._writer,
            client=client,
            events=events,
            journal=self._journal,
            on_foreign_reload=self._on_foreign_reload,
            on_result=self._applied,
        )
        self._applier.start()
        self._offline_reason = None
        self._changed()

    def _manifest(self) -> Manifest:
        return Manifest.load(
            self._paths.manifest,
            app_version=self._app_version,
            schema_version=self._schema.hyprland_version,
        )

    def _owned(self) -> tuple[ResolvedOption, ...]:
        """Exactly the Options the Manifest records this app as having written."""
        return app_owned_options(self._schema, self._manifest())

    async def _recover(self, client: CommandClient) -> ReRead:
        """Re-read the Options this app already wrote (ADR-0010, `reread.py`).

        Options are recovered from the compositor that loaded them. Binds cannot be --
        `hyprctl binds` is blind to `code:N` and reports every Lua bind as `__lua` -- so
        they are read from `binds.lua` itself first (ADR-0007). Without that read the model
        would open holding no binds while the file holds dozens, the Page would say the
        user has none, and the next Option write would prune the file as stale. Rules are
        in the same position -- `hyprctl clients` is helper data, never rule state
        (ADR-0008) -- so they come from their own Modules the same way.
        """
        self._load_entities()
        owned = self._owned()
        result = await read_state(self._model, client, owned)
        _log.info(
            "recovered %d option(s) from %d owned; %d unreadable, %d unknown",
            len(result.adopted),
            len(owned),
            len(result.unreadable),
            len(result.unknown),
        )
        # "On launch ... the full re-read + drift scan attributes any errors and raises the
        # same Banner" (ADR-0016 §Surfacing). Breakage that happened while the app was closed
        # is not a lesser kind of breakage, and the app has to open saying so.
        await self._scan(client)
        return result

    async def _scan(self, client: CommandClient) -> None:
        """Read what the live config is complaining about, and raise the Banner for it.

        For the two reloads the app did not perform: the one before it started, and any
        foreign one since. Failures are swallowed -- a session that could not read
        `configerrors` has learned nothing, and refusing to start over it would turn a
        transient socket hiccup into an app that will not open.
        """
        try:
            errors = await client.configerrors()
            binds = await client.bind_count() if errors else None
        except IpcError as error:
            _log.warning("could not read the config's health: %s", error)
            return
        self._observe_foreign(errors, binds)

    async def drain(self) -> None:
        """Wait until every pending edit has been applied and confirmed.

        The seam `aclose` uses to keep an edit made in the last moment before the window
        closes, and the one anything driving a session without a user has to wait on -- the
        integration Harness, and every test that asserts about what a change *did*. Returns
        at once on a session that never connected, because there is then nothing in flight
        and never will be.
        """
        if self._applier is not None:
            await self._applier.drain()

    async def aclose(self) -> None:
        """Flush pending edits, then drop the connection. Safe to call twice.

        `drain` before `aclose` on purpose: an edit made in the last moments before the
        window closes is still inside the apply debounce, and dropping it would lose a
        change the user watched land in the UI.
        """
        self._closing = True
        self._client = None
        applier, self._applier = self._applier, None
        if applier is not None:
            try:
                await applier.drain()
            except IpcError as error:
                _log.warning("could not flush pending edits on close: %s", error)
            await applier.aclose()

        events, self._events = self._events, None
        if events is not None:
            await events.aclose()

    def close(self, done: Callable[[], None]) -> None:
        """Shut down on the main loop and call `done` when there is nothing left running.

        `done` is called even when there is nothing to shut down, and that is not a detail:
        the window holds itself open until it arrives. A session that never connected has no
        coroutine to run -- and on a machine with no asyncio integration at all, `spawn` is a
        no-op -- so routing that case through the loop would leave a window that cannot be
        closed.
        """
        if self._applier is None and self._events is None:
            self._closing = True
            done()
            return

        async def run() -> None:
            try:
                await self.aclose()
            finally:
                done()

        self._spawn(run())

    # --- what the compositor tells us -------------------------------------------------------

    def _on_foreign_reload(self) -> None:
        """Somebody else reloaded the config: everything the model holds may be stale.

        ADR-0010 requires a *full* re-read here rather than a merge: the model's values
        describe a config that has just been replaced, so re-reading only the keys that look
        wrong would keep whichever of them the new config silently dropped. What a re-read
        cannot revise is an Option the app deliberately set to null -- no reply spells "no
        value" -- so those keep their state and surface an override as a drift badge (#57).

        The errors that reload produced are attributed and raised on the same Banner by the
        scan at the end of the re-read (ADR-0016 §Surfacing).
        """
        if self._closing:
            return
        # A reload rebuilds the Lua VM, so every Eval preview this session ever sent is
        # already gone (ADR-0010). Dropping the un-sent tick with them is what keeps the
        # re-read below from being immediately contradicted by a preview of the value it
        # just replaced.
        if self._applier is not None:
            self._applier.forget_previews()
        # Every open gesture is void: the values it started from describe a config that has
        # just been replaced, and the re-read below is about to overwrite the model they
        # would have been measured against. The *stack* survives -- a recorded step is a
        # model delta and replays through a normal transaction whatever else has happened
        # since (ADR-0010 §Undo) -- but a half-open one would produce a delta spanning
        # somebody else's reload.
        self._open_gestures.clear()
        self._spawn(self._reread_after_foreign_reload())

    async def _reread_after_foreign_reload(self) -> None:
        client = self._client
        if client is None:
            return
        # Both halves, because they answer different failures: the model's own keys catch a
        # value the new config changed or dropped, and the owned set catches a key a hand
        # edit *added* to one of the app's Modules -- which the next Apply would otherwise
        # re-render without, silently undoing it (ADR-0016 ownership class 2).
        wanted = {option.name for option, _ in self._model.set_options()}
        wanted.update(option.name for option in self._owned())
        stale = tuple(option for option in self._schema.options if option.name in wanted)
        try:
            result = await read_state(self._model, client, stale)
        except IpcError as error:
            self.set_read_only(f"lost contact with Hyprland: {error}")
            return
        _log.info(
            "foreign reload: %d option(s) re-read, %d no longer set",
            len(result.adopted),
            len(result.cleared),
        )
        self._reread_binds()
        self._reread_rules()
        self._reread_monitors()
        self._reread_declarations()
        # The other half ADR-0016 asks for: somebody else's reload can break the config just
        # as thoroughly as the app's own, and it surfaces identically.
        await self._scan(client)
        self._changed()

    def _reread_binds(self) -> None:
        """Adopt a hand-edited `binds.lua` instead of overwriting it (ADR-0007).

        The Options half of a foreign reload is re-read over IPC, which binds cannot be:
        `hyprctl binds` is blind to `code:N`, so the compositor is not a source of truth for
        them. The file is, and it is the file the user just edited -- so this reads it.

        Gated on the Manifest hash, which is what makes it cheap and what keeps it honest.
        Bytes the app wrote need no re-read: the model already says exactly that, and
        re-parsing them would spend a Lua evaluation to learn nothing. Bytes the app did not
        write are the whole point, and adopting them is what stops the next Apply from
        rendering the model over somebody's edit.

        Failure is silence by design. A `binds.lua` that does not evaluate is a config the
        user has already broken, it is surfaced through `configerrors` on the Banner like
        any other, and throwing away the binds the model holds on the strength of a file
        that would not load would turn one broken reload into lost state.
        """
        path = self._paths.app_dir / BINDS_MODULE
        if not path.is_file():
            return
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            return

        record = self._manifest().modules.get(BINDS_MODULE)
        if record is not None and record.sha256 == content_hash(current):
            return

        self._load_binds()

    def _reread_rules(self) -> None:
        """Adopt hand-edited rule Modules, gated on the Manifest hash like binds.

        One gate over both files, one load for both: `_load_rules` splices the two lists
        together anyway (a misfiled rule belongs to whichever kind it *is*), so re-reading
        them separately would let the un-edited file's stale parse overwrite the edited
        one's adoption.
        """
        changed = False
        for module in (WINDOW_RULES_MODULE, LAYER_RULES_MODULE):
            path = self._paths.app_dir / module
            if not path.is_file():
                continue
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                continue
            record = self._manifest().modules.get(module)
            if record is None or record.sha256 != content_hash(current):
                changed = True
        if changed:
            self._load_rules()

    def _reread_monitors(self) -> None:
        """Adopt hand-edited monitor and workspace-rule Modules, gated like the others.

        The gap ADR-0015 turns from cosmetic to load-bearing: "hand edits to
        `monitors.lua` while a profile is active are drift", and drift is judged against
        the model -- so a hand edit the model never hears about is a drift badge that
        never lights. One gate over both files, one load for both, for `_reread_rules`'s
        reason: `_load_monitors` splices misfiled entities to the kind they are.
        """
        changed = False
        for module in (MONITORS_MODULE, WORKSPACE_RULES_MODULE):
            path = self._paths.app_dir / module
            if not path.is_file():
                continue
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                continue
            record = self._manifest().modules.get(module)
            if record is None or record.sha256 != content_hash(current):
                changed = True
        if changed:
            self._load_monitors()

    def _reread_declarations(self) -> None:
        """Adopt hand edits to the six declarative Modules, gated like the others.

        One gate over all six and one load for all six, for `_reread_rules`'s reason:
        `_load_declarations` splices misfiled entities to the kind they are, so re-reading
        one file without the others would drop whatever it found belonging to a list the
        other five own.
        """
        changed = False
        for module in self.DECLARATION_MODULES:
            path = self._paths.app_dir / module
            if not path.is_file():
                continue
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                continue
            record = self._manifest().modules.get(module)
            if record is None or record.sha256 != content_hash(current):
                changed = True
        if changed:
            self._load_declarations()

    def _load_entities(self) -> None:
        """The startup read of every Entity Module, and the keeper of `entities_loaded`.

        The flag is load bearing rather than informational -- it is what stops the Writer
        from reading "the model renders no binds" as "the user deleted their binds" and
        pruning the file. It is also *global* to all Entity Modules, so it is only set when
        every load succeeded: marking it with `window_rules.lua` unread would let the next
        Option write prune a rules file the user merely broke.
        """
        if (
            self._load_binds()
            and self._load_rules()
            and self._load_monitors()
            and self._load_declarations()
        ):
            self._model.mark_entities_loaded()

    def _load_binds(self) -> bool:
        """Read `binds.lua` into the model, or leave the model alone saying why.

        The startup read as well as the foreign-reload one: binds are the half of the model
        the compositor cannot answer for, so the file is the only place they come from.

        Returns whether the model now speaks for the file -- `False` leaves
        `entities_loaded` unset (`_load_entities`).
        """
        path = self._paths.app_dir / BINDS_MODULE
        if not path.is_file():
            # No file is a real answer: a fresh install has no binds, and the model saying
            # so is correct rather than ignorant.
            return True

        parsed = parse_binds_module(path)
        if not parsed.ok:
            _log.warning("binds.lua would not evaluate, leaving it alone: %s", parsed.errors[0])
            return False

        binds = list(parsed.binds)
        # Constructs the model cannot represent are kept as action-less Binds rather than
        # dropped, so the Page lists them read-only with their trigger intact (ADR-0007:
        # "never silently dropped"). The file itself is protected separately -- its hash no
        # longer matches the Manifest, so the Writer treats it as hand-edited and skips it.
        binds.extend(
            Bind(keys=entry.keys, dispatcher=None, origin=entry.origin)
            for entry in parsed.read_only
        )

        _log.info(
            "read %d bind(s) from binds.lua (%d read-only)", len(binds), len(parsed.read_only)
        )
        self._model.entities.binds[:] = binds
        self._model.entities.unbinds[:] = list(parsed.unbinds)
        self._model.entities.submaps[:] = list(parsed.submaps)
        return True

    def _load_rules(self) -> bool:
        """Read `window_rules.lua` and `layer_rules.lua` into the model.

        Both files feed both lists: `parse_rules_module` reports a misfiled rule as what
        it *is*, so a layer rule hand-added to `window_rules.lua` still lands in the layer
        list rather than vanishing. Either file failing to evaluate adopts neither --
        splicing half an edit would leave the model disagreeing with one file in order to
        agree with the other -- and returns `False`, which keeps `entities_loaded` unset
        and the Writer's hands off both files (`_load_entities`).
        """
        window: list[WindowRule] = []
        layer: list[LayerRule] = []
        for module in (WINDOW_RULES_MODULE, LAYER_RULES_MODULE):
            path = self._paths.app_dir / module
            if not path.is_file():
                continue
            parsed = parse_rules_module(path, module=module)
            if not parsed.ok:
                _log.warning(
                    "%s would not evaluate, leaving it alone: %s", module, parsed.errors[0]
                )
                return False
            window.extend(parsed.window_rules)
            layer.extend(parsed.layer_rules)

        _log.info("read %d window rule(s) and %d layer rule(s)", len(window), len(layer))
        self._model.entities.window_rules[:] = window
        self._model.entities.layer_rules[:] = layer
        return True

    def _load_monitors(self) -> bool:
        """Read `monitors.lua` and `workspace_rules.lua` into the model.

        The same shape as `_load_rules`, and for the same reasons: both files feed both
        lists (a misfiled rule comes back as what it *is*), either file failing to
        evaluate adopts neither and keeps `entities_loaded` unset, so the Writer cannot
        prune a display layout the user merely broke.
        """
        monitors: list[MonitorRule] = []
        workspace: list[WorkspaceRule] = []
        for module in (MONITORS_MODULE, WORKSPACE_RULES_MODULE):
            path = self._paths.app_dir / module
            if not path.is_file():
                continue
            parsed = parse_monitors_module(path, module=module)
            if not parsed.ok:
                _log.warning(
                    "%s would not evaluate, leaving it alone: %s", module, parsed.errors[0]
                )
                return False
            monitors.extend(parsed.monitors)
            workspace.extend(parsed.workspace_rules)

        _log.info(
            "read %d monitor rule(s) and %d workspace rule(s)", len(monitors), len(workspace)
        )
        self._model.entities.monitors[:] = monitors
        self._model.entities.workspace_rules[:] = workspace
        return True

    DECLARATION_MODULES: tuple[str, ...] = (
        ANIMATIONS_MODULE,
        GESTURES_MODULE,
        DEVICES_MODULE,
        ENV_MODULE,
        PERMISSIONS_MODULE,
        AUTOSTART_MODULE,
    )
    """The six Modules `_load_declarations` reads, in Entrypoint order (#70)."""

    def _load_declarations(self) -> bool:
        """Read the six declarative Entity Modules into the model.

        The same shape as `_load_rules` and `_load_monitors`, one tier wider: every file
        feeds every list, so an entity someone hand-moved into the wrong Module comes back
        as what it is rather than vanishing -- and vanishing is not cosmetic here, because a
        list the model believes is empty is a Module the Writer prunes.

        All six are adopted together or none is. Six files is where that rule starts to
        look expensive, and it is exactly where it starts to matter: a single unparseable
        `gestures.lua` must not license the Writer to delete a user's `env.lua`, which is
        the one Module whose contents Hyprland will not restore on the next reload.
        """
        curves: list[Curve] = []
        animations: list[Animation] = []
        gestures: list[Gesture] = []
        devices: list[Device] = []
        env: list[EnvVar] = []
        permissions: list[Permission] = []
        startup: list[StartupCommand] = []

        for module in self.DECLARATION_MODULES:
            path = self._paths.app_dir / module
            if not path.is_file():
                continue
            parsed = parse_declarations_module(path, module=module)
            if not parsed.ok:
                _log.warning(
                    "%s would not evaluate, leaving it alone: %s", module, parsed.errors[0]
                )
                return False
            curves.extend(parsed.curves)
            animations.extend(parsed.animations)
            gestures.extend(parsed.gestures)
            devices.extend(parsed.devices)
            env.extend(parsed.env)
            permissions.extend(parsed.permissions)
            startup.extend(parsed.startup)

        _log.info(
            "read %d curve(s), %d animation(s), %d gesture(s), %d device(s), "
            "%d env var(s), %d permission(s), %d startup command(s)",
            len(curves),
            len(animations),
            len(gestures),
            len(devices),
            len(env),
            len(permissions),
            len(startup),
        )
        self._model.entities.curves[:] = curves
        self._model.entities.animations[:] = animations
        self._model.entities.gestures[:] = gestures
        self._model.entities.devices[:] = devices
        self._model.entities.env[:] = env
        self._model.entities.permissions[:] = permissions
        self._model.entities.startup[:] = startup
        return True

    def _on_stream_lost(self) -> None:
        """Hyprland closed the event stream -- it has exited."""
        self.set_read_only("Hyprland is no longer running")

    def _applied(self, result: ApplyResult) -> None:
        """One transaction finished: close its gestures, then recover or record.

        The order is the whole design. Closing first turns "which Options were mid-gesture?"
        into a concrete delta; that delta is then either the thing to *undo automatically*
        (Hyprland rejected our own write) or the thing to *remember* (it stands, so Ctrl+Z
        should be able to take it back). A gesture can never be both, which is why the failed
        one is never pushed rather than pushed and popped.
        """
        if self._reverting:
            # The restore transaction's own result. It carries no gesture of the user's, and
            # a second auto-revert on top of a failed one is the loop ADR-0016 forbids.
            #
            # A restore carries its own keys alone (`apply_now`), so an edit made in the
            # ~25 ms it takes is still mid-gesture: its entry stays open in
            # `_open_gestures`, and the next transaction records it from the value it really
            # started at rather than from the one the revert put back.
            self._recovery_result(result)
            self._observe(result)
            self._report(result)
            return

        delta = self._close(result.keys)
        blamed = self._own_write_errors(result)
        if blamed and self._may_auto_revert(delta):
            # No Banner for this one. The auto-revert is about to reload, and the errors it
            # is answering will be gone by the time the user could read about them -- the
            # toast is what tells that story (ADR-0016 reserves one for exactly this).
            self._auto_revert(result, delta, blamed)
            return
        if blamed and not self._recovery_halted:
            # Our own write, rejected, with nothing recorded to put back -- which the app
            # cannot reach by editing, only by writing without an edit. Reverting blind would
            # mean re-rendering the same model into the same bad bytes, so this reports and
            # stops, and the Banner says so.
            _log.error("own write rejected with no model delta to revert: %s", result.errors)
            self._recovery_halted = True

        step = self._step(delta)
        self._undo.record(step)
        self._observe(result)
        self._repoll_if_timed_out(result)
        self._report(result)
        if step is not None and result.ok and self.on_recorded is not None:
            # After `on_applied`, and only for a transaction that stands: the window shows one
            # toast, and an offer to undo a change that did not land would be an offer to undo
            # nothing.
            self.on_recorded(step)

    def _may_auto_revert(self, delta: Mapping[str, OptionValue]) -> bool:
        """Whether the app is still allowed to answer a rejected write by writing again.

        Two gates, and ADR-0016 names both. There has to be a delta to put back -- reverting
        blind would re-render the same model into the same bad bytes. And recovery must not
        already have failed: "if the restore transaction itself errors ... stop auto-writing
        until the user acts", which is a gate on the *next* rejection as much as on this one.
        """
        return bool(delta) and not self._recovery_halted

    def _repoll_if_timed_out(self, result: ApplyResult) -> None:
        """ADR-0016 §Timeout: "re-poll once; if still unconfirmed, treat as a foreign-unknown
        state -- full re-read, Banner if errors".

        A timed-out transaction is the one outcome where the app genuinely does not know what
        happened: the Modules are on disk, and whether Hyprland applied them is unanswered.
        Guessing either way is worse than asking again, and the "foreign-unknown" treatment is
        exactly the re-read the app already performs for somebody else's reload -- so this
        routes to it rather than inventing a third recovery.
        """
        if result.outcome is not ApplyOutcome.TIMEOUT or self._closing:
            self._repolled = False
            return
        if self._repolled:
            return
        self._repolled = True
        self._spawn(self._reread_after_foreign_reload())

    def _report(self, result: ApplyResult) -> None:
        self._pending_restart.update(result.pending_restart)
        if self.on_applied is not None:
            self.on_applied(result)

    def _close(self, keys: Sequence[str]) -> dict[str, OptionValue]:
        """Take the pre-gesture values of every Option this transaction carried.

        Scoped to `keys` rather than draining `_open_gestures`, because coalescing is per key: a
        transaction confirms exactly the Options it was handed, and an edit that arrived
        while it was in flight belongs to the *next* one and is still mid-gesture.
        """
        return {
            name: self._open_gestures.pop(name) for name in keys if name in self._open_gestures
        }

    def _step(self, delta: Mapping[str, OptionValue]) -> UndoStep | None:
        return UndoStep.of(
            [Edit(name, before, self._model.get(name)) for name, before in delta.items()]
        )

    def _own_write_errors(self, result: ApplyResult) -> tuple[str, ...]:
        """The Modules this transaction wrote that Hyprland then complained about.

        Narrow on purpose (ADR-0016 §Attribution): an error in `user.lua`, in a Bridge
        module, or in an app Module this write did not touch is somebody else's to fix, and
        answering it with an automatic write would be the app overwriting a file on the
        strength of an error it did not cause.
        """
        if result.outcome is not ApplyOutcome.CONFIG_ERRORS:
            return ()
        return own_write_modules(result.errors, written=result.written)

    # --- what is wrong, and what may be done about it (ADR-0016) ------------------------------

    def _observe(self, result: ApplyResult) -> None:
        """Take this reload's findings as the app's current unhealthy state.

        Every finished reload lands here, clean ones included -- a clean reload is how a
        Banner *clears*, and a recovery that only ever raised one would leave the user
        looking at a problem they had already fixed.
        """
        self._note(
            result.errors,
            written=result.written,
            binds=result.binds,
            mismatches=result.mismatches,
        )

    def _observe_foreign(self, errors: Sequence[str], binds: int | None) -> None:
        """The same, for a reload this app did not perform.

        `written` is empty on purpose: nothing the app wrote is in flight, so no error here
        can be an `OWN_WRITE` -- and claiming one would authorise an automatic rewrite of a
        file somebody else has just changed (ADR-0016 §Attribution). There are no Read-back
        mismatches either: nothing was written, so nothing was checked.
        """
        self._note(errors, written=(), binds=binds, mismatches=())

    def _note(
        self,
        errors: Sequence[str],
        *,
        written: Sequence[str],
        binds: int | None,
        mismatches: Sequence[Mismatch],
    ) -> None:
        """Replace the unhealthy state, then act on it if the user is stranded.

        One body for both callers, because "what is wrong" and "does that strand the user"
        are the same two steps whoever asked -- and a second copy of the emergency gate is a
        second place for it to drift from `Recovery.auto_restorable`.

        Every field it touches is *replaced*, never merged. `configerrors` describes the last
        parse and nothing older, so a Banner assembled from anything but the newest reload
        would name a file the user has since fixed.
        """
        self._recovery = plan(errors, written=written, binds=binds)
        self._unapplied = tuple(mismatch.name for mismatch in mismatches if mismatch.unapplied)
        # The other half of the same Read-back, and ADR-0005's drift badge: a key the live
        # config sets to something else is one `user.lua` or a Bridge won on purpose.
        self._overridden = tuple(
            mismatch.name for mismatch in mismatches if mismatch.overridden
        )
        # Cleared with the rest: the rescue notice belongs to the reload that prompted it.
        # `_restore_transaction` re-raises it *after* observing its own result, which is what
        # lets the notice outlive the restore that earned it without outliving anything else.
        self._rescued = ()
        if not errors:
            # A reload the compositor accepted is proof the app can write to this config
            # again, which is the plainest reading of ADR-0016's "stop auto-writing **until
            # the user acts**". Without this the halt is permanent for the session, and a
            # stale one from an unrelated auto-revert would silently disable the zero-binds
            # rescue -- the one recovery a stranded user cannot start themselves.
            self._recovery_halted = False
        if self._recovery.auto_restorable and self._may_recover():
            self._emergency_restore(self._recovery.auto_restorable)

    def _may_recover(self) -> bool:
        """Whether the app may still answer a broken config by writing to it unprompted.

        The same gate `_may_auto_revert` applies to the other automatic recovery, and it is
        needed here for a sharper reason. The emergency restore fires on a *state* -- errors
        plus zero binds -- rather than on an event, and its own restore ends in a reload that
        re-observes that state. If the restore does not fix things, the app would find itself
        stranded again and restore again, forever, hammering the config it cannot repair.

        ADR-0016 draws the line in as many words: "if the restore transaction itself errors
        ... escalate to the Banner and stop auto-writing until the user acts".
        """
        return not self._restoring and not self._recovery_halted

    def _emergency_restore(self, modules: Sequence[str]) -> None:
        """Put app-owned Modules back without asking, because the user has no keybinds.

        ADR-0016 §Zero-binds: "stranded-user beats hand-edit sanctity". The one path in this
        app that overwrites a hand edit with no consent, and it is deliberately narrow --
        `Recovery.auto_restorable` is empty unless the bind count came back as exactly zero,
        and never names a file the app does not own. What the user loses is preserved: the
        restore snapshots the bytes it replaces into the Journal before touching them.
        """
        _log.error("no keybinds after a failed reload; restoring %s", ", ".join(modules))
        # Held, not announced yet. The Banner has to be able to say what was taken -- "the
        # overwritten hand edit is preserved in the Journal *and reported in the Banner*"
        # (ADR-0016 §Zero-binds), and a restore the user never asked for and is never told
        # about is indistinguishable from the app having eaten their work. But the restore's
        # own reload observes the config afresh, and announcing before that would have the
        # notice wiped by the very transaction it describes.
        self._pending_rescue = tuple(modules)
        self.restore_last_good(*modules)

    # --- the recovery actions -----------------------------------------------------------------

    def last_good_for(self, module: str) -> LastKnownGood | None:
        """What Restore last good would put back for one Module, without putting it back.

        What the Banner asks before offering the button: a Module the app has never confirmed
        a write to has nothing to restore *to*, and an action that did nothing would be worse
        than one that was never offered.
        """
        return self._journal.last_known_good(module)

    def restore_last_good(self, *modules: str) -> bool:
        """Put `modules` back to their newest confirmed bytes. `False` if nothing can be.

        ADR-0016's Restore last good, for both the classes that offer it: the hand-edited app
        Module the user chose it for, and the emergency that takes it without asking. The
        difference between those two is entirely in *who calls this* -- by the time it runs,
        the decision is made.
        """
        if not self.live or self._applier is None or self._restoring:
            return False

        restores = [
            good
            for good in (self._journal.last_known_good(module) for module in modules)
            if good is not None
        ]
        if not restores:
            _log.warning("nothing to restore: no confirmed write to %s", ", ".join(modules))
            return False

        self._spawn(self._restore_transaction(restores))
        return True

    async def _restore_transaction(self, restores: Sequence[LastKnownGood]) -> None:
        applier = self._applier
        if applier is None:
            return

        self._restoring = True
        try:
            result = await applier.restore_now(applier.restore(restores))
        except (IpcError, RuntimeError) as error:
            _log.error("the restore transaction failed: %s", error)
            self._recovery_halted = True
            self._changed()
            return
        finally:
            self._restoring = False

        if not result.ok:
            # A restore that did not land is exactly the escalation ADR-0016 names: stop
            # recovering automatically and leave the Banner up for the user.
            _log.error("restore last good did not land: %s", result.outcome)
            self._recovery_halted = True

        # The restore re-read the model itself, so the Rows have moved; and its own reload's
        # errors are the current truth about the config, replacing the ones it was answering.
        self._observe(result)
        # After the observation, which clears the field: this notice is about what the
        # restore just did, so it has to survive the restore's own reload and nothing later.
        self._rescued, self._pending_rescue = self._pending_rescue, ()
        self._report(result)
        self._changed()

    def quarantine(self, require: str) -> bool:
        """Regenerate the Entrypoint without `require`, and reload (ADR-0016 §Quarantine).

        The only recovery the app can offer for a file it must never write. Consent is the
        caller's to have obtained -- the ADR gates this behind an explicit dialog -- and
        reversal is `release_quarantine`, which is the same act with the name taken out
        again. That symmetry is what makes the ADR's "one-click re-enable" true.
        """
        return self._set_quarantine({*self.quarantined, require})

    def release_quarantine(self, *requires: str) -> bool:
        """Put `requires` back in the Entrypoint and reload. The one-click reversal.

        Variadic, and that is not a convenience: releasing two files as two calls would be two
        Entrypoint rewrites racing each other through the queue, each rendering from a
        Manifest the other was in the middle of changing. One call is one rewrite and one
        reload, whether it lifts one quarantine or all of them.
        """
        return self._set_quarantine(set(self.quarantined) - set(requires))

    def file_for(self, problem: Problem) -> Path | None:
        """Which file on this machine a Problem names, for Open file. `None` if none does.

        An app-owned Module is resolved through `ConfigPaths`, never through the path
        Hyprland printed: the app knows exactly where its own files are, and the printed one
        may have travelled through a symlinked dotfile directory (`ownership.py`). For a
        foreign file the printed path is the only thing there is -- and it is trusted only
        when it is absolute, because a relative fragment is not something to go guessing a
        root for.
        """
        if problem.module is not None:
            return self._paths.file_for(problem.module)
        if problem.path.startswith("/"):
            return Path(problem.path)
        return None

    def quarantine_target(self, problem: Problem) -> str | None:
        """The `require` path a foreign Problem names, or `None` when it names none.

        Matched against the require list the app would actually generate rather than derived
        from the path Hyprland printed. The two can differ -- a symlinked dotfile directory,
        a bind mount, a `$HOME` resolved differently -- and a quarantine recorded under a
        name the Entrypoint never emits would be a Banner that says a file is disabled while
        the config goes on loading it.
        """
        if not problem.offers(Action.QUARANTINE) or not problem.path:
            return None
        return ModuleSet.discover(self._paths, []).require_for(problem.path)

    def regenerate_entrypoint(self) -> bool:
        """Rewrite `hyprland.lua` and reload -- ADR-0016's Entrypoint Fix.

        Offered as a one-click fix, unlike every other app-owned file, because the Entrypoint
        holds no decisions of the user's: it is derived entirely from which Modules exist and
        which requires are quarantined, so there is nothing in it a regeneration could lose.
        """
        return self._recovery_write(
            lambda: self._writer.regenerate_entrypoint(self._model),
            "regenerate the Entrypoint",
        )

    def _set_quarantine(self, requires: set[str]) -> bool:
        return self._recovery_write(
            lambda: self._writer.set_quarantine(self._model, sorted(requires)),
            "change the Quarantine",
        )

    def _recovery_write(self, write: Callable[[], object], what: str) -> bool:
        """Rewrite the Entrypoint out of band, then reload. `False` if it could not be done.

        One body for the two recoveries that work by changing which files are required, since
        they differ only in what they write. Both need the same three things around it: a
        live session to reload into, a write that may fail without taking the app down, and a
        reload that is *not* an apply -- an apply would render the model over the App dir and
        reload with the require list it would have generated rather than the one just written.
        """
        if not self.live or self._applier is None:
            return False
        try:
            write()
        except (LuaSyntaxError, ProtectedFile, OSError, ValueError) as error:
            _log.error("could not %s: %s", what, error)
            return False
        self._spawn(self._reload_after_recovery())
        return True

    async def _reload_after_recovery(self) -> None:
        """Make an Entrypoint rewrite take effect, and re-read what the config now says.

        A plain apply would do the wrong thing here: it renders the model over the App dir,
        and the file that just changed is the one file the model does not describe. So this
        restores nothing and writes nothing -- it reloads, and finds out what happened.
        """
        applier = self._applier
        if applier is None:
            return
        # Every owned Option, not a narrow set: quarantining `user.lua` changes the value of
        # everything that file was overriding, and the app cannot know which those were
        # without asking about all of them.
        wanted = tuple(option.name for option in self._owned())
        try:
            result = await applier.restore_now(applier.reload_only(wanted))
        except (IpcError, RuntimeError) as error:
            _log.error("could not reload after a recovery: %s", error)
            self._changed()
            return
        self._observe(result)
        self._report(result)
        self._changed()

    # --- auto-revert (ADR-0016) ---------------------------------------------------------------

    def _auto_revert(
        self,
        result: ApplyResult,
        delta: Mapping[str, OptionValue],
        modules: Sequence[str],
    ) -> None:
        """Put the model back and re-apply it, so the file goes back with it.

        The model *is* the restore. Modules are rendered whole and deterministically
        (ADR-0010), so a model returned to its pre-transaction values renders byte-for-byte
        the Snapshot this transaction replaced -- which is why step 1 and step 2 of ADR-0016's
        auto-revert are one act here rather than two writes racing each other. The Snapshot
        is still what makes it *checkable*, and `_verify` checks it.

        No confirmation, per the ADR: instant apply has no cancel, and the bytes being
        restored were live and confirmed moments ago.
        """
        _log.warning("Hyprland rejected our own write to %s; reverting", ", ".join(modules))
        # Read before re-applying: right now the newest Journal entry for each Module is the
        # failed write, so its `before` digest is the Snapshot the revert has to reproduce.
        # A moment later the revert's own entry is newest, and its `before` is the bad bytes.
        expected = self._snapshot_digests(modules)
        self._restore(delta)
        self._changed()
        self._spawn(self._revert_transaction(result, tuple(delta), expected))

    def _snapshot_digests(self, modules: Sequence[str]) -> dict[str, str | None]:
        """Each Module's pre-write Snapshot digest -- what the revert has to reproduce."""
        return {module: self._journal.previous_digest(module) for module in modules}

    async def _revert_transaction(
        self, result: ApplyResult, keys: tuple[str, ...], expected: Mapping[str, str | None]
    ) -> None:
        applier = self._applier
        if applier is None:
            return

        self._reverting = True
        try:
            await applier.apply_now(*keys)
        except (IpcError, RuntimeError) as error:
            _log.error("could not re-apply after reverting: %s", error)
            self._recovery_halted = True
        finally:
            self._reverting = False

        restored = self._verify(expected)
        if self.on_reverted is not None:
            self.on_reverted(
                AutoRevert(
                    keys=keys,
                    modules=tuple(expected),
                    errors=result.errors,
                    restored=restored and not self._recovery_halted,
                )
            )
        self._changed()

    def _verify(self, expected: Mapping[str, str | None]) -> bool:
        """Whether each reverted Module is now byte-for-byte its pre-write Snapshot.

        Checked rather than assumed, because the assumption is load-bearing: auto-revert
        restores the file *by re-rendering the model*, which is only the same thing as
        restoring the Snapshot for as long as rendering is deterministic and the model was
        put back completely. A disagreement means it was not -- schema drift after a Hyprland
        upgrade is the case ADR-0016 names -- and the honest answer is to say the recovery
        did not complete and stop auto-writing, rather than to toast "reverted" over a config
        that is still broken.

        A Module whose Snapshot digest is `None` did not exist before the write; the revert
        should have deleted it again, and a file still there is a failure like any other.
        """
        for module, digest in expected.items():
            current = self._journal.read_module(module)
            if digest is None:
                if current is None:
                    continue
            elif current is not None and content_hash(current) == digest:
                continue
            _log.error("auto-revert did not restore %s to its Snapshot", module)
            self._recovery_halted = True
            return False
        return True

    def _recovery_result(self, result: ApplyResult) -> None:
        """Whether the restore transaction itself was refused -- ADR-0016's escalation."""
        if not result.ok:
            _log.error("the restore transaction failed: %s", result.outcome)
            self._recovery_halted = True

    @property
    def recovery_halted(self) -> bool:
        """Whether the app has stopped recovering automatically and needs the user.

        Set when a restore fails to land the Snapshot it promised. ADR-0016 puts a Banner
        behind this, and `Health` is what puts it there: a halted recovery is one of the four
        states the one Banner ranks.
        """
        return self._recovery_halted

    # --- notification -----------------------------------------------------------------------

    def _go_offline(self, reason: str) -> None:
        """Stop applying, and record the one line the Banner will show.

        The `Applier` is deliberately *not* torn down here: this can fire from inside its
        own event stream's callback, and cleanup belongs to `aclose`. `live` is what gates
        every write, so a session that has gone read-only is read-only whatever is still
        holding a socket.
        """
        if self._offline_reason != reason:
            _log.info("session is read-only: %s", reason)
        self._offline_reason = reason

    def _changed(self) -> None:
        if self.on_state_changed is not None:
            self.on_state_changed()
