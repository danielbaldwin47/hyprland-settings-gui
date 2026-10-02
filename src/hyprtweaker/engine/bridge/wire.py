"""Wiring a theming tool to its Bridge module: detect, plan, wire, unwire (ADR-0006, #166).

ADR-0006 mechanism 1: point a tool at Lua output (adopt its own, or install a Template pack)
and have the Entrypoint require what it writes. This module is the only place the engine
writes a file that belongs to another program -- a tool's config, a template the tool reads
-- and so it carries the rules for that (settled S3, S5):

- **Consent.** `plan_wire` is pure: it reads and returns every file it would touch, with the
  exact text before and after. `wire` writes only that plan, only with a `WireConsent` made
  from it, and only while each file still holds what the plan was made from.
- **A way back.** Before a tool file changes, its original is copied under
  `ConfigPaths.bridge_backups_dir`, with a `wire.json` recording the hash of each text `wire`
  wrote. `unwire` restores only a file that still holds one of those texts; a file changed
  since is never silently overwritten (`NeedsChoice`).
- **Converges.** Each step is atomic and every order of crash leaves "unwired" or "wired with
  a backup": a second `wire` reuses the record and its originals, and `unwire` restores from
  wherever the first stopped.
- **Runs nothing.** No tool, no daemon, no watcher; no reload hook is installed (S5: every
  v1 tool writes its file in place, which already reloads Hyprland). A hook the user wrote is
  left as they wrote it. `detect` reads the tool path and files only.

The Writer's prune and hand-edit check never see these files: they are not the App dir's.
The Bridge entry itself goes through the `register`/`unregister` callables the caller passes
(the Session's `add_bridge`/`remove_bridge`, or the Migration wizard's staged Manifest).
"""

from __future__ import annotations

import contextlib
import copy
import difflib
import enum
import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..paths import ConfigPaths
from ..state.manifest import Manifest
from ..tools import find_tool
from .registry import (
    DMS,
    MATUGEN,
    NOCTALIA,
    REGISTRY,
    SHELL_SWITCH,
    WALLUST,
    TemplatePack,
    ToolSpec,
    VersionState,
    toml_string,
    version_state,
)

RECORD_NAME = "wire.json"
RECORD_FORMAT = 1
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

Find = Callable[[str], Path | None]
Register = Callable[[str], bool]
"""Adds (or removes) the tool's Bridge entries and regenerates the Entrypoint; `False` when
that cannot be done now. Takes the tool id."""

_NOT_REGISTERED = "hyprland.lua could not be updated right now, so nothing was changed."


# --- what callers see -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolDetection:
    """What is known about one tool without running it (settled #166 "Detection")."""

    tool: str
    installed: bool
    """Its program is on the tool path."""

    configured: bool
    """Its own config already sends Hyprland colours (or its module) into the hypr dir."""

    wired: bool
    """The Manifest has its Bridge entry."""

    version_state: VersionState

    @property
    def found(self) -> bool:
        """Whether to show the tool at all: installed, or its config is here."""
        return self.installed or self.configured or self.wired

    @property
    def needs_update(self) -> str | None:
        """The sentence for a version this app cannot bridge, else `None`."""
        if self.version_state is VersionState.NEEDS_UPDATE:
            return REGISTRY[self.tool].detection.needs_update
        return None


class Change(enum.StrEnum):
    NEW = "new"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class FileRef:
    """A file outside the App dir, and how the user is shown its path (`~/` form)."""

    path: Path
    shown: str


@dataclass(frozen=True, slots=True)
class FileEdit:
    """One file `wire` writes: the whole text before and after, as it will be written."""

    path: Path
    shown: str
    before: str | None
    """`None`: the file does not exist yet."""
    after: str

    @property
    def change(self) -> Change:
        return Change.NEW if self.before is None else Change.CHANGED

    @property
    def excerpt_before(self) -> str:
        """The stanza that changes, as it is now. Empty for a new file or an added stanza."""
        return _excerpts(self.before or "", self.after)[0]

    @property
    def excerpt_after(self) -> str:
        """The same stanza as `wire` leaves it."""
        return _excerpts(self.before or "", self.after)[1]


@dataclass(frozen=True, slots=True)
class WirePlan:
    """Everything one Set up does, for the confirm to show before anything is touched."""

    tool: str
    title: str
    files: tuple[FileEdit, ...]
    """Every file outside the App dir it writes. Empty when the tool needs nothing of its
    own changed (DMS, or a tool already pointed at Lua)."""

    lines: tuple[str, ...]
    """What the Entrypoint gains: one `require` line per Bridge module."""

    backups: Path
    backups_shown: str
    """Where the copy of each changed file goes, in `~/` form."""

    patch: str = ""
    """Changes the user makes to their own copy of the tool (shell-switch); never applied."""


