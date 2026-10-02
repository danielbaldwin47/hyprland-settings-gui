"""The curated tables reach the Overlay only through `tools/curate_overlay.py` (#157).

The owner reviews the group taxonomy in `tools/overlay_groups.toml`, which reads better than
a thousand lines of JSON; the script writes it into `data/schema/overlay.json`. The sync
test is what keeps the two from drifting: a hand edit of a `group` or `order` in the
Overlay fails here, with the table and the command named.
"""

from __future__ import annotations

import sys

import pytest
from _support import ROOT, SCHEMA_DIR

sys.path.insert(0, str(ROOT / "tools"))

import curate_overlay

SMALL = """{
  "format_version": 1,
  "sections": {
    "a": {
      "title": "A"
    },
    "c": {
      "title": "C"
    }
  },
  "options": {
    "a:b": { "title": "B" },
    "a:d": { "title": "D", "group": "Old", "order": 1 },
    "c:e": { "title": "E" }
  }
}
"""

TABLE = """
[[a]]
title = "Typing"
description = "How keys repeat."
options = ["a:d", "a:b"]
"""


def test_the_overlay_carries_exactly_the_curated_tables() -> None:
    text = (SCHEMA_DIR / "overlay.json").read_text(encoding="utf-8")
    tables = curate_overlay.load_tables()

    assert curate_overlay.curate(text, tables) == text, (
        "data/schema/overlay.json disagrees with tools/overlay_groups.toml: edit the table, "
        "then run `.venv/bin/python tools/curate_overlay.py`"
    )


def test_curating_writes_the_groups_and_each_members_position() -> None:
    written = curate_overlay.curate(SMALL, curate_overlay.parse_groups(TABLE))

    assert written == SMALL.replace(
        '    "a": {\n      "title": "A"\n    },',
        '    "a": {\n      "title": "A",\n'
        '      "groups": [{ "title": "Typing", "description": "How keys repeat." }]\n    },',
    ).replace(
        '"a:b": { "title": "B" },', '"a:b": { "title": "B", "group": "Typing", "order": 2 },'
    ).replace('"group": "Old", "order": 1', '"group": "Typing", "order": 1')


def test_an_option_the_table_no_longer_names_loses_its_group() -> None:
    """Curation converges on the table: what it drops, the Overlay drops."""
    once = curate_overlay.curate(SMALL, curate_overlay.parse_groups(TABLE))
    table = TABLE.replace('options = ["a:d", "a:b"]', 'options = ["a:b"]')

    written = curate_overlay.curate(once, curate_overlay.parse_groups(table))

    assert '"a:d": { "title": "D" },' in written
    assert '"a:b": { "title": "B", "group": "Typing", "order": 1 },' in written


def test_curating_twice_changes_nothing_the_second_time() -> None:
    tables = curate_overlay.parse_groups(TABLE)
    once = curate_overlay.curate(SMALL, tables)

    assert curate_overlay.curate(once, tables) == once


def test_a_table_placing_an_option_of_another_section_is_rejected() -> None:
    with pytest.raises(ValueError, match="'c:e' is not an option of section 'a'"):
        curate_overlay.parse_groups(TABLE.replace('"a:b"]', '"a:b", "c:e"]'))


def test_a_table_placing_an_option_twice_is_rejected() -> None:
    table = TABLE + '\n[[a]]\ntitle = "Layout"\noptions = ["a:b"]\n'
    with pytest.raises(ValueError, match="'a:b' is placed twice"):
        curate_overlay.parse_groups(table)


def test_a_table_with_an_unknown_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        curate_overlay.parse_groups(TABLE.replace("description", "descripton"))
