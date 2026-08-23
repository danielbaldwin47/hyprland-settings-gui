"""A Hyprland of our own: nested, disposable, and unable to reach the host session.

**ADR-0011 tier 3.** This is prototype #9's `nested.py` promoted from throwaway to test
infrastructure. The prototype proved the method (it caught two real converter bugs that
reading the spec had not); what it lacked was the isolation guarantees a test suite needs
when the developer is *sitting in* the compositor the tests could otherwise talk to.

Three isolation rules, each of which has a way of going wrong:

1. **Its own instance signature.** `HYPRLAND_INSTANCE_SIGNATURE` is stripped from the child's
   environment so Hyprland mints a fresh one, and we learn it by diffing `hyprctl instances`
   across the launch. Discovery goes through `instances` rather than by listing
   `$XDG_RUNTIME_DIR/hypr/`, because dead instances leave their signature directory behind --
   a nested compositor that crashed an hour ago is still a directory, and picking it would
   point every subsequent call at a socket nobody is listening on.
2. **Its own `$HOME`.** Hyprland records first launch in
   `~/.local/share/hyprland/lastVersion`, and `hl.env`, `hl.permission` and the donate screen
   all behave differently on a second run (prototype #9 §4.6). Two configs compared in one
   `$HOME` are therefore not comparable at all -- the second is never a first launch. Each
   `NestedHyprland` gets a pristine home, which is also what keeps a rice's own state files
   out of the developer's real dotfiles.
3. **Its own Wayland socket.** The child advertises a new `wl_socket`; every `hyprctl`, `grim`
   and probe-window call made through `env` carries both that and the new signature, so no
   command in this package can be delivered to the host by accident.

**A host Wayland session is required.** `HYPRLAND_HEADLESS_ONLY=1` on its own is not enough:
without a host compositor to nest into, backend creation fails outright
(`CBackend::create() failed!`) even when a render node is handed to it explicitly, because
the DRM backend wants a seat that the developer's own session already owns. So this tier
runs *nested*, and `unavailable_reason` reports the missing session as a skip rather than
letting the launch fail deep inside a test. The headless *output* created inside the nested
compositor (see `visual.py`) is what makes rendering independent of the host's screen size --
that part needs no seat.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.ipc import Instance  # noqa: E402

REGISTER_TIMEOUT_SECONDS = 40.0
IPC_TIMEOUT_SECONDS = 20.0
EXIT_TIMEOUT_SECONDS = 10.0
POLL_SECONDS = 0.25

#: `hyprctl -j` surfaces worth capturing. `clients` and `workspaces` are here for the visual
#: tier's geometry assertions; the rest are the config-derived state a reload rebuilds.
STATE_SURFACES = (
    "binds",
    "monitors",
    "workspacerules",
    "layers",
    "animations",
    "devices",
    "clients",
    "workspaces",
    "layouts",
    "configerrors",
    "globalshortcuts",
)


class HarnessUnavailable(RuntimeError):
    """This machine cannot run the Harness tier. Carries the reason for the skip message."""


class NestedHyprlandError(RuntimeError):
    """The nested compositor failed to start, or died while we were driving it."""


def hyprland_binary() -> str | None:
    """The `Hyprland` binary, or `None` -- the tier's primary skip condition."""
    return shutil.which("Hyprland")


def unavailable_reason() -> str | None:
    """Why the Harness cannot run here, or `None` if it can.

    Checked as one function rather than as separate skip decorators so a test module, a
    fixture and a command-line runner all agree on what "available" means -- and so the
    reason a developer sees names the thing to install or the session to start.
    """
    if hyprland_binary() is None:
        return "no Hyprland binary on this machine"
    if shutil.which("hyprctl") is None:
        return "no hyprctl binary on this machine"
    if not os.environ.get("WAYLAND_DISPLAY"):
        return (
            "no host Wayland session (WAYLAND_DISPLAY unset): a nested Hyprland cannot "
            "create a backend without one"
        )
    if not os.environ.get("XDG_RUNTIME_DIR"):
        return "XDG_RUNTIME_DIR unset: no directory for the nested instance's sockets"
    return None


