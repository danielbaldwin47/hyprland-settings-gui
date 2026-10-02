"""Running a foreign `hyprland.lua` under the recording stub, and typing what came back.

This module owns the one place the app executes somebody else's code. Everything it knows
about safety lives either here or in `runner.lua`; the mapper above it only ever sees a
`Recording`, which is inert data.

Two things are worth stating plainly, because both are easy to get wrong later:

* **Evaluating at all needs consent.** ADR-0009 puts import behind the Migration wizard,
  and `Consent.evaluate` is that gate expressed in the type system -- there is no way to
  reach `evaluate()` from a default-constructed `Consent`.
* **Side effects need consent separately.** The default policy fakes them. Passthrough is
  a second, narrower grant for configs that produce nothing useful without it, and it is
  never inferred from the first.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

RUNNER = Path(__file__).with_name("runner.lua")

#: Tried in order. The runner needs `math.type` and `utf8`, so 5.3 is the floor.
INTERPRETERS: tuple[str, ...] = ("lua5.5", "lua5.4", "lua5.3", "lua", "luajit")

#: A foreign config is a program, and a program can loop forever.
DEFAULT_TIMEOUT = 60.0

#: How often a cancellable read looks at its cancel token while the config runs.
CANCEL_POLL_SECONDS = 0.05

#: Stripped from the child's environment even under passthrough: with these set, anything
#: the config shells out to can reach the *running* compositor and reconfigure the session
#: the user is importing from. The prototype found this the hard way via
#: `Hyprland --verify-config`, which executes the file it is checking. The session bus goes
#: too: through it a config's `systemctl --user import-environment` or
#: `dbus-update-activation-environment` rewrites the user manager's environment (addendum 40
#: of the #153 review).
STRIPPED_ENV: frozenset[str] = frozenset(
    {"HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
)


class Policy(StrEnum):
    """How the sandbox treats a side effect."""

    BLOCK = "block"
    """Intercept and fake it. Reads still work; nothing the config does escapes."""

    PASSTHROUGH = "passthrough"
    """Let it really happen, and record it anyway. Requires `Consent.passthrough`."""


@dataclass(frozen=True, slots=True)
class Consent:
    """What the user has agreed to for this one import.

    Default-constructed means "nothing", so a caller that forgets to ask gets a
    `ConsentRequired` rather than a silently-executed config.
    """

    evaluate: bool = False
    """May the file be run at all."""

    passthrough: bool = False
    """May its side effects reach the real system."""

    def policy(self) -> Policy:
        return Policy.PASSTHROUGH if self.passthrough else Policy.BLOCK


class ConsentRequired(RuntimeError):
    """Raised when an import would run user code the user has not agreed to run."""


class LuaUnavailable(RuntimeError):
    """No Lua interpreter to evaluate with -- an installation problem, not a config one."""


class Cancelled(RuntimeError):
    """The caller's cancel token was set: the read was stopped and there is no `Recording`.

    Raised rather than returned as an `errors` entry, because a cancelled read has no
    report to show -- the user asked for nothing to come of it (#216).
    """


@dataclass(frozen=True, slots=True)
class Upvalue:
    """One name a captured closure closed over."""

    name: str
    type: str
    value: Any = None


@dataclass(frozen=True, slots=True)
class Script:
    """A function the config handed to `hl`, located in its source file."""

    id: int
    source: str
    start: int
    end: int
    context: str = ""
    upvalues: tuple[Upvalue, ...] = ()


@dataclass(frozen=True, slots=True)
class Call:
    """One `hl.*` call, in the order it happened."""

    name: str
    args: Any = None
    src: str = ""
    line: int = 0
    argc: int | None = None
    submap: str | None = None

    @property
    def origin(self) -> str:
        """`file:line`, the spelling the Loss report uses everywhere."""
        return f"{self.src}:{self.line}" if self.src else ""


@dataclass(frozen=True, slots=True)
class ShellUse:
    """A command the config ran, or would have run."""

    kind: str
    cmd: str
    policy: str = ""
    src: str = ""


@dataclass(frozen=True, slots=True)
class Query:
    """A live-state question the config asked, answered with a stand-in."""

    fn: str
    src: str = ""
    line: int = 0

    @property
    def origin(self) -> str:
        return f"{self.src}:{self.line}" if self.src else ""


@dataclass(frozen=True, slots=True)
class FileWrite:
    """A file the config opened for writing."""

    path: str
    mode: str
    policy: str = ""


@dataclass(frozen=True, slots=True)
class FileRead:
    """A file the config read while loading -- state it has now baked in."""

    path: str
    src: str = ""


@dataclass(frozen=True, slots=True)
class Recording:
    """Everything one evaluation saw. Inert data: the mapper never re-runs anything."""

    calls: tuple[Call, ...] = ()
    scripts: tuple[Script, ...] = ()
    queries: tuple[Query, ...] = ()
    shell: tuple[ShellUse, ...] = ()
    writes: tuple[FileWrite, ...] = ()
    reads: tuple[FileRead, ...] = ()
    requires: tuple[str, ...] = ()
    prints: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    exited: bool = False
    policy: Policy = Policy.BLOCK
    basedir: Path = field(default_factory=Path)

    @property
    def ok(self) -> bool:
        """Nothing went wrong while running it."""
        return not self.errors

    @property
    def side_effects(self) -> bool:
        """The config tried to change something outside itself."""
        return bool(self.shell or self.writes)

    def script(self, script_id: int) -> Script | None:
        for script in self.scripts:
            if script.id == script_id:
                return script
        return None


def lua_missing_reason() -> str | None:
    """Why this machine cannot read a Lua config, in the user's words, or `None` if it can."""
    if lua_binary() is not None:
        return None
    return (
        "This app reads your settings with Lua, which is not installed. Install Lua ("
        + ", ".join(INTERPRETERS[:-1])
        + f" or {INTERPRETERS[-1]}) and open the app again"
    )


def lua_binary() -> str | None:
    """The interpreter that will be used, or `None` if there is not one."""
    for name in INTERPRETERS:
        if (found := shutil.which(name)) is not None:
            return found
    return None


def _child_env(env: dict[str, str] | None) -> dict[str, str]:
    base = dict(os.environ if env is None else env)
    for name in STRIPPED_ENV:
        base.pop(name, None)
    return base


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    cancel: threading.Event | None,
) -> tuple[int, str, str] | None:
    """`command`'s exit status, stdout and stderr; `None` when it overran `timeout`.

    The child leads a new session, so stopping it is one `killpg` that also takes whatever
    it started. Raises `Cancelled` once `cancel` is set, before the start or while it runs.
    """
    if cancel is not None and cancel.is_set():
        raise Cancelled("the read was cancelled before it started")
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        with _running_lock:
            _running.add(process)
        try:
            return _wait(process, deadline, cancel)
        finally:
            with _running_lock:
                _running.discard(process)