@dataclass(frozen=True, slots=True)
class WireConsent:
    """The user agreed to exactly this plan. Built by the confirm from the plan it showed."""

    plan: WirePlan


class ConsentRequired(RuntimeError):
    """`wire` was called without consent to the plan it was given: nothing was written."""


@dataclass(frozen=True, slots=True)
class NotDone:
    """Refused, with the reason in words. Nothing was changed."""

    tool: str
    reason: str


@dataclass(frozen=True, slots=True)
class Wired:
    tool: str
    written: tuple[FileRef, ...]
    backup: Path | None
    """The record `unwire` reads; `None` when no tool file was changed."""


class IfChanged(enum.StrEnum):
    """What `unwire` does with a file changed since setup (S3's confirm)."""

    ASK = "ask"
    """Change nothing and answer `NeedsChoice`: the default."""

    RESTORE = "restore"
    """"Restore the copy": keep the current file beside the backups, then restore."""

    LEAVE = "leave"
    """"Leave it as it is": every tool file stays; only the Bridge entry goes."""


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """A tool file that is not what setup left: the file as it is now, and the copy of it
    kept at setup, so the user is shown both before choosing."""

    path: Path
    shown: str
    copy: str | None
    """The copy from before setup, in `~/` form. `None`: setup created the file, so
    "Restore the copy" deletes it."""


@dataclass(frozen=True, slots=True)
class NeedsChoice:
    """Files changed since setup: the caller asks, then calls `unwire` again with a choice.
    Nothing was changed."""

    tool: str
    changed: tuple[ChangedFile, ...]


@dataclass(frozen=True, slots=True)
class Unwired:
    tool: str
    restored: tuple[FileRef, ...] = ()
    removed: tuple[FileRef, ...] = ()
    """Files `wire` had created."""
    left: tuple[FileRef, ...] = ()
    """Files kept as they are ("Leave it as it is")."""
    note: str = ""
    """Set when there was nothing to undo, saying so."""


# --- detect -----------------------------------------------------------------------------------


def detect(
    tool: str, *, paths: ConfigPaths, manifest: Manifest, find: Find = find_tool
) -> ToolDetection:
    """What is here of `tool`: never runs it, cheap enough to call on every "Check again"."""
    spec = REGISTRY[tool]
    binaries = [name for name in spec.detection.binaries if find(name) is not None]
    return ToolDetection(
        tool=tool,
        installed=bool(binaries),
        configured=_CONFIGURED[tool](spec, paths),
        wired=any(entry.tool == tool for entry in manifest.bridges),
        version_state=_version(spec, paths, binaries),
    )


def _version(spec: ToolSpec, paths: ConfigPaths, binaries: Iterable[str]) -> VersionState:
    rule = spec.detection
    candidates = (*rule.config_files, *rule.outdated_by, *filter(None, [rule.supported_by]))
    files = [rel for rel in candidates if (paths.config_home / rel).is_file()]
    return version_state(spec, binaries=list(binaries), files=files)


def _matugen_configured(spec: ToolSpec, paths: ConfigPaths) -> bool:
    config = paths.config_home / "matugen/config.toml"
    return any(
        _under(paths.hypr_dir, _resolve(out, paths, config.parent))
        for entry in _toml_tables(config, "templates")
        for out in _strings(entry.get("output_path"))
    )


def _wallust_configured(spec: ToolSpec, paths: ConfigPaths) -> bool:
    config = paths.config_home / "wallust/wallust.toml"
    return any(
        _under(paths.hypr_dir, _resolve(target, paths, config.parent))
        for entry in _toml_tables(config, "templates")
        for target in _strings(entry.get("target"))
    )


def _noctalia_configured(spec: ToolSpec, paths: ConfigPaths) -> bool:
    files = [*_noctalia_user_files(paths), _noctalia_dropin(paths), _noctalia_settings(paths)]
    ids = [each for path in files for each in _builtin_ids(_parse_file(path)) or ()]
    return "hyprland" in ids or (paths.hypr_dir / "noctalia.lua").is_file()


def _dms_configured(spec: ToolSpec, paths: ConfigPaths) -> bool:
    return (paths.hypr_dir / spec.modules[0].file).is_file()


def _shell_switch_configured(spec: ToolSpec, paths: ConfigPaths) -> bool:
    return any((paths.config_home / rel).is_file() for rel in spec.detection.config_files)


_CONFIGURED: Mapping[str, Callable[[ToolSpec, ConfigPaths], bool]] = {
    MATUGEN.tool: _matugen_configured,
    WALLUST.tool: _wallust_configured,
    NOCTALIA.tool: _noctalia_configured,
    DMS.tool: _dms_configured,
    SHELL_SWITCH.tool: _shell_switch_configured,
}


# --- plan -------------------------------------------------------------------------------------


