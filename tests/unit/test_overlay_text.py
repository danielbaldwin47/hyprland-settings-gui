"""The one writer for `data/schema/overlay.json` (`tools/overlay_text.py`, #157).

Four tickets write the Overlay in parallel branches. Re-serialising the whole file rewrites
every line and conflicts with all of them, so the writer edits the text of the entries it
changes and leaves every other byte alone. These tests pin both halves: the file's own
layout is what the writer emits, and a write touches only the entries it names.
"""

from __future__ import annotations

import json
import sys

import pytest
from _support import ROOT, SCHEMA_DIR

sys.path.insert(0, str(ROOT / "tools"))

import overlay_text

OVERLAY = (SCHEMA_DIR / "overlay.json").read_text(encoding="utf-8")

SMALL = """{
  "format_version": 1,
  "sections": {
    "a": {
      "title": "A",
      "help_url": "https://example.org/a"
    }
  },
  "options": {
    "a:b": { "title": "B" },
    "a:c": { "title": "C", "unit": "px" }
  }
}
"""


@pytest.mark.parametrize("block", ["sections", "options"])
def test_every_entry_reads_as_the_writer_would_write_it(block: overlay_text.Block) -> None:
    """The layout rule is the file's own: re-emitting an entry reproduces it byte for byte."""
    payload = json.loads(OVERLAY)[block]
    differ = [
        name
        for name, (start, end) in overlay_text.entry_spans(OVERLAY, block).items()
        if OVERLAY[start:end] != overlay_text.format_entry(block, name, payload[name])
    ]

    assert differ == []


def test_the_spans_cover_every_entry_of_the_block() -> None:
    spans = overlay_text.entry_spans(OVERLAY, "options")

    assert list(spans) == list(json.loads(OVERLAY)["options"])


def test_setting_a_field_rewrites_only_that_entry_and_appends_the_key() -> None:
    written = overlay_text.set_fields(
        SMALL, "options", {"a:b": {"group": "Typing", "order": 1}}
    )

    assert written == SMALL.replace(
        '"a:b": { "title": "B" },', '"a:b": { "title": "B", "group": "Typing", "order": 1 },'
    )


def test_an_existing_key_keeps_its_place_and_none_removes_one() -> None:
    written = overlay_text.set_fields(SMALL, "options", {"a:c": {"title": "See", "unit": None}})

    assert written == SMALL.replace('{ "title": "C", "unit": "px" }', '{ "title": "See" }')


def test_an_entry_longer_than_the_width_puts_one_key_on_each_line() -> None:
    long = "A title long enough that the entry no longer fits on one line of the file"
    written = overlay_text.set_fields(SMALL, "options", {"a:b": {"title": long, "order": 2}})

    assert written == SMALL.replace(
        '    "a:b": { "title": "B" },',
        f'    "a:b": {{\n      "title": "{long}",\n      "order": 2\n    }},',
    )


def test_a_sections_groups_are_written_one_per_line_when_they_do_not_fit_on_one() -> None:
    groups = [
        {"title": "Typing", "description": "How long a key is held before it repeats."},
        {"title": "Layout"},
    ]
    written = overlay_text.set_fields(SMALL, "sections", {"a": {"groups": groups}})

    assert written == SMALL.replace(
        '      "help_url": "https://example.org/a"\n',
        '      "help_url": "https://example.org/a",\n'
        '      "groups": [\n'
        '        { "title": "Typing", "description": "How long a key is held before it '
        'repeats." },\n'
        '        { "title": "Layout" }\n'
        "      ]\n",
    )


def test_writing_the_same_fields_twice_changes_nothing_the_second_time() -> None:
    updates = {"a:b": {"group": "Typing", "order": 1}, "a:c": {"group": "Typing", "order": 2}}
    once = overlay_text.set_fields(SMALL, "options", updates)

    assert overlay_text.set_fields(once, "options", updates) == once


def test_a_write_that_changes_no_value_leaves_a_hand_wrapped_entry_alone() -> None:
    written = overlay_text.set_fields(
        OVERLAY, "options", {"general:layout": {"title": "Layout"}}
    )

    assert written == OVERLAY


def test_an_entry_the_file_does_not_have_is_an_error_naming_it() -> None:
    with pytest.raises(KeyError, match="a:z"):
        overlay_text.set_fields(SMALL, "options", {"a:z": {"group": "Typing"}})
