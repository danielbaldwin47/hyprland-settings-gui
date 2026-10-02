"""Every setting's Row text is assessed, short and in the app's voice (#129).

`help` replaces the upstream line in the Row subtitle, the Help popover and the search text,
and the subtitle has no line cap: a long entry makes its Row tall on every Page. So the
prose lives in `tools/overlay_help.toml`, one entry per setting that either carries `help`
or says why it does not (`skip`), and this file enforces what a reviewer would otherwise
have to read for: the length bound, the banned words, no copy of the upstream line, and
that `data/schema/overlay.json` carries exactly what the table says.

The table is written from a seed (`tools/seed_overlay_help.py`) and applied by
`tools/curate_overlay.py`; never edit `help` in the Overlay by hand.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from _support import ROOT, SCHEMA_DIR

sys.path.insert(0, str(ROOT / "tools"))

import curate_overlay
import overlay_help
import seed_overlay_help

from hyprtweaker.engine.schema import generated as generated_module
from hyprtweaker.engine.schema import humanise, load_schema
from hyprtweaker.engine.schema.resolve import available_versions

VERSIONS = list(available_versions(SCHEMA_DIR))
UPSTREAM = "size of the border around windows"


def _upstream(version: str) -> dict[str, str]:
    schema = generated_module.load(SCHEMA_DIR / f"hyprland-{version}.json")
    return {option.name: option.description for option in schema.options}


def _table() -> dict[str, overlay_help.HelpEntry]:
    return overlay_help.load_table()


def _each_shipped_option() -> Iterator[tuple[str, str, str]]:
    for version in VERSIONS:
        for name, description in _upstream(version).items():
            yield version, name, description


# -- the table covers the shipped settings ------------------------------------------------


def test_there_are_versions_to_check() -> None:
    assert VERSIONS, f"no hyprland-<ver>.json files in {SCHEMA_DIR}"


@pytest.mark.parametrize("version", VERSIONS)
def test_every_setting_is_assessed_exactly_once(version: str) -> None:
    table = _table()
    names = list(_upstream(version))

    undecided = [
        name
        for name in names
        if name not in table or (table[name].help is None) == (table[name].skip is None)
    ]
    assert not undecided, (
        f"{len(undecided)} setting(s) of {version} in tools/overlay_help.toml lack exactly "
        f"one of `help` or `skip`: {undecided[:8]}. Run tools/seed_overlay_help.py, then "
        "decide each entry"
    )


def test_the_table_names_only_shipped_settings() -> None:
    shipped = {name for _, name, _ in _each_shipped_option()}

    assert sorted(set(_table()) - shipped) == []


def _shown_labels(version: str) -> dict[str, dict[str, str]]:
    """Each labelled control's stored values and the labels the control shows for them."""
    shown: dict[str, dict[str, str]] = {}
    for option in load_schema(version, SCHEMA_DIR):
        if option.labels:
            shown[option.name] = dict(option.labels)
        elif option.map:
            shown[option.name] = {str(v): humanise(k) for k, v in option.map.items()}
    return shown


def test_every_help_obeys_the_length_and_voice_rules() -> None:
    table = _table()
    labels = {version: _shown_labels(version) for version in VERSIONS}
    problems = [
        f"{version} {name}: {problem}"
        for version, name, description in _each_shipped_option()
        if (entry := table.get(name)) is not None
        for problem in overlay_help.entry_problems(
            entry, description, labels[version].get(name, {})
        )
    ]
    assert not problems, "\n".join(sorted(set(problems)))


def test_the_overlay_carries_exactly_the_help_table() -> None:
    text = (SCHEMA_DIR / "overlay.json").read_text(encoding="utf-8")
    tables = curate_overlay.load_tables()

    curated = curate_overlay.curate(text, tables, overlay_help.helps(_table()))

    assert curated == text, (
        "data/schema/overlay.json disagrees with tools/overlay_help.toml: edit the table, "
        "then run `.venv/bin/python tools/curate_overlay.py`"
    )


def test_general_layout_keeps_the_voice_reference() -> None:
    """Its text is the reference every other entry is held to, and the search test finds it."""
    assert _table()["general:layout"].help == (
        "Which tiling layout to use. "
        "Layouts your Lua files register are listed too, marked “Lua layout”."
    )


def test_every_option_of_prototype_8_has_help() -> None:
    table = _table()
    prototype = [
        "input:kb_layout",
        "input:kb_variant",
        "input:kb_options",
        "input:kb_file",
        "input:resolve_binds_by_sym",
        "input:repeat_rate",
        "input:repeat_delay",
        "input:sensitivity",
        "input:scroll_points",
        "input:force_no_accel",
        "input:left_handed",
        "input:scroll_factor",
        "input:scroll_button",
        "input:scroll_button_lock",
        "input:follow_mouse_threshold",
        "input:touchpad:clickfinger_behavior",
        "input:tablettool:pressure_range_min",
        "decoration:rounding_power",
        "decoration:blur:passes",
        "decoration:blur:special",
        "general:layout",
        "general:allow_tearing",
    ]
    missing = [name for name in prototype if table[name].help is None]
    assert missing == []


# -- the check itself: it passes good prose and fails planted prose ----------------------


GOOD = "How wide the border around each window is, in pixels. Set it to 0 to remove borders."


def test_a_good_entry_has_no_problems() -> None:
    assert overlay_help.help_problems(GOOD, UPSTREAM) == []


def test_a_planted_over_long_entry_fails() -> None:
    long = "x" * 161 + "."

    assert overlay_help.help_problems(long, UPSTREAM) == [
        "162 characters is over the 160-character limit"
    ]


