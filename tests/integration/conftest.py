"""Fixtures and the skip decision for the Harness tier (ADR-0011 tier 3).

The tier is deliberately outside `testpaths`, so a plain `pytest` never collects it. Running
it is an explicit act::

    pytest tests/integration              # the whole tier
    pytest tests/integration -m hyprland  # only the tests that need a compositor

The corpus fixture checks carry no marker on purpose: they are file-tree and metadata
assertions that need no compositor, so `pytest tests/integration` still does useful work on a
machine where every compositor test skips.

**Skipping is centralised here** rather than left to a `skipif` per module. A `skipif`
evaluates its condition at import time, which for this tier means every module would
re-answer "is there a compositor" in a slightly different way and drift apart; worse, an
incomplete condition turns a missing dependency into a hard error deep inside a fixture,
which reads as a broken test rather than an unsuitable machine. One reason, computed once,
attached to every marked item.

`HYPRTWEAKER_REQUIRE_HARNESS=1` turns the skip into a failure -- the same escape hatch
`tests/ui/conftest.py` gives the UI tier. A nightly job that is *supposed* to have a
compositor should not go green by quietly skipping everything.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TESTS_INTEGRATION = Path(__file__).resolve().parent
ROOT = TESTS_INTEGRATION.parents[1]
for entry in (str(TESTS_INTEGRATION), str(ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from harness import unavailable_reason  # noqa: E402

REQUIRE_VARIABLE = "HYPRTWEAKER_REQUIRE_HARNESS"


# trylast, and scoped by path, for the reasons tests/ui/conftest.py documents: pytest hands
# this hook every collected item in the run, not just this directory's, and it applies
# -k/-m deselection in its own copy of the hook -- so running last means `items` is the final
# selection and a `-k` that picked no Harness test cannot trip the gate.
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip every `hyprland`-marked item at once when the machine cannot run them."""
    marked = [
        item
        for item in items
        if item.path is not None
        and TESTS_INTEGRATION in item.path.parents
        and "hyprland" in item.keywords
    ]
    if not marked:
        return

    reason = unavailable_reason()
    if reason is None:
        return

    if os.environ.get(REQUIRE_VARIABLE) == "1":
        # pytest.exit rather than raise: a bare exception here surfaces as an INTERNALERROR
        # traceback, which buries the one line explaining why.
        pytest.exit(
            f"{REQUIRE_VARIABLE}=1 but the Harness tier cannot run: {reason}", returncode=1
        )

    skip = pytest.mark.skip(reason=f"Harness tier: {reason}")
    for item in marked:
        item.add_marker(skip)


@pytest.fixture
def harness_home(tmp_path: Path) -> Path:
    """A pristine `$HOME` for one nested compositor.

    One per test, never shared: Hyprland consumes first-launch state on its first run, and
    `hl.env`, `hl.permission` and the donate screen all behave differently afterwards
    (prototype #9 §4.6). Two compositors sharing a home are not comparable.
    """
    from harness import make_home

    return make_home(tmp_path / "home")


@pytest.fixture
def artifacts(tmp_path: Path) -> Path:
    """Where a test drops logs, screenshots and state dumps for post-mortem reading."""
    directory = tmp_path / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
