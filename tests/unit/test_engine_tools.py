"""`engine/tools.py` finds and runs another program only from the tool search path (#233).

Every test here runs inside the suite's fence (`tests/conftest.py`), where the tool search
path is an empty directory of the test's own. A stub a test writes there stands in for a
theming tool or a wallpaper daemon; nothing here runs a real one.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from hyprtweaker.engine.tools import (
    TOOL_PATH_ENV,
    ToolRefused,
    ToolRun,
    ToolTimedOut,
    find_tool,
    run_tool,
    tool_search_path,
)


def counting_script(path: Path, counter: Path, *, output: str = "", code: int = 0) -> Path:
    """An executable that appends one line to `counter` each time it runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!/bin/sh\necho ran >> '{counter}'\nprintf '{output}'\nprintf 'warned' >&2\n"
        f"exit {code}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_the_tool_path_wins_over_path_when_it_is_set() -> None:
    environ = {"PATH": "/usr/bin:/bin", TOOL_PATH_ENV: "/opt/tools:/srv/more"}

    assert tool_search_path(environ) == "/opt/tools:/srv/more"
    assert tool_search_path({"PATH": "/usr/bin:/bin"}) == "/usr/bin:/bin"


def test_a_set_but_empty_tool_path_finds_nothing_rather_than_falling_back() -> None:
    environ = {"PATH": "/usr/bin:/bin", TOOL_PATH_ENV: ""}

    assert tool_search_path(environ) == ""
    assert find_tool("sh", environ) is None


def test_relative_and_empty_entries_never_reach_the_current_directory(tmp_path: Path) -> None:
    """`shutil.which` reads an empty or relative entry against the working directory."""
    environ = {TOOL_PATH_ENV: f"::.:bin:{tmp_path}"}

    assert tool_search_path(environ) == str(tmp_path)


def test_a_tool_on_the_search_path_is_found_by_name(tmp_path: Path) -> None:
    stub = counting_script(tmp_path / "tools" / "matugen", tmp_path / "count")
    environ = {"PATH": "/usr/bin:/bin", TOOL_PATH_ENV: str(stub.parent)}

    assert find_tool("matugen", environ) == stub
    assert find_tool("wallust", environ) is None


def test_a_path_is_not_a_tool_name() -> None:
    """`shutil.which` answers a path itself, whatever the search path says."""
    with pytest.raises(ValueError, match="a program's name"):
        find_tool("/bin/sh")


def test_a_stub_on_the_tool_path_runs_once_and_its_output_is_captured(
    tmp_path: Path, stub_tool: Callable[[str, str], Path]
) -> None:
    counter = tmp_path / "count"
    stub = stub_tool(
        "matugen", f"echo ran >> '{counter}'\nprintf 'generated'\nprintf 'warned' >&2\nexit 3"
    )

    found = find_tool("matugen")
    run = run_tool([str(found), "image", "wall.png"], timeout=5)

    assert found == stub
    assert counter.read_text(encoding="utf-8") == "ran\n"
    assert run == ToolRun(
        argv=(str(stub), "image", "wall.png"), returncode=3, stdout="generated", stderr="warned"
    )


def test_a_program_outside_the_tool_path_is_refused_before_it_starts(tmp_path: Path) -> None:
    counter = tmp_path / "count"
    elsewhere = counting_script(tmp_path / "elsewhere" / "matugen", counter)

    with pytest.raises(ToolRefused) as refused:
        run_tool([str(elsewhere)], timeout=5)

    assert not counter.exists()
    assert str(elsewhere) in str(refused.value)
    assert "find_tool" in str(refused.value)


@pytest.mark.parametrize("argv0", ["matugen", "tools/matugen", "../tools/matugen"])
def test_a_bare_or_relative_program_is_refused(argv0: str) -> None:
    """A bare name would be looked up on `PATH`, the very lookup the seam exists to replace."""
    with pytest.raises(ToolRefused):
        run_tool([argv0], timeout=5)


def test_a_path_that_climbs_out_of_the_tool_path_is_refused(tmp_path: Path) -> None:
    counter = tmp_path / "count"
    outside = counting_script(tmp_path / "outside" / "matugen", counter)
    tools = Path(os.environ[TOOL_PATH_ENV])

    with pytest.raises(ToolRefused):
        run_tool([f"{tools}/../../outside/matugen"], timeout=5)

    assert outside.exists() and not counter.exists()


def test_an_empty_argv_is_refused() -> None:
    with pytest.raises(ToolRefused):
        run_tool([], timeout=5)


def test_the_environment_reaches_the_tool_unchanged(
    stub_tool: Callable[[str, str], Path],
) -> None:
    stub = stub_tool("wallust", 'printf "%s|%s" "$HYPRLAND_INSTANCE_SIGNATURE" "$WALLPAPER"')
    environ = {
        **os.environ,
        "HYPRLAND_INSTANCE_SIGNATURE": "sig-1",
        "WALLPAPER": "/w.png",
    }

    run = run_tool([str(stub)], timeout=5, environ=environ)

    assert run.stdout == "sig-1|/w.png"


def test_a_tool_that_overruns_its_timeout_is_stopped_and_named(
    stub_tool: Callable[[str, str], Path],
) -> None:
    stub = stub_tool("swww", "sleep 30")

    with pytest.raises(ToolTimedOut, match="swww") as timed_out:
        run_tool([str(stub), "img"], timeout=0.2)

    assert timed_out.value.argv == (str(stub), "img")


def _alive(pid: int) -> bool:
    """Whether `pid` runs: a zombie, or one gone between two reads, does not."""
    try:
        return "zombie" not in Path(f"/proc/{pid}/status").read_text().lower()
    except OSError:  # gone (ENOENT), or going (ESRCH) as it is read
        return False


def test_a_hook_left_running_in_the_background_does_not_hold_the_run(
    stub_tool: Callable[[str, str], Path], tmp_path: Path
) -> None:
    """Finding 5 of the #153 review: a `post_hook = "waybar &"` kept the output pipe open,
    so a run that had finished reported "did not finish" at the timeout."""
    import time

    pid_file = tmp_path / "background.pid"
    stub = stub_tool("matugen", f"sleep 20 &\necho $! > '{pid_file}'\necho done")
    started = time.monotonic()
    try:
        ran = run_tool([str(stub)], timeout=5)
        assert time.monotonic() - started < 2
        assert (ran.returncode, ran.stdout) == (0, "done\n")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 9)


def test_a_timeout_stops_everything_the_tool_started(
    stub_tool: Callable[[str, str], Path], tmp_path: Path
) -> None:
    import time

    pid_file = tmp_path / "background.pid"
    stub = stub_tool("wallust", f"sleep 20 &\necho $! > '{pid_file}'\nsleep 30")

    with pytest.raises(ToolTimedOut):
        run_tool([str(stub)], timeout=0.5)

    background = int(pid_file.read_text())
    deadline = time.monotonic() + 10  # a loaded machine reaps the orphan late
    while _alive(background) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _alive(background)


def test_only_the_end_of_a_long_output_is_kept(stub_tool: Callable[[str, str], Path]) -> None:
    from hyprtweaker.engine.tools import OUTPUT_LIMIT

    stub = stub_tool("matugen", "head -c 200000 /dev/zero | tr '\\0' a\necho; echo last line")

    ran = run_tool([str(stub)], timeout=5)

    assert len(ran.stdout.encode()) == OUTPUT_LIMIT
    assert ran.stdout.endswith("\nlast line\n")
