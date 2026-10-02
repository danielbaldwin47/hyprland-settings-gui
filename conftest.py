"""The suite lock: one pytest run at a time on this machine, whatever the checkout.

On 2026-10-01 ten agents ran `pytest -n auto` at once (20 workers and an Xvfb apiece);
app.slice crossed systemd-oomd's memory-pressure limit and oomd killed the owner's
terminal. So the controller takes an exclusive `flock` before it collects and holds it
until the run ends, and a second run says it is waiting, and for whom, then queues.

It lives in the root conftest because that is the one every run loads, whatever paths it
is given. Three kinds of process never take the lock: xdist workers and pytest runs a
test starts inside a locked run (both would wait on their own parent), and CI, where each
job is a machine of its own. `HYPRTWEAKER_SUITE_LOCK` points the lock at another file,
which the lock's own tests (`tests/unit/test_suite_lock.py`) do.

The wait has no deadline: a run that gives up has proven nothing, and the done checks'
`timeout 900` bounds the whole run, wait included.
"""

from __future__ import annotations

import fcntl
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

LOCK_ENV = "HYPRTWEAKER_SUITE_LOCK"
_HELD = pytest.StashKey[int]()


def _lock_path() -> Path:
    if override := os.environ.get(LOCK_ENV):
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(runtime) / "hyprtweaker-pytest.lock"


def _ancestors() -> set[int]:
    """This process's parent, its parent, and so on, from /proc."""
    pids: set[int] = set()
    pid = os.getppid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            break
        pid = int(stat.rpartition(")")[2].split()[1])  # comm may hold spaces and ")"
    return pids


def _say(config: pytest.Config, line: str) -> None:
    capture = config.pluginmanager.getplugin("capturemanager")
    if capture is None:
        print(line, file=sys.stderr, flush=True)
        return
    with capture.global_and_fixture_disabled():
        print(line, file=sys.stderr, flush=True)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config, "workerinput") or os.environ.get("CI") or config.option.help:
        return
    path = _lock_path()
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pid, _, checkout = os.pread(fd, 4096, 0).decode(errors="replace").partition("\n")
        if pid.isdigit() and int(pid) in _ancestors():
            os.close(fd)  # a run inside the run that holds the lock
            return
        holder = f"PID {pid} ({checkout.strip()})" if pid.isdigit() else "another pytest run"
        _say(config, f"pytest: waiting for {path}, held by {holder}")
        started = time.monotonic()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except KeyboardInterrupt:
            os.close(fd)
            pytest.exit(f"stopped waiting for {path}", returncode=2)
        _say(config, f"pytest: took {path} after {time.monotonic() - started:.0f} s")
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"{os.getpid()}\n{config.rootpath}\n".encode(), 0)
    config.stash[_HELD] = fd


def pytest_unconfigure(config: pytest.Config) -> None:
    fd = config.stash.get(_HELD, None)
    if fd is not None:
        os.ftruncate(fd, 0)  # a run that waits after this names no stale holder
        os.close(fd)
        del config.stash[_HELD]


MAX_WORKERS = 8
"""What `-n auto` resolves to at most. Measured on the owner's 20-thread, 31 GB machine: one
uncapped run is 20 workers at about 0.73 GB each plus 21 Xvfb, and available memory fell to
7.4 GB; systemd-oomd had already killed the owner's terminal once. Here rather than in a doc,
so it holds for every caller."""


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    """`-n auto`, capped at `MAX_WORKERS`. Optional: meson's system pytest may lack xdist."""
    return min(MAX_WORKERS, os.cpu_count() or 1)