class _Refuse(Exception):
    """A planner's reason for refusing; becomes `NotDone`."""


def plan_wire(tool: str, *, paths: ConfigPaths, find: Find = find_tool) -> WirePlan | NotDone:
    """Every file wiring `tool` would write, before and after. Reads, never writes.

    Refuses, with the reason, a version that cannot be bridged (noctalia 4, DMS 1.4) and a
    config it cannot edit safely: an edit is kept only if the edited text parses back to the
    original plus exactly the intended keys, so every other key and comment stays.
    """
    spec = REGISTRY[tool]
    binaries = [name for name in spec.detection.binaries if find(name) is not None]
    if _version(spec, paths, binaries) is VersionState.NEEDS_UPDATE:
        return NotDone(tool, spec.detection.needs_update)
    try:
        files = [edit for edit in _PLANNERS[tool](spec, paths) if edit.before != edit.after]
    except _Refuse as refusal:
        return NotDone(tool, str(refusal))
    for edit in files:
        if not _writable(edit.path):
            return NotDone(
                tool,
                f"{edit.shown} cannot be changed: its folder is read-only, as it is when a "
                "dotfiles manager such as home-manager owns it. It was left alone. Make it "
                f"writable, then set {spec.title} up again.",
            )
    return WirePlan(
        tool=tool,
        title=spec.title,
        files=tuple(files),
        lines=tuple(module.line for module in spec.modules),
        backups=paths.bridge_backups_dir,
        backups_shown=shown(paths.bridge_backups_dir, paths) + "/",
        patch=spec.template_pack.patch if spec.template_pack else "",
    )


def _pack(spec: ToolSpec) -> TemplatePack:
    assert spec.template_pack is not None, spec.tool
    return spec.template_pack


def _edit(path: Path, paths: ConfigPaths, after: str) -> FileEdit:
    said = shown(path, paths)
    if path.is_symlink():
        said += f" (a link to {shown(path.resolve(), paths)})"
    return FileEdit(path=path, shown=said, before=_read(path, paths), after=after)


def _templates(spec: ToolSpec, paths: ConfigPaths) -> list[FileEdit]:
    return [
        _edit(paths.config_home / each.path, paths, each.text) for each in _pack(spec).templates
    ]


def _plan_matugen(spec: ToolSpec, paths: ConfigPaths) -> list[FileEdit]:
    """S5: the app-owned template, and the user's Hyprland entry retargeted at it, else an
    entry of the app's own. Every other key -- a `post_hook` included -- stays."""
    pack = _pack(spec)
    config = paths.config_home / (pack.config_file or "")
    stanza = pack.stanza_for(paths.config_home, paths.hypr_dir, spec.modules[0].file)
    wanted = tomllib.loads(stanza)["templates"]["hyprtweaker_hyprland"]
    before = _read(config, paths)
    if before is None:
        return [*_templates(spec, paths), _edit(config, paths, "[config]\n\n" + stanza)]
    data = _parse_or_refuse(before, config, paths, spec)
    templates = _table(data.get("templates"))
    if any(_has(entry, wanted) for entry in templates.values()):
        return _templates(spec, paths)
    candidates: list[tuple[str | None, str]] = [
        (_retarget(before, ("templates", name), wanted), name)
        for name in _matugen_targets(templates)
    ]
    candidates.append((_append(before, stanza), "hyprtweaker_hyprland"))
    for text, name in candidates:
        expected = copy.deepcopy(data)
        entry = _table(expected.setdefault("templates", {})).setdefault(name, {})
        entry.update(wanted)
        if text is not None and _parse(text) == expected:
            return [*_templates(spec, paths), _edit(config, paths, text)]
    raise _Refuse(_cannot_edit(spec, config, paths))


def _matugen_targets(templates: Mapping[str, Any]) -> list[str]:
    """The entry to retarget: the app's own, else the one Hyprland entry. Several that look
    like Hyprland's are ambiguous, so the app adds its own rather than pick one."""
    if "hyprtweaker_hyprland" in templates:
        return ["hyprtweaker_hyprland"]
    hyprland = [
        name
        for name, entry in templates.items()
        if isinstance(entry, dict)
        and (
            name == "hyprland"
            or any(
                Path(each).name.startswith("hyprland-colors")
                for each in _strings(entry.get("input_path"))
            )
        )
    ]
    return hyprland if len(hyprland) == 1 else []


