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
holds by construction rather than by remembering to pop. Entity edits follow the same rule
(#189): the step is built at the edit, held against its commit's serial, and pushed only when
the transaction that carried it comes back ok (`_commit_entity_edit`, `_settle_entities`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Collection, Coroutine, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from hyprtweaker.engine import binds_analysis
from hyprtweaker.engine.apply import (
    Action,
    Applier,
    ApplyOutcome,
    ApplyResult,
    Edit,
    EntityEdit,
    EntityStep,
    Mismatch,
    PresetStep,
    Problem,
    Recovery,
    ReRead,
    SourceChange,
    Step,
    UndoGroup,
    UndoStack,
    UndoStep,
    WallpaperChange,
    app_owned_options,
    overrides,
    own_write_modules,
    plan,
    read_state,
)
from hyprtweaker.engine.apply.reread import Answer, classify_reply
from hyprtweaker.engine.bridge import (
    REGISTRY,
    BridgeEntry,
    ChosenSource,
    ColorSource,
    ManualColors,
    Off,
    PresetColors,
    Several,
    ToolSpec,
    Wallpaper,
    bridge_states_for,
    color_source_of,
    entries_for,
    files_present,
    owners,
    with_presence,
)
from hyprtweaker.engine.bridge.wire import bridge_output
from hyprtweaker.engine.bridge.wire import shown as tilde_path
from hyprtweaker.engine.entities_catalog import (
    IDENTITY_FIELD,
    device_field_bounds,
    overridden_options,
)
from hyprtweaker.engine.importer.lua.sandbox import LuaUnavailable, lua_missing_reason
from hyprtweaker.engine.ipc import (
    MONITOR_ADDED,
    MONITOR_REMOVED,
    CommandClient,
    EventStream,
    Instance,
    IpcError,
    LiveHyprland,
    NoInstance,
    NoSuchOption,
    fetch_live_hyprland,
    read_live_hyprland,
)
from hyprtweaker.engine.model import UNSET, ConfigModel, OptionValue
from hyprtweaker.engine.model.entities import (
    DISPLAY_KINDS,
    Animation,
    Bind,
    Curve,
    Device,
    EnvVar,
    Gesture,
    LayerRule,
    MonitorRule,
    Permission,
    PluginLoad,
    StartupCommand,
    WindowRule,
    WorkspaceRule,
    entity_title,
)
from hyprtweaker.engine.model.values import parse_lua, parse_value
from hyprtweaker.engine.paths import (
    ANIMATIONS_MODULE,
    AUTOSTART_MODULE,
    BINDS_MODULE,
    DEVICES_MODULE,
    ENTRYPOINT_NAME,
    ENV_MODULE,
    GESTURES_MODULE,
    LAYER_RULES_MODULE,
    MONITORS_MODULE,
    PERMISSIONS_MODULE,
    PLUGINS_MODULE,
    WINDOW_RULES_MODULE,
    WORKSPACE_RULES_MODULE,
    ConfigPaths,
)
from hyprtweaker.engine.presets import MAX_NAME as MAX_PRESET_NAME
from hyprtweaker.engine.presets import (
    CaptureScope,
    ColorChoice,
    Preset,
    PresetApplied,
    PresetApplyResult,
    PresetChange,
    PresetColorConflict,
    PresetImported,
    PresetImportResult,
    PresetNameTaken,
    PresetNotApplied,
    PresetNotImported,
    PresetNotSaved,
    PresetPreview,
    PresetSaved,
    PresetSaveResult,
    PresetSection,
    PresetStore,
    scoped_options,
    stored_value,
)
from hyprtweaker.engine.presets_archive import ThemeArchive
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
from hyprtweaker.engine.schema import (
    MINIMUM_HYPRLAND,
    ResolvedOption,
    Schema,
    SupplementKind,
    below_lua_floor,
    load_schema,
    newer_than_shipped,
    supplement,
)
from hyprtweaker.engine.state import Journal, LastKnownGood, Manifest, content_hash, retirement
from hyprtweaker.engine.state.retirement import (
    RenamedNotice,
    Restoration,
    RetiredNotice,
    UnkeptNotice,
)
from hyprtweaker.engine.triggers import trigger_load_problem
from hyprtweaker.engine.wallpaper import Daemon, WallpaperError, Wallpapers
from hyprtweaker.engine.writer import (
    ENTITY_KIND_MODULES,
    BeforeReplace,
    LuaSyntaxError,
    ModuleSet,
    Writer,
    is_entity_module,
    load_manifest,
    module_relpath,
)
from hyprtweaker.engine.writer.binds import parse_binds_module
from hyprtweaker.engine.writer.declarations import parse_declarations_module
from hyprtweaker.engine.writer.monitors import parse_monitors_module
from hyprtweaker.engine.writer.rules import parse_rules_module

_log = logging.getLogger(__name__)

ENTRYPOINT_EDITED = (
    "hyprland.lua was edited outside this app. Press “Regenerate hyprland.lua…” on the "
    "Theming page, then change where colors come from."
)
"""Why the Color source cannot change while the Entrypoint holds a hand edit: the next step
is named by the button the Theming page shows beside it (finding 19 of the #153 review)."""

_Answer = TypeVar("_Answer")
"""What one helper-data query answers: a tuple of mappings, or of names."""


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
    """The App-dir Modules put back: the ones the errors blamed on this transaction's own
    write, or the ones a failed write changed."""

    errors: tuple[str, ...]
    """The `configerrors` lines, `file:line` prefixes intact."""

    restored: bool = True
    """Whether the Modules came back byte-for-byte as their pre-write Snapshot.

    False is the escalation ADR-0016 names -- "if the restore transaction itself errors ...
    escalate to the Banner and stop auto-writing until the user acts". The toast still says
    what was attempted; what it must not do is claim a recovery that did not happen."""

    outcome: ApplyOutcome = ApplyOutcome.CONFIG_ERRORS
    """What the reverted transaction ran into: Hyprland's rejection, or `WRITE_FAILED`
    when the disk refused the write. The toast says which."""


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

    held_back: tuple[str, ...] = ()
    """App Modules an edit was not saved into because they were edited outside the app.

    The Writer leaves a hand-edited Module alone (ADR-0005), so an edit that belongs in one
    never reaches disk. Unhealthy until the user replaces the file or it stops differing:
    every later edit to it would be refused the same way, and saying so once in a toast
    that times out would leave the next refusal unexplained."""

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
            or self.held_back
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
        if self.held_back:
            if len(self.held_back) == 1:
                name = self.held_back[0].rsplit("/", 1)[-1]
                return f"{name} was edited outside this app, so changes to it are not saved."
            return (
                f"{len(self.held_back)} files were edited outside this app, "
                f"so changes to them are not saved."
            )
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
            disabled = ", ".join(f"{name.replace('.', '/')}.lua" for name in self.quarantined)
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
        if self.recovery.unhealthy or self.held_back:
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

Notice = RetiredNotice | UnkeptNotice | RenamedNotice
"""A one-time Info notice of ADR-0012's: a release removed settings (kept, or not), or
renamed them."""

_NOT_CONNECTED_YET = "Connecting to Hyprland…"


@dataclass(slots=True)
class _HeldBack:
    """The edits one hand-edited Module kept off disk, so "Replace file" can still save them."""

    options: dict[str, OptionValue] = field(default_factory=dict)
    lists: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    entity_titles: list[str] = field(default_factory=list)
    """What each held-back Entity gesture was called, in the words of the undo toast."""


@dataclass(frozen=True, slots=True)
class _PendingEntityStep:
    """An Entity step waiting for the verdict on the commit that carries it."""

    serial: int
    """`commit_entities`'s serial: the step stands or falls with the result that reports it."""
    step: EntityStep
    group: UndoGroup | None
    """The undo group holding it, when one was open over its kinds at commit time."""


"""The reason a session is read-only between construction and `start()` finishing.

A reason rather than a fourth state: the Rows are genuinely not editable yet, and a Banner
that says why is better than one that appears a moment later."""


@dataclass(frozen=True, slots=True)
class _WallpaperOrder:
    """A Preset's image, to be shown through `daemon` once its transaction stands."""

    daemon: Daemon
    image: Path


@dataclass(frozen=True, slots=True)
class _AppliedPreset:
    """A Preset whose Options are queued: its name, each Option's value before it, the
    Bridge entries it gated ("Use preset's colors"), and the wallpaper it will set."""

    name: str
    before: dict[str, OptionValue]
    after: dict[str, OptionValue]
    """The values it sets: its step's "after" when a later Preset lands in the same batch."""
    source: SourceChange | None = None
    wallpaper: _WallpaperOrder | None = None


@dataclass(frozen=True, slots=True)
class _UndonePreset:
    """A Preset step whose Options are being put back: what follows once that stands."""

    names: frozenset[str]
    source: SourceChange | None
    wallpaper: WallpaperChange | None
    notes: tuple[str, ...]


def _gates(entries: Sequence[BridgeEntry]) -> tuple[tuple[str, str, object], ...]:
    """What the Color source is made of: each entry on or gated off, and by what. A tool's
    entry going from waiting to loaded is not the user changing where colors come from."""
    return tuple(
        (entry.tool, entry.module, entry.state if isinstance(entry.state, Off) else "on")
        for entry in entries
    )


def _typed(option: ResolvedOption, value: Any) -> Any:
    """`value` as the model types it: a Schema default is display text, a set value is not."""
    if value is None:
        return None
    try:
        return parse_value(option.type, value)
    except (ValueError, TypeError):
        return value


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
        read_live: Callable[[], LiveHyprland | None] | None = None,
        wallpapers: Wallpapers | None = None,
    ) -> None:
        """`connect` names the compositor to talk to; by default, the one we run under.

        Injected for the same reason the Harness can drive the real `Applier`: an `Instance`
        is a frozen dataclass over a socket directory, so a nested Hyprland or a scripted
        pair of sockets is a first-class session and needs no monkeypatching.

        `read_live` answers which Hyprland that is, before anything else here runs: the
        model is built from the Schema for that version (ADR-0012 §Pinning). By default a
        blocking read over `connect`, bounded at one second; tests that have no compositor
        to ask, or want to pose as another version, hand in the answer. It runs whether or
        not `schema` is injected, so `live_hyprland` means the same thing in every session.

        `wallpapers` is where a wallpaper daemon is looked for and how it is called: by
        default the tool search path and this process's environment (`engine/tools.py`).
        """
        self._spawn = spawn
        self._wallpapers = wallpapers if wallpapers is not None else Wallpapers()
        self._live_hyprland = (
            read_live() if read_live is not None else read_live_hyprland(connect)
        )
        live_version = self._live_hyprland.version if self._live_hyprland else None
        self._unsupported_reason = (
            f"Hyprland {live_version} is running, and this app needs Hyprland "
            f"{MINIMUM_HYPRLAND} or newer"
            if live_version is not None and below_lua_floor(live_version)
            else None
        )
        """Why this session can never go live, or `None`. Set only for a Hyprland too old to
        have a Lua config: no amount of reconnecting changes that, so `start` honours it."""
        if schema is None:
            # Below the floor, the oldest shipped Schema: the nearest to what is running,
            # and a read-only session only displays it.
            wanted = MINIMUM_HYPRLAND if self._unsupported_reason else live_version
            schema = load_schema(wanted)
        live = self._live_hyprland
        if live is not None and newer_than_shipped(live.version):
            # ADR-0012 §Pinning: what a newer compositor added beyond every shipped schema
            # still gets a Row, inferred from its own description and flagged as such.
            schema = supplement(schema, live.descriptions, version=live.version)
        if live is not None:
            # ADR-0018 §Plugins: a loaded plugin's settings, whatever the version, flagged as
            # a plugin's. Only a plugin loaded at this read adds any (no rebuild mid-session).
            schema = supplement(
                schema, live.descriptions, version=live.version, kind=SupplementKind.PLUGIN
            )
        self._schema = schema
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

        self.on_notice: Callable[[Notice], None] | None = None
        """Called at startup with each notice ADR-0012 owes the user: per release that
        retired Options they set, and once for values that moved to a renamed Option.

        A Retired notice keeps coming, start after start, until `notice_seen` records it."""

        self.on_held_back: Callable[[tuple[str, ...], tuple[str, ...]], None] | None = None
        """Called with the gestures a transaction could not save, and the files that stopped
        them: Modules edited outside the app, which the Writer leaves alone (ADR-0005). The
        model is already back to what the file holds, and no undo step was recorded."""

        self._held_back: dict[str, _HeldBack] = {}
        self._model_read = False
        self._lua_missing: str | None = None
        self._offline_sentence: str | None = None
        """Per hand-edited Module, the edits it kept off disk (`replace_edited_file`)."""

        self.on_recorded: Callable[[Step], None] | None = None
        """Called with the gesture a finished transaction put on the undo stack.

        The step is handed over rather than left for the window to read off the stack top.
        Those are not the same thing: a transaction that recorded nothing -- an undo's own
        write, or one whose rendered bytes were already on disk -- would otherwise have the
        window offer to take back whatever gesture happened to be underneath, which is a
        gesture the user did not just make."""

        self._model = ConfigModel(self._schema)
        self._writer = Writer(self._paths, app_version=app_version)
        self._journal = Journal(self._paths)
        self._retired = self._manifest().retired
        """The Options still Retired, by name, as `retired_in` reads them for a Row's pill.

        Copied from the Manifest at construction and replaced by the startup pass, so a Row
        asks no file; an offline session still badges what earlier sessions retired."""

        self._events: EventStream | None = None
        self._client: CommandClient | None = None
        self._applier: Applier | None = None
        self._offline_reason: str | None = self._unsupported_reason or _NOT_CONNECTED_YET
        self._closing = False
        self._pending_restart: set[str] = set()
        self._monitor_watchers: list[Callable[[], None]] = []
        """Who wants to hear about display hotplug -- the Monitors page's canvas (#68).

        A list of the session's own rather than a raw `EventStream.subscribe`, because
        watchers register before the stream exists (the window builds against a session
        that has not connected yet) and must survive it never existing at all."""

        self._profiles: ProfileStore | None = None
        """The Monitor-profile store, built lazily over `monitor-profiles/` (#69)."""
        self._presets: PresetStore | None = None
        """The Preset store, built lazily over `presets/` (ADR-0014)."""
        self._applying_presets: list[_AppliedPreset] = []
        """Presets whose Options are queued and not yet reported (`apply_preset`), oldest
        first. A list, not a slot: a second Preset applied before the first lands is its own
        gesture, with its own step (finding 14 of the #153 review). A foreign reload leaves
        them: the transaction carrying them still reports, and its verdict is theirs."""
        self._undoing_presets: list[_UndonePreset] = []
        """Preset steps whose Options an undo has queued and not yet reported, oldest first."""

        self.on_preset_note: Callable[[str], None] | None = None
        """Called with a sentence saying what applying or undoing a Preset could not do: a
        wallpaper with no daemon to show it, or a Color source or wallpaper changed since
        and so left as it is (S7). The window shows it as a toast."""

        self._undo = UndoStack()
        self._open_gestures: dict[str, OptionValue] = {}
        """Per Option, what it held when the current gesture's first edit arrived.

        Opened by any edit that finds no entry, closed by the transaction that carries the
        key. Between those two moments the Option is mid-gesture, however many model writes
        the widget makes -- which is what turns fifty slider ticks into one undo step."""

        self._pending_entities: list[_PendingEntityStep] = []
        """Entity steps whose commit has not reported yet, oldest first (#189).

        Built where the edit is made -- before and after are both known there -- and held
        until the transaction carrying the commit says whether it stood (`_applied`): a
        rejected entity write never reaches the stack, as ADR-0016 has it for Options."""

        self._undo_group: UndoGroup | None = None
        """The open undo group, if any -- one at a time (`begin_undo_group`)."""

        self._undo_waits_for: EntityStep | None = None
        """The in-flight step a Ctrl+Z is waiting to undo once it lands (`undo`)."""

        self.on_undo_due: Callable[[], object] | None = None
        """Called when the edit a waiting Ctrl+Z was pressed over has landed, to undo it.

        The window's own undo, so an undo the window puts behind a countdown still goes
        there; without one the session undoes it itself."""

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
        """Keys the app's own Modules set that the live config does not.

        ADR-0016's "unexplained read-back mismatch (value didn't take, no error, no
        override)", which "joins the Banner". A quiet value disagreement is not this: that is
        usually `user.lua` winning the override order on purpose, and the drift badge's
        business. `Mismatch.unapplied` is the loud shape -- the model sets the key and the
        live config sets nothing, which means the Module never ran."""

        self._overridden: tuple[str, ...] = ()
        """Keys whose live value disagrees with what the app wrote because something later won.

        The quiet counterpart of `_unapplied`: the value did not take, but for a reason the
        app can name. ADR-0005 fixes the mechanism -- "after each reload the app compares
        `get_config`/`getoption` against its model and badges diverging options" -- so this
        comes from `getoption` alone (a transaction's Read-back, and the drift scan at launch
        and after a foreign reload), never from reading `user.lua` itself. ADR-0018 rejects
        that: running the user's own code to answer a question about a badge is
        consent-and-safety weight no badge earns. Both marks change through `_mark` only."""

        self._unconfirmed: tuple[str, ...] = ()
        """Keys a timed-out transaction wrote: saved to the config, never confirmed live.

        Neither drift mark is true of them -- nothing overrides the value, and it may well
        have applied -- so their Row says "Not confirmed" instead (owner call 3 of the #153
        review). The timeout's own re-read leaves them marked; the next Read-back or drift
        scan that covers a key replaces its mark, as for the others (`_mark`)."""

        self._drift_watches: list[set[str]] = []
        """One set per drift scan in flight, of the keys a transaction read back meanwhile.

        A scan reads the Modules first and `getoption` after, so a transaction that lands in
        between holds newer evidence for its keys than the scan does: the scan leaves those
        marks alone (`_scan_drift`)."""

        self._lua_missing_logged = False
        """Whether "no Lua, so no drift scan" was logged: once per session is news enough."""

        self._rescued: tuple[str, ...] = ()
        """Modules the emergency restore overwrote without asking, so the Banner can say so."""

        self._pending_rescue: tuple[str, ...] = ()
        """A rescue announced only once its own restore has been observed -- see
        `_emergency_restore`, which explains why it cannot be announced any earlier."""

        self._bridge_owners: dict[str, str] | None = None
        """`bridge_owners`, read off the Manifest once per state change rather than per Row."""

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
    def live_hyprland(self) -> LiveHyprland | None:
        """The running compositor's version and option descriptions, read at startup, or on
        connect when that missed.

        `None` when there was no compositor, it did not answer either time, or its version
        is not a release number. A read on connect does not rebuild the Schema (#217): an
        Option only its supplement would add has no Row until the next start.

        Not the same fact as `live`: a compositor can be described here and still refuse
        every edit (one too old for a Lua config, or a socket that died later).
        The Schema it selected is `schema.hyprland_version`, which can be older (ADR-0012
        degradation).
        """
        return self._live_hyprland

    @property
    def hyprland_too_old(self) -> bool:
        """Whether the running Hyprland predates the Lua config, so nothing here can apply.

        Read-only for the whole run, whatever the config on disk: there is nothing to
        convert a hyprlang config *to* on a compositor that only reads hyprlang.
        """
        return self._unsupported_reason is not None

    @property
    def unsupported_reason(self) -> str | None:
        """Why the running Hyprland is too old for this app, as one sentence without a full
        stop, or `None` when it is not."""
        return self._unsupported_reason

    @property
    def offline_reason(self) -> str | None:
        """Why this session is read-only, in one line fit for the Banner."""
        return self._offline_reason

    @property
    def offline_sentence(self) -> str | None:
        """Why applying is off, as one true sentence for a dialog, a toast or a tooltip, or
        `None` when it is on. No socket path or exception text: the Banner has the detail.
        Hyprland may be running and still not reachable, so it never says "not running"."""
        if self._offline_reason is None:
            return None
        if self._unsupported_reason is not None:
            return f"{self._unsupported_reason}."
        if self._lua_missing is not None and self._offline_reason == self._lua_missing:
            return f"{self._lua_missing}."
        if self._offline_sentence is not None:
            # A reason the caller can say better: an import still on offer (F20).
            return self._offline_sentence
        return "This app is not connected to Hyprland."

    @property
    def entrypoint_edited(self) -> bool:
        """Whether `hyprland.lua` was edited outside this app (its bytes are not the
        Manifest's), which blocks every change to where colors come from."""
        return ENTRYPOINT_NAME in self._manifest().hand_edited(self._paths)

    @property
    def unapplied(self) -> frozenset[str]:
        """Keys the app's own Modules set that the live config does not.

        The Row badge ADR-0016 carves out of "errors never appear on Rows": an unexplained
        read-back mismatch is *key*-scoped, unlike a config error, so it is the one thing
        error surfacing has to say on the Row itself as well as on the Banner.

        Per key, from the newest reading of it, as `overridden` is: see there.
        """
        return frozenset(self._unapplied)

    @property
    def unconfirmed(self) -> frozenset[str]:
        """Keys a timed-out transaction wrote that Hyprland never confirmed (`_unconfirmed`)."""
        return frozenset(self._unconfirmed)

    @property
    def overridden(self) -> frozenset[str]:
        """Keys the live config sets to something other than what the app's Modules set.

        `user.lua` is required last (ADR-0005), so a key it sets beats the Module the app
        wrote -- the Row wears the "Overridden" pill rather than pretending the edit took,
        from launch on, before anything is edited (#191).

        Per key, from the newest reading of it. The drift scan at launch and after each
        foreign reload reads every key the app's Modules set and replaces the whole set; a
        transaction's Read-back replaces only the keys it read back. A badge outliving the
        reading that earned it would be a lie about a value that has since applied, and an
        edit to one Option erasing another Option's badge would hide an override nothing
        has changed.
        """
        return frozenset(self._overridden)

    @property
    def bridge_owners(self) -> Mapping[str, str]:
        """Option name to the theming tool whose Bridge module sets it ("Set by <tool>").

        Shaped like `overridden`, for `RowContext`. From the registry's static key lists of
        the entries that load and are not quarantined (ADR-0006, ADR-0018: the app never
        evaluates tool Lua). Read once per state change, so a Row asking costs no I/O.
        """
        if self._bridge_owners is None:
            manifest = self._manifest()
            self._bridge_owners = owners(manifest.bridges, quarantined=manifest.quarantined)
        return self._bridge_owners

    def color_source(self) -> ColorSource:
        """The Color source in force: read off the Entrypoint on disk, never stored (ADR-0014).

        The file rather than the Manifest because the file is what Hyprland loads, so a hand
        edit that enables both backends reads as `Several` rather than as the app's last
        choice. No Entrypoint is Manual: nothing overrides the colours.
        """
        try:
            text = self._paths.entrypoint.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ManualColors()
        return color_source_of(text)

    @property
    def color_source_blocked(self) -> str | None:
        """Why the Color source cannot change right now, as a sentence, or `None` if it can."""
        if self._offline_reason is not None:
            return self.offline_sentence
        if self.entrypoint_edited:
            return ENTRYPOINT_EDITED
        return None

    def set_color_source(self, source: ChosenSource) -> bool:
        """Make `source` the Color source: one Entrypoint rewrite, one reload (ADR-0014).

        Rides the Apply queue as an `EntrypointTransaction`, like Quarantine (S6): one
        Journal draft, one Entrypoint write, one reload, then a re-read. `False` when the
        session is read-only, the Entrypoint was hand-edited (`color_source_blocked` says
        so; its bytes are never written over), a Wallpaper backend is not set up, or the
        syntax gate refuses. The caller re-reads `color_source()` rather than keeping a copy.

        The re-read leaves out the Options a loading bridge sets: adopting a tool's colours
        into the model would write them into the app's own Module, and a switch back to
        Manual would then bring back the tool's colours instead of the user's.
        """
        if self.color_source_blocked is not None:
            return False
        manifest = self._manifest()
        try:
            entries = bridge_states_for(
                source, manifest.bridges, present=self._bridge_files_present(manifest.bridges)
            )
        except ValueError as error:
            _log.error("could not change the Color source: %s", error)
            return False
        return self._set_bridges(
            lambda current: bridge_states_for(
                source, current, present=self._bridge_files_present(current)
            ),
            manifest.with_bridges(entries),
            "change the Color source",
        )

    def load_waiting_bridges(self) -> bool:
        """Load every Bridge module whose tool has now written its file (S4's "Load now").

        A file a tool creates is not watched until something requires it, so nothing else
        would notice. Called at launch and after every foreign reload, and by the Theming
        page after a run or on "Load now". A module whose file is gone goes back to waiting,
        so a reload never requires a missing file. Gated entries stay gated. `False` when
        nothing changed or the change cannot be written now.
        """
        manifest = self._manifest()
        entries = with_presence(
            manifest.bridges, present=self._bridge_files_present(manifest.bridges)
        )
        if entries == manifest.bridges or self.color_source_blocked is not None:
            return False
        return self._set_bridges(
            lambda current: with_presence(current, present=self._bridge_files_present(current)),
            manifest.with_bridges(entries),
            "load a theming tool's colors",
        )

    def add_bridge(self, tool: str, *, source: ChosenSource | None = None) -> bool:
        """Give `tool` its Bridge entries and the Entrypoint its lines: one transaction.

        What #166's `wire` calls before it touches the tool's own files. Never gates (#163)
        unless `source` is given: then every entry, the new ones included, takes its state
        for that Color source in the same transaction. The Theming page's "Switch to <tool>"
        on a backend not yet set up passes `Wallpaper(tool)`, so no reload ever loads both
        backends. `False` when the session is read-only or the Entrypoint was hand-edited
        (`color_source_blocked` says why).
        """
        if self.color_source_blocked is not None:
            return False
        spec = REGISTRY[tool]

        def added(current: Sequence[BridgeEntry]) -> Sequence[BridgeEntry]:
            kept = [entry for entry in current if entry.tool != tool]
            entries = [*kept, *entries_for(spec, present=self._module_files_present(spec))]
            if source is None:
                return entries
            return bridge_states_for(
                source, entries, present=self._bridge_files_present(entries)
            )

        manifest = self._manifest()
        prospective = manifest.with_bridges(added(manifest.bridges))
        return self._set_bridges(added, prospective, f"set up {spec.title}")

    def current_wallpaper(self) -> Path | None:
        """The image the wallpaper daemon shows on the first output, or `None` with no
        daemon, no image, or a daemon that did not answer. Runs the daemon's client: call it
        off the main loop. What the Theming page's Regenerate runs a tool on (#164)."""
        daemon = self._wallpapers.detect()
        if daemon is None:
            return None
        try:
            shown = daemon.current()
        except WallpaperError:
            return None
        return shown[0].image if shown else None

    def wallpaper_absent_reason(self) -> str | None:
        """Why a Preset's wallpaper cannot be saved or changed from here, or `None` when a
        daemon this app can drive is running. A sentence for the Presets group (#171). Reads
        the tool path and runtime directory only, so it is safe on the main loop."""
        if self._wallpapers.detect() is not None:
            return None
        return self._wallpapers.absent_reason()

    def manifest(self) -> Manifest:
        """The Manifest as it is on disk now: what #166's `detect` and `unwire` take."""
        return self._manifest()

    def remove_bridge(self, tool: str) -> bool:
        """Take `tool`'s Bridge entries and lines out: what #166's `unwire` calls first.

        `True` with nothing done when the tool has no entry, so an `unwire` that converges
        after a crash never costs a reload.
        """
        manifest = self._manifest()
        output = bridge_output(tool, self._paths)
        if not output and not any(entry.tool == tool for entry in manifest.bridges):
            return True
        if self.color_source_blocked is not None:
            return False

        def delete_output() -> None:
            # Before the Entrypoint is rendered: a file left in the bridge folder loads with
            # no entry at all, which kept a removed tool loading (#148 hand-test 1).
            for path in bridge_output(tool, self._paths):
                path.unlink(missing_ok=True)

        return self._set_bridges(
            lambda current: [entry for entry in current if entry.tool != tool],
            manifest.remove_bridge(tool),
            f"remove {REGISTRY[tool].title if tool in REGISTRY else tool}",
            first=delete_output,
        )

    def _module_files_present(self, spec: ToolSpec) -> frozenset[str]:
        return files_present(self._paths.hypr_dir, (each.file for each in spec.modules))

    def _set_bridges(
        self,
        change: Callable[[Sequence[BridgeEntry]], Sequence[BridgeEntry]],
        prospective: Manifest,
        what: str,
        *,
        first: Callable[[], None] | None = None,
    ) -> bool:
        """Rewrite the Entrypoint with `change` applied to the Manifest's entries as they are
        when the queued write runs, so a change landing in between is built on, not lost.
        `first` runs in the queued write, just before it."""

        def write(before: BeforeReplace | None) -> bool:
            if first is not None:
                first()
            return self._writer.set_bridges(
                self._model, change(self._manifest().bridges), before_replace=before
            )

        return self._recovery_write(
            write,
            prospective,
            what,
            exclude=tuple(owners(prospective.bridges, quarantined=prospective.quarantined)),
        )

    def _bridge_files_present(self, entries: Sequence[BridgeEntry]) -> frozenset[str]:
        return files_present(self._paths.hypr_dir, (entry.file for entry in entries))

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

    def instance(self) -> Instance:
        """The compositor this Session talks to: its `connect`. Raises `NoInstance`.

        Exposed for the Migration wizard, which builds its own client for the same reason
        it builds its own Writer. Asking here rather than the environment keeps a sandboxed
        or Harness Session's wizard on the compositor it was given (#201).
        """
        return self._connect()

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
            held_back=tuple(sorted(self._held_back)),
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

    def unknown_to_version(self, option: ResolvedOption) -> bool:
        """Whether the running Hyprland was described and does not have this Option.

        The `unknown-to-this-version` Row state (#77): the app degraded onto a Schema the
        compositor does not match (ADR-0012). False with no snapshot -- no compositor is
        no evidence -- so an offline session badges nothing as missing.
        """
        live = self._live_hyprland
        return live is not None and option.name not in live.names

    def retired_in(self, option: ResolvedOption) -> str | None:
        """The release that retired this Option while the user set it, if it is Retired.

        ADR-0012's "the Row is badged": the app keeps the value and has stopped writing it.
        Only a Retired Option the loaded Schema still describes has a Row to badge, and only
        one a release removed: a value kept for a quiet reason belongs to an Option the
        user's Hyprland still has.
        """
        entry = self._retired.get(option.name)
        return entry.retired_in if entry is not None and entry.reason.announced else None

    def kept_value(self, name: str) -> OptionValue:
        """The value the app kept for this Option when it stopped writing it, or `UNSET`.

        Every retirement reason, announced or quiet: a kept value is what makes a Row
        read-only (#215), whether or not it wears a pill. Typed against the loaded Schema's
        Option the way a restore would type it (`parse_lua`), so the Row renders it as its
        own control would; a value that Option will not take is shown as it was kept.
        """
        entry = self._retired.get(name)
        if entry is None:
            return UNSET
        option = self._schema.get(name)
        if option is None:
            return entry.value
        try:
            return parse_lua(option, entry.value)
        except (ValueError, TypeError):
            return entry.value

    def notice_seen(self, notice: Notice) -> None:
        """The user has dismissed `notice`: a Retired one is not shown again (ADR-0012).

        Called on dismissal, not on display, so a notice the app closed before the user saw
        it comes back on the next start. A rename notice records nothing: the move it
        reports is done, and the next start has nothing to say about it.
        """
        if isinstance(notice, RetiredNotice):
            self._writer.record_retired_notice(self._model, notice.release)

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

    def edit_binds(
        self, mutate: Callable[[list[Bind]], None], *, title: str | None = None
    ) -> bool:
        """Change the Bind list and write it, returning whether the edit was accepted.

        `mutate` is handed the whole list because for Binds position *is* identity
        (ADR-0007): adding is an append at a chosen index, reordering is a move, and there
        is no key to address a bind by. Duplicates are legal, so nothing here de-duplicates.

        Returns `False` on a read-only session, for the same reason `_refuse` exists -- a
        model holding binds that were never written would show them in the list, survive a
        re-read, and get written later without the user asking again.

        Also `False`, with nothing written, when the edit leaves an enabled Bind whose
        Trigger cannot load (`trigger_load_problem`) that the list before did not hold:
        Lua would fail the whole Module (ADR-0007). `mutate` runs on a copy so a refused
        edit never touches the model. Disabling is never refused, and one already there is
        carried along.

        On the undo stack as one Entity step titled `title` (`_commit_entity_edit`).
        """
        binds = self._model.entities.binds
        edited = list(binds)
        mutate(edited)
        if any(
            bind.enabled and bind not in binds and trigger_load_problem(bind.keys) is not None
            for bind in edited
        ):
            return False

        def store() -> None:
            binds[:] = edited

        return self._commit_entity_edit("binds", store, title=title)

    def add_bind(self, bind: Bind) -> bool:
        """Append a Bind. `hl.bind` appends, so the end of the list is where a new one goes."""
        return self.edit_binds(
            lambda binds: binds.append(bind), title=entity_title("binds", "added")
        )

    def replace_bind(self, index: int, bind: Bind) -> bool:
        """Replace the Bind at `index`, keeping its position.

        In place rather than remove-and-append: position *is* identity, so a bind that
        jumped to the end of the list would change which of two duplicates fires first.
        """

        def swap(binds: list[Bind]) -> None:
            if 0 <= index < len(binds):
                binds[index] = bind

        return self.edit_binds(swap, title=entity_title("binds", "changed"))

    def remove_bind(self, index: int) -> bool:
        """Delete the Bind at `index`."""

        def drop(binds: list[Bind]) -> None:
            if 0 <= index < len(binds):
                del binds[index]

        return self.edit_binds(drop, title=entity_title("binds", "removed"))

    def set_bind_enabled(self, index: int, enabled: bool) -> bool:
        """Enable or disable the Bind at `index`, in place.

        The conflict surface's "disable it" (ADR-0007, #66). In place because the point of
        `enabled` over deletion is exactly that nothing moves: every other bind keeps its
        position, and re-enabling restores the world as it was. Enabling a Bind whose Trigger
        cannot load is refused (`edit_binds`); disabling never is, so the conflict surface's
        "disable it" always works.
        """

        def flip(binds: list[Bind]) -> None:
            if 0 <= index < len(binds):
                binds[index] = replace(binds[index], enabled=enabled)

        return self.edit_binds(
            flip, title=entity_title("binds", "enabled" if enabled else "disabled")
        )

    def swap_binds(self, first: int, second: int) -> bool:
        """Exchange the positions of two Binds -- which of two duplicates fires first.

        A swap rather than a general move because that is what fire order among duplicates
        *is* (ADR-0007): the conflict badge says "fires 1st", and this is the control that
        makes it say otherwise. Everything between the two stays put.
        """

        def exchange(binds: list[Bind]) -> None:
            if 0 <= first < len(binds) and 0 <= second < len(binds) and first != second:
                binds[first], binds[second] = binds[second], binds[first]

        return self.edit_binds(exchange, title=entity_title("binds", "reordered", plural=True))

    def move_bind(self, index: int, to: int) -> bool:
        """Move the Bind at `index` to position `to` -- the Binds page's drag reorder.

        A move rather than a swap, as for Rules: everything between shifts by one, and the
        moved bind takes the target's place within its group. Both are flat-list indices.

        Refused (`False`, nothing written, no undo step) for an index out of range, a move
        onto itself, and a target in another group: `binds.lua` keeps root binds first and
        one block per Submap, so order *between* groups is not something the file can hold.
        Not refused for an unloadable stored bind (#199): a move changes order, not whether
        Hyprland can load it.
        """
        binds = self._model.entities.binds
        if not (0 <= index < len(binds) and 0 <= to < len(binds)) or index == to:
            return False
        if binds[index].submap != binds[to].submap:
            return False

        def shift(binds: list[Bind]) -> None:
            binds.insert(to, binds.pop(index))

        return self.edit_binds(shift, title=entity_title("binds", "reordered", plural=True))

    def save_submap(self, *, original: str | None, name: str, reset_target: str) -> bool:
        """Create a Submap, or rename one and retune its reset target (#66).

        The cascade semantics live in `engine.binds_analysis.save_submap`, where they are
        tested headless; this is only the write gate around them, shaped like `edit_binds`.
        A rename that rewrites binds is one undo step over both lists.
        """
        return self._commit_entity_edit(
            "submaps",
            lambda: binds_analysis.save_submap(
                self._model.entities, original=original, name=name, reset_target=reset_target
            ),
            title=entity_title("submaps", "added" if original is None else "changed"),
        )

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

    def edit_rules(
        self, kind: str, mutate: Callable[[list[Any]], None], *, title: str | None = None
    ) -> bool:
        """Change a rule list and write it, returning whether the edit was accepted.

        `mutate` is handed the live list because for Rules position *is* identity
        (ADR-0008): later rules win per Effect, and there is no key to address an
        anonymous rule by. Shaped exactly like `edit_binds`, refusal and undo step and all.
        """
        rules = self.rules(kind)
        return self._commit_entity_edit(f"{kind} rules", lambda: mutate(rules), title=title)

    @staticmethod
    def _rule_title(kind: str, verb: str, *, plural: bool = False) -> str:
        return entity_title(f"{kind}_rules", verb, plural=plural)

    def add_rule(self, kind: str, rule: WindowRule | LayerRule) -> bool:
        """Append a Rule -- last, where it wins over everything it conflicts with."""
        return self.edit_rules(
            kind, lambda rules: rules.append(rule), title=self._rule_title(kind, "added")
        )

    def replace_rule(self, kind: str, index: int, rule: WindowRule | LayerRule) -> bool:
        """Replace the Rule at `index`, keeping its position."""

        def swap(rules: list[Any]) -> None:
            if 0 <= index < len(rules):
                rules[index] = rule

        return self.edit_rules(kind, swap, title=self._rule_title(kind, "changed"))

    def remove_rule(self, kind: str, index: int) -> bool:
        """Delete the Rule at `index`."""

        def drop(rules: list[Any]) -> None:
            if 0 <= index < len(rules):
                del rules[index]

        return self.edit_rules(kind, drop, title=self._rule_title(kind, "removed"))

    def set_rule_enabled(self, kind: str, index: int, enabled: bool) -> bool:
        """Enable or disable the Rule at `index`, in place.

        The point of `enabled` over deletion is that nothing moves (ADR-0008): the rule
        stays in the file at its position, and re-enabling restores the world as it was.
        """

        def flip(rules: list[Any]) -> None:
            if 0 <= index < len(rules):
                rules[index] = replace(rules[index], enabled=enabled)

        verb = "enabled" if enabled else "disabled"
        return self.edit_rules(kind, flip, title=self._rule_title(kind, verb))

    def move_rule(self, kind: str, index: int, to: int) -> bool:
        """Move the Rule at `index` to position `to` -- the drag reorder (ADR-0008).

        A move rather than a swap because that is what dragging *is*: everything between
        the two positions shifts by one, which is exactly how the user read the gesture.
        """

        def shift(rules: list[Any]) -> None:
            if 0 <= index < len(rules) and 0 <= to < len(rules) and index != to:
                rules.insert(to, rules.pop(index))

        return self.edit_rules(
            kind, shift, title=self._rule_title(kind, "reordered", plural=True)
        )

    # --- monitor rules ----------------------------------------------------------------------

    def _commit_entity_edit(
        self, what: str, mutate: Callable[[], None], *, title: str | None = None
    ) -> bool:
        """The write gate every Entity edit shares, and where its undo step is made (#189).

        Refuse on a read-only session (leaving the model alone, `_refuse`), run the
        mutation, commit one entity transaction. Every Entity list is snapshotted around
        `mutate` and the ones that moved become one `EntityStep` -- all of them, so a
        cascade such as a submap rename rewriting binds is one step over both lists. The
        step waits for its transaction's verdict (`_pending_entities`); an edit that moved
        nothing records nothing. `title` is the undo toast's ("Keybind removed"); without one
        the step is "<Kinds> changed" after the first list it moved.

        Entities are frozen and the snapshots share them, so a step is pointer arrays. A
        `mutate` that changed an entity, or its `fields` dict, in place would change every
        snapshot holding it: history would silently agree with the present.
        """
        if self._refuse(what):
            return False
        before = self._entity_lists()
        mutate()
        after = self._entity_lists()
        serial = self._applier.commit_entities()  # type: ignore[union-attr]  # _refuse proved it
        step = EntityStep.of(
            (EntityEdit(kind, before[kind], after[kind]) for kind in before), title or ""
        )
        if step is None:
            return True
        if not title:
            step = replace(step, title=entity_title(step.edits[0].kind, "changed", plural=True))
        group = self._undo_group
        held = group if group is not None and step.kinds & group.kinds else None
        self._pending_entities.append(_PendingEntityStep(serial, step, held))
        return True

    def _entity_lists(self) -> dict[str, tuple[Any, ...]]:
        return {kind: tuple(items) for kind, items in self._model.entities.kinds()}

    @property
    def monitor_rules(self) -> list[MonitorRule]:
        """The live monitor rule list. Identity is the `output` string (ADR-0008)."""
        return self._model.entities.monitors

    def edit_monitor_rules(
        self, mutate: Callable[[list[MonitorRule]], None], *, title: str | None = None
    ) -> bool:
        """Change the monitor rule list and write it, returning whether it was accepted."""
        return self._commit_entity_edit(
            "monitor rules", lambda: mutate(self._model.entities.monitors), title=title
        )

    def patch_monitor_rule(self, output: str, fields: Mapping[str, Any]) -> bool:
        """Merge `fields` into the rule for `output`, creating it if it has none.

        A merge because that is what `hl.monitor` itself does (`lua-api-surface.md` §3):
        the per-monitor rows each own one field, and a row that replaced the whole rule
        would erase every sibling's value on each toggle.

        A field whose value is `UNSET` is removed after the merge: a row's "Not set" means
        "not in my config", so the file must not gain an explicit default instead.
        """

        def patch() -> None:
            rules = self._model.entities.monitors
            existing = next((rule for rule in rules if rule.output == output), None)
            merged = {**(existing.fields if existing is not None else {}), **fields}
            kept = {key: value for key, value in merged.items() if value is not UNSET}
            if existing is None and not kept:
                return  # "not set" on a display with no rule: nothing to write
            self._model.entities.add_monitor_rule(
                MonitorRule(output=output, fields=kept), merge=False
            )

        return self._commit_entity_edit(
            "monitor rules", patch, title=entity_title("monitors", "changed")
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

        return self._commit_entity_edit(
            "monitor rules", rename, title=entity_title("monitors", "changed")
        )

    def remove_monitor_rule(self, output: str) -> bool:
        """Delete the rule whose identity is `output`."""

        def drop() -> None:
            rules = self._model.entities.monitors
            rules[:] = [rule for rule in rules if rule.output != output]

        return self._commit_entity_edit(
            "monitor rules", drop, title=entity_title("monitors", "removed")
        )

    def restore_monitor_rules(self, snapshot: Sequence[MonitorRule]) -> bool:
        """Put the monitor rule list back to `snapshot`, through a normal transaction.

        The revert half of Confirm-or-revert (ADR-0008), given `revert_breaking`'s list so a
        benign edit made during the countdown survives (#192): a normal Apply rather than a file
        restore, because rendering the previous model produces the previous `monitors.lua`
        byte for byte -- same renderer, same input -- and a second way for bytes to reach
        the App dir would be a second place for bugs to live (ADR-0010 made the same call
        for undo).
        """

        def put_back(rules: list[MonitorRule]) -> None:
            rules[:] = list(snapshot)

        return self.edit_monitor_rules(put_back, title=entity_title("monitors", "changed"))

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

        replacing = original is not None and any(
            existing.workspace == original for existing in rules
        )

        def save() -> None:
            for index, existing in enumerate(rules):
                if existing.workspace == original:
                    rules[index] = rule
                    return
            rules.append(rule)

        title = entity_title("workspace_rules", "changed" if replacing else "added")
        return self._commit_entity_edit("workspace rules", save, title=title)

    def remove_workspace_rule(self, selector: str) -> bool:
        """Delete the rule whose identity is `selector`."""

        def drop() -> None:
            rules = self._model.entities.workspace_rules
            rules[:] = [rule for rule in rules if rule.workspace != selector]

        return self._commit_entity_edit(
            "workspace rules", drop, title=entity_title("workspace_rules", "removed")
        )

    # --- declarative entities (#70) -----------------------------------------------------

    DECLARATION_KINDS: tuple[str, ...] = (
        "curves",
        "animations",
        "gestures",
        "devices",
        "env",
        "permissions",
        "startup",
        "plugins",
    )
    """The Entity kinds the one generic list API below serves, named as `EntitySet` names.

    One parameterised API rather than seven copies of `edit_rules`, for the reason
    `rules(kind)` gives: every caller is already parameterised by kind, because the seven
    Pages are one Page class seven times over a field catalogue. The names are `EntitySet`'s
    own attribute names so this list and that dataclass cannot drift into disagreeing about
    what a kind is called. `plugins` is the eighth (#174): its editor is a group on the
    Scripting Page rather than a Page of its own, but its list is edited the same way.
    """

    def declarations(self, kind: str) -> list[Any]:
        """The live list for one declarative Entity kind."""
        if kind not in self.DECLARATION_KINDS:
            raise ValueError(f"unknown declaration kind {kind!r}")
        entities: list[Any] = getattr(self._model.entities, kind)
        return entities

    def edit_declarations(
        self, kind: str, mutate: Callable[[list[Any]], None], *, title: str | None = None
    ) -> bool:
        """Change one declarative list and write it, returning whether it was accepted.

        Shaped exactly like `edit_rules`, refusal and undo step and all.
        """
        items = self.declarations(kind)
        return self._commit_entity_edit(kind, lambda: mutate(items), title=title)

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
        return self.edit_declarations(
            kind, lambda items: items.append(entity), title=entity_title(kind, "added")
        )

    def replace_declaration(self, kind: str, index: int, entity: Any) -> bool:
        """Replace the entity at `index`, keeping its position."""
        if self._identity_taken(kind, entity, index=index):
            return False

        def swap(items: list[Any]) -> None:
            if 0 <= index < len(items):
                items[index] = entity

        return self.edit_declarations(kind, swap, title=entity_title(kind, "changed"))

    def remove_declaration(self, kind: str, index: int) -> bool:
        """Delete the entity at `index`."""

        def drop(items: list[Any]) -> None:
            if 0 <= index < len(items):
                del items[index]

        return self.edit_declarations(kind, drop, title=entity_title(kind, "removed"))

    def move_declaration(self, kind: str, index: int, to: int) -> bool:
        """Move the entity at `index` to position `to`: the plugin list's reorder (#174).

        A move, as `move_rule` is, because that is what a drag is; a move off either end
        or onto itself changes nothing and so records nothing.
        """

        def shift(items: list[Any]) -> None:
            if 0 <= index < len(items) and 0 <= to < len(items) and index != to:
                items.insert(to, items.pop(index))

        return self.edit_declarations(
            kind, shift, title=entity_title(kind, "reordered", plural=True)
        )

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
        # Every Option, including the unbounded ones: a field that shadows one bounded and
        # one unbounded Option has no bound, and filtering here would hide the second and
        # impose the first (`device_field_bounds`).
        return device_field_bounds(
            {
                option.name: (
                    (option.range.min, option.range.max)
                    if option.range is not None
                    else (None, None)
                )
                for option in self._schema.options
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

    @property
    def monitor_profiles_revision(self) -> int:
        """Moves whenever a profile is saved, updated or deleted: the finder re-lists on it."""
        return self._profile_store.revision

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

        The one Entity commit that records no undo step, and it forgets every step over
        the two lists, held ones included (#189). The pointer lives outside the model, so a
        model-level undo (ADR-0010) could not take an activation back whole, and replaying
        an older display step over the profile's lists would quietly undo half of it. The
        activation's own take-back is its Confirm-or-revert countdown (ADR-0015).
        """
        if self._refuse("monitor profile"):
            return False
        self._model.entities.monitors[:] = list(monitors)
        self._model.entities.workspace_rules[:] = list(workspaces)
        self._forget_entities(DISPLAY_KINDS)
        self._applier.commit_entities()  # type: ignore[union-attr]  # _refuse proved it is here
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

        Both lists and the pointer because activation touches workspace pins and the
        pointer too: reverting an activation that only put the monitor list back would
        leave the pins of the profile the user just refused. A tuple of frozen rules, so
        the snapshot cannot drift while a countdown runs however many edits land in it.
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

    # --- presets ----------------------------------------------------------------------------

    @property
    def _preset_store(self) -> PresetStore:
        store = self._presets
        if store is None:
            store = self._presets = PresetStore(self._paths.presets_dir)
        return store

    def presets(self) -> tuple[tuple[str, Preset], ...]:
        """Every saved Preset as `(slug, preset)`, sorted by name (ADR-0014)."""
        return self._preset_store.list()

    @property
    def presets_revision(self) -> int:
        """Moves whenever a Preset is saved, replaced or deleted. Presets are files, not
        model state, so nothing else announces it: a reader pulls this when it lists."""
        return self._preset_store.revision

    def scope_size(self, scope: CaptureScope) -> int:
        """How many Options `scope` captures in the loaded Schema: the checklist's count."""
        return len(scoped_options(self._schema, scope))

    def delete_preset(self, slug: str) -> str | None:
        """Remove a Preset's file. Allowed on a read-only session, as saving is.

        `None` once it is gone; otherwise why it is still there, as a sentence (finding 23
        of the #153 review: the user saw nothing and the row stayed)."""
        try:
            self._preset_store.delete(slug)
        except OSError as error:
            why = (error.strerror or str(error)).lower()
            return f"Its file could not be removed ({why}), so it is still in your presets."
        return None

    def save_preset(
        self,
        name: str,
        scopes: Collection[CaptureScope],
        *,
        replace: bool = False,
        done: Callable[[PresetSaveResult], None],
    ) -> None:
        """Capture the chosen scopes as a Preset named `name`, and report through `done`.

        Live, each scoped Option is read off the compositor (ADR-0014: colours are frozen to
        the values live at capture, whatever generated them -- a Bridge module sets colours
        the model never holds), so `done` runs once the reads are back. Not live, the model's
        set values are saved and `done` runs before this returns. Either way an Option that
        nothing sets is never saved: a Preset holds values, and applying one never unsets.

        Allowed on a read-only session: a Preset is a file in the App dir, not a config
        write. An existing name's slug answers `PresetNameTaken` unless `replace`.
        """
        name = name.strip()
        if not name:
            done(PresetNotSaved("Give the preset a name."))
            return
        if len(name) > MAX_PRESET_NAME:
            done(
                PresetNotSaved(f"A preset's name can be at most {MAX_PRESET_NAME} characters.")
            )
            return
        chosen = frozenset(scopes)
        if not chosen:
            done(PresetNotSaved("Choose at least one thing to save."))
            return
        options = tuple(
            option
            for scope in CaptureScope
            if scope in chosen
            for option in scoped_options(self._schema, scope)
            if option.name not in self._retired
        )
        client = self._client
        if not self.live or client is None:
            if CaptureScope.COLORS in chosen and isinstance(
                self.color_source(), Wallpaper | Several
            ):
                # The model holds the user's colours, not the ones the wallpaper made (S7).
                done(
                    PresetNotSaved(
                        f"Wallpaper colors can only be captured while applying is on. "
                        f"{self.offline_sentence}"
                    )
                )
                return
            if CaptureScope.WALLPAPER in chosen:
                done(
                    PresetNotSaved(
                        "The wallpaper can only be saved while applying is on. "
                        f"{self.offline_sentence}"
                    )
                )
                return
            values = {
                option.name: stored_value(value)
                for option in options
                if (value := self._model.get(option.name)) is not UNSET
            }
            if not values and not self._model_read:
                # Empty because nothing was read, not because it is all default (#148
                # hand-test 12): saying "at Hyprland's default" would be false.
                done(
                    PresetNotSaved(
                        f"Your settings have not been read, so there is nothing to save. "
                        f"{self.offline_sentence or ''}".rstrip()
                    )
                )
                return
            done(self._write_preset(name, chosen, values, replace=replace))
            return
        daemon = self._wallpapers.detect() if CaptureScope.WALLPAPER in chosen else None
        if CaptureScope.WALLPAPER in chosen and daemon is None:
            done(
                PresetNotSaved(
                    f"The wallpaper could not be saved. {self._wallpapers.absent_reason()}"
                )
            )
            return

        async def capture() -> None:
            try:
                values = await self._live_values(client, options)
            except IpcError as error:
                _log.warning("preset capture failed: %s", error)
                done(PresetNotSaved("Hyprland stopped answering, so nothing was saved."))
                return
            image: str | None = None
            if daemon is not None:
                try:
                    shown = await asyncio.to_thread(daemon.current)
                except WallpaperError as error:
                    done(PresetNotSaved(f"The wallpaper could not be saved. {error}"))
                    return
                if not shown:
                    done(PresetNotSaved(f"{daemon.name} is not showing an image to save."))
                    return
                image = str(shown[0].image)
            done(self._write_preset(name, chosen, values, replace=replace, wallpaper=image))

        self._spawn(capture())

    async def _live_values(
        self, client: CommandClient, options: Sequence[ResolvedOption]
    ) -> dict[str, Any]:
        """What the compositor shows for each of `options`, as the Preset file holds it.

        An Option the running config does not set is skipped unless the model sets it: that
        is "at Hyprland's default", and saving it would freeze a default as a choice. The
        model's explicit null is its own statement, as in `read_state`.
        """
        values: dict[str, Any] = {}
        for option in options:
            model = self._model.get(option.name)
            try:
                reply = await client.getoption(option.name)
            except NoSuchOption:
                continue
            if model is None:
                values[option.name] = None
                continue
            # The re-read's own reading of a reply (F17 of the #148 review).
            answer = classify_reply(option, reply)
            if answer.kind is Answer.VALUE:
                values[option.name] = stored_value(answer.value)
                continue
            if answer.kind is Answer.NO_VALUE:
                values[option.name] = None
                continue
            if model is not UNSET:
                values[option.name] = stored_value(model)
        return values

    def _write_preset(
        self,
        name: str,
        scopes: frozenset[CaptureScope],
        values: Mapping[str, Any],
        *,
        replace: bool,
        wallpaper: str | None = None,
    ) -> PresetSaveResult:
        if not values and wallpaper is None:
            return PresetNotSaved(
                "Nothing to save: everything you chose is at Hyprland's default."
            )
        store = self._preset_store
        slug = store.slug_for(name)
        if not replace and store.exists(slug):
            existing = store.load(slug)
            return PresetNameTaken(slug, existing.name if existing is not None else slug)
        live = self._live_hyprland
        preset = Preset(
            name=name,
            created=datetime.now(UTC),
            scopes=scopes,
            options=values,
            app_version=self._app_version,
            hyprland_version=live.version
            if live is not None
            else self._schema.hyprland_version,
            wallpaper=wallpaper,
        )
        try:
            store.write(slug, preset)
        except OSError as error:
            _log.warning("could not write preset %s: %s", slug, error)
            return PresetNotSaved(f"The preset could not be saved: {error.strerror or error}.")
        return PresetSaved(slug, preset)

    def preset_color_conflict(self, preset: Preset) -> Wallpaper | Several | None:
        """The wallpaper source a Preset's Colors would fight, or `None` (ADR-0014).

        Not `None` exactly when the Preset holds a colour and a wallpaper sets the colours
        now: then `apply_preset` needs a `ColorChoice`. For the import preview to ask before
        anything is saved, as the Presets group asks before applying.
        """
        colours = {option.name for option in scoped_options(self._schema, CaptureScope.COLORS)}
        if colours.isdisjoint(preset.options):
            return None
        source = self.color_source()
        return source if isinstance(source, Wallpaper | Several) else None

    def apply_preset(
        self, slug: str, *, colors: ColorChoice | None = None, wallpaper: bool = False
    ) -> PresetApplyResult:
        """Set every Option the Preset holds, as one gesture: one Apply transaction, one step.

        One gesture because the edits are made in one synchronous burst: the queue's worker
        runs on this loop, so it takes every key in one batch, and the step it records is a
        `PresetStep` that one Ctrl+Z takes back whole. A Preset holds set values only, so
        applying never unsets an Option the Preset does not name.

        Colors while a wallpaper sets them (`preset_color_conflict`): without `colors`,
        nothing is applied and the answer is `PresetColorConflict`. `USE_PRESET` gates the
        wallpaper's Bridge in the same transaction (S6), so the source becomes Preset;
        `KEEP_WALLPAPER` applies everything but the colours.

        `wallpaper` is "change" rather than "keep mine": the Preset's image is shown through
        the running daemon once the transaction stands, never before. When it cannot be,
        `on_preset_note` says why and the rest still applies.

        What this session cannot set is skipped and named in the result, never fatal (ADR-0014
        §Sharing). Refused, with the Banner's reason, on a read-only session.
        """
        if not self.live or self._applier is None:
            return PresetNotApplied(
                f"Applying is off. {self.offline_sentence or 'This app is not connected.'}"
            )
        preset = self._preset_store.load(slug)
        if preset is None:
            return PresetNotApplied("This preset could not be read. It may have been deleted.")
        conflict = self.preset_color_conflict(preset)
        if conflict is not None and colors is None:
            return PresetColorConflict(conflict)
        source: SourceChange | None = None
        kept: frozenset[str] = frozenset()
        if conflict is not None and colors is ColorChoice.USE_PRESET:
            if (blocked := self.color_source_blocked) is not None:
                return PresetNotApplied(blocked)
            bridges = self._manifest().bridges
            source = SourceChange(
                bridges,
                tuple(
                    bridge_states_for(
                        PresetColors(), bridges, present=self._bridge_files_present(bridges)
                    )
                ),
            )
        elif conflict is not None:
            kept = frozenset(o.name for o in scoped_options(self._schema, CaptureScope.COLORS))
        values, _unknown, _invalid = self._preset_values(preset)
        skipped = [name for name in preset.options if name not in values and name not in kept]
        values = {name: value for name, value in values.items() if name not in kept}
        if skipped:
            _log.warning("preset %s: skipped %s", slug, ", ".join(skipped))
        order = self._wallpaper_order(preset) if wallpaper else None
        if values:
            self._applying_presets.append(
                _AppliedPreset(
                    preset.name,
                    {name: self._model.get(name) for name in values},
                    dict(values),
                    source,
                    order,
                )
            )
            if source is not None:
                # Manifest only: this transaction's write renders the gated Entrypoint, so
                # one reload lands the colours and the source together (S6).
                self._writer.record_bridges(self._model, source.after)
            for name, value in values.items():
                self.set_option(name, value)
        elif order is not None:
            # Nothing to write, so no transaction to wait for.
            self._spawn(self._show_preset_wallpaper(preset.name, order, None))
        return PresetApplied(tuple(values), tuple(skipped))

    def _wallpaper_order(self, preset: Preset) -> _WallpaperOrder | None:
        """The Preset's image and the daemon to show it, or `None` after saying why not."""
        if preset.wallpaper is None:
            return None
        image = Path(preset.wallpaper)
        if not image.is_absolute():
            image = self._paths.app_dir / image
        if not image.is_file():
            self._say_preset(
                f"The wallpaper was not changed. {tilde_path(image, self._paths)} is missing."
            )
            return None
        daemon = self._wallpapers.detect()
        if daemon is None:
            reason = self._wallpapers.absent_reason()
            self._say_preset(f"The wallpaper was not changed. {reason}")
            return None
        return _WallpaperOrder(daemon, image)

    def _say_preset(self, text: str) -> None:
        if self.on_preset_note is not None:
            self.on_preset_note(text)

    async def _show_preset_wallpaper(
        self, name: str, order: _WallpaperOrder, step: PresetStep | None
    ) -> None:
        """Show the Preset's image, after reading what it replaces, and add that to `step`.

        Off the main loop: `query` and `img` are processes, and `img` decodes the image.
        The step is already on the stack (the transaction stood), so it is replaced by one
        that also puts the wallpaper back; with no step, the wallpaper is the whole gesture.
        """
        try:
            before = await asyncio.to_thread(order.daemon.current)
        except WallpaperError as error:
            _log.warning("could not read the wallpaper before setting it: %s", error)
            before = ()
        try:
            await asyncio.to_thread(order.daemon.set, order.image)
        except WallpaperError as error:
            self._say_preset(f"The wallpaper was not changed. {error}")
            return
        change = WallpaperChange(before, order.image)
        if step is not None:
            if not self._undo.replace(step, replace(step, wallpaper=change)):
                # Ctrl+Z came while the daemon was still showing the image: the user has
                # already asked for the look before, so that is shown now (finding 12).
                said = await self._put_wallpaper_back(change)
                if said:
                    self._say_preset(" ".join(["Settings restored.", *said]))
            return
        recorded = PresetStep.of(name, None, wallpaper=change)
        self._undo.record(recorded)
        if recorded is not None and self.on_recorded is not None:
            self.on_recorded(recorded)

    def _preset_values(
        self, preset: Preset
    ) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
        """The Preset's values this session can set, parsed; then the names it cannot set
        (`unknown`) and the values that do not parse (`invalid`), each in file order.

        The one place a stored value is read, so the preview and the apply cannot disagree
        about what a Preset does. Every value enters through `parse_value`: a Preset may
        come from another machine (#169), and its strings reach the Writer's Lua.
        """
        values: dict[str, Any] = {}
        unknown: list[str] = []
        invalid: list[str] = []
        for name, raw in preset.options.items():
            option = self._schema.get(name)
            if option is None or name in self._retired or self.unknown_to_version(option):
                unknown.append(name)
            elif raw is None and not option.nullable:
                invalid.append(name)
            else:
                try:
                    values[name] = None if raw is None else parse_value(option.type, raw)
                except (ValueError, TypeError):
                    invalid.append(name)
        return values, tuple(unknown), tuple(invalid)

    def preview_preset(self, preset: Preset) -> PresetPreview:
        """What applying `preset` would change, Option by Option, grouped by Section.

        Reads only: nothing is written and nothing is queued, so a Theme archive can be
        shown before the user has agreed to anything (#169). `before` is what the Row
        shows today, the model's value or Hyprland's default, and it is compared typed,
        so a colour spelled two ways is not a change.
        """
        values, unknown, invalid = self._preset_values(preset)
        changed: dict[str, list[PresetChange]] = {}
        unchanged: list[str] = []
        for name, after in values.items():
            option = self._schema[name]
            before = _typed(option, self.effective_value(option))
            if before == after:
                unchanged.append(name)
            else:
                changed.setdefault(option.section, []).append(
                    PresetChange(option, before, after)
                )
        sections = tuple(
            PresetSection(
                section,
                self._schema.section_title(section),
                tuple(sorted(changed[section], key=lambda change: change.option.order)),
            )
            for section in self._schema.section_names
            if section in changed
        )
        return PresetPreview(sections, tuple(unchanged), unknown, invalid)

    def import_name(self, preset: Preset) -> str:
        """The name an import of `preset` lands under: its own, or "<name> 2" when taken."""
        return self._preset_store.free_name(preset.name)[0]

    def import_preset(self, archive: ThemeArchive) -> PresetImportResult:
        """Add a Theme archive's Preset to the store, its wallpaper beside it. Applies nothing.

        Never an overwrite: a taken name is kept, and the import becomes "<name> 2"
        (`PresetStore.add`). Allowed on a read-only session, as saving is. The caller asks
        first (`ui/dialogs/theme_import.py`), then applies with `apply_preset(slug)`.
        """
        image = archive.wallpaper
        try:
            slug, preset = self._preset_store.add(
                archive.preset, None if image is None else (image.extension, image.data)
            )
        except OSError as error:
            _log.warning("could not import preset %s: %s", archive.preset.name, error)
            return PresetNotImported(
                f"The theme could not be added to your presets: {error.strerror or error}."
            )
        return PresetImported(slug, preset)

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

    def fetch_switches(
        self, done: Callable[[tuple[Mapping[str, Any], ...] | None], None]
    ) -> None:
        """Live switch devices for the switch picker, or `None` when unanswerable.

        Helper data only, never rule state (ADR-0008). `None` is "nobody is there to ask";
        `()` is "the compositor answered and has no switch", and the picker words the two
        differently: the first points at manual entry, the second explains the empty list.
        """
        self._fetch_helper_data("switches", lambda client: client.switches(), done)

    def fetch_loaded_plugins(self, done: Callable[[tuple[str, ...] | None], None]) -> None:
        """The names of the plugins Hyprland has loaded right now, or `None` if unanswerable.

        Asked every time and never cached: a reload loads and unloads plugins, so the only
        current answer is a fresh one. `hyprctl plugin list` rather than the `eval` of
        `hl.get_loaded_plugins()` ADR-0018 first named: on 0.56.2 `eval` answers `ok`
        whatever the Lua prints or returns, and `eval` clears `configerrors` on entry, while
        `plugin list` is a plain read that is safe between a reload and its read-back
        (probed on a nested instance, #174).
        """
        self._fetch_helper_data("plugins", lambda client: client.loaded_plugins(), done)

    def _fetch_helper_data(
        self,
        what: str,
        query: Callable[[CommandClient], Coroutine[Any, Any, _Answer]],
        done: Callable[[_Answer | None], None],
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

        A Retired Option is declined on a live session too, for every retirement reason
        (#215): Hyprland does not take it, so a write is a config error and an auto-revert,
        and a value in the model would also shadow the one the Manifest keeps for it. Its
        Row is read-only; this holds the line for every other caller (Search, scripts).
        An Option merely Not in this Hyprland is not retired and is not refused here: its
        Reset is the user's way out of the config error the key raises.
        """
        why = self._refusal(name)
        if why is None:
            return False
        _log.debug("refusing the edit to %s: %s", name, why)
        return True

    def _refusal(self, name: str) -> str | None:
        """Why an edit to `name` is declined, or `None` when it may go ahead."""
        if not self.live or self._applier is None:
            return "the session is read-only"
        if name in self._retired:
            return "it is Retired; the Manifest keeps its value"
        return None

    # --- undo -------------------------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        """Whether there is a gesture to take back, and a live session to take it back in."""
        return self.live and self._undo.can_undo

    @property
    def last_gesture(self) -> Step | None:
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

        `False` without touching the stack while an Entity edit is in flight -- the undo
        then waits for it (`undo_queued`) and takes it back once it lands -- or while an
        undo group holds edits over the top step's lists.
        """
        if not self.live or self._applier is None:
            return False
        in_flight = [p.step for p in self._pending_entities if p.group is None]
        if in_flight:
            # The newest gesture is still being written, so the stack top is not "the last
            # one" yet -- and an Entity step beneath it would read as stale and be dropped
            # (review of #151, finding 12). Undo that gesture once it lands instead.
            self._undo_waits_for = in_flight[-1]
            return False
        if self._held_over(self._undo.top):
            # A group (a display countdown) holds edits over the top step's lists: undoing
            # under it would find the step stale and drop it. The window reverts instead.
            return False
        step = self._undo.pop()
        if step is None:
            return False
        if isinstance(step, EntityStep):
            return self._undo_entities(step)
        if isinstance(step, PresetStep):
            return self._undo_preset(step)

        self._restore({edit.name: edit.before for edit in step.edits})
        self._applier.commit(*step.names)
        self._changed()
        return True

    def _undo_preset(self, step: PresetStep) -> bool:
        """Put back everything a Preset changed: Options and Color source in one Apply
        transaction, then the wallpaper once that stands (S7).

        A Color source changed since the apply is the user's newer choice and stays; so does
        a wallpaper changed since. `on_preset_note` says which, after "Settings restored."
        """
        notes: list[str] = []
        source = step.color_source
        current = self._manifest().bridges
        if source is not None and (
            _gates(current) != _gates(source.after) or self.color_source_blocked is not None
        ):
            notes.append("Wallpaper colors were changed since, so they stay as they are.")
            source = None
        elif source is not None:
            # Only the gates go back: an entry whose tool has written its first file since
            # stays loaded rather than waiting again (addendum 36 of the #153 review).
            present = self._bridge_files_present(source.before)
            source = SourceChange(tuple(with_presence(source.before, present=present)), current)
        if step.options is None:
            # The values matched already, so there are no Options to carry the gate back:
            # the Entrypoint goes back on its own transaction (`set_color_source`'s).
            if source is not None:
                before = source.before
                self._set_bridges(
                    lambda _current: before,
                    self._manifest().with_bridges(before),
                    "put the Color source back",
                )
            self._spawn(self._finish_preset_undo(step.wallpaper, tuple(notes)))
            return True
        if source is not None:
            self._writer.record_bridges(self._model, source.before)
        self._undoing_presets.append(
            _UndonePreset(frozenset(step.options.names), source, step.wallpaper, tuple(notes))
        )
        self._restore({edit.name: edit.before for edit in step.options.edits})
        self._applier.commit(*step.options.names)  # type: ignore[union-attr]  # undo checked
        self._changed()
        return True

    async def _finish_preset_undo(
        self, wallpaper: WallpaperChange | None, notes: tuple[str, ...]
    ) -> None:
        """Put the wallpaper back if it still shows the Preset's, then say what stayed."""
        said = list(notes)
        if wallpaper is not None:
            said.extend(await self._put_wallpaper_back(wallpaper))
        if said:
            self._say_preset(" ".join(["Settings restored.", *said]))

    async def _put_wallpaper_back(self, change: WallpaperChange) -> list[str]:
        """Show `change.before` again; what could not be done, as sentences."""
        daemon = self._wallpapers.detect()
        if daemon is None:
            return [f"The wallpaper was not put back. {self._wallpapers.absent_reason()}"]
        try:
            now = await asyncio.to_thread(daemon.current)
            if not now or any(shown.image != change.after for shown in now):
                return ["The wallpaper was changed since, so it stays as it is."]
            if not change.before:
                return ["The wallpaper was not put back: what it was could not be read."]
            await asyncio.to_thread(daemon.show, change.before)
        except WallpaperError as error:
            return [f"The wallpaper was not put back. {error}"]
        return []

    @property
    def undo_queued(self) -> bool:
        """Whether a Ctrl+Z is waiting for an edit in flight, to undo it once it lands."""
        return self._undo_waits_for is not None

    def _held_over(self, step: Step | None) -> bool:
        """Whether an undo group holds edits, landed or in flight, over `step`'s lists."""
        if not isinstance(step, EntityStep):
            return False
        held = [p.step for p in self._pending_entities if p.group is not None]
        if self._undo_group is not None:
            held += self._undo_group.held
        return any(each.kinds & step.kinds for each in held)

    def _undo_when_landed(self) -> bool:
        """Run the undo a Ctrl+Z left waiting, if this result landed its edit. `True` if so.

        Only while that edit is the stack top and nothing newer is in flight: a newer edit,
        or a failed one, means the gesture the user pressed Ctrl+Z over is gone or no longer
        the last, and the wait is dropped rather than undoing something else.
        """
        waited = self._undo_waits_for
        if waited is None or any(p.step is waited for p in self._pending_entities):
            return False
        self._undo_waits_for = None
        if self._undo.top is not waited or any(p.group is None for p in self._pending_entities):
            return False
        (self.on_undo_due or self.undo)()
        return True

    def _undo_entities(self, step: EntityStep) -> bool:
        """Put each list back to `before` and write it -- or refuse a step gone stale.

        Stale means a list no longer reads as the step left it: something the stack never
        saw changed it, and writing `before` over it would undo that change too. The step is
        dropped rather than kept, because it can never become replayable again. The write
        is a bare `commit_entities`, outside `_commit_entity_edit`, so it records nothing.
        """
        entities = self._model.entities
        if any(tuple(getattr(entities, edit.kind)) != edit.after for edit in step.edits):
            _log.info("undo: %r is stale, its list changed since; dropped", step.title)
            return False
        for edit in step.edits:
            getattr(entities, edit.kind)[:] = edit.before
        self._applier.commit_entities()  # type: ignore[union-attr]  # undo checked it is here
        self._changed()
        return True

    def begin_undo_group(self, kinds: frozenset[str]) -> UndoGroup:
        """Hold every Entity step over `kinds` until `end_undo_group` makes it one, or none.

        For a caller whose several commits are one gesture to the user -- a Confirm-or-revert
        countdown: kept, its edits are one step; reverted (the revert committed inside the
        group), they cancel out and nothing is recorded. Held steps raise no toast. Steps
        over other lists go to the stack as usual. One group at a time.
        """
        if self._undo_group is not None:
            raise RuntimeError("an undo group is already open")
        group = self._undo_group = UndoGroup(frozenset(kinds))
        return group

    def end_undo_group(self, group: UndoGroup, *, title: str) -> None:
        """Close `group`: push what it held as one step titled `title`, if anything moved.

        Commits still in flight are waited for -- the merge happens when the last one
        reports. If any of the group's transactions failed, nothing is pushed. Ending a group
        twice is a no-op.
        """
        if group.title is not None:
            return
        group.title = title
        if self._undo_group is group:
            self._undo_group = None
        self._announce(self._close_group(group))

    def _close_group(self, group: UndoGroup) -> EntityStep | None:
        """The merged step, once `group` is ended and nothing it holds is in flight."""
        if group.finished or group.title is None:
            return None
        if any(pending.group is group for pending in self._pending_entities):
            return None
        group.finished = True
        if group.failed:
            return None
        return EntityStep.merge(group.held, group.title)

    def _announce(self, step: EntityStep | None) -> None:
        if step is None:
            return
        self._undo.record(step)
        if self.on_recorded is not None:
            self.on_recorded(step)

    def _forget_entities(self, kinds: Collection[str]) -> None:
        """Drop every Entity step over `kinds`: stacked, in flight and held in a group.

        For lists changed off the stack (S1.5 of #151): a foreign reload adopted a hand edit,
        or a profile was activated. Replaying any of those steps would overwrite the change.
        """
        gone = frozenset(kinds)
        self._undo.forget(gone)
        groups = {id(p.group): p.group for p in self._pending_entities if p.group is not None}
        self._pending_entities = [
            pending for pending in self._pending_entities if not pending.step.kinds & gone
        ]
        if self._undo_group is not None:
            groups[id(self._undo_group)] = self._undo_group
        for group in groups.values():
            group.held = [step for step in group.held if not step.kinds & gone]
            # A dropped in-flight step may have been all an ended group was waiting for.
            self._announce(self._close_group(group))

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
        """Connect, recover the model, and begin applying. Returns immediately.

        A Hyprland too old for a Lua config is not connected to at all: `_go_live` ends by
        clearing the read-only reason, and this one has to outlive it.
        """
        if self._unsupported_reason is not None:
            self.set_read_only(self._unsupported_reason)
            return
        if (missing := lua_missing_reason()) is not None:
            # Ruling A1 of the #148 review (F9): without Lua the app cannot read its own
            # Modules back, and a write from a model it could not read drops the keys it
            # missed. Read-only, saying what to install.
            self._lua_missing = missing
            self.set_read_only(missing)
            return
        if self._applier is None and self._offline_reason != _NOT_CONNECTED_YET:
            # A reason set before the start (a first-run offer that has since been kept) is
            # no longer why; until `_go_live` answers, connecting is.
            self._offline_reason = _NOT_CONNECTED_YET
            self._changed()
        self._spawn(self._go_live())

    def set_read_only(self, reason: str, *, sentence: str | None = None) -> None:
        """Declare the session read-only for a reason it could not discover itself.

        The app knows one such reason: without PyGObject's asyncio integration there is no
        loop to run a transaction on, so no amount of connecting would help. Saying so up
        front beats leaving the Banner on "Connecting to Hyprland…" forever.
        """
        self._offline_sentence = sentence
        self._go_offline(reason)
        self._changed()

    async def _go_live(self) -> None:
        try:
            instance = self._connect()
        except NoInstance as error:
            await self._read_files()
            # The reason in the user's words; the variable name is for the log (hand-test 2).
            _log.info("no compositor: %s", error)
            self.set_read_only("Hyprland is not running in this session")
            return

        events = EventStream(instance, on_lost=self._on_stream_lost)
        client = CommandClient(instance)
        try:
            await events.start()
            await self._recover(client)
        except IpcError as error:
            await events.aclose()
            await self._read_files()
            _log.warning("%s is not answering: %s", instance.command_socket, error)
            self.set_read_only("Hyprland is not answering")
            return

        if self._live_hyprland is None:
            # The startup read missed (#214), so the Schema lacks what a supplement would
            # have added. Asked before the Applier exists, so nothing writes in between, and
            # before retirement, which tells "removed" from "not in this schema" by it.
            self._live_hyprland = await fetch_live_hyprland(client)
        # Before the Applier exists: the first write rewrites the Modules the scan reads.
        await self._scan_drift(client)

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
        self._retire_and_restore(self._applier)
        self._offline_reason = None
        self._offline_sentence = None
        # A tool that ran while the app was closed may have written its file (S4).
        self.load_waiting_bridges()
        self._changed()

    def _retire_and_restore(self, applier: Applier) -> None:
        """ADR-0012 §Retirement, once per start, before the session's first write.

        The first write rewrites each Module from the model, which cannot hold a retired
        Option, so the value is read out of the Module and kept in the Manifest first. Then
        a write without the key clears the config error a removed key raises, and puts back
        any kept value whose Option has returned or been renamed. A restored value leaves
        the Manifest only once that write has put it in a Module (`landed`), so stopping in
        between loses nothing: the next start finds it kept and restores it again.
        """
        live = self._live_hyprland
        before = self._manifest()
        found = retirement.detect(before, self._schema, live)
        values = retirement.capture(self._paths.app_dir, found)
        kept = retirement.retire(before, found, values)
        remaining, restored = retirement.restore(kept, self._schema, live)
        if kept.retired != before.retired:
            self._writer.set_retired(self._model, kept.retired)
        self._retired = remaining.retired
        for each in restored:
            self._model.set(each.option.name, each.value)
        if found or restored:
            self._spawn(self._write_retirement(applier, restored))

        notices: list[Notice] = [
            *retirement.unannounced(remaining),
            *UnkeptNotice.of(found, values),
        ]
        if (renamed := RenamedNotice.of(restored)) is not None:
            notices.append(renamed)
        for notice in notices:
            if self.on_notice is not None:
                self.on_notice(notice)

    async def _write_retirement(
        self, applier: Applier, restored: Sequence[Restoration]
    ) -> None:
        """Write the model without the retired keys and with the restored values, then
        stop keeping each restored value the write recorded."""
        # Dropping a retired key changes the Module, not any Option the model holds.
        applier.force_write()
        await applier.apply(*(each.option.name for each in restored))
        if restored:
            self._writer.set_retired(
                self._model, retirement.landed(self._manifest(), restored).retired
            )

    def _manifest(self) -> Manifest:
        return load_manifest(
            self._paths,
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
        result = await self._read_model(client, owned, launch=True)
        _log.info(
            "recovered %d option(s) from %d owned; %d unreadable, %d unknown",
            len(result.adopted),
            len(owned),
            len(result.unreadable),
            len(result.unknown),
        )
        self._model_read = True
        # "On launch ... the full re-read + drift scan attributes any errors and raises the
        # same Banner" (ADR-0016 §Surfacing). Breakage that happened while the app was closed
        # is not a lesser kind of breakage, and the app has to open saying so.
        await self._scan(client)
        return result

    async def _read_files(self) -> None:
        """With no compositor to ask, read the App dir's own Modules, read-only.

        So the window shows the user's settings rather than Hyprland's defaults, and lists
        their binds and rules rather than "none yet" (#148 hand-test 12). Every options
        Module the Manifest records is read, hand-edited or not: nothing is written from
        this, and a hand edit is what the file says. Without Lua there is nothing to read
        the Options with, and `model_read` stays false so nothing claims otherwise.
        """
        if not self._paths.manifest.is_file():
            return
        self._load_entities()
        try:
            values = await asyncio.to_thread(
                overrides.written_values,
                self._paths.app_dir,
                self._schema,
                self._manifest(),
                verified=False,
            )
        except LuaUnavailable as error:
            _log.warning("no Lua and no Hyprland, so the settings cannot be read: %s", error)
            return
        for name, value in values.items():
            if value is None:
                self._model.set_null(name)
            else:
                self._model.set(name, value)
        self._model_read = True

    @property
    def model_read(self) -> bool:
        """Whether the model holds the user's config: read at launch, live or off the files.

        False before that, for a config not converted yet, and offline without Lua. What
        Export and an offline Save-as-preset ask before acting on the model (F6 of the
        #148 review): an empty model is not "everything at Hyprland's default".
        """
        return self._model_read

    async def _read_model(
        self, client: CommandClient, options: Sequence[ResolvedOption], *, launch: bool
    ) -> ReRead:
        """Bring the model into step with the config, never taking an override as the user's.

        The one re-read behind launch, a foreign reload and a timed-out transaction
        (finding 11 of the #153 review). The live value of a key is the user's own only when
        nothing loaded after the app's Module set it, and the app cannot see that from
        `getoption`: `user.lua` and a theming tool's Bridge module both load later. So:

        - A key an app Module sets whose bytes still hash to the Manifest (`verified`) takes
          the Module's value: the user set it here, and an override of it is the drift
          scan's to badge, not the model's to adopt. At launch the value is read off the
          Module; after a reload the model already holds it, since it rendered the Module.
        - A key a loading Bridge module sets (`bridge_owners`) is never read live: the
          answer is the tool's colour.
        - Every other key -- one in a hand-edited Module, or one only `user.lua` names -- is
          read live, as before, so a hand edit is adopted rather than written over.

        At launch without a Lua interpreter the Modules cannot be read, so verified keys are
        read live too, still leaving out the Bridge-owned ones.
        """
        manifest = self._manifest()
        tools = owners(manifest.bridges, quarantined=manifest.quarantined)
        if not launch:
            pinned = overrides.verified_options(self._paths.app_dir, manifest)
            live = [o for o in options if o.name not in pinned and o.name not in tools]
            return await read_state(self._model, client, live)
        try:
            written = await asyncio.to_thread(
                overrides.written_values, self._paths.app_dir, self._schema, manifest
            )
        except LuaUnavailable as error:
            _log.warning("no Lua, so the app's Modules are read off the compositor: %s", error)
            written = {}
        # Every key is still asked about, so one this Hyprland no longer has stays out of
        # the model (retirement reads it out of the Module next); then the Module's value
        # replaces whatever the compositor answered.
        result = await read_state(
            self._model, client, [o for o in options if o.name not in tools]
        )
        wanted = {option.name for option in options}.difference(result.unknown)
        for name, value in written.items():
            if name not in wanted:
                continue
            if value is None:
                self._model.set_null(name)
            else:
                self._model.set(name, value)
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

    async def _scan_drift(self, client: CommandClient, *, keep: Collection[str] = ()) -> None:
        """ADR-0005's drift badge for every key the app's Modules set (`overrides.py`, #191).

        At launch and after every foreign reload, after `_scan`, so the Row wears its pill
        before the user edits anything. Replaces both marks, except on keys a transaction
        read back while this ran (`_drift_watches`). A scan that cannot run -- no Lua, or a
        compositor that stopped answering -- clears them: marks from before a reload
        describe a config that has just been replaced, and no scan is no evidence.

        The Modules are evaluated in a worker thread: one Lua process per Module, and the
        main loop is the window's (#214).
        """
        watch: set[str] = set()
        self._drift_watches.append(watch)
        try:
            expected = await asyncio.to_thread(
                overrides.reference, self._paths.app_dir, self._schema, self._manifest()
            )
            mismatches = await overrides.scan(client, expected)
        except LuaUnavailable as error:
            if not self._lua_missing_logged:
                self._lua_missing_logged = True
                _log.warning("no drift scan, so no Overridden pills until an edit: %s", error)
            mismatches = ()
        except IpcError as error:
            _log.warning("could not scan for overridden settings: %s", error)
            mismatches = ()
        finally:
            self._drift_watches.remove(watch)
        self._mark(mismatches, covers=lambda name: name not in watch and name not in keep)

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
        # would have been measured against. The *stack* survives -- a recorded Option step
        # is a model delta and replays through a normal transaction whatever else has
        # happened since (ADR-0010 §Undo) -- but a half-open one would produce a delta
        # spanning somebody else's reload. Entity steps over a list the re-read changes are
        # dropped there (`_reread_after_foreign_reload`).
        self._open_gestures.clear()
        self._spawn(self._reread_after_foreign_reload())

    def adopt_import(self) -> None:
        """A kept Import into a running session: the App dir is the wizard's now (ADR-0009).

        The wizard writes every Module with its own Writer and records them in the Manifest,
        so the hash-gated re-read of a foreign reload finds nothing to adopt and the model
        keeps the config from before the import -- which the next edit would write back
        over it (F5 of the #148 review). So the model is read again as at launch, every
        Module whole, and the undo stack goes: its steps are over a config that is gone.
        A session that never went live has nothing to re-read; its start reads the files.
        """
        if self._client is None or self._applier is None:
            return
        self._spawn(self._adopt_import(self._client))

    async def _adopt_import(self, client: CommandClient) -> None:
        self._model.clear()
        self._undo = UndoStack()
        self._open_gestures.clear()
        self._pending_entities = []
        self._undo_group = None
        self._undo_waits_for = None
        self._held_back.clear()
        try:
            await self._recover(client)
        except IpcError as error:
            _log.warning("lost contact with Hyprland: %s", error)
            self.set_read_only("Lost contact with Hyprland")
            return
        await self._scan_drift(client)
        self.load_waiting_bridges()
        if self._lua_missing is None and self._unsupported_reason is None:
            # Held read-only behind an import offer (a rolled-back switch, #148 hand-test
            # 20): the import is kept now, so the session applies again.
            self._offline_reason = None
            self._offline_sentence = None
        self._changed()

    async def _reread_after_foreign_reload(self, keep: Collection[str] = ()) -> None:
        """`keep`: keys whose drift marks this re-read's scan leaves alone -- a timed-out
        transaction's, which read "Not confirmed" until a later reading covers them."""
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
            result = await self._read_model(client, stale, launch=False)
        except IpcError as error:
            _log.warning("lost contact with Hyprland: %s", error)
            self.set_read_only("Lost contact with Hyprland")
            return
        _log.info(
            "foreign reload: %d option(s) re-read, %d no longer set",
            len(result.adopted),
            len(result.cleared),
        )
        before = self._entity_lists()
        self._reread_binds()
        self._reread_rules()
        self._reread_monitors()
        self._reread_declarations()
        # An adopted hand edit changed lists the stack's steps were taken over; replaying
        # one would write its old list over the edit (ADR-0010 §Undo, amended in #189).
        after = self._entity_lists()
        self._forget_entities([kind for kind in before if before[kind] != after[kind]])
        # The other half ADR-0016 asks for: somebody else's reload can break the config just
        # as thoroughly as the app's own, and it surfaces identically.
        await self._scan(client)
        await self._scan_drift(client, keep=keep)
        # A tool run from the user's own script may have written its first file (S4).
        self.load_waiting_bridges()
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
        """Adopt hand edits to the seven declarative Modules, gated like the others.

        One gate over all seven and one load for all seven, for `_reread_rules`'s reason:
        `_load_declarations` splices misfiled entities to the kind they are, so re-reading
        one file without the others would drop whatever it found belonging to a list the
        other six own.
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
        PLUGINS_MODULE,
    )
    """The seven Modules `_load_declarations` reads: the six of #70 and `plugins.lua` (#174)."""

    def _load_declarations(self) -> bool:
        """Read the seven declarative Entity Modules into the model.

        The same shape as `_load_rules` and `_load_monitors`, one tier wider: every file
        feeds every list, so an entity someone hand-moved into the wrong Module comes back
        as what it is rather than vanishing -- and vanishing is not cosmetic here, because a
        list the model believes is empty is a Module the Writer prunes.

        All seven are adopted together or none is. Seven files is where that rule starts to
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
        plugins: list[PluginLoad] = []

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
            plugins.extend(parsed.plugins)

        _log.info(
            "read %d curve(s), %d animation(s), %d gesture(s), %d device(s), "
            "%d env var(s), %d permission(s), %d startup command(s), %d plugin(s)",
            len(curves),
            len(animations),
            len(gestures),
            len(devices),
            len(env),
            len(permissions),
            len(startup),
            len(plugins),
        )
        self._model.entities.curves[:] = curves
        self._model.entities.animations[:] = animations
        self._model.entities.gestures[:] = gestures
        self._model.entities.devices[:] = devices
        self._model.entities.env[:] = env
        self._model.entities.permissions[:] = permissions
        self._model.entities.startup[:] = startup
        self._model.entities.plugins[:] = plugins
        return True

    def _on_stream_lost(self) -> None:
        """Hyprland closed the event stream -- it has exited."""
        self.set_read_only("Hyprland is no longer running")

    def _applied(self, result: ApplyResult) -> None:
        """One transaction finished: close its gestures, then recover or record.

        The order is the whole design. Closing first turns "which Options were mid-gesture?"
        into a concrete delta; that delta is then either the thing to *take out again* (the
        transaction does not stand, `_stands`) or the thing to *remember* (it stands, so
        Ctrl+Z should be able to take it back). A gesture can never be both, which is why the
        failed one is never pushed rather than pushed and popped.
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
        held_titles = self._hold_back_options(result, delta)
        presets = self._carried_presets(result.keys)
        for preset in reversed(presets):
            # From each Preset's own snapshot, the oldest last so its value wins: an Option
            # it set while an earlier edit of it was in flight had its gesture closed by that
            # edit's transaction, and a Preset applied over another goes back past both.
            delta = {**delta, **preset.before}
        undone = self._carried_undos(result.keys)
        stands = self._stands(result)
        entity_steps, failed = self._settle_entities(result, stands=stands)
        if not stands:
            # The gate goes back first, so the auto-revert's write renders the Entrypoint
            # with the wallpaper's Bridge loading again (S6). Newest first: each one's
            # `before` is the one under it's `after`.
            for preset in reversed(presets):
                if preset.source is not None:
                    self._put_bridges(preset.source.after, preset.source.before)
            for each in reversed(undone):
                if each.source is not None:
                    self._put_bridges(each.source.before, each.source.after)
            self._fell(result, delta, self._lists_before(failed))
            return

        entity_steps, held_steps = self._hold_back_entities(result, entity_steps)
        held_titles += [step.title for step in held_steps]
        if held_titles:
            # The Rows show the model, which just went back to what the files hold.
            self._changed()
            if self.on_held_back is not None:
                files = tuple(m for m in sorted(self._held_back) if m in result.skipped)
                self.on_held_back(tuple(held_titles), files)

        if self._client is not None and any(p.source is not None for p in presets):
            # A Preset that gated a Bridge off changed which file sets the colours (F4).
            self._spawn(self._scan_drift(self._client))
        steps = self._preset_steps(presets, delta)
        for recorded in steps:
            self._undo.record(recorded)
        for entity_step in entity_steps:
            self._undo.record(entity_step)
        self._observe(result)
        self._repoll_if_timed_out(result)
        self._report(result)
        for preset, recorded in zip(presets, steps, strict=False):
            if preset.wallpaper is not None:
                carried = recorded if isinstance(recorded, PresetStep) else None
                self._spawn(self._show_preset_wallpaper(preset.name, preset.wallpaper, carried))
        for each in undone:
            self._spawn(self._finish_preset_undo(each.wallpaper, each.notes))
        step = steps[-1] if steps else None
        if self._undo_when_landed():
            # The user already asked for this gesture back: no offer to undo it.
            return
        newest: Step | None = entity_steps[-1] if entity_steps else step
        # A Preset that stands is offered back even when a key did not take: its step is on
        # the stack, and the toast names what did not take (finding 13 of the #153 review).
        offered = result.ok or (isinstance(newest, PresetStep) and not entity_steps)
        if newest is not None and offered and self.on_recorded is not None:
            # After `on_applied`, and only for a clean one: the window shows one toast, and a
            # transaction that stands with a failure (a timeout, a `user.lua` error) has its
            # failure to say. One toast per transaction, naming the newest gesture it carried.
            self.on_recorded(newest)

    def _hold_back_options(
        self, result: ApplyResult, delta: dict[str, OptionValue]
    ) -> list[str]:
        """Take out of the model, and out of `delta`, every edit a hand-edited Module kept
        off disk; remember it for `replace_edited_file`. Returns the titles of those edits.

        The Writer skips a hand-edited Module rather than overwrite it (ADR-0005), so such an
        edit never reached disk and must not stand: the Row goes back to what the file says,
        and no undo step claims a change that did not happen (hand-test defect 13, #148).
        Also forgets what an earlier refusal held for a Module that is no longer skipped:
        the user replaced it, or put it back as the app wrote it.
        """
        if result.write is None:
            return []
        skipped = set(result.skipped)
        self._held_back = {m: h for m, h in self._held_back.items() if m in skipped}
        titles: list[str] = []
        for name in list(delta):
            option = self._schema.get(name)
            if option is None or module_relpath(option) not in skipped:
                continue
            value = self._model.get(name)
            before = delta.pop(name)
            if value == before:
                continue
            held = self._held_back.setdefault(module_relpath(option), _HeldBack())
            held.options[name] = value
            titles.append(option.title)
            self._restore({name: before})
        return titles

    def _hold_back_entities(
        self, result: ApplyResult, steps: list[EntityStep]
    ) -> tuple[list[EntityStep], list[EntityStep]]:
        """`steps` split into those that reached disk and those a hand-edited Module kept
        off it, the second taken back out of the model and remembered as `_hold_back_options`
        does for Options. A step over several lists keeps the lists that were written."""
        skipped = set(result.skipped)
        if not skipped:
            return steps, []
        kept: list[EntityStep] = []
        held_steps: list[EntityStep] = []
        for step in steps:
            blocked = [e for e in step.edits if ENTITY_KIND_MODULES.get(e.kind) in skipped]
            if not blocked:
                kept.append(step)
                continue
            for edit in blocked:
                held = self._held_back.setdefault(ENTITY_KIND_MODULES[edit.kind], _HeldBack())
                held.lists[edit.kind] = edit.after
                if step.title not in held.entity_titles:
                    held.entity_titles.append(step.title)
            self._put_back(self._lists_before([EntityStep(tuple(blocked), step.title)]))
            held_steps.append(step)
            rest = EntityStep.of((e for e in step.edits if e not in blocked), step.title)
            if rest is not None:
                kept.append(rest)
        return kept, held_steps

    def held_back_titles(self, module: str) -> tuple[str, ...]:
        """What the user changed that `module`, edited outside the app, kept off disk."""
        held = self._held_back.get(module)
        if held is None:
            return ()
        return (*(self._schema[name].title for name in held.options), *held.entity_titles)

    @property
    def edited_copies_shown(self) -> str:
        """Where "Replace file" keeps the edited copy, as the user knows the path."""
        return tilde_path(self._paths.edited_copies_dir, self._paths)

    def held_back_path(self, module: str) -> Path:
        """Where a held-back Module lives, for "Open file"."""
        return self._paths.app_dir / module

    def replace_edited_file(self, module: str) -> None:
        """The user's answer to a held-back edit: overwrite `module` with the app's version,
        carrying the edits it kept off disk (ADR-0005's "overwrite").

        The edited bytes go to the Journal before the file is replaced, as every write's do.
        The edits are made again as edits, so the change lands as one undo step.
        """
        held = self._held_back.get(module)
        if held is None or self._refuse(module):
            return
        self._keep_edited_copy(module)
        self._applier.allow_overwrite(module)  # type: ignore[union-attr]  # _refuse proved it
        if held.lists:

            def put() -> None:
                for kind, items in held.lists.items():
                    getattr(self._model.entities, kind)[:] = items

            title = held.entity_titles[-1] if held.entity_titles else None
            self._commit_entity_edit(module, put, title=title)
        for name, value in held.options.items():
            self._begin_edit(name)
            if value is UNSET:
                self._model.unset(name)
            else:
                self._model.set(name, value)
        if held.options:
            self._applier.commit(*held.options)  # type: ignore[union-attr]  # _refuse proved it
        if not held.options and not held.lists:
            self._applier.force_write()  # type: ignore[union-attr]  # _refuse proved it
        # The Rows still show what the file held; the model now holds the user's change.
        self._changed()

    def _keep_edited_copy(self, module: str) -> None:
        """Copy the hand-edited `module` where the user can find it, before it is replaced."""
        source = self._paths.app_dir / module
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self._paths.edited_copies_dir / stamp / module
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        except OSError as error:
            _log.warning("could not keep a copy of %s: %s", source, error)

    def _preset_steps(
        self, presets: Sequence[_AppliedPreset], delta: Mapping[str, OptionValue]
    ) -> list[Step]:
        """The steps one standing transaction records: one per Preset it carried, oldest
        first, so each Ctrl+Z takes back one; without a Preset, the plain Option step.

        Each Preset's step goes from its own `before` to its own values; the newest goes to
        the model's, and also carries any other Option edit that landed in the same batch.
        """
        if not presets:
            step = self._step(delta)
            return [step] if step is not None else []
        steps: list[Step] = []
        for index, preset in enumerate(presets):
            newest = index == len(presets) - 1
            names = dict(preset.before)
            if newest:
                claimed = {name for each in presets for name in each.before}
                names.update({k: v for k, v in delta.items() if k not in claimed})
            edits = [
                Edit(
                    name,
                    before,
                    self._model.get(name) if newest else preset.after.get(name, before),
                )
                for name, before in names.items()
            ]
            made = PresetStep.of(preset.name, UndoStep.of(edits), color_source=preset.source)
            if made is not None:
                steps.append(made)
        return steps

    def _carried_presets(self, keys: Sequence[str]) -> list[_AppliedPreset]:
        """The applied Presets this transaction carries, taken, oldest first: each one every
        key of which is in `keys`.

        All or none per Preset, because `apply_preset` commits its keys in one synchronous
        burst and the queue takes a batch whole; two applied before either landed coalesce
        into one batch and come back together.
        """
        carried = [p for p in self._applying_presets if p.before.keys() <= set(keys)]
        self._applying_presets = [p for p in self._applying_presets if p not in carried]
        return carried

    def _carried_undos(self, keys: Sequence[str]) -> list[_UndonePreset]:
        """The Preset undos this transaction carries, taken, as `_carried_presets` does."""
        carried = [u for u in self._undoing_presets if u.names <= set(keys)]
        self._undoing_presets = [u for u in self._undoing_presets if u not in carried]
        return carried

    def _put_bridges(
        self, expected: Sequence[BridgeEntry], entries: Sequence[BridgeEntry]
    ) -> None:
        """Record `entries` as the Bridge entries, if the Manifest still holds `expected`."""
        if tuple(self._manifest().bridges) == tuple(expected):
            self._writer.record_bridges(self._model, entries)

    def _stands(self, result: ApplyResult) -> bool:
        """Whether this transaction's edits are kept: in the model, and on the undo stack.

        The one verdict every step is recorded through -- Option, Entity, and any later kind
        (#227). A transaction does **not** stand when Hyprland rejected a Module it wrote
        (ADR-0016 §Auto-revert), when it aborted before writing, or when its write failed
        part-way: its edits leave the model, and no step reaches the stack.

        Everything else stands, because its edit is on disk: an error in a file this
        transaction did not write (`user.lua`, ADR-0016's table), a read-back mismatch and a
        compositor that went away ("saved but not applied"). So does a `TIMEOUT`: its fate is
        unknown, not failed, and the re-read it triggers (`_repoll_if_timed_out`) is the check
        -- a list that re-read changes takes its steps off the stack (`_forget_entities`).
        """
        if result.outcome in (ApplyOutcome.ABORTED, ApplyOutcome.WRITE_FAILED):
            return False
        return not self._own_write_errors(result)

    def _settle_entities(
        self, result: ApplyResult, *, stands: bool
    ) -> tuple[list[EntityStep], list[EntityStep]]:
        """The Entity steps this result lets stand, and the ungrouped ones it failed.

        Every pending step with a serial up to `result.entities` was rendered by this
        transaction. Standing: it is recorded, or handed to its undo group. Not standing: it
        is returned as failed for `_fell` to take out of the model, and a grouped one marks
        its group failed -- ADR-0016's failed gesture, never on the stack; the group's owner
        (a display countdown) puts its own lists back. A group this result completes is
        merged here, and lands after the steps it was held beside. Both lists are in commit
        order.
        """
        if result.entities is None:
            return [], []
        reported = [p for p in self._pending_entities if p.serial <= result.entities]
        self._pending_entities = [
            p for p in self._pending_entities if p.serial > result.entities
        ]
        steps: list[EntityStep] = []
        failed: list[EntityStep] = []
        groups: dict[int, UndoGroup] = {}
        for pending in reported:
            if pending.group is None:
                (steps if stands else failed).append(pending.step)
                continue
            groups[id(pending.group)] = pending.group
            if stands:
                pending.group.held.append(pending.step)
            else:
                pending.group.failed = True
        for group in groups.values():
            merged = self._close_group(group)
            if merged is not None:
                steps.append(merged)
        return steps, failed

    def _lists_before(self, failed: Sequence[EntityStep]) -> dict[str, tuple[Any, ...]]:
        """Each list the `failed` steps moved, as it was before the first of them.

        Only lists that still read as the failed steps left them: an edit committed since is
        built on top, and writing `before` over it would take that edit out too. Such a list
        is left for its own transaction to settle.
        """
        merged = EntityStep.merge(failed, "")
        if merged is None:
            return {}
        entities = self._model.entities
        return {
            edit.kind: edit.before
            for edit in merged.edits
            if tuple(getattr(entities, edit.kind)) == edit.after
        }

    def _fell(
        self,
        result: ApplyResult,
        delta: Mapping[str, OptionValue],
        lists: Mapping[str, tuple[Any, ...]],
    ) -> None:
        """A transaction that does not stand (`_stands`): take its edits out, record nothing.

        `delta` and `lists` are the model as it was before the transaction's edits. Aborted,
        nothing reached disk, so the model goes back and nothing is written. Rejected or
        failed mid-write, the file may hold the edit, so the model goes back through a write
        (`_auto_revert`) -- unless recovery is halted, when the edit is left where it is and
        reported. Either way no step: Ctrl+Z still means the gesture before this one.
        """
        self._repoll_if_timed_out(result)
        if result.outcome is ApplyOutcome.ABORTED:
            self._restore(delta)
            self._put_back(lists)
            self._finish_failed(result)
            self._changed()
            return
        modules = self._failed_modules(result)
        if self._may_auto_revert(delta, lists):
            # No Banner for this one. The auto-revert is about to reload, and the errors it
            # is answering will be gone by the time the user could read about them -- the
            # toast is what tells that story (ADR-0016 reserves one for exactly this).
            self._undo_when_landed()
            self._auto_revert(result, delta, lists, modules)
            return
        if modules and not (delta or lists) and not self._recovery_halted:
            # Our own write, rejected, with nothing recorded to put back -- which the app
            # cannot reach by editing, only by writing without an edit. Reverting blind would
            # mean re-rendering the same model into the same bad bytes, so this reports and
            # stops, and the Banner says so.
            _log.error("own write rejected with no model delta to revert: %s", result.errors)
            self._recovery_halted = True
        self._finish_failed(result)

    def _finish_failed(self, result: ApplyResult) -> None:
        """Report a transaction that did not stand and is not being reverted."""
        self._observe(result)
        self._report(result)
        # A Ctrl+Z waiting on a gesture this result failed is dropped, not run.
        self._undo_when_landed()

    def _put_back(self, lists: Mapping[str, tuple[Any, ...]]) -> None:
        for kind, before in lists.items():
            getattr(self._model.entities, kind)[:] = before

    def _failed_modules(self, result: ApplyResult) -> tuple[str, ...]:
        """The App-dir Modules a failed transaction may have left holding its edit.

        For a rejection, the ones Hyprland blamed (`_own_write_errors`). For a write that
        failed part-way, the ones the Journal saw change -- it records what the disk says,
        not what was meant (`ApplyTransaction.run`).
        """
        if result.outcome is not ApplyOutcome.WRITE_FAILED:
            return self._own_write_errors(result)
        entries = self._journal.entries()
        if entries and entries[-1].outcome == str(ApplyOutcome.WRITE_FAILED):
            return entries[-1].modules
        return ()

    def _may_auto_revert(
        self, delta: Mapping[str, OptionValue], lists: Mapping[str, tuple[Any, ...]]
    ) -> bool:
        """Whether the app is still allowed to answer a rejected write by writing again.

        Two gates, and ADR-0016 names both. There has to be a delta to put back, Options or
        Entity lists -- reverting blind would re-render the same model into the same bad
        bytes. And recovery must not already have failed: "if the restore transaction itself
        errors ... stop auto-writing until the user acts", which is a gate on the *next*
        rejection as much as on this one.
        """
        return bool(delta or lists) and not self._recovery_halted

    def _repoll_if_timed_out(self, result: ApplyResult) -> None:
        """ADR-0016 §Timeout: "re-poll once; if still unconfirmed, treat as a foreign-unknown
        state -- full re-read, Banner if errors".

        A timed-out transaction is the one outcome where the app genuinely does not know what
        happened: the Modules are on disk, and whether Hyprland applied them is unanswered.
        Guessing either way is worse than asking again, and the "foreign-unknown" treatment is
        exactly the re-read the app already performs for somebody else's reload -- so this
        routes to it rather than inventing a third recovery.

        It is also the timed-out transaction's check (`_stands`): its steps are already on the
        stack, in order, and the re-read drops those over any list it changes. The result
        itself is never observed (`ApplyResult.reloaded`); the re-read's scan raises the
        Banner from what the compositor now says. Every other outcome resets the once-only
        guard, whether it stood or not.
        """
        if result.outcome is not ApplyOutcome.TIMEOUT or self._closing:
            self._repolled = False
            return
        # Its keys read "Not confirmed", and a scan already running leaves them so.
        keys = set(result.keys)
        for watch in self._drift_watches:
            watch.update(keys)
        self._mark((), covers=keys.__contains__)
        self._unconfirmed = (*self._unconfirmed, *result.keys)
        if self._repolled:
            return
        self._repolled = True
        self._spawn(self._reread_after_foreign_reload(keep=frozenset(keys)))

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
        looking at a problem they had already fixed. A result that ran no reload, or whose
        reload went unanswered, is dropped here, whichever transaction produced it: it learnt
        nothing about the config, so the Banner stays as the last reload left it (#227).
        """
        if not result.reloaded:
            return
        self._note(result.errors, written=result.written, binds=result.binds)
        if result.outcome in (ApplyOutcome.OK, ApplyOutcome.READ_BACK_MISMATCH):
            # Its Read-back ran, over exactly the keys it carried. A reload that reported
            # errors read nothing back, so it changes no key's marks.
            for watch in self._drift_watches:
                watch.update(result.keys)
            self._mark(result.mismatches, covers=set(result.keys).__contains__)

    def _observe_foreign(self, errors: Sequence[str], binds: int | None) -> None:
        """The same, for a reload this app did not perform.

        `written` is empty on purpose: nothing the app wrote is in flight, so no error here
        can be an `OWN_WRITE` -- and claiming one would authorise an automatic rewrite of a
        file somebody else has just changed (ADR-0016 §Attribution). The drift marks are the
        drift scan's, which follows (`_scan_drift`).
        """
        self._note(errors, written=(), binds=binds)

    def _note(
        self,
        errors: Sequence[str],
        *,
        written: Sequence[str],
        binds: int | None,
    ) -> None:
        """Replace the unhealthy state, then act on it if the user is stranded.

        One body for both callers, because "what is wrong" and "does that strand the user"
        are the same two steps whoever asked -- and a second copy of the emergency gate is a
        second place for it to drift from `Recovery.auto_restorable`.

        Every field it touches is *replaced*, never merged. `configerrors` describes the last
        parse and nothing older, so a Banner assembled from anything but the newest reload
        would name a file the user has since fixed.
        """
        self._recovery = plan(
            errors,
            written=written,
            binds=binds,
            bridges=[entry.file for entry in self._manifest().bridges],
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

    def _mark(self, mismatches: Sequence[Mismatch], *, covers: Callable[[str], bool]) -> None:
        """Replace the drift marks on every key `covers` from `mismatches`; others keep theirs.

        `_unapplied` is the loud shape (the Module never ran), `_overridden` the quiet one
        (something later won): `Mismatch` decides which, and never both.
        """
        fresh = [mismatch for mismatch in mismatches if covers(mismatch.name)]
        self._unconfirmed = tuple(name for name in self._unconfirmed if not covers(name))
        self._unapplied = (
            *(name for name in self._unapplied if not covers(name)),
            *(mismatch.name for mismatch in fresh if mismatch.unapplied),
        )
        self._overridden = (
            *(name for name in self._overridden if not covers(name)),
            *(mismatch.name for mismatch in fresh if mismatch.overridden),
        )

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
        if not self.restore_last_good(*modules):
            # Declined before anything ran -- no confirmed write to go back to. A notice
            # left pending would surface on the next restore the user chooses themselves.
            self._pending_rescue = ()

    # --- the recovery actions -----------------------------------------------------------------

    def last_good_for(self, module: str) -> LastKnownGood | None:
        """What Restore last good would put back for one Module, without putting it back.

        What the Banner asks before offering the button: a Module the app has never confirmed
        a write to has nothing to restore *to*, and an action that did nothing would be worse
        than one that was never offered.
        """
        return self._journal.last_known_good(module)

    def restorable(self, module: str) -> bool:
        """Whether `module` has a restore point: a version a confirmed write left."""
        return self._journal.last_known_good(module) is not None

    def restore_last_good(
        self, *modules: str, done: Callable[[bool], None] | None = None
    ) -> bool:
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

        for good in restores:
            # The bytes being replaced, where the user can find them (#148 hand-test 17).
            self._keep_edited_copy(good.module)
        self._spawn(self._restore_transaction(restores, done))
        return True

    async def _restore_transaction(
        self, restores: Sequence[LastKnownGood], done: Callable[[bool], None] | None = None
    ) -> None:
        applier = self._applier
        if applier is None:
            if done is not None:
                done(False)
            return

        self._restoring = True
        try:
            result = await applier.restore_now(applier.restore(restores))
        except (IpcError, RuntimeError) as error:
            _log.error("the restore transaction failed: %s", error)
            self._recovery_halted = True
            # Nothing was restored, so there is nothing to announce -- now or on the next
            # restore, which would otherwise inherit this one's notice.
            self._pending_rescue = ()
            self._changed()
            if done is not None:
                done(False)
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
        if any(is_entity_module(good.module) for good in restores):
            # Entity lists are the restored files' too, or the next write renders the old
            # lists over the bytes just put back.
            self._load_entities()
        if self._client is not None and result.reloaded:
            # `_observe` took the OK as a read-back and cleared the marks; an override of a
            # restored key is still an override (F3, F4 of the #148 review).
            await self._scan_drift(self._client)
        self._repoll_if_timed_out(result)
        # After the observation, which clears the field: this notice is about what the
        # restore just did, so it has to survive the restore's own reload and nothing later.
        self._rescued, self._pending_rescue = self._pending_rescue, ()
        self._report(result)
        self._changed()
        if done is not None:
            done(result.ok)

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
        manifest = self._manifest()
        return ModuleSet.discover(self._paths, [], bridges=manifest.bridges).require_for(
            problem.path
        )

    def regenerate_entrypoint(self) -> bool:
        """Rewrite `hyprland.lua` and reload -- ADR-0016's Entrypoint Fix.

        Offered as a one-click fix, unlike every other app-owned file, because the Entrypoint
        holds no decisions of the user's: it is derived entirely from which Modules exist and
        which requires are quarantined, so there is nothing in it a regeneration could lose.
        """
        return self._recovery_write(
            lambda before: self._writer.regenerate_entrypoint(
                self._model, before_replace=before
            ),
            self._manifest(),
            "regenerate the Entrypoint",
        )

    def _set_quarantine(self, requires: set[str]) -> bool:
        ordered = sorted(requires)
        return self._recovery_write(
            lambda before: self._writer.set_quarantine(
                self._model, ordered, before_replace=before
            ),
            self._manifest().with_quarantine(ordered),
            "change the Quarantine",
        )

    def _recovery_write(
        self,
        write: Callable[[BeforeReplace | None], bool],
        prospective: Manifest,
        what: str,
        exclude: Sequence[str] = (),
    ) -> bool:
        """Rewrite the Entrypoint out of band, then reload. `False` if it could not be done.

        One body for the two recoveries that work by changing which files are required, since
        they differ only in what they write. Both need the same three things around it: a
        live session to reload into, a write that may fail without taking the app down, and a
        reload that is *not* an apply -- an apply would render the model over the App dir and
        reload with the require list it would have generated rather than the one just written.

        The Entrypoint the `prospective` Manifest would produce is rendered and syntax-gated
        here, so a recovery the gate refuses answers `False` and leaves the Banner as it is.
        `exclude` names owned Options the re-read leaves alone. The write
        itself runs queued (`EntrypointTransaction`): its Journal draft stays open until the
        reload answers, and nothing else may open one meanwhile.
        """
        if not self.live or self._applier is None:
            return False
        try:
            self._writer.entrypoint_text(self._model, prospective)
        except (LuaSyntaxError, ValueError) as error:
            _log.error("could not %s: %s", what, error)
            return False
        self._spawn(self._recover_entrypoint(write, what, exclude))
        return True

    async def _recover_entrypoint(
        self,
        write: Callable[[BeforeReplace | None], bool],
        what: str,
        exclude: Sequence[str] = (),
    ) -> None:
        """Rewrite the Entrypoint, reload, and re-read what the config now says.

        A plain apply would do the wrong thing here: it renders the model over the App dir,
        and the file that changes is the one file the model does not describe. So this
        renders nothing -- it rewrites one file, reloads, and finds out what happened.
        """
        applier = self._applier
        if applier is None:
            return
        # Every owned Option, not a narrow set: quarantining `user.lua` changes the value of
        # everything that file was overriding, and the app cannot know which those were
        # without asking about all of them.
        # Not the keys a verified Module sets: the model rendered them and holds them, and
        # what the compositor answers for one may be `user.lua`'s or a tool's (finding 11).
        kept = {*exclude, *overrides.verified_options(self._paths.app_dir, self._manifest())}
        wanted = tuple(option.name for option in self._owned() if option.name not in kept)
        try:
            result = await applier.restore_now(applier.recover_entrypoint(write, wanted))
        except (IpcError, RuntimeError) as error:
            _log.error("could not reload after a recovery: %s", error)
            self._changed()
            return
        if result.outcome in (ApplyOutcome.ABORTED, ApplyOutcome.WRITE_FAILED):
            _log.error("could not %s: %s", what, result.detail)
        self._observe(result)
        if self._client is not None and result.reloaded:
            # Which files load changed, so which keys something overrides changed with it:
            # a quarantined user.lua, or a Bridge gated off (F4 of the #148 review).
            await self._scan_drift(self._client)
        self._repoll_if_timed_out(result)
        self._report(result)
        self._changed()

    # --- auto-revert (ADR-0016) ---------------------------------------------------------------

    def _auto_revert(
        self,
        result: ApplyResult,
        delta: Mapping[str, OptionValue],
        lists: Mapping[str, tuple[Any, ...]],
        modules: Sequence[str],
    ) -> None:
        """Put the model back and re-apply it, so the file goes back with it.

        `delta` is the Options and `lists` the Entity lists, each as before the transaction.
        An Entity-only revert carries no keys: the re-apply renders the whole model anyway.

        The model *is* the restore. Modules are rendered whole and deterministically
        (ADR-0010), so a model returned to its pre-transaction values renders byte-for-byte
        the Snapshot this transaction replaced -- which is why step 1 and step 2 of ADR-0016's
        auto-revert are one act here rather than two writes racing each other. The Snapshot
        is still what makes it *checkable*, and `_verify` checks it.

        No confirmation, per the ADR: instant apply has no cancel, and the bytes being
        restored were live and confirmed moments ago.
        """
        _log.warning("own write to %s did not stand (%s); reverting", modules, result.outcome)
        # Read before re-applying: right now the newest Journal entry for each Module is the
        # failed write, so its `before` digest is the Snapshot the revert has to reproduce.
        # A moment later the revert's own entry is newest, and its `before` is the bad bytes.
        expected = self._snapshot_digests(modules)
        self._restore(delta)
        self._put_back(lists)
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
                    outcome=result.outcome,
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
        self._bridge_owners = None
        if self.on_state_changed is not None:
            self.on_state_changed()
