"""`tests/ui/private_display.py` without an X server (review #151, findings 25 and 26).

The display files live under a temporary directory and the X server is a stand-in: a
Python script that takes the lock and socket the way Xvfb does, or a process that only
sleeps. Nothing here starts an X server or touches `/tmp/.X11-unix` or `/tmp/.X*-lock`.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

MODULE = Path(__file__).resolve().parents[1] / "ui" / "private_display.py"


@pytest.fixture
def private_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The module, its display files moved under `tmp_path`, on a session at `:0`."""
    spec = importlib.util.spec_from_file_location("private_display_under_test", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses (`PrivateBus`, #212) resolve their
    # string annotations through `sys.modules`.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_SOCKET", f"{tmp_path}/X{{}}")
    monkeypatch.setattr(module, "_LOCK", f"{tmp_path}/.X{{}}-lock")
    monkeypatch.setattr(module, "_TEMP_LOCK", f"{tmp_path}/.tX{{}}-lock")
    monkeypatch.setenv("DISPLAY", ":0")
    return module


def fake_xvfb(tmp_path: Path) -> Path:
    """An executable that serves `:<n>` as Xvfb does: its pid in the lock, then a socket."""
    script = tmp_path / "fake-xvfb"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, socket, sys, time\n"
        "number = sys.argv[1].lstrip(':')\n"
        f"with open('{tmp_path}/.X' + number + '-lock', 'w') as lock:\n"
        "    lock.write(f'{os.getpid():>10}\\n')\n"
        "server = socket.socket(socket.AF_UNIX)\n"
        f"server.bind('{tmp_path}/X' + number)\n"
        "server.listen()\n"
        "time.sleep(600)\n"
    )
    script.chmod(0o755)
    return script


def test_a_number_whose_xvfb_temp_lock_exists_is_skipped(
    private_display: ModuleType, tmp_path: Path
) -> None:
    # Xvfb sleeps about 6 s on a leftover /tmp/.tX<n>-lock before it gives up the number.
    (tmp_path / ".tX200-lock").write_text("")

    display = private_display.start_xvfb(str(fake_xvfb(tmp_path)))
    try:
        assert display == ":201"
        assert not (tmp_path / ".X200-lock").exists()
    finally:
        os.kill(int((tmp_path / ".X201-lock").read_text()), signal.SIGKILL)


def test_an_xvfb_that_misses_the_deadline_is_stopped_and_reaped(
    private_display: ModuleType,
) -> None:
    sleeper = subprocess.Popen(["sleep", "600"])

    assert private_display._serving(sleeper, 200, time.monotonic() - 1) is False
    # Asked first, so Xvfb removes its own lock and socket; reaped, so no zombie.
    assert sleeper.returncode == -signal.SIGTERM


def test_an_xvfb_that_ignores_the_request_to_stop_is_killed_and_reaped(
    private_display: ModuleType,
) -> None:
    stubborn = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(600)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert stubborn.stdout is not None and stubborn.stdout.readline() == "ready\n"

    assert private_display._serving(stubborn, 200, time.monotonic() - 1) is False
    assert stubborn.returncode == -signal.SIGKILL
    stubborn.stdout.close()
