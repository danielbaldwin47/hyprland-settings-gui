"""The desktop fence (#204, #209, review of #151): a PreToolUse hook on Bash and Monitor that
refuses an agent's bare `hyprctl`, `Hyprland`, `wtype`, `xdotool` and `ydotool` against the
owner's desktop compositor, a pattern kill (`pkill`, `killall`, `kill $(pgrep …)`), a GTK start
outside the probe route, an X server started from the agent's shell, a removal, move or link of
the session's X sockets and locks, and the owner's `HYPRTWEAKER_UI_HOST_DISPLAY` opt-in. A
command line handed to a shell (`bash -c`, `eval`, `watch`) is judged in turn.

The hook is fed tool-call payloads on stdin, as Claude Code feeds it, and its stdout is read
for the verdict. Nothing here runs `hyprctl`, `Hyprland`, `wtype`, `ydotool`, `pkill`,
`killall`, an X server or an `rm` of an X socket. Both compositors are faked the way Hyprland
presents them: an instance directory under a fake
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
import re
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


def verdict(
    hook: Path, command: str, env: dict[str, str], tool_name: str = "Bash"
) -> str | None:
    """The hook's refusal reason for `command`, or None when it lets the call through."""
    payload = json.dumps(
        {"tool_name": tool_name, "tool_input": {"command": command}, "cwd": str(ROOT)}
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
    # a pattern kill (#209): by name across the whole session
    "pkill -f foo",
    "pkill Hyprland",
    "killall Hyprland",
    "timeout 5 pkill -f 'a|b'",
    "make && killall foot",
    "sudo /usr/bin/killall -9 foot",
    # a GTK start outside the probe route
    '.venv/bin/python -c "from gi.repository import Gtk"',
    ".venv/bin/python3 -c 'from gi.repository import Adw'",
    'python3 -c \'import gi; gi.require_version("Gtk", "4.0")\'',
    "python -Ic 'from gi.repository import Gdk'",
    "python3 - <<'EOF'\nfrom gi.repository import Gtk\nEOF",
    ".venv/bin/python <<EOF\nimport gi\ngi.require_version('Adw', '1')\nEOF",
    "xvfb-run python x.py",
    "xvfb-run -a .venv/bin/python tools/widget_probe.py p.py",
    "python -m hyprtweaker",
    ".venv/bin/python -um hyprtweaker --config x",
    "python -m hyprtweaker.main",
    "python src/hyprtweaker/__main__.py",
    "PYTHONPATH=src .venv/bin/python -m hyprtweaker",
    # an X server started from the agent's shell
    "Xvfb :99",
    "Xvfb -displayfd 3",
    "timeout 5 Xvfb :201 &",
    "/usr/bin/Xvfb :250 -screen 0 1x1x24",
    "Xorg :1",
    "Xwayland :5 -rootless",
    # a removal, move or link of the session's X sockets and locks
    "rm /tmp/.X11-unix/X0",
    "rm -f /tmp/.X0-lock",
    "rm -rf /tmp/.X11-unix",
    "rm /tmp/.X11-unix/X*",
    "rm /tmp/.X*-lock",
    "sudo rm /tmp/.X1-lock",
    "mv /tmp/.X11-unix/X0 /tmp/x0.bak",
    "mv /tmp/x0 /tmp/.X11-unix/X0",
    "ln /tmp/.X11-unix/X0_ /tmp/.X11-unix/X0",
    "ln -sf /tmp/elsewhere /tmp/.X11-unix/X0",
    "unlink /tmp/.X11-unix/X0",
    "find /tmp/.X11-unix -name 'X*' -delete",
    "ls /tmp/.X11-unix && rm /tmp/.X11-unix/X0",
    # review #151, 4: a kill by name or of a process group, in the shapes tried after pkill
    "kill $(pgrep -f Xvfb)",
    "kill $(pidof Hyprland)",
    "pgrep -f foot | xargs kill",
    "kill -9 -1",
    "kill 0",
    "kill -- -4321",
    "kill -TERM `pgrep foot`",
    "kill -s KILL $(pgrep foot)",
    "pids=$(pgrep -f Xvfb); kill $pids",
    'pgrep -f foot | while read -r p; do kill "$p"; done',
    'for p in $(pidof foot); do kill "$p"; done',
    "ps aux | grep Xvfb | awk '{print $2}' | xargs kill -9",
    "fuser -k /tmp/.X11-unix/X0",
    # review #151, 5: the owner's opt-in that maps the UI tier on the desktop
    "HYPRTWEAKER_UI_HOST_DISPLAY=1 .venv/bin/pytest tests/ui",
    "export HYPRTWEAKER_UI_HOST_DISPLAY=1",
    "export HYPRTWEAKER_UI_HOST_DISPLAY=1 && .venv/bin/pytest -q tests/ui",
    "env HYPRTWEAKER_UI_HOST_DISPLAY=1 .venv/bin/pytest tests/ui",
    # review #151, 7: a wrapper option that takes a value, and the other wrappers
    "sudo -u diggle hyprctl reload",
    "timeout -s KILL 5 hyprctl reload",
    "timeout --signal KILL 5 hyprctl reload",
    "xargs -a cmds.txt hyprctl",
    "sudo -u diggle env hyprctl reload",
    "nice -n 5 hyprctl reload",
    "ionice -c 3 hyprctl reload",
    "taskset -c 0 hyprctl reload",
    "flock /tmp/x.lock hyprctl reload",
    "flock /tmp/x.lock -c 'hyprctl reload'",
    "systemd-run --user -p Nice=5 hyprctl reload",
    "strace -o /tmp/t hyprctl reload",
    "watch -n 1 hyprctl -j clients",
    "watch 'hyprctl -j clients | jq length'",
    # review #151, 8: a command line handed to a shell
    "bash -c 'hyprctl reload'",
    "timeout 60 sh -c 'Xvfb :1'",
    "bash -lc 'cd /tmp && hyprctl -j clients'",
    "bash -c 'ls\nhyprctl reload'",
    "bash <<'EOF'\nls\nhyprctl reload\nEOF",
    "eval 'hyprctl reload'",
    # review #151, 9: the other X servers, and xdotool on the session's display
    "Xephyr :1",
    "Xnest :1",
    "Xvnc :1",
    "X :1",
    "startx",
    "xdotool key ctrl+c",
    "DISPLAY=:0 xdotool type hi",
    "DISPLAY=:1 xdotool type hi",
    "DISPLAY=$D xdotool type hi",
    # review #151, 10: python code that runs a fenced command, or loads GTK on stdin
    "python3 -c \"import subprocess; subprocess.run(['hyprctl','reload'])\"",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['hyprctl', 'reload'])\nEOF",
    "python3 -c 'import os; os.system(\"Xvfb :1 &\")'",
    "python3 -c \"import os; os.unlink('/tmp/.X11-unix/X0')\"",
    "python3 <<< 'from gi.repository import Gtk'",
    "echo 'from gi.repository import Gtk' | python3 -",
    # review #151, 22: the module attached to its flag
    ".venv/bin/python -mhyprtweaker",
    "python3 -c'from gi.repository import Gtk'",
    # review #151, 23: what the narrower X-file checks still refuse
    "find /tmp/.X11-unix -exec rm {} +",
    "cd /tmp && rm .X0-lock",
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
    # what the new refusals leave: a recorded pid, the probe routes, the checks, data
    "kill 1234",
    "kill $!",
    "kill -TERM $pid",
    "pgrep -af Xvfb",
    "ps aux | grep -E 'Xvfb|Xwayland'",
    ".venv/bin/python tools/widget_probe.py probe.py --flag",
    "env FOO=1 .venv/bin/python tools/widget_probe.py probe.py",
    ".venv/bin/python tools/sandbox.py --config ~/.config/hypr/hyprland.lua",
    ".venv/bin/pytest tests/ui",
    ".venv/bin/python -m pytest -q tests/ui",
    "grep -rn Gtk src/",
    "grep -rn 'from gi.repository import Gtk' src/hyprtweaker/",
    "grep -rn foo src/hyprtweaker/",
    ".venv/bin/mypy",
    ".venv/bin/python -m mypy src/hyprtweaker",
    ".venv/bin/ruff check src/hyprtweaker",
    ".venv/bin/python -c 'print(1 + 1)'",
    "python3 - <<'EOF'\nprint(1)\nEOF",
    "python3 -c 'import json; print(json.dumps({}))'",
    "git commit -m 'Never pkill -f or xvfb-run; rm /tmp/.X11-unix/X0 is refused'",
    "echo Xvfb Xorg Xwayland >> notes.md",
    "which Xvfb xvfb-run",
    # the session's X files may be read, and other files removed, moved and linked
    "ls -la /tmp/.X11-unix/",
    "ss -xlp | grep X11",
    "cat /tmp/.X0-lock",
    "rm -f /tmp/scratch.txt",
    "rm -rf build/",
    "mv notes.md /tmp/notes.md",
    "ln -s /tmp/a /tmp/b",
    "find src -name '*.py'",
    "cd /tmp && ls .X11-unix",
    # review #151, 4: a recorded pid, a signal by number, a listing
    'kill "$(cat /tmp/x.pid)"',
    "kill -1 1234",
    "kill -9 1234 5678",
    "kill -l",
    "kill %1",
    "pgrep -f Xvfb || echo none",
    "pgrep -f foot | wc -l",
    "pids=$(pgrep -f Xvfb); echo $pids",
    "pids=$(pgrep -f Xvfb); kill -0 $pids && echo alive",
    "kill -s 0 $(pidof foot)",
    "pgrep -af Xvfb; kill 1234",
    "fuser /tmp/.X11-unix/X0",
    # review #151, 5: the variable as data, cleared, or empty
    "grep -rn HYPRTWEAKER_UI_HOST_DISPLAY tests/ docs/",
    "unset HYPRTWEAKER_UI_HOST_DISPLAY",
    "HYPRTWEAKER_UI_HOST_DISPLAY= .venv/bin/pytest tests/ui",
    "echo 'HYPRTWEAKER_UI_HOST_DISPLAY=1 is for the owner'",
    # review #151, 7: wrappers around commands that cannot reach the desktop
    "timeout -s KILL 60 .venv/bin/pytest -q tests/unit",
    "timeout 60 grep -rn hyprctl docs/",
    "sudo -u diggle ls /root",
    "xargs -a files.txt grep hyprctl",
    "flock /tmp/x.lock .venv/bin/pytest -q",
    "taskset -c 0 .venv/bin/pytest -q",
    "watch -n 1 'grep -c hyprctl docs/agents/local-checks.md'",
    # review #151, 8: a shell whose command line cannot reach the desktop
    "bash -c 'echo hyprctl'",
    "sh -c 'ls /tmp/.X11-unix'",
    "bash -c 'cd /tmp && ls'",
    "bash <<'EOF'\necho hyprctl reload\nEOF",
    "bash tools/some_script.sh",
    "eval 'echo hyprctl'",
    # review #151, 9: xdotool on a private display, the words as data
    "DISPLAY=:250 xdotool type hi",
    "env DISPLAY=:201.0 xdotool key Return",
    "which Xephyr xdotool",
    # review #151, 10: python that only names GTK, or runs nothing fenced
    "python3 - <<'EOF'\nprint(1)\nEOF\ngit commit -m \"Binds: Gtk row\"",
    "python3 -c \"print('Gtk')\"",
    "python3 - <<'EOF'\nimport ast, pathlib\n"
    "for p in pathlib.Path('src').rglob('*.py'):\n"
    "    tree = ast.parse(p.read_text())\n"
    "    print(p, sum(getattr(n.value, 'id', '') == 'Gtk' for n in ast.walk(tree)"
    " if isinstance(n, ast.Attribute)))\nEOF",
    "python3 -c \"import subprocess; subprocess.run(['git', 'log'])\"",
    "python3 -c \"import subprocess; subprocess.run(['grep', '-c', 'x', 'hyprland.lua'])\"",
    "python3 -c \"import os; print(os.listdir('/tmp/.X11-unix'))\"",
    # review #151, 22: another module attached to its flag
    ".venv/bin/python -mpytest -q tests/unit",
    # review #151, 23: an X lock name outside /tmp, and a find that only reads
    "rm /tmp/pytest-of-diggle/pytest-1/test_lock0/.X200-lock",
    "find /tmp/.X11-unix -exec ls -l {} +",
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
        ("pkill -f foo", "`pkill`", "kill <pid>"),
        ("killall Hyprland", "`killall`", "kill <pid>"),
        (
            ".venv/bin/python -c 'from gi.repository import Gtk'",
            "`python -c`",
            "tools/widget_probe.py <probe.py>",
        ),
        ("xvfb-run python x.py", "`xvfb-run`", "tools/widget_probe.py <probe.py>"),
        ("python -m hyprtweaker", "`python -m hyprtweaker`", "tools/sandbox.py"),
        ("Xvfb :99", "`Xvfb`", "start_xvfb"),
        ("Xwayland :5", "`Xwayland`", "start_xvfb"),
        ("rm /tmp/.X11-unix/X0", "`rm`", "ls /tmp/.X11-unix"),
        ("ln /tmp/.X11-unix/X0_ /tmp/.X11-unix/X0", "`ln`", "ls /tmp/.X11-unix"),
        ("kill $(pgrep -f Xvfb)", "`kill`", "kill <pid>"),
        ("pgrep -f foot | xargs kill", "`pgrep`", "kill <pid>"),
        ("kill -9 -1", "process group", "kill <pid>"),
        ("fuser -k /tmp/.X11-unix/X0", "`fuser -k`", "kill <pid>"),
        (
            "HYPRTWEAKER_UI_HOST_DISPLAY=1 .venv/bin/pytest tests/ui",
            "owner's opt-in",
            ".venv/bin/pytest tests/ui",
        ),
        ("Xephyr :1", "`Xephyr`", "start_xvfb"),
        ("DISPLAY=:0 xdotool type hi", "`xdotool`", "DISPLAY=:<200-999>"),
        ("bash -c 'hyprctl reload'", "`hyprctl reload`", "hyprctl --instance <signature"),
        (
            "python3 -c \"import subprocess; subprocess.run(['hyprctl','reload'])\"",
            "`hyprctl`",
            "hyprctl --instance <signature",
        ),
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