def live_instances(env: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Signature -> instance record, for the instances actually answering right now."""
    result = subprocess.run(
        ["hyprctl", "instances", "-j"],
        capture_output=True,
        text=True,
        env=dict(env),
        timeout=IPC_TIMEOUT_SECONDS,
        check=False,
    )
    try:
        return {record["instance"]: record for record in json.loads(result.stdout)}
    except (ValueError, KeyError, TypeError):
        return {}


def make_home(root: Path) -> Path:
    """A pristine `$HOME` with the XDG directories Hyprland expects to exist."""
    home = root
    for relative in (".config", ".local/share", ".local/state", ".cache"):
        (home / relative).mkdir(parents=True, exist_ok=True)
    return home


def home_environment(home: Path, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """`base` with every XDG variable repointed inside `home`.

    All four are set explicitly rather than letting the fallbacks apply: Hyprland and the
    app both consult `XDG_*` first, and a single unset variable would silently route one
    file back into the developer's real dotfiles.
    """
    environment = dict(os.environ if base is None else base)
    environment["HOME"] = str(home)
    environment["XDG_CONFIG_HOME"] = str(home / ".config")
    environment["XDG_DATA_HOME"] = str(home / ".local" / "share")
    environment["XDG_STATE_HOME"] = str(home / ".local" / "state")
    environment["XDG_CACHE_HOME"] = str(home / ".cache")
    return environment


class NestedHyprland:
    """A running nested Hyprland, used as a context manager.

    ``with NestedHyprland(config, home=home) as nested:`` yields a started compositor and
    guarantees it is stopped again, including when the body raises -- an escaped nested
    compositor holds a Wayland socket and a GPU context for as long as the developer's
    session lives, and the only sign of it is a machine getting slower.
    """

    def __init__(
        self,
        config: Path,
        *,
        home: Path,
        log: Path | None = None,
        timeout: float = REGISTER_TIMEOUT_SECONDS,
    ) -> None:
        self.config = Path(config)
        self.home = Path(home)
        self.log = Path(log) if log else None
        self.timeout = timeout
        self.signature: str | None = None
        self.wayland_display: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None

    # ---- lifecycle ---------------------------------------------------------------

    def __enter__(self) -> NestedHyprland:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.stop()
        return False

    @property
    def launch_environment(self) -> dict[str, str]:
        """The environment the child is launched with: our `$HOME`, the *host's* socket.

        The host `WAYLAND_DISPLAY` is deliberately kept -- that is the session the child
        nests into. What is dropped is `HYPRLAND_INSTANCE_SIGNATURE`, so the child cannot
        inherit our identity and must mint its own.
        """
        environment = home_environment(self.home)
        environment.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        environment["HYPRLAND_HEADLESS_ONLY"] = "1"
        return environment

    @property
    def env(self) -> dict[str, str]:
        """The environment for talking *to* the nested compositor.

        Every `hyprctl`, `grim` and probe-window invocation in this package goes through
        here. Asking for it before the compositor has registered is a bug, not a wait:
        the caller would otherwise address the host.
        """
        if self.signature is None or self.wayland_display is None:
            raise NestedHyprlandError("nested Hyprland has not registered yet")
        environment = home_environment(self.home)
        environment["HYPRLAND_INSTANCE_SIGNATURE"] = self.signature
        environment["WAYLAND_DISPLAY"] = self.wayland_display
        return environment

    @property
    def instance(self) -> Instance:
        """The nested compositor as the engine's own `Instance`.

        This is the seam that makes the tier worth having: `Instance` is a frozen dataclass
        over a socket directory, so the real `CommandClient`, `EventStream` and `Applier`
        drive the nested compositor unmodified. Nothing is stubbed, monkeypatched or
        re-implemented for the test -- an end-to-end run exercises the shipping pipeline.
        """
        if self.signature is None:
            raise NestedHyprlandError("nested Hyprland has not registered yet")
        runtime = Path(self.env["XDG_RUNTIME_DIR"])
        return Instance(runtime / "hypr" / self.signature)

    def start(self) -> None:
        reason = unavailable_reason()
        if reason is not None:
            raise HarnessUnavailable(reason)
        if self._process is not None:
            raise NestedHyprlandError("already started")

        make_home(self.home)
        launch_environment = self.launch_environment
        before = set(live_instances(launch_environment))

        if self.log is not None:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log.open("wb")
        stdout: Any = self._log_handle if self._log_handle else subprocess.DEVNULL

        self._process = subprocess.Popen(
            [str(hyprland_binary()), "-c", str(self.config)],
            env=launch_environment,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        self._await_registration(launch_environment, before)
        self._await_ipc()

    def _await_registration(self, environment: Mapping[str, str], before: set[str]) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise NestedHyprlandError(
                    f"Hyprland exited with {self._process.returncode} before registering "
                    f"(config {self.config}){self._log_hint()}"
                )
            current = live_instances(environment)
            fresh = sorted(set(current) - before)
            if fresh:
                self.signature = fresh[0]
                self.wayland_display = current[self.signature]["wl_socket"]
                return
            time.sleep(POLL_SECONDS)
        self.stop()
        raise NestedHyprlandError(
            f"nested Hyprland never registered within {self.timeout:.0f}s "
            f"(config {self.config}){self._log_hint()}"
        )

    def _await_ipc(self) -> None:
        """Registered is not the same as ready: the socket appears before it answers."""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["hyprctl", "-j", "monitors"],
                capture_output=True,
                text=True,
                env=self.env,
                timeout=IPC_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip().startswith("["):
                return
            time.sleep(POLL_SECONDS)
        raise NestedHyprlandError(f"nested Hyprland never answered IPC{self._log_hint()}")

    def _log_hint(self) -> str:
        return f"; see {self.log}" if self.log else ""

    def stop(self) -> None:
        """Ask politely, then insist.

        The polite form is engine-dependent -- `hyprctl dispatch` takes a legacy dispatcher
        name under hyprlang and *evaluates Lua* under a Lua config (prototype #9 §4.1) -- so
        both spellings are tried before falling back to signalling the process group. The
        group, not the pid: Hyprland is started with `start_new_session=True`, and killing
        only the leader would strand whatever it spawned.
        """
        process, self._process = self._process, None
        if process is None:
            self._close_log()
            return

        if self.signature is not None:
            for expression in ("hl.dsp.exit()", "exit"):
                try:
                    self.hyprctl_text("dispatch", expression)
                    break
                except (subprocess.SubprocessError, OSError, NestedHyprlandError):
                    continue

        try:
            process.wait(timeout=EXIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=EXIT_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self._signal_group(process, signal.SIGKILL)
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=EXIT_TIMEOUT_SECONDS)
        finally:
            self.signature = None
            self.wayland_display = None
            self._close_log()

    @staticmethod
    def _signal_group(process: subprocess.Popen[bytes], number: int) -> None:
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(process.pid), number)

    def _close_log(self) -> None:
        if self._log_handle is not None:
            with suppress(OSError):
                self._log_handle.close()
            self._log_handle = None

    # ---- IPC ---------------------------------------------------------------------

    def hyprctl_text(self, *args: str, timeout: float = IPC_TIMEOUT_SECONDS) -> str:
        result = subprocess.run(
            ["hyprctl", *args],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=timeout,
            check=False,
        )
        return result.stdout

    def hyprctl(self, *args: str, timeout: float = IPC_TIMEOUT_SECONDS) -> Any:
        """`hyprctl -j`, decoded. Returns `None` when the reply is not JSON."""
        result = subprocess.run(
            ["hyprctl", "-j", *args],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=timeout,
            check=False,
        )
        try:
            return json.loads(result.stdout)
        except ValueError:
            return None

    def getoptions(self, names: Sequence[str], chunk: int = 60) -> dict[str, Any]:
        """Every named option's live value, batched.

        `hyprctl -j descriptions` reports each option's *default*, not its current value
        (issue #3), so the only way to read 353 live values is 353 `getoption` calls -- which
        is why they are batched. `--batch` answers with concatenated JSON objects rather than
        an array, hence the stitching; a batch whose reply does not line up with its request
        falls back to one call per option rather than silently mispairing names and values.
        """
        values: dict[str, Any] = {}
        for start in range(0, len(names), chunk):
            part = list(names[start : start + chunk])
            batch = " ; ".join(f"getoption {name}" for name in part)
            result = subprocess.run(
                ["hyprctl", "-j", "--batch", batch],
                capture_output=True,
                text=True,
                env=self.env,
                timeout=IPC_TIMEOUT_SECONDS * 3,
                check=False,
            )
            decoded = _decode_batch(result.stdout)
            if len(decoded) != len(part):
                for name in part:
                    values[name] = self.hyprctl("getoption", name)
                continue
            values.update(zip(part, decoded, strict=True))
        return values

    def dispatch(self, expression: str) -> str:
        """Run a Lua dispatcher expression, e.g. `hl.dsp.window.close()`.

        Under a Lua config `hyprctl dispatch` evaluates its argument as Lua source; the
        legacy `dispatch movefocus l` spelling is a syntax error there, and the Lua spelling
        is "Invalid dispatcher" under hyprlang (prototype #9 §4.1). The two CLI surfaces are
        disjoint, so the caller has to know which engine it is driving -- and everything this
        harness writes is Lua.
        """
        return self.hyprctl_text("dispatch", expression)

    def dispatch_legacy(self, name: str, *args: str) -> str:
        """The hyprlang spelling, for the corpus `.conf` trees."""
        return self.hyprctl_text("dispatch", name, *args)

    def config_errors(self) -> tuple[str, ...]:
        """`configerrors`, with Hyprland's `[""]` for "none" read as empty."""
        raw = self.hyprctl("configerrors")
        if not isinstance(raw, list):
            return ()
        return tuple(line for line in raw if isinstance(line, str) and line.strip())


def _decode_batch(text: str) -> list[Any]:
    """`--batch` returns concatenated JSON objects; make them an array."""
    stripped = text.strip()
    if not stripped:
        return []
    joined = stripped.replace("}\n\n{", "},{").replace("}{", "},{")
    try:
        decoded = json.loads(f"[{joined}]")
    except ValueError:
        return []
    return decoded if isinstance(decoded, list) else []
