"""The desktop fence (#204): a PreToolUse hook on Bash that refuses an agent's bare `hyprctl`,
`Hyprland`, `wtype` and `ydotool` against the owner's desktop compositor.

The hook is fed tool-call payloads on stdin, as Claude Code feeds it, and its stdout is read
for the verdict. Nothing here runs `hyprctl`, `Hyprland`, `wtype` or `ydotool`. Both
compositors are faked the way Hyprland presents them: an instance directory under a fake
`$XDG_RUNTIME_DIR/hypr` whose `hyprland.lock` names a pid and a Wayland socket. The pid is a
`sleep` started with the account's `HOME` (the session's own compositor) or with a sandbox
`HOME` (a nested instance, as `NestedHyprland` and `tools/sandbox.py` give it). The hook runs
with `HYPRLAND_INSTANCE_SIGNATURE`, `XDG_RUNTIME_DIR`, `WAYLAND_DISPLAY` and `HOME` pinned to
those fakes, so it never reads the owner's real `/run/user/<uid>/hypr`.

These tests need `bash` and `jq`, and fail without them rather than skip. The `unit` CI job
installs neither: it relies on the `ubuntu-latest` image, which ships both.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FENCE = ROOT / ".claude" / "hooks" / "desktop-fence.sh"
BASE_GUARD = ROOT / ".claude" / "hooks" / "pr-base-guard.sh"

DESKTOP = "efb5_1789944280_1905786262"  # the inherited HYPRLAND_INSTANCE_SIGNATURE
RESTARTED_DESKTOP = "efb5_1790920661_785608408"  # the account's HOME, another signature
NESTED = "c0ffee_1790000000_42"
NESTED_DISPLAY = "wayland-9"
DEAD = "dead_1790000001_7"  # an instance directory with no lock


@dataclass(frozen=True)
class World:
    env: dict[str, str]
    runtime: Path


def tool(name: str) -> str:
    path = shutil.which(name)
    assert path is not None, (
        f"{name} not on PATH: the desktop fence's tests need it (see module docstring)"
    )
    return path


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    root = tmp_path_factory.mktemp("fence")
    home, sandbox, runtime = root / "home", root / "sandbox", root / "run"
    for directory in (home, sandbox, runtime):
        directory.mkdir()
    sleep = tool("sleep")
    started: list[subprocess.Popen[bytes]] = []

    def compositor(signature: str, *, home_dir: Path, display: str) -> None:
        process = subprocess.Popen([sleep, "600"], env={"HOME": str(home_dir)})
        started.append(process)
        directory = runtime / "hypr" / signature
        directory.mkdir(parents=True)
        (directory / "hyprland.lock").write_text(f"{process.pid}\n{display}\n")

    compositor(DESKTOP, home_dir=home, display="wayland-1")
    compositor(RESTARTED_DESKTOP, home_dir=home, display="wayland-3")
    compositor(NESTED, home_dir=sandbox, display=NESTED_DISPLAY)
    (runtime / "hypr" / DEAD).mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "XDG_RUNTIME_DIR": str(runtime),
        "HYPRLAND_INSTANCE_SIGNATURE": DESKTOP,
        "WAYLAND_DISPLAY": "wayland-1",
    }
    try:
        yield World(env=env, runtime=runtime)
    finally:
        for process in started:
            process.kill()
            process.wait()


def verdict(hook: Path, command: str, env: dict[str, str]) -> str | None:
    """The hook's refusal reason for `command`, or None when it lets the call through."""
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(ROOT)}
    )
    result = subprocess.run(
        [tool("bash"), str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny", output
    reason: str = output["permissionDecisionReason"]
    return reason


REFUSED = [
    # the ticket's table
    "hyprctl dispatch workspace 2",
    "hyprctl -j clients",
    "env FOO=1 hyprctl keyword general:gaps_in 0",
    'hyprctl --batch "dispatch workspace 2; keyword general:gaps_in 0"',
    "hyprctl -i 0 clients",
    f"hyprctl --instance {DESKTOP} clients",
    "ydotool key 28:1",
    "wtype hello",
    "Hyprland",
    # the verdict on an unexpanded variable: the hook cannot know what it names
    "HYPRLAND_INSTANCE_SIGNATURE=$SIG hyprctl clients",
    # selectors that do not name a nested instance
    f"HYPRLAND_INSTANCE_SIGNATURE={DESKTOP} hyprctl clients",
    f"hyprctl -i {RESTARTED_DESKTOP} clients",
    f"hyprctl -i {DEAD} clients",
    "hyprctl -i nosuchinstance clients",
    f"hyprctl -i ../{NESTED} clients",
    'hyprctl -i "$(cat sig)" clients',
    f"hyprctl clients -i {NESTED}",
    "env -u HYPRLAND_INSTANCE_SIGNATURE hyprctl clients",
    # command position behind operators, wrappers and paths
    "cd /tmp && /usr/bin/hyprctl clients",
    "timeout 5 hyprctl -j clients",
    "if hyprctl -j clients > /dev/null; then echo up; fi",
    "echo $(hyprctl -j clients)",
    'echo "focused: $(hyprctl activewindow)"',
    "echo `hyprctl reload`",
    "true\nhyprctl reload",
    "hyp\\\nrctl reload",
    # the other three
    "Hyprland --version",
    "start-hyprland",
    "ydotoold",
    "WAYLAND_DISPLAY=wayland-1 wtype hello",
    "WAYLAND_DISPLAY=wayland-3 wtype hello",
    "WAYLAND_DISPLAY=wayland-5 wtype hello",
    "WAYLAND_DISPLAY=$NESTED_DISPLAY wtype hello",
    f"HYPRLAND_INSTANCE_SIGNATURE={NESTED} wtype hello",
]

ALLOWED = [
    # the ticket's table
    "hyprctl instances",
    "hyprctl instances -j",
    "hyprctl instances -j | jq length",
    "systemctl --user show-environment",
    f"hyprctl --instance {NESTED} dispatch workspace 2",
    f"WAYLAND_DISPLAY={NESTED_DISPLAY} wtype hello",
    ".venv/bin/pytest -q tests/unit",
    "grep hyprctl docs/",
    # the other nested selector shapes
    f"hyprctl -i {NESTED} -j clients",
    f"hyprctl --instance={NESTED} reload",
    f"HYPRLAND_INSTANCE_SIGNATURE={NESTED} hyprctl -j clients",
    f'hyprctl -i "{NESTED}" --batch "dispatch workspace 2; keyword general:gaps_in 0"',
    f"env WAYLAND_DISPLAY={NESTED_DISPLAY} wtype hello",
    # the words only as data
    'git commit -m "Refuse hyprctl dispatch; hyprctl reload too"',
    "echo 'hyprctl clients | wtype -'",
    "git commit -F - <<'EOF'\nFence the shell\n\nhyprctl dispatch workspace 2\nwtype hi\nEOF",
    "cat <<EOF > notes.md\nydotool key 28:1\nEOF\nls",
    "command -v hyprctl wtype",
    "which ydotool",
    "ls /usr/bin/Hyprland  # Hyprland itself stays unrun",
    "pacman -Q hyprland",
]


@pytest.mark.parametrize("command", REFUSED)
def test_the_fence_refuses_a_call_aimed_at_the_desktop(world: World, command: str) -> None:
    tool("jq")
    assert verdict(FENCE, command, world.env) is not None


@pytest.mark.parametrize("command", ALLOWED)
def test_the_fence_lets_through_what_cannot_reach_the_desktop(
    world: World, command: str
) -> None:
    tool("jq")
    assert verdict(FENCE, command, world.env) is None


@pytest.mark.parametrize(
    ("command", "what", "working_shape"),
    [
        ("hyprctl dispatch workspace 2", "`hyprctl dispatch`", "hyprctl --instance <signature"),
        ("hyprctl -i 0 clients", "`hyprctl clients`", "hyprctl --instance <signature"),
        ("Hyprland", "`Hyprland`", "tools/sandbox.py"),
        ("wtype hello", "`wtype`", "WAYLAND_DISPLAY=<display"),
        ("ydotool key 28:1", "`ydotool`", "WAYLAND_DISPLAY=<display"),
    ],
)
def test_a_refusal_names_the_call_why_and_the_shape_that_works(
    world: World, command: str, what: str, working_shape: str
) -> None:
    tool("jq")
    reason = verdict(FENCE, command, world.env)
    assert reason is not None
    assert what in reason
    assert "desktop" in reason
    assert working_shape in reason
    assert "docs/agents/local-checks.md" in reason


def test_a_refusal_says_why_this_selector_is_not_nested(world: World) -> None:
    tool("jq")
    index = verdict(FENCE, "hyprctl -i 0 clients", world.env)
    assert index is not None and "index" in index
    inherited = verdict(FENCE, f"hyprctl -i {DESKTOP} clients", world.env)
    assert inherited is not None and "HYPRLAND_INSTANCE_SIGNATURE" in inherited
    unexpanded = verdict(FENCE, "HYPRLAND_INSTANCE_SIGNATURE=$SIG hyprctl clients", world.env)
    assert unexpanded is not None and "$SIG" in unexpanded


def test_without_jq_the_fence_fails_closed_on_the_four_words(
    world: World, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("cat", "grep"):
        (bin_dir / name).symlink_to(tool(name))
    env = {**world.env, "PATH": str(bin_dir)}
    reason = verdict(FENCE, "grep hyprctl docs/", env)
    assert reason is not None and "install jq" in reason
    assert verdict(FENCE, "ls docs/", env) is None


def test_settings_register_both_hooks_on_bash_and_the_base_guard_still_refuses_a_merge(
    world: World,
) -> None:
    tool("jq")
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    commands = [
        hook["command"]
        for entry in settings["hooks"]["PreToolUse"]
        if entry["matcher"] == "Bash"
        for hook in entry["hooks"]
    ]
    assert any(command.endswith("/.claude/hooks/pr-base-guard.sh") for command in commands)
    assert any(command.endswith("/.claude/hooks/desktop-fence.sh") for command in commands)
    assert verdict(BASE_GUARD, "gh pr merge 5", world.env) is not None