def test_a_command_past_the_argument_limit_is_judged_not_refused_unread(world: World) -> None:
    # 140 KB is past the 128 KiB that one environment string may hold (review #151, 21).
    filler = "a plain line of notes, nothing fenced in it\n" * 3300
    assert len(filler) > 140_000
    tool("jq")
    assert verdict(FENCE, f"cat > notes.md <<'EOF'\n{filler}EOF", world.env) is None
    assert verdict(FENCE, f"cat > notes.md <<'EOF'\n{filler}EOF\nhyprctl reload", world.env)


@pytest.fixture
def no_jq_env(world: World, tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("cat", "grep"):
        (bin_dir / name).symlink_to(tool(name))
    return {**world.env, "PATH": str(bin_dir)}


def test_without_jq_the_fence_fails_closed_on_the_four_words(no_jq_env: dict[str, str]) -> None:
    reason = verdict(FENCE, "grep hyprctl docs/", no_jq_env)
    assert reason is not None and "install jq" in reason
    assert verdict(FENCE, "ls docs/", no_jq_env) is None


@pytest.mark.parametrize(
    "command",
    [
        "pkill -f foo",
        "killall Hyprland",
        "xvfb-run python x.py",
        '.venv/bin/python -c "from gi.repository import Gtk"',
        '.venv/bin/python3 -c \'import gi; gi.require_version("Adw", "1")\'',
        "python -m hyprtweaker",
        "python3 - <<'EOF'\nfrom gi.repository import Gtk\nEOF",
        "Xvfb :99",
        "timeout 5 Xorg :1",
        "rm /tmp/.X11-unix/X0",
        "ln /tmp/.X11-unix/X0_ /tmp/.X11-unix/X0",
        "rm -f /tmp/.X0-lock",
        "kill $(pgrep -f Xvfb)",
        "pgrep -f foot | xargs kill",
        "kill -9 -1",
        "fuser -k /tmp/.X11-unix/X0",
        "HYPRTWEAKER_UI_HOST_DISPLAY=1 .venv/bin/pytest tests/ui",
        "Xephyr :1",
        "X :1",
        "xdotool key ctrl+c",
        ".venv/bin/python -mhyprtweaker",
    ],
)
def test_without_jq_the_fence_refuses_the_new_words(
    no_jq_env: dict[str, str], command: str
) -> None:
    reason = verdict(FENCE, command, no_jq_env)
    assert reason is not None and "install jq" in reason


@pytest.mark.parametrize(
    "command",
    [
        "kill 1234",
        "kill $!",
        "grep -rn Gtk src/",
        "grep -rn foo src/hyprtweaker/",
        ".venv/bin/python tools/widget_probe.py probe.py",
        ".venv/bin/python tools/sandbox.py --config x",
        ".venv/bin/pytest tests/ui",
        ".venv/bin/mypy",
        ".venv/bin/ruff check src/hyprtweaker",
        "ls /tmp/.X11-unix/",
        "rm -f /tmp/scratch.txt",
        "kill -9 1234",
        "kill -1 1234",
        "pgrep -af foot",
        "HYPRTWEAKER_UI_HOST_DISPLAY= .venv/bin/pytest tests/ui",
    ],
)
def test_without_jq_the_fence_still_lets_the_ordinary_checks_through(
    no_jq_env: dict[str, str], command: str
) -> None:
    assert verdict(FENCE, command, no_jq_env) is None


def hooks_for(tool_name: str) -> list[str]:
    """The PreToolUse hook commands Claude Code runs for `tool_name`: a matcher is a regex."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    return [
        hook["command"]
        for entry in settings["hooks"]["PreToolUse"]
        if re.fullmatch(entry["matcher"], tool_name)
        for hook in entry["hooks"]
    ]


def test_settings_register_both_hooks_on_bash_and_the_base_guard_still_refuses_a_merge(
    world: World,
) -> None:
    tool("jq")
    commands = hooks_for("Bash")
    assert any(command.endswith("/.claude/hooks/pr-base-guard.sh") for command in commands)
    assert any(command.endswith("/.claude/hooks/desktop-fence.sh") for command in commands)
    assert verdict(BASE_GUARD, "gh pr merge 5", world.env) is not None


def test_the_fence_also_judges_a_monitor_command(world: World) -> None:
    # The Monitor tool runs a shell command too; agents arm one while CI runs (review #151, 6).
    assert any(
        command.endswith("/.claude/hooks/desktop-fence.sh") for command in hooks_for("Monitor")
    )
    assert not any(
        command.endswith("/.claude/hooks/desktop-fence.sh") for command in hooks_for("Read")
    )
    tool("jq")
    loop = "while true; do hyprctl -j clients; sleep 5; done"
    assert verdict(FENCE, loop, world.env, tool_name="Monitor") is not None
    assert (
        verdict(FENCE, "gh run watch 5 --exit-status", world.env, tool_name="Monitor") is None
    )
