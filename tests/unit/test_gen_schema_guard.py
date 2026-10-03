"""`tools/gen_schema.py` reads only a nested Hyprland (F15 of the #148 review).

It spawned bare `hyprctl` with the inherited environment, so from an agent's shell it read
the owner's desktop compositor. It now asks the Harness's guard first. Both compositors are
faked as `test_desktop_fence_hook.py` fakes them: a `hyprland.lock` naming a `sleep` started
with the account's `HOME` (the desktop) or a sandbox `HOME` (a nested instance).
"""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import gen_schema  # noqa: E402

SIGNATURE = "efb5_1789944280_1905786262"


@pytest.fixture
def compositor(tmp_path: Path) -> Iterator[object]:
    started: list[subprocess.Popen[bytes]] = []

    def start(home: Path) -> None:
        sleep = shutil.which("sleep")
        assert sleep is not None
        process = subprocess.Popen([sleep, "60"], env={"HOME": str(home)})
        started.append(process)
        directory = tmp_path / "run" / "hypr" / SIGNATURE
        directory.mkdir(parents=True)
        (directory / "hyprland.lock").write_text(f"{process.pid}\nwayland-1\n")
        (directory / ".socket.sock").touch()

    yield start
    for process in started:
        process.kill()
        process.wait()


def test_the_desktops_own_compositor_is_refused(
    tmp_path: Path, compositor: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    compositor(Path(pwd.getpwuid(os.getuid()).pw_dir))  # type: ignore[operator]
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", SIGNATURE)

    with pytest.raises(SystemExit) as refused:
        gen_schema.hyprctl("-j", "descriptions")

    assert str(refused.value).startswith(
        f"gen_schema reads only a nested Hyprland, and {SIGNATURE} is the session's own "
        "compositor"
    )
    assert gen_schema.hyprland_commit() is None


def test_a_nested_compositor_is_read(
    tmp_path: Path, compositor: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    compositor(tmp_path / "sandbox-home")  # type: ignore[operator]
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", SIGNATURE)

    assert gen_schema.compositor_refusal() is None


def test_with_no_compositor_named_it_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)

    with pytest.raises(SystemExit, match="no Hyprland named"):
        gen_schema.hyprland_version()