def test_an_entry_of_exactly_the_limit_passes() -> None:
    assert overlay_help.help_problems("x" * 159 + ".", UPSTREAM) == []


def test_a_planted_banned_word_fails() -> None:
    problems = overlay_help.help_problems("Edit the option to change borders.", UPSTREAM)

    assert problems == ["says 'option', a word the app does not use"]


@pytest.mark.parametrize(
    "word",
    ["schema", "Overlay", "options", "wiki", "release check", "config variable"],
)
def test_every_banned_word_fails_case_insensitively(word: str) -> None:
    problems = overlay_help.help_problems(f"Changes the {word} of a window.", UPSTREAM)

    assert len(problems) == 1


def test_xkb_options_are_the_one_allowed_use() -> None:
    assert overlay_help.help_problems("Extra XKB options, such as caps:escape.", UPSTREAM) == []


def test_a_word_that_only_contains_a_banned_word_passes() -> None:
    assert overlay_help.help_problems("Pick an optional border colour.", UPSTREAM) == []


def test_three_sentences_fail() -> None:
    problems = overlay_help.help_problems("One. Two. Three.", UPSTREAM)

    assert problems == ["3 sentences; at most 2"]


def test_an_entry_must_end_with_a_period() -> None:
    assert overlay_help.help_problems("Border width in pixels", UPSTREAM) == [
        "does not end with a period"
    ]


def test_a_copy_of_the_upstream_line_fails_whatever_its_case_or_punctuation() -> None:
    problems = overlay_help.help_problems("Size of the border around windows.", UPSTREAM)

    assert problems == ["is the upstream description; write what it says in full"]


def test_a_stored_value_a_labelled_control_never_shows_fails() -> None:
    labels = {"0": "Disabled", "1": "Enabled", "2": "Auto"}

    problems = overlay_help.help_problems("Set 2 to decide per monitor.", UPSTREAM, labels)

    assert problems == ["quotes the stored value 2, which the control shows as 'Auto'"]


def test_a_number_that_is_no_stored_value_passes_on_a_labelled_control() -> None:
    labels = {"0": "Off", "1": "On"}

    assert overlay_help.help_problems("Waits 500 milliseconds.", UPSTREAM, labels) == []


def test_an_entry_needs_exactly_one_of_help_or_skip() -> None:
    assert overlay_help.entry_problems(overlay_help.HelpEntry(None, None), UPSTREAM) == [
        "has neither `help` nor `skip`"
    ]
    assert overlay_help.entry_problems(overlay_help.HelpEntry("A.", "why"), UPSTREAM) == [
        "has both `help` and `skip`"
    ]
    assert (
        overlay_help.entry_problems(overlay_help.HelpEntry(None, "fine as is"), UPSTREAM) == []
    )


def test_a_skip_must_give_a_reason() -> None:
    assert overlay_help.entry_problems(overlay_help.HelpEntry(None, "  "), UPSTREAM) == [
        "has an empty `skip`"
    ]


def test_parsing_reads_help_and_skip_and_rejects_other_keys() -> None:
    table = overlay_help.parse_table(
        '["a:b"]\nhelp = "Hi."\n\n["a:c"]\nskip = "fine"\n\n["a:d"]\n'
    )

    assert table == {
        "a:b": overlay_help.HelpEntry("Hi.", None),
        "a:c": overlay_help.HelpEntry(None, "fine"),
        "a:d": overlay_help.HelpEntry(None, None),
    }
    with pytest.raises(ValueError, match="unknown key"):
        overlay_help.parse_table('["a:b"]\nhelps = "typo"\n')


def test_helps_is_what_the_overlay_receives() -> None:
    table = {
        "a:b": overlay_help.HelpEntry("Hi.", None),
        "a:c": overlay_help.HelpEntry(None, "fine"),
    }

    assert overlay_help.helps(table) == {"a:b": "Hi."}


# -- the seed: it flags candidates and never loses a decision -----------------------------


def test_the_seed_flags_a_short_line_and_a_line_that_lists_the_combo() -> None:
    assert seed_overlay_help.flags_for("gaps between windows", None, []) == (
        "short (20 chars)",
    )
    assert seed_overlay_help.flags_for(
        "Pick one of dwindle or master, whichever layout you like to use.",
        None,
        ["dwindle", "master", "monocle"],
    ) == ("lists values the combo shows: dwindle, master",)


def test_the_seed_flags_a_wiki_text_much_longer_than_the_line() -> None:
    upstream = "How the pointer speeds up when you move it."

    assert seed_overlay_help.flags_for(upstream, upstream + " " + "More. " * 5, []) == (
        "wiki is longer (74 chars)",
    )


def test_the_seed_flags_a_line_too_long_for_a_row() -> None:
    flags = seed_overlay_help.flags_for("word " * 40, None, [])

    assert flags == ("too long for a Row (200 chars)",)


def test_reseeding_keeps_every_decision_and_adds_the_undecided() -> None:
    facts = seed_overlay_help.gather(VERSIONS[-1])[:3]
    decided = {
        facts[0].name: overlay_help.HelpEntry("Kept prose.", None),
        facts[1].name: overlay_help.HelpEntry(None, "kept reason"),
    }

    rendered = seed_overlay_help.render(facts, decided)

    assert overlay_help.parse_table(rendered) == {
        facts[0].name: overlay_help.HelpEntry("Kept prose.", None),
        facts[1].name: overlay_help.HelpEntry(None, "kept reason"),
        facts[2].name: overlay_help.HelpEntry(None, None),
    }