def _plan_wallust(spec: ToolSpec, paths: ConfigPaths) -> list[FileEdit]:
    """S5: the app's Template pack and its `[templates]` entry; no `[hooks]`."""
    pack = _pack(spec)
    config = paths.config_home / (pack.config_file or "")
    stanza = pack.stanza_for(paths.config_home, paths.hypr_dir, spec.modules[0].file)
    wanted = tomllib.loads(stanza)["templates"]
    before = _read(config, paths)
    if before is None:
        return [*_templates(spec, paths), _edit(config, paths, stanza)]
    data = _parse_or_refuse(before, config, paths, spec)
    if all(_table(data.get("templates")).get(k) == v for k, v in wanted.items()):
        return _templates(spec, paths)
    entry = stanza.split("\n", 1)[1]
    expected = copy.deepcopy(data)
    _table(expected.setdefault("templates", {})).update(wanted)
    for text in (_insert_in_section(before, ("templates",), entry), _append(before, stanza)):
        if text is not None and _parse(text) == expected:
            return [*_templates(spec, paths), _edit(config, paths, text)]
    raise _Refuse(_cannot_edit(spec, config, paths))


def _plan_noctalia(spec: ToolSpec, paths: ConfigPaths) -> list[FileEdit]:
    """#167 Decision 3: a drop-in of the app's own turning on noctalia's `hyprland` template.

    It lists the user's own template ids too: whether noctalia merges `builtin_ids` across
    its files or lets the last one win is not documented, and either way the user's other
    templates stay on.
    """
    settings = _noctalia_settings(paths)
    chosen = _builtin_ids(_parse_or_refuse_file(settings, paths, spec))
    if chosen is not None:
        if "hyprland" in chosen:
            return []
        raise _Refuse(
            f"noctalia's own settings ({shown(settings, paths)}) choose its templates, so a "
            "file from hyprtweaker would be ignored. Turn on noctalia's Hyprland template "
            "there, then set noctalia up here."
        )
    ids: list[str] = []
    for path in _noctalia_user_files(paths):
        ids += [each for each in _builtin_ids(_parse_or_refuse_file(path, paths, spec)) or ()]
    if "hyprland" in ids:
        return []
    unique = list(dict.fromkeys([*ids, "hyprland"]))
    text = (
        "# Written by hyprtweaker: turns on noctalia's Hyprland colors.\n"
        "# Remove noctalia on hyprtweaker's Theming page to take it out again.\n"
        "[theme.templates]\n"
        f"builtin_ids = [{', '.join(json.dumps(each) for each in unique)}]\n"
    )
    return [_edit(_noctalia_dropin(paths), paths, text)]


def _plan_dms(spec: ToolSpec, paths: ConfigPaths) -> list[FileEdit]:
    """DMS 1.5+ writes `dms/colors.lua` on its own: only the Entrypoint line is added."""
    return []


def _plan_shell_switch(spec: ToolSpec, paths: ConfigPaths) -> list[FileEdit]:
    """S5: the two Lua templates beside the user's; the script patch is shown, not applied."""
    return _templates(spec, paths)


_PLANNERS: Mapping[str, Callable[[ToolSpec, ConfigPaths], list[FileEdit]]] = {
    MATUGEN.tool: _plan_matugen,
    WALLUST.tool: _plan_wallust,
    NOCTALIA.tool: _plan_noctalia,
    DMS.tool: _plan_dms,
    SHELL_SWITCH.tool: _plan_shell_switch,
}


def _cannot_edit(spec: ToolSpec, config: Path, paths: ConfigPaths) -> str:
    return (
        f"{spec.title}'s config ({shown(config, paths)}) is laid out in a way hyprtweaker "
        "cannot add to without changing something else, so it was left alone."
    )


# --- wire -------------------------------------------------------------------------------------


