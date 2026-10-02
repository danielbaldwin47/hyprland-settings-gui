"""The suite lock (`conftest.py` at the repo root): one pytest run at a time on a machine.

Each test starts child pytest controllers on `_suite_lock_probe.py`, pointed at a lock file
of their own: this run already holds the machine's lock, so children on that one would wait
on their own parent.
"""

from __future__ import annotations

import fcntl
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = "tests/unit/_suite_lock_probe.py"
PROBE_RUN = [
    sys.executable,
    "-m",
    "pytest",
    PROBE,
    "-q",
    "-p",
    "no:cacheprovider",
    "--color=no",
]
WAIT_SECONDS = 60.0


class Run:
    """A child pytest controller on the probe, its output read line by line as it comes."""

    def __init__(
        self, tmp_path: Path, name: str, *args: str, extra: dict[str, str] | None = None
    ) -> None:
        self.record = tmp_path / f"{name}.record"
        env = {
            key: value
            for key, value in os.environ.items()
            # A GitHub runner skips the lock; ADDOPTS could add -n.
            if key not in ("GITHUB_ACTIONS", "PYTEST_ADDOPTS")
        }
        env.update(extra or {})
        env["HYPRTWEAKER_SUITE_LOCK"] = str(tmp_path / "suite.lock")
        env["SUITE_LOCK_PROBE_RECORD"] = str(self.record)
        env["SUITE_LOCK_PROBE_RELEASE"] = str(tmp_path / "release")
        self.process = subprocess.Popen(
            [*PROBE_RUN, *args],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()  # None: the output has ended
        self.output: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\n"))
        self.lines.put(None)

    def _next_line(self, deadline: float) -> str | None:
        line = self.lines.get(timeout=max(deadline - time.monotonic(), 0.01))
        if line is not None:
            self.output.append(line)
        return line

    def wait_for_line(self, fragment: str) -> str:
        deadline = time.monotonic() + WAIT_SECONDS
        try:
            while (line := self._next_line(deadline)) is not None:
                if fragment in line:
                    return line
        except queue.Empty:
            pass
        raise AssertionError(f"no line with {fragment!r} in:\n" + "\n".join(self.output))

    def wait_for_records(self, event: str, count: int) -> None:
        deadline = time.monotonic() + WAIT_SECONDS
        while len(self.times(event)) < count:
            assert self.process.poll() is None, self.finish()
            assert time.monotonic() < deadline, f"{count} {event!r} records never appeared"
            time.sleep(0.05)

    def times(self, event: str) -> list[float]:
        if not self.record.exists():
            return []
        words = (line.split() for line in self.record.read_text().splitlines())
        return [float(w[1]) for w in words if len(w) == 2 and w[0] == event]  # w: a whole line

    def finish(self) -> str:
        deadline = time.monotonic() + WAIT_SECONDS
        while self._next_line(deadline) is not None:
            pass
        self.process.wait(timeout=WAIT_SECONDS)
        return "\n".join(self.output)

    def kill(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()


def test_a_second_run_waits_for_the_first_and_says_who_holds_the_lock(tmp_path: Path) -> None:
    first = Run(tmp_path, "first")
    second = None
    try:
        first.wait_for_records("start", 1)
        second = Run(tmp_path, "second")
        waiting = second.wait_for_line("waiting for")
        (tmp_path / "release").touch()
        first_output, second_output = first.finish(), second.finish()
    finally:
        first.kill()
        if second is not None:
            second.kill()

    lock = tmp_path / "suite.lock"
    assert waiting == f"pytest: waiting for {lock}, held by PID {first.process.pid} ({ROOT})"
    assert "waiting for" not in first_output
    assert first.process.returncode == 0, first_output
    assert second.process.returncode == 0, second_output
    assert "2 passed" in second_output
    assert max(first.times("end")) < min(second.times("start"))


def test_a_parallel_run_holds_the_lock_while_its_own_workers_run(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    run = Run(tmp_path, "parallel", "-n", "2")
    try:
        run.wait_for_records("start", 2)  # both workers are inside a test
        with (tmp_path / "suite.lock").open("a") as lock, pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (tmp_path / "release").touch()
        output = run.finish()
    finally:
        run.kill()

    assert run.process.returncode == 0, output
    assert "2 passed" in output
    assert "waiting for" not in output


def test_ci_set_in_a_shell_still_takes_the_lock(tmp_path: Path) -> None:
    """Ruling A13 of the #148 review: `CI=1 pytest` from an agent's shell ran unlocked."""
    first = Run(tmp_path, "first", extra={"CI": "1"})
    second = None
    try:
        first.wait_for_records("start", 1)
        second = Run(tmp_path, "second", extra={"CI": "1"})
        waiting = second.wait_for_line("waiting for")
        (tmp_path / "release").touch()
        first.finish(), second.finish()
    finally:
        first.kill()
        if second is not None:
            second.kill()

    assert waiting.startswith(f"pytest: waiting for {tmp_path / 'suite.lock'}, held by PID")


def test_an_explicit_worker_count_above_the_cap_is_lowered_to_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import importlib.util
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location("root_conftest", ROOT / "conftest.py")
    assert spec is not None and spec.loader is not None
    root_conftest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(root_conftest)
    config = SimpleNamespace(option=SimpleNamespace(numprocesses=20, maxprocesses=None))

    root_conftest.pytest_cmdline_main(config)

    assert config.option.maxprocesses == 8
    assert capsys.readouterr().err == (
        "pytest: -n 20 lowered to 8 workers (MAX_WORKERS, conftest.py)\n"
    )
