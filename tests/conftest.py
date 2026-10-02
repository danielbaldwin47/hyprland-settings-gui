"""The fence every tier under `tests/` runs inside: no owner's home, compositor or tools (#233).

`hermetic.py` says what the fence is and why. Two layers apply it:

- `pytest_configure`, first in every pytest process (xdist workers included): `HOME`, the
  four `XDG_*` homes and the tool search path point into a directory of the process's own,
  and refusing stand-ins for every theming tool and wallpaper daemon go first on `PATH`.
  That covers collection, wider-scoped fixtures, and GTK, which reads its user
  directories once, when the UI tier opens its display.
- `hermetic_home`, autouse: the same variables under the test's `tmp_path`, and no
  `HYPRLAND_INSTANCE_SIGNATURE`. A test that ran a stand-in fails at teardown.

`XDG_RUNTIME_DIR` is left alone: the suite lock (root `conftest.py`) and the nested Harness
live there. There is no opt-out: a test that needs another home sets it itself.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from hermetic import (
    FENCE_LOG_ENV,
    TOOL_PATH_ENV,
    install_refusals,
    make_fence,
    refusals,
    write_stub,
)

_PROCESS_FENCE = pytest.StashKey[Path]()


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    root = Path(tempfile.mkdtemp(prefix="hyprtweaker-tests-fence-"))
    config.stash[_PROCESS_FENCE] = root
    os.environ.update(make_fence(root))
    install_refusals(root / "refuse")
    os.environ["PATH"] = os.pathsep.join([str(root / "refuse"), os.environ.get("PATH", "")])


def pytest_unconfigure(config: pytest.Config) -> None:
    root = config.stash.get(_PROCESS_FENCE, None)
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def hermetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point home, the XDG homes and the tool search path under `tmp_path`; yield the root.

    `Path.home()` is `<tmp_path>/fence/home` and `ConfigPaths.default()` lives under it; the
    tool search path, `<tmp_path>/fence/tools`, is empty, so `find_tool` finds nothing until
    the test writes a stub there (`stub_tool`). The signature of the compositor the suite
    was started under is dropped: a `Session` built without `connect` would otherwise ask
    `Instance.current()` and reach the developer's desktop.
    """
    root = tmp_path / "fence"
    environment = make_fence(root)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    yield root
    if problem := refusals(Path(environment[FENCE_LOG_ENV])):
        pytest.fail(problem)


@pytest.fixture
def stub_tool(hermetic_home: Path) -> Callable[..., Path]:
    """Write `name` onto this test's tool search path, as a `#!/bin/sh` running `script`.

    `find_tool(name)` then finds it, and `run_tool` runs it: a stand-in for a theming tool
    or wallpaper daemon whose effects the test reads back, for example from a file the
    script writes.
    """

    def write(name: str, script: str = "exit 0") -> Path:
        return write_stub(Path(os.environ[TOOL_PATH_ENV]), name, script)

    return write