def wire(
    plan: WirePlan,
    consent: WireConsent | None,
    *,
    register: Register,
    unregister: Register,
    now: datetime | None = None,
) -> Wired | NotDone:
    """Do exactly `plan`: add the Bridge entry, back each file up, then write it.

    Raises `ConsentRequired` unless `consent` is for this very plan. Refuses, changing
    nothing, when a file no longer holds what the plan was made from (the user would be
    agreeing to text they never saw), when `register` cannot add the entry, or when a file
    cannot be read or written (a read-only dotfiles store, a full disk): every file this
    call wrote is put back, and the entry goes again through `unregister`. So a tool is
    either set up or not, never in between; the caller has nothing to clean up.

    The entry goes first, so noctalia, once its template is on, finds its `require` in the
    Entrypoint and never appends its own (#167 Cross-cutting 4). The record goes before any
    tool file, so a crash at any point leaves either nothing changed or a way back.
    """
    if consent is None or consent.plan != plan:
        raise ConsentRequired(f"{plan.title} was not set up: the user has not agreed to it")

    def refused(reason: str) -> NotDone:
        unregister(plan.tool)
        return NotDone(plan.tool, reason)

    targets = [(edit, _target(edit.path)) for edit in plan.files]
    for edit, target in targets:
        try:
            now_text = _read_raw(target)
        except OSError as error:
            return refused(
                f"{edit.shown} could not be read ({_why(error)}), so nothing was changed."
            )
        if now_text != edit.before:
            return refused(
                f"{edit.shown} changed after the changes were shown, so nothing was changed. "
                "Review them again."
            )
    if not register(plan.tool):
        return refused(_NOT_REGISTERED)
    if not targets:
        return Wired(plan.tool, written=(), backup=None)
    previous = _Record.active(plan.backups, plan.tool)
    record = previous or _Record.new(plan.backups, plan.tool, now)
    try:
        for edit, target in targets:
            record = record.wrote(target, edit.after)
        record.save()
    except OSError as error:
        return refused(
            f"The copy of each file could not be kept in {plan.backups_shown} ({_why(error)}), "
            "so nothing was changed."
        )
    made = record.dirs
    written: list[tuple[FileEdit, Path]] = []
    for edit, target in targets:
        try:
            _write_atomic(target, edit.after)
        except OSError as error:
            put_back = _put_back(written, made)
            with contextlib.suppress(OSError):
                if previous is not None:
                    previous.save()
                else:
                    record.close()
            said = f"{edit.shown} could not be written ({_why(error)})"
            if put_back:
                return refused(f"{said}, so nothing was changed.")
            return refused(
                f"{said}, and the files changed before it could not all be put back. The "
                f"copy of each is in {plan.backups_shown}."
            )
        written.append((edit, target))
    return Wired(
        plan.tool,
        written=tuple(FileRef(edit.path, edit.shown) for edit, _ in targets),
        backup=record.directory,
    )


def _put_back(written: list[tuple[FileEdit, Path]], made: Iterable[Path]) -> bool:
    """Undo this call's own writes: each file gets the text the plan was made from back.
    `False` if one could not be."""
    whole = True
    for edit, target in reversed(written):
        try:
            if edit.before is None:
                target.unlink(missing_ok=True)
            else:
                _write_atomic(target, edit.before)
        except OSError:
            whole = False
    _remove_empty(made)
    return whole


# --- unwire -----------------------------------------------------------------------------------


def unwire(
    tool: str,
    *,
    paths: ConfigPaths,
    manifest: Manifest,
    unregister: Register,
    if_changed: IfChanged = IfChanged.ASK,
) -> Unwired | NeedsChoice | NotDone:
    """Undo `wire`: the Bridge entry goes, and each file goes back to its copy.

    A file that still holds what `wire` wrote is restored (or removed, if `wire` created it);
    one that already holds the original is left. A file changed since setup is never
    silently overwritten: with `IfChanged.ASK` nothing is done and `NeedsChoice` names each,
    with the copy it would be restored from. A record it cannot trust -- a copy gone from
    the backups -- counts as changed, and "Restore the copy" then refuses rather than guess.
    Idempotent: a second call, or one after a crash part-way, converges; with nothing wired
    it changes nothing and says so.
    """
    spec = REGISTRY.get(tool)
    title = spec.title if spec else tool
    record = _Record.active(paths.bridge_backups_dir, tool)
    entry = any(each.tool == tool for each in manifest.bridges)
    if record is None and not entry:
        return Unwired(tool, note=f"{title} is not set up here, so there was nothing to undo.")
    files = record.files if record else ()
    status = {each.path: record.status(each) for each in files} if record else {}
    changed = [each for each in files if status[each.path] is _Status.CHANGED]
    if changed and if_changed is IfChanged.ASK:
        assert record is not None
        return NeedsChoice(tool, tuple(record.changed_file(each, paths) for each in changed))
    restoring = (
        []
        if record is None or (changed and if_changed is IfChanged.LEAVE)
        else [each for each in files if status[each.path] is not _Status.ORIGINAL]
    )
    if record is not None and (refusal := record.cannot_restore(restoring, paths)):
        return NotDone(tool, refusal)
    if entry and not unregister(tool):
        return NotDone(tool, _NOT_REGISTERED)
    if record is None:
        return Unwired(tool)
    if changed and if_changed is IfChanged.LEAVE:
        record.close()
        return Unwired(tool, left=tuple(_ref(each.path, paths) for each in files))
    restored: list[FileRef] = []
    removed: list[FileRef] = []
    try:
        for each in changed:
            record.keep_replaced(each)
        for each in restoring:
            if each.copy is None:
                each.path.unlink(missing_ok=True)
                removed.append(_ref(each.path, paths))
            else:
                _write_atomic(each.path, (record.directory / each.copy).read_bytes())
                restored.append(_ref(each.path, paths))
    except OSError as error:
        return NotDone(
            tool,
            f"{title} no longer loads, but {shown(Path(error.filename or ''), paths)} could "
            f"not be put back ({_why(error)}). The copy of each file from before setup is in "
            f"{shown(record.directory, paths)}/.",
        )
    _remove_empty(record.dirs)
    record.close()
    return Unwired(tool, restored=tuple(restored), removed=tuple(removed))


