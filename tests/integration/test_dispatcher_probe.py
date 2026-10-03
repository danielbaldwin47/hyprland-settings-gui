"""The dispatcher probe, run for real, and the catalog held against it (#126, ADR-0007).

`harness/dispatcher_probe.py` asks a nested compositor what each `free_form` dispatcher
takes; this file is its one command, the one a release check re-runs (#127):

    HARNESS_DRM_CARD=/dev/dri/card0 pytest tests/integration/test_dispatcher_probe.py \\
        -m hyprland

It needs `foot` for the effect probes (a window to fire at) and skips nothing without it: the
record then says so in `effects_skipped` and the golden comparison fails, which is the
honest outcome on a machine that cannot reproduce the record.

Two claims, both against the compositor and not against anything this app wrote:

- the record a fresh probe produces is the committed one
  (`tests/golden/dispatcher-probe-<version>.json`; `UPDATE_GOLDEN=1` rewrites it, and the diff
  is the release check's evidence of what a new Hyprland changed);
- every curated catalog entry names the keys the compositor reads and the calls the editor
  would write for it load.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from harness import REQUIRE_VARIABLE, HarnessUnavailable, NestedHyprland
from harness.dispatcher_probe import (
    ProbeRecord,
    Verdict,
    probe_dispatchers,
    probe_unseen,
    verify_catalog,
)

pytestmark = pytest.mark.hyprland

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
CONFIG = "hl.config({ general = { gaps_in = 3 } })\n"


@dataclass(frozen=True)
class Probed:
    nested: NestedHyprland
    record: ProbeRecord
    unseen: list[Verdict]
    unseen_skipped: str


@pytest.fixture(scope="module")
def probed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Probed]:
    """One nested compositor for the module, probed once.

    `HarnessUnavailable` is handled here because `pytest_runtest_call` does not see fixture
    setup (the same reason `guarded_hyprland` handles it).
    """
    from harness import make_home

    home = make_home(tmp_path_factory.mktemp("probe") / "home")
    config = home / "hyprland.lua"
    config.write_text(CONFIG)
    nested = NestedHyprland(config, home=home, log=home / "nested.log")
    try:
        nested.start()
    except HarnessUnavailable as unavailable:
        if os.environ.get(REQUIRE_VARIABLE) == "1":
            raise
        pytest.skip(f"Harness tier: {unavailable}")
    try:
        record = probe_dispatchers(nested)
        unseen, unseen_skipped = probe_unseen(nested)
        yield Probed(nested, record, unseen, unseen_skipped)
    finally:
        nested.stop()


def test_the_probe_record_is_the_committed_one(probed: Probed) -> None:
    text = json.dumps(probed.record.to_json(), indent=2, sort_keys=True) + "\n"
    golden = GOLDEN_DIR / f"dispatcher-probe-{probed.record.hyprland_version}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        golden.write_text(text, encoding="utf-8")
        pytest.skip(f"regenerated {golden.name}")
    assert golden.is_file(), f"no record for {probed.record.hyprland_version}: UPDATE_GOLDEN=1"
    assert text == golden.read_text(encoding="utf-8"), (
        f"the compositor no longer answers as {golden.name} says. If this Hyprland changed a "
        "dispatcher on purpose, regenerate with UPDATE_GOLDEN=1, read the diff and curate "
        "src/hyprtweaker/engine/dispatchers.py to match."
    )


def test_every_effect_probe_ran(probed: Probed) -> None:
    assert probed.record.effects_skipped == ""
    assert [e.key for e in probed.record.effects if not e.confirmed] == [], (
        "an effect probe could not confirm its key; the record would drop it silently"
    )


def test_the_unseen_keys_still_do_nothing(probed: Probed) -> None:
    """The keys the catalog leaves out because no effect could be read back (#211).

    Each was fired at grouped and fullscreen windows and changed nothing. When one starts to
    act, this fails: curate it with an `ArgSpec` and let the next record hold its effect.
    """
    assert probed.unseen_skipped == ""
    assert [(v.path, v.key) for v in probed.unseen if v.acted] == [], (
        "a key the catalog leaves out now acts; give it a row in engine/dispatchers.py and "
        "an effect probe in harness/dispatcher_probe.py"
    )
    assert [(v.path, v.key) for v in probed.unseen] == [
        ("group.lock_active", "window"),
        ("window.deny_from_group", "window"),
        ("group.lock", "window"),
        ("group.move_window", "window"),
        ("window.fullscreen", "layout_aware"),
        ("window.fullscreen_state", "layout_aware"),
    ]


def test_the_catalog_agrees_with_the_compositor(probed: Probed) -> None:
    assert verify_catalog(probed.nested, probed.record) == []
