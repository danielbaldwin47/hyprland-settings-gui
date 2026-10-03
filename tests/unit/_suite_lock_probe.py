"""Run only by `test_suite_lock.py`, in child pytest runs that share a lock of their own.

Not named `test_*.py`, so the suite never collects it; a child run names it explicitly.
Each test records when it starts and ends, and holds its run open until the parent creates
the release file, so the parent decides how long a child holds the lock.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


def _record(event: str) -> None:
    with Path(os.environ["SUITE_LOCK_PROBE_RECORD"]).open("a") as record:
        record.write(f"{event} {time.time()}\n")


@pytest.mark.parametrize("case", ["one", "two"])
def test_holds_the_run_until_released(case: str) -> None:
    _record("start")
    release = Path(os.environ["SUITE_LOCK_PROBE_RELEASE"])
    deadline = time.monotonic() + 60
    while not release.exists():
        assert time.monotonic() < deadline, f"{release} never appeared"
        time.sleep(0.05)
    _record("end")