def _wait(
    process: subprocess.Popen[str], deadline: float, cancel: threading.Event | None
) -> tuple[int, str, str] | None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_group(process)
            return None
        wait = remaining if cancel is None else min(remaining, CANCEL_POLL_SECONDS)
        try:
            stdout, stderr = process.communicate(timeout=wait)
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                _kill_group(process)
                raise Cancelled("the read was cancelled") from None
            continue
        return process.returncode, stdout, stderr


_running: set[subprocess.Popen[str]] = set()
"""Reads in flight. A worker thread dies with the app, and the config, in a session of
its own, would then run on with nothing left to time it out (#216)."""
_running_lock = threading.Lock()


@atexit.register
def _stop_running() -> None:
    """At exit, kill every read still running, with whatever it started."""
    with _running_lock:
        for process in _running:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)


def _kill_group(process: subprocess.Popen[str]) -> None:
    """Kill the session `process` leads and reap it. Its pipes close with the `with`."""
    with contextlib.suppress(ProcessLookupError):  # it already ended, with everything in it
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _upvalues(raw: Any) -> tuple[Upvalue, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        Upvalue(
            name=str(item.get("name", "")),
            type=str(item.get("type", "")),
            value=item.get("value"),
        )
        for item in raw
        if isinstance(item, dict)
    )


def _decode(payload: dict[str, Any], *, policy: Policy, basedir: Path) -> Recording:
    """The runner's JSON, given types. Missing keys are absent findings, not errors."""

    def records(key: str) -> list[dict[str, Any]]:
        value = payload.get(key)
        return (
            [item for item in value if isinstance(item, dict)]
            if isinstance(value, list)
            else []
        )

    def strings(key: str) -> tuple[str, ...]:
        value = payload.get(key)
        return tuple(str(item) for item in value) if isinstance(value, list) else ()

    return Recording(
        calls=tuple(
            Call(
                name=str(item.get("call", "")),
                args=item.get("args"),
                src=str(item.get("src") or ""),
                line=int(item.get("line") or 0),
                argc=item.get("argc") if isinstance(item.get("argc"), int) else None,
                submap=str(item["submap"]) if item.get("submap") else None,
            )
            for item in records("calls")
        ),
        scripts=tuple(
            Script(
                id=int(item.get("id", 0)),
                source=str(item.get("source", "")),
                start=int(item.get("from", 0)),
                end=int(item.get("to", 0)),
                context=str(item.get("context") or ""),
                upvalues=_upvalues(item.get("upvalues")),
            )
            for item in records("scripts")
        ),
        queries=tuple(
            Query(
                fn=str(item.get("fn", "")),
                src=str(item.get("src") or ""),
                line=int(item.get("line") or 0),
            )
            for item in records("queries")
        ),
        shell=tuple(
            ShellUse(
                kind=str(item.get("kind", "")),
                cmd=str(item.get("cmd", "")),
                policy=str(item.get("policy") or ""),
                src=str(item.get("src") or ""),
            )
            for item in records("shell")
        ),
        writes=tuple(
            FileWrite(
                path=str(item.get("path", "")),
                mode=str(item.get("mode", "")),
                policy=str(item.get("policy") or ""),
            )
            for item in records("iowrites")
        ),
        reads=tuple(
            FileRead(path=str(item.get("path", "")), src=str(item.get("src") or ""))
            for item in records("reads")
        ),
        requires=strings("requires"),
        prints=strings("prints"),
        errors=strings("errors"),
        exited=bool(payload.get("exited")),
        policy=policy,
        basedir=basedir,
    )