# --- the record -------------------------------------------------------------------------------


class _Status(enum.Enum):
    ORIGINAL = "original"
    """Holds what was there before `wire` (or is absent, if there was nothing)."""
    WIRED = "wired"
    """Holds a text `wire` wrote."""
    CHANGED = "changed"
    """Neither: changed since setup."""


@dataclass(frozen=True, slots=True)
class _FileRecord:
    path: Path
    copy: str | None
    """The original's copy, relative to the record's directory. `None`: `wire` created it."""
    wrote: tuple[str, ...]
    """The sha256 of every text `wire` wrote here: any of them is "still as wired"."""

    def as_json(self) -> dict[str, Any]:
        return {"path": str(self.path), "copy": self.copy, "wrote": list(self.wrote)}


@dataclass(frozen=True, slots=True)
class _Record:
    """One wiring's `wire.json` and copies, under `<backups>/<tool>-<stamp>/`."""

    directory: Path
    tool: str
    files: tuple[_FileRecord, ...]
    dirs: tuple[Path, ...] = ()
    """Directories `wire` created for its files, deepest first: `unwire` removes each that
    is empty again, and never one that was there before."""

    @classmethod
    def new(cls, backups: Path, tool: str, now: datetime | None) -> _Record:
        stamp = (now or datetime.now(UTC)).strftime(STAMP_FORMAT)
        directory = backups / f"{tool}-{stamp}"
        suffix = 1
        while directory.exists():
            suffix += 1
            directory = backups / f"{tool}-{stamp}-{suffix}"
        return cls(directory, tool, ())

    @classmethod
    def active(cls, backups: Path, tool: str) -> _Record | None:
        """The tool's newest record not yet undone. A damaged one is skipped."""
        for directory in sorted(backups.glob(f"{tool}-*"), reverse=True):
            record = cls._load(directory, tool)
            if record is not None:
                return record
        return None

    @classmethod
    def _load(cls, directory: Path, tool: str) -> _Record | None:
        try:
            payload = json.loads((directory / RECORD_NAME).read_text(encoding="utf-8"))
            if payload["tool"] != tool or not payload["wired"]:
                return None
            files = tuple(
                _FileRecord(
                    Path(each["path"]),
                    None if each["copy"] is None else str(each["copy"]),
                    tuple(str(digest) for digest in each["wrote"]),
                )
                for each in payload["files"]
            )
            dirs = tuple(Path(str(each)) for each in payload.get("dirs", ()))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        return cls(directory, tool, files, dirs)

    def wrote(self, target: Path, text: str) -> _Record:
        """This record once `text` is written to `target`, copying the original first time.

        A file the record already knows but that holds neither its original nor a text
        `wire` wrote was changed by someone else since (the Bridge entry went missing, then
        the user edited it, then set the tool up again). The record is stale for that file:
        what is on disk now becomes its original, copied like a first one, so a later
        `unwire` puts back the user's edit rather than the file from before the first setup.
        The older copy stays in the record's directory as history.
        """
        digest = _sha(text.encode("utf-8"))
        missing: list[Path] = []
        parent = target.parent
        while not parent.exists() and parent != parent.parent:
            missing.append(parent)
            parent = parent.parent
        dirs = tuple(dict.fromkeys([*self.dirs, *missing]))
        for index, each in enumerate(self.files):
            if each.path == target:
                if self.status(each) is _Status.CHANGED:
                    updated = _FileRecord(target, self._copy(target), (digest,))
                else:
                    updated = replace(each, wrote=tuple(dict.fromkeys([*each.wrote, digest])))
                files = (*self.files[:index], updated, *self.files[index + 1 :])
                return replace(self, files=files, dirs=dirs)
        added = _FileRecord(target, self._copy(target), (digest,))
        return replace(self, files=(*self.files, added), dirs=dirs)

    def _copy(self, target: Path) -> str | None:
        """Copy `target` under `copies/<n>/` (a number no copy uses yet); `None` if absent."""
        if not target.is_file():
            return None
        number = 0
        while (self.directory / f"copies/{number}").exists():
            number += 1
        copy_name = f"copies/{number}/{target.name}"
        (self.directory / copy_name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, self.directory / copy_name)
        return copy_name

    def status(self, each: _FileRecord) -> _Status:
        current = _read_bytes(each.path)
        original = None if each.copy is None else _read_bytes(self.directory / each.copy)
        if each.copy is not None and original is None:
            return _Status.CHANGED  # its copy is gone: nothing vouches for what to put back
        if current == original:
            return _Status.ORIGINAL
        if current is not None and _sha(current) in each.wrote:
            return _Status.WIRED
        return _Status.CHANGED

    def changed_file(self, each: _FileRecord, paths: ConfigPaths) -> ChangedFile:
        copy = None if each.copy is None else shown(self.directory / each.copy, paths)
        return ChangedFile(each.path, shown(each.path, paths), copy)

    def cannot_restore(self, files: Iterable[_FileRecord], paths: ConfigPaths) -> str | None:
        """Why putting `files` back would fail part-way, checked before anything changes."""
        for each in files:
            if each.copy is not None and not (self.directory / each.copy).is_file():
                return (
                    f"The copy of {shown(each.path, paths)} from before setup is missing from "
                    f"{shown(self.directory, paths)}/, so it cannot be put back and nothing "
                    "was changed. Choose Leave it as it is to keep the file as it is now."
                )
            if not _writable(each.path):
                return (
                    f"{shown(each.path, paths)} cannot be changed (its folder is read-only), "
                    "so nothing was changed."
                )
        return None

    def keep_replaced(self, each: _FileRecord) -> None:
        """Copy the user's changed file beside the originals before it is restored over."""
        if each.path.is_file():
            index = self.files.index(each)
            destination = self.directory / f"replaced/{index}/{each.path.name}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(each.path, destination)

    def save(self, *, wired: bool = True) -> None:
        payload = {
            "format": RECORD_FORMAT,
            "tool": self.tool,
            "wired": wired,
            "files": [each.as_json() for each in self.files],
            "dirs": [str(each) for each in self.dirs],
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.directory / RECORD_NAME, json.dumps(payload, indent=2) + "\n")

    def close(self) -> None:
        """Mark it undone; the copies stay as history."""
        self.save(wired=False)


