"""The Harness tier's desktop guard (#201): it refuses the session's own compositor.

No compositor here. Both sides are faked the way the kernel and Hyprland present them: a
runtime directory whose instance holds a `hyprland.lock` (pid, then Wayland socket), and a
`/proc` whose `<pid>/environ` carries that compositor's `HOME`. The stand-in for the
session's own compositor is a process running with the account's home; a nested instance
runs with a sandbox home, as `NestedHyprland` and `tools/sandbox.py` always give it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from harness.guard import REQUIRE_VARIABLE, guarded, session_compositor_reason

from hyprtweaker.engine.ipc import Instance

SIGNATURE = "efb5_1789944280_1905786262"


def fake_compositor(
    root: Path,
    *,
    home: str,
    pid: int = 4242,
    wl_socket: str = "wayland-7",
    lock: bool = True,
    running: bool = True,
    binary_dir: Path | None = None,
) -> tuple[Instance, Path]:
    """An instance directory and the `/proc` it is checked against."""
    directory = root / "runtime" / "hypr" / SIGNATURE
    directory.mkdir(parents=True)
    if lock:
        (directory / "hyprland.lock").write_text(f"{pid}\n{wl_socket}\n")
    proc = root / "proc"
    proc.mkdir()
    if running:
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "environ").write_bytes(f"PATH=/usr/bin\0HOME={home}\0".encode())
        binary = (binary_dir or root / "usr-bin") / "Hyprland"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.touch()
        (proc / str(pid) / "exe").symlink_to(binary)
    return Instance(directory), proc


ACCOUNT_HOME = Path("/home/owner")


def test_the_session_compositor_is_refused_with_the_fix_and_skips_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(REQUIRE_VARIABLE, raising=False)
    instance, proc = fake_compositor(tmp_path, home="/home/owner")

    with pytest.raises(pytest.skip.Exception) as refused:
        guarded(instance, proc=proc, account_home=ACCOUNT_HOME)

    message = str(refused.value)
    assert SIGNATURE in message
    assert "the session's own compositor" in message
    assert "NestedHyprland" in message
    assert "tools/sandbox.py" in message


def test_under_require_harness_the_refusal_fails_the_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REQUIRE_VARIABLE, "1")
    instance, proc = fake_compositor(tmp_path, home="/home/owner/")

    with pytest.raises(pytest.fail.Exception) as refused:
        guarded(instance, proc=proc, account_home=ACCOUNT_HOME)

    assert "NestedHyprland" in str(refused.value)


def test_a_nested_instance_is_handed_back_with_an_environment_aimed_only_at_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "the-desktop's")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setenv("DISPLAY", ":0")
    instance, proc = fake_compositor(tmp_path, home=str(tmp_path / "sandbox-home"))

    accepted = guarded(instance, proc=proc, account_home=ACCOUNT_HOME)

    assert accepted.instance == instance
    assert accepted.env["HYPRLAND_INSTANCE_SIGNATURE"] == SIGNATURE
    assert accepted.env["XDG_RUNTIME_DIR"] == str(tmp_path / "runtime")
    assert accepted.env["WAYLAND_DISPLAY"] == "wayland-7"
    assert "DISPLAY" not in accepted.env


def test_the_compositors_own_hyprctl_comes_first_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release check may nest a Hyprland built elsewhere; its `hyprctl` must answer."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    build = tmp_path / "build" / "bin"
    build.mkdir(parents=True)
    (build / "hyprctl").touch()
    instance, proc = fake_compositor(tmp_path, home="/tmp/sandbox", binary_dir=build)

    accepted = guarded(instance, proc=proc, account_home=ACCOUNT_HOME)

    assert accepted.env["PATH"] == f"{build}:/usr/bin:/bin"


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ({"lock": False}, "no hyprland.lock"),
        ({"running": False}, "cannot read the environment of pid 4242"),
    ],
    ids=["no-lock", "dead-pid"],
)
def test_an_instance_it_cannot_identify_is_refused(
    tmp_path: Path, setup: dict[str, bool], expected: str
) -> None:
    """Fail closed: not knowing which compositor this is must never read as "nested"."""
    instance, proc = fake_compositor(tmp_path, home="/tmp/sandbox", **setup)

    reason = session_compositor_reason(instance, proc=proc, account_home=ACCOUNT_HOME)

    assert reason is not None
    assert expected in reason
