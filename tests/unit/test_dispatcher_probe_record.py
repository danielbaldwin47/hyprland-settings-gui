"""The dispatcher catalog against the committed probe record (#126, ADR-0007).

The newest `tests/golden/dispatcher-probe-<version>.json` is what a nested Hyprland answered
when `harness/dispatcher_probe.py` asked it about every dispatcher (the integration tier
regenerates it and fails if it drifts). This is the half that needs no compositor and so runs
on every commit: each catalog entry must name exactly the keys the compositor read, mark the
ones it required, and take the call form it accepted.

The reply strings below are captured from 0.56.2, not invented.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from harness.dispatcher_probe import (
    PLANS,
    ProbeRecord,
    check_catalog,
    check_entry,
    clean,
    generated_calls,
    hint_keys,
    required_keys,
)

from hyprtweaker.engine.dispatchers import BY_PATH, ArgSpec, Dispatcher
from hyprtweaker.engine.schema.resolve import version_key


def newest_record(golden: Path) -> Path:
    """The newest Hyprland's record in `golden`, by release order rather than by name."""
    return max(
        golden.glob("dispatcher-probe-*.json"),
        key=lambda path: version_key(path.stem.removeprefix("dispatcher-probe-")),
    )


RECORD = newest_record(Path(__file__).resolve().parents[1] / "golden")
"""The catalog describes the newest release the app ships for, so a release check that
commits a new record is checked against it with no edit here."""


def test_the_newest_record_is_chosen_by_release_not_by_name(tmp_path: Path) -> None:
    for version in ("0.56.2", "0.100.0", "0.57.0"):
        (tmp_path / f"dispatcher-probe-{version}.json").write_text("{}")

    assert newest_record(tmp_path).name == "dispatcher-probe-0.100.0.json"


@pytest.fixture(scope="module")
def record() -> ProbeRecord:
    return ProbeRecord.from_json(json.loads(RECORD.read_text(encoding="utf-8")))


def test_the_catalog_matches_what_the_compositor_read(record: ProbeRecord) -> None:
    assert check_catalog(record) == []


def test_every_catalog_path_has_a_probe_plan_and_a_record(record: ProbeRecord) -> None:
    assert set(PLANS) == set(BY_PATH)
    assert set(record.dispatchers) == set(BY_PATH)


def test_every_effect_in_the_record_was_confirmed(record: ProbeRecord) -> None:
    assert [e.key for e in record.effects if not e.confirmed] == []
    assert record.effects_skipped == ""


class TestTheCheckCatchesTheDefectsItIsFor:
    """A check that cannot fail tests nothing: break an entry, and it must say so."""

    def test_an_entry_that_drops_a_key_the_compositor_reads(self, record: ProbeRecord) -> None:
        dropped = Dispatcher(
            path="send_shortcut", label="x", args=(ArgSpec("mods", required=True),)
        )
        problems = check_entry(record, dropped)
        assert any("omits keys the compositor reads" in p and "'window'" in p for p in problems)
        assert any("required keys ['mods'] differ" in p for p in problems)

    def test_an_entry_that_names_a_key_nothing_read(self, record: ProbeRecord) -> None:
        invented = Dispatcher(path="window.drag", label="x", args=(ArgSpec("window"),))
        assert check_entry(record, invented) == [
            "names keys no probe saw the compositor read: ['window']"
        ]

    def test_a_table_entry_for_a_bare_argument_dispatcher(self, record: ProbeRecord) -> None:
        wrong = Dispatcher(path="layout", label="x", args=(ArgSpec("message", required=True),))
        assert any("takes a bare argument" in p for p in check_entry(record, wrong))

    def test_a_free_form_entry_makes_no_claim(self, record: ProbeRecord) -> None:
        free = Dispatcher(path="focus", label="x", free_form_reason="Give one.")
        assert check_entry(record, free) == []


class TestReadingTheCompositorsAnswers:
    def test_a_clean_eval_is_ok(self) -> None:
        assert clean("ok\n") == (True, "")

    def test_several_errors_become_one_line(self) -> None:
        reply = (
            "error: =[C]:-1: hl.send_key_state: 'mods' is required\n"
            "error: =[C]:-1: hl.send_key_state: 'key' is required\n"
        )
        assert clean(reply) == (
            False,
            "hl.send_key_state: 'mods' is required | hl.send_key_state: 'key' is required",
        )

    def test_required_keys_in_the_order_named(self) -> None:
        error = (
            "hl.send_key_state: 'mods' is required | hl.send_key_state: 'key' is required"
            " | hl.send_key_state: 'state' is required"
        )
        assert required_keys(error) == ("mods", "key", "state")

    def test_two_keys_named_in_one_sentence(self) -> None:
        error = "hl.window.fullscreen_state: 'internal' and 'client' are required"
        assert required_keys(error) == ("internal", "client")

    def test_a_shape_hint_splits_required_from_optional(self) -> None:
        error = "send_key_state: expected a table { mods, key, state, window? }"
        assert hint_keys(error) == (("mods", "key", "state"), ("window",))

    def test_an_example_is_not_a_hint(self) -> None:
        error = 'hl.focus: expected a table, e.g. { direction = "left" }'
        assert hint_keys(error) == ((), ())

    def test_a_one_of_list_is_optional_keys(self) -> None:
        error = (
            "hl.window.move: unrecognized arguments. Expected one of: direction, "
            "x+y(+relative), workspace, into_group, out_of_group"
        )
        assert hint_keys(error) == (
            (),
            ("direction", "x", "y", "workspace", "into_group", "out_of_group"),
        )


class TestGeneratedCalls:
    """What the bind editor would write for an entry: the calls the integration tier loads."""

    def test_a_table_dispatcher_with_an_optional_key(self) -> None:
        assert list(generated_calls(BY_PATH["send_shortcut"])) == [
            ("required", "hl.dsp.send_shortcut{mods = 'SUPER', key = 'a'}"),
            ("all", "hl.dsp.send_shortcut{mods = 'SUPER', key = 'a', window = 'activewindow'}"),
        ]

    def test_a_positional_dispatcher_with_an_optional_argument(self) -> None:
        assert list(generated_calls(BY_PATH["workspace.toggle_special"])) == [
            ("required", "hl.dsp.workspace.toggle_special()"),
            ("all", "hl.dsp.workspace.toggle_special('probe')"),
        ]