def evaluate(
    entry: Path,
    *,
    consent: Consent,
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    basedir: Path | None = None,
    run_handlers: bool = False,
    assume_plugins_loaded: bool = False,
    cancel: threading.Event | None = None,
) -> Recording:
    """Run `entry` under the recording stub and report what it did.

    Raises `ConsentRequired` when the user has not agreed to evaluation, and
    `LuaUnavailable` when there is no interpreter. Everything else -- a syntax error, a
    config that raises, one that loops until the timeout -- comes back as an `errors`
    entry, because a failed import still has to produce a report the wizard can show.

    `cancel` is set from another thread to stop the read (the wizard's Cancel and Close,
    #216): the config's process group is killed and `Cancelled` raised. The config runs in
    a session of its own, so the kill takes the commands a passthrough read started with
    it, unless one detached itself; a timeout kills the same way.

    `run_handlers` enters `hl.on` handlers instead of only capturing them, bracketing what
    they declare with `on_enter`/`on_leave` calls. Off for every foreign config -- ADR-0009
    lifts whole handlers into `legacy.lua`, so entering one would double-count it -- and on
    for the single caller that reads back a handler this app wrote (`autostart.lua`, #70).

    `assume_plugins_loaded` answers `hl.get_config` for any `plugin:` key as if its plugin
    were loaded. Off for every foreign config; on for the single caller that reads a plugin
    setting back out of a Module this app wrote, where each one sits behind that guard
    (`retirement.capture`, #175).
    """
    if not consent.evaluate:
        raise ConsentRequired(f"importing {entry} would run it; no consent was given")

    interpreter = lua_binary()
    if interpreter is None:
        raise LuaUnavailable(
            "Reading a Lua config needs Lua, which is not installed. Install Lua ("
            + ", ".join(INTERPRETERS[:-1])
            + f" or {INTERPRETERS[-1]}) and try again."
        )

    entry = entry.resolve()
    root = (basedir or entry.parent).resolve()
    policy = consent.policy()

    with tempfile.TemporaryDirectory(prefix="hyprtweaker-import-") as scratch:
        out_path = Path(scratch) / "record.json"
        command = [
            interpreter,
            str(RUNNER),
            str(entry),
            str(root),
            str(out_path),
            str(policy),
            "run" if run_handlers else "keep",
            "plugins-loaded" if assume_plugins_loaded else "plugins-as-engine",
        ]
        completed = _run(command, cwd=root, env=_child_env(env), timeout=timeout, cancel=cancel)
        if completed is None:
            return Recording(
                errors=(
                    f"Your config took longer than {timeout:g} seconds to run, so reading "
                    "it was stopped.",
                ),
                policy=policy,
                basedir=root,
            )

        if not out_path.is_file():
            returncode, stdout, stderr = completed
            detail = (stderr or stdout or "").strip().splitlines()
            reason = detail[-1] if detail else f"exit status {returncode}"
            return Recording(
                errors=(f"evaluation produced nothing: {reason}",), policy=policy, basedir=root
            )

        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return Recording(
                errors=(f"unreadable evaluation record: {error}",), policy=policy, basedir=root
            )

    if not isinstance(payload, dict):
        return Recording(
            errors=("evaluation record was not an object",), policy=policy, basedir=root
        )
    return _decode(payload, policy=policy, basedir=root)


_ABSENT = object()


def config_values(
    module: Path, keys: Iterable[str], *, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any]:
    """What a Module this app wrote sets for each dotted Lua key in `keys`, JSON-native.

    Evaluated with consent granted, which is defensible only because of what the file is:
    the app's own output, already required by the Entrypoint on every reload (the footing
    `writer/binds.py` reads `binds.lua` back on). Never a foreign config. Plugin guards
    answer as if the plugin were loaded, because the app wrote every plugin setting behind
    one (#175).

    A key the Module does not set is left out, and a later `hl.config` wins, as in Lua. A
    missing file sets nothing. Raises `LuaUnavailable` without an interpreter: a caller
    that would read "nothing" as "the Module sets nothing" has to be able to tell.
    """
    if not module.is_file():
        return {}
    recording = evaluate(
        module, consent=Consent(evaluate=True), timeout=timeout, assume_plugins_loaded=True
    )
    tables = [
        call.args
        for call in recording.calls
        if call.name == "config" and isinstance(call.args, dict)
    ]
    values: dict[str, Any] = {}
    for key in keys:
        for table in tables:
            value = _lookup(table, key.split("."))
            if value is not _ABSENT:
                values[key] = value
    return values


def _lookup(table: dict[str, Any], path: list[str]) -> Any:
    node: Any = table
    for step in path:
        if not isinstance(node, dict) or step not in node:
            return _ABSENT
        node = node[step]
    return node