# --- files ------------------------------------------------------------------------------------


def shown(path: Path, paths: ConfigPaths) -> str:
    """`path` the way the user knows it: `~/...` under their home, else absolute.

    The home is the config home's parent when that is `.config`, the XDG default; the app
    never looks the home up itself (#233).
    """
    home = _home(paths)
    if home is not None and path.is_relative_to(home):
        return "~/" + str(path.relative_to(home)) if path != home else "~"
    return str(path)


def _home(paths: ConfigPaths) -> Path | None:
    return paths.config_home.parent if paths.config_home.name == ".config" else None


def _ref(path: Path, paths: ConfigPaths) -> FileRef:
    return FileRef(path, shown(path, paths))


def _target(path: Path) -> Path:
    """Where to write `path`: through a symlink, so a dotfile manager's link survives."""
    return path.resolve() if path.is_symlink() else path


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _read_raw(path: Path) -> str | None:
    data = _read_bytes(path)
    return None if data is None else data.decode("utf-8", errors="replace")


def _read(path: Path, paths: ConfigPaths) -> str | None:
    """A tool file's text, `None` when absent; a planner refuses one it cannot read."""
    try:
        data = _read_bytes(_target(path))
        return None if data is None else data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _Refuse(f"{shown(path, paths)} could not be read ({error}).") from error


def _write_atomic(path: Path, content: str | bytes) -> None:
    """Temp file in the same directory, then a rename: the old file or the new, never half.
    Keeps the file's permissions; the bytes reach the disk before the rename."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            with contextlib.suppress(OSError):
                os.fsync(stream.fileno())
        with contextlib.suppress(OSError):
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _writable(path: Path) -> bool:
    """Whether `path` can be replaced: the nearest folder of it that exists takes writes."""
    folder = _target(path).parent
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return os.access(folder, os.W_OK)


def _remove_empty(dirs: Iterable[Path]) -> None:
    """Remove each directory `wire` created that is empty again, deepest first."""
    for each in sorted(dirs, key=lambda path: len(path.parts), reverse=True):
        with contextlib.suppress(OSError):
            each.rmdir()


def _why(error: OSError) -> str:
    """An `OSError` in words, without its number or path: "permission denied"."""
    return (error.strerror or str(error)).lower()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve(value: str, paths: ConfigPaths, base: Path) -> Path | None:
    """A path as a tool reads it from its config: `~` is the home, relative is `base`."""
    if value == "~" or value.startswith("~/"):
        home = _home(paths)
        return None if home is None else home / value[2:]
    path = Path(value)
    return path if path.is_absolute() else base / path


def _under(directory: Path, path: Path | None) -> bool:
    return path is not None and path.is_relative_to(directory)


# --- TOML, edited as text and checked by parsing ----------------------------------------------

_HEADER = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(?:#.*)?$")
_ANY_HEADER = re.compile(r"^\s*\[")
_STRING = r"(\"(?:[^\"\\]|\\.)*\"|'[^'\n]*')"


def _parse(text: str) -> dict[str, Any] | None:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _parse_file(path: Path) -> dict[str, Any] | None:
    try:
        return _parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _parse_or_refuse(
    text: str, path: Path, paths: ConfigPaths, spec: ToolSpec
) -> dict[str, Any]:
    data = _parse(text)
    if data is None:
        raise _Refuse(
            f"{spec.title}'s config ({shown(path, paths)}) could not be read as TOML, so it "
            "was left alone. Fix it in an editor, then try again."
        )
    return data


def _parse_or_refuse_file(path: Path, paths: ConfigPaths, spec: ToolSpec) -> dict[str, Any]:
    text = _read(path, paths)
    return {} if text is None else _parse_or_refuse(text, path, paths, spec)


def _table(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _toml_tables(path: Path, key: str) -> list[dict[str, Any]]:
    data = _parse_file(path) or {}
    return [entry for entry in _table(data.get(key)).values() if isinstance(entry, dict)]


def _strings(value: Any) -> list[str]:
    """A path key's value: one string or an array of them (matugen's `output_path`)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [each for each in value if isinstance(each, str)]
    return []


