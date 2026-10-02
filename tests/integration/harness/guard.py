"""The one door to a live Hyprland in the test suite: it refuses the session's own compositor.

The developer running this suite is sitting in a Hyprland, and an agent's shell names it in
`HYPRLAND_INSTANCE_SIGNATURE`. A test that took its instance from the environment would
reload that session, or rewrite its config errors, from any shell. So a test that talks to a
compositor gets it through `guarded`, and `tests/unit/test_no_unguarded_instance.py` fails
the per-commit run on any `Instance.current()` or bare `hyprctl` spawn under `tests/`.

**How the session's own compositor is told apart.** By the `HOME` it runs with. Hyprland
writes `<pid>\\n<wl_socket>` to `hyprland.lock` in its instance directory (the file
`hyprctl instances` reads), so the compositor's process and its environment are one read
away, with no IPC. The session's compositor runs the account's own config, so its `HOME` is
the account's home. Every compositor the Harness or `tools/sandbox.py` starts runs under
`NestedHyprland`, which always hands it a pristine sandbox `HOME`. Anything the guard
cannot read -- no lock, a dead pid, an unreadable environment -- is refused: not knowing
which compositor it is must never read as "nested".
"""

from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from pathlib import Path

import pytest

from hyprtweaker.engine.ipc import Instance

#: Set to 1, a refusal fails the test instead of skipping it, as for every Harness skip.
REQUIRE_VARIABLE = "HYPRTWEAKER_REQUIRE_HARNESS"

LOCK_NAME = "hyprland.lock"

FIX = (
    "Run it against the Harness's NestedHyprland (the `guarded_hyprland` fixture in "
    "tests/integration/conftest.py) or against a tools/sandbox.py instance: "
    "docs/agents/local-checks.md § Harness tier and § Running the app."
)


@dataclass(frozen=True)
class GuardedInstance:
    """An instance the guard accepted, and the environment that addresses only it.

    `env` is the one environment a test may spawn `hyprctl` (or anything that runs it, such
    as `tools/gen_schema.py`) with: the instance's signature and runtime directory, its own
    Wayland socket, no host X display, and the compositor's own `hyprctl` first on `PATH`.
    """

    instance: Instance
    env: dict[str, str]


def _account_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _lock(instance: Instance) -> tuple[int, str] | None:
    try:
        pid, wl_socket = instance.directory.joinpath(LOCK_NAME).read_text().splitlines()[:2]
        return int(pid), wl_socket
    except (OSError, ValueError):
        return None


def _environ(proc: Path, pid: int) -> dict[str, str] | None:
    try:
        raw = (proc / str(pid) / "environ").read_bytes()
    except OSError:
        return None
    entries = (entry.partition("=") for entry in raw.decode(errors="replace").split("\0"))
    return {name: value for name, _, value in entries if name}


def session_compositor_reason(
    instance: Instance, *, proc: Path = Path("/proc"), account_home: Path | None = None
) -> str | None:
    """Why `instance` may not be talked to, or `None` for a nested instance."""
    signature = instance.directory.name
    lock = _lock(instance)
    if lock is None:
        return f"{signature} has no {LOCK_NAME} to name its compositor"
    environment = _environ(proc, lock[0])
    if environment is None:
        return f"{signature}: cannot read the environment of pid {lock[0]}"
    home = environment.get("HOME")
    account = account_home or _account_home()
    if not home or Path(home).resolve() == account.resolve():
        return f"{signature} is the session's own compositor (it runs with HOME={home})"
    return None


def guarded(
    instance: Instance, *, proc: Path = Path("/proc"), account_home: Path | None = None
) -> GuardedInstance:
    """`instance`, if it is not the session's own compositor; otherwise skip or fail."""
    reason = session_compositor_reason(instance, proc=proc, account_home=account_home)
    if reason is not None:
        message = f"refused {reason}. {FIX}"
        if os.environ.get(REQUIRE_VARIABLE) == "1":
            pytest.fail(message)
        pytest.skip(message)

    lock = _lock(instance)
    assert lock is not None  # read above, or refused
    pid, wl_socket = lock
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env["HYPRLAND_INSTANCE_SIGNATURE"] = instance.directory.name
    env["XDG_RUNTIME_DIR"] = str(instance.directory.parent.parent)
    env["WAYLAND_DISPLAY"] = wl_socket
    binary_dir = (proc / str(pid) / "exe").resolve().parent
    if (binary_dir / "hyprctl").is_file():
        env["PATH"] = os.pathsep.join([str(binary_dir), env.get("PATH", "")])
    return GuardedInstance(instance, env)