def _has(entry: Any, wanted: Mapping[str, Any]) -> bool:
    return isinstance(entry, dict) and all(entry.get(k) == v for k, v in wanted.items())


def _key_path(header: str) -> tuple[str, ...]:
    return tuple(part.strip().strip("\"'") for part in header.split("."))


def _section(lines: list[str], table: tuple[str, ...]) -> tuple[int, int] | None:
    """The `[table]` header's line index and the index where its section ends."""
    for index, line in enumerate(lines):
        match = _HEADER.match(line)
        if match and _key_path(match.group(1)) == table:
            end = next(
                (j for j in range(index + 1, len(lines)) if _ANY_HEADER.match(lines[j])),
                len(lines),
            )
            return index, end
    return None


def _retarget(text: str, table: tuple[str, ...], values: Mapping[str, str]) -> str | None:
    """`text` with each key of `values` in `[table]` given its new string, comments kept.
    `None` when a key is not one single-line string in that section."""
    lines = text.splitlines(keepends=True)
    section = _section(lines, table)
    if section is None:
        return None
    start, end = section
    for key, value in values.items():
        pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*){_STRING}(.*)$", re.DOTALL)
        hits = [(i, m) for i in range(start + 1, end) if (m := pattern.match(lines[i]))]
        if len(hits) != 1:
            return None
        index, match = hits[0]
        lines[index] = match.group(1) + toml_string(value) + match.group(3)
    return "".join(lines)


def _insert_in_section(text: str, table: tuple[str, ...], entry: str) -> str | None:
    """`entry` added after the last non-blank line of `[table]`; `None` without one."""
    lines = text.splitlines(keepends=True)
    section = _section(lines, table)
    if section is None:
        return None
    start, end = section
    last = max((i for i in range(start, end) if lines[i].strip()), default=start)
    if not lines[last].endswith("\n"):
        lines[last] += "\n"
    lines.insert(last + 1, entry if entry.endswith("\n") else entry + "\n")
    return "".join(lines)


def _append(text: str, stanza: str) -> str:
    return text.rstrip("\n") + "\n\n" + stanza if text.strip() else stanza


def _excerpts(before: str, after: str) -> tuple[str, str]:
    """The lines that differ, widened up to their TOML table header, before and after."""
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    changes = [
        op
        for op in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
        if op[0] != "equal"
    ]
    if not changes:
        return "", ""
    i1, j1 = changes[0][1], changes[0][3]
    i2, j2 = changes[-1][2], changes[-1][4]
    if not any(_ANY_HEADER.match(line) for line in (*old[i1:i2], *new[j1:j2])):
        header = next((k for k in range(i1 - 1, -1, -1) if _ANY_HEADER.match(old[k])), None)
        if header is not None:
            i1, j1 = header, j1 - (i1 - header)
    return _joined(old[i1:i2]), _joined(new[j1:j2])


def _joined(lines: list[str]) -> str:
    text = "".join(lines).strip("\n")
    return text + "\n" if text else ""


# --- noctalia's files -------------------------------------------------------------------------


def _noctalia_dropin(paths: ConfigPaths) -> Path:
    return paths.config_home / (_pack(NOCTALIA).config_file or "")


def _noctalia_settings(paths: ConfigPaths) -> Path:
    """What noctalia's own settings UI writes; it wins over every config file (#167)."""
    return paths.state_home / "noctalia/settings.toml"


def _noctalia_user_files(paths: ConfigPaths) -> list[Path]:
    dropin = _noctalia_dropin(paths)
    return sorted(
        path for path in (paths.config_home / "noctalia").glob("*.toml") if path != dropin
    )


def _builtin_ids(data: Mapping[str, Any] | None) -> list[str] | None:
    """`[theme.templates] builtin_ids`, or `None` when this file does not set it."""
    ids = _table(_table((data or {}).get("theme")).get("templates")).get("builtin_ids")
    return None if ids is None else _strings(ids)
