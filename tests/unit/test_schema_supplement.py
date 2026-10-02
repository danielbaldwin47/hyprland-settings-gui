"""The runtime supplement: options a newer Hyprland has that no shipped schema does (#177).

ADR-0012 §Pinning: on a Hyprland newer than every shipped schema, an option absent from the
shipped schema gets a minimal record inferred from its live `descriptions` record alone (no
stub, no source) and renders flagged. The records below are shaped like the live reply
(`_fake_hyprland.DESCRIPTIONS`, captured off 0.56.2): `default`, `current`, and the bounds
keys a type prints.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from _support import synthetic_schema_dir

from hyprtweaker.engine.schema import (
    GeneratedOption,
    GeneratedSchema,
    GetOptionKey,
    OptionType,
    Overlay,
    Schema,
    SectionOverlay,
    Supplement,
    SupplementKind,
    Visibility,
    Widget,
    newer_than_shipped,
    supplement,
)


def option(name: str, order: int) -> GeneratedOption:
    return GeneratedOption(
        name=name,
        lua_key=name.replace(":", "."),
        section=name.split(":", 1)[0],
        path=tuple(name.replace(":", ".").split(".")),
        order=order,
        type=OptionType.INT,
        widget=Widget.INT_RANGE,
        description="size of the border",
        default=1,
        default_raw=1,
        sentinel_default=False,
        getoption_key=GetOptionKey.INT,
    )


def shipped() -> Schema:
    """A shipped 0.56.2 with two Options, a curated Section title and a hidden Section."""
    return Schema.merge(
        GeneratedSchema(
            hyprland_version="0.56.2",
            options=(option("general:border_size", 0), option("debug:overlay", 7)),
            provenance={},
        ),
        Overlay(
            sections={
                "general": SectionOverlay(title="General"),
                "debug": SectionOverlay(visibility=Visibility.HIDDEN),
            },
            options={},
        ),
    )


BOOL = {"name": "general:snap_new", "description": "snap new windows", "default": False}
INT = {
    "name": "general:new_size",
    "description": "a new size",
    "default": 3,
    "current": 3,
    "min": 0,
    "max": 20,
    "map": None,
}
FLOAT = {
    "name": "decoration:new_alpha",
    "description": "a new alpha",
    "default": 1,
    "current": 1,
    "min": 0,
    "max": 1,
}
STRING = {"name": "misc:new_text", "description": "some text", "default": "hello"}
COLOR = {"name": "group:new_color", "description": "a colour", "default": "ffffffff"}


# --- what is added ---------------------------------------------------------------------


def test_two_records_the_shipped_schema_lacks_become_two_flagged_options() -> None:
    records = (
        {"name": "general:border_size", "description": "x", "default": 9, "map": None},
        BOOL,
        INT,
    )

    schema = supplement(shipped(), records, version="0.58.0")

    assert len(schema) == 4
    for name in ("general:snap_new", "general:new_size"):
        assert schema[name].supplement == Supplement(SupplementKind.NEWER_VERSION, "0.58.0")
        assert schema[name].added_in == "0.58.0"
    assert schema["general:border_size"].default == 1, "a shipped option is not replaced"
    assert schema["general:border_size"].supplement is None


def test_the_schema_version_stays_the_shipped_one() -> None:
    """ADR-0012: "a degradation state, not a schema source". The Writer stamps this
    version into the Manifest."""
    assert supplement(shipped(), (BOOL,), version="0.58.0").hyprland_version == "0.56.2"


def test_added_options_follow_every_shipped_one_in_declaration_order() -> None:
    schema = supplement(shipped(), (BOOL, INT), version="0.58.0")

    assert [option.order for option in schema.section("general")] == [0, 8, 9]


def test_the_shipped_curation_and_animation_leaves_are_kept() -> None:
    base = replace(shipped(), animation_leaves=("border", "windowsIn"))

    schema = supplement(base, (BOOL,), version="0.58.0")

    assert schema.section_title("general") == "General"
    assert schema.animation_leaves == ("border", "windowsIn")


@pytest.mark.parametrize(
    ("record", "option_type", "widget"),
    [
        (BOOL, OptionType.BOOL, Widget.TOGGLE),
        (INT, OptionType.INT, Widget.INT_RANGE),
        (FLOAT, OptionType.FLOAT, Widget.FLOAT_RANGE),
        (STRING, OptionType.STRING, Widget.STRING),
        # Only the C++ `MS<Color>` table tells a colour from text, and the supplement has
        # no source: the conservative control is a text entry (ADR-0012).
        (COLOR, OptionType.STRING, Widget.STRING),
    ],
)
def test_each_shape_gets_the_widget_its_record_alone_supports(
    record: dict[str, Any], option_type: OptionType, widget: Widget
) -> None:
    added = supplement(shipped(), (record,), version="0.58.0")[str(record["name"])]

    assert (added.type, added.widget) == (option_type, widget)


def test_an_added_option_is_titled_from_its_key_and_written_under_its_lua_key() -> None:
    added = supplement(shipped(), (INT,), version="0.58.0")["general:new_size"]

    assert (added.title, added.lua_key, added.section) == (
        "New size",
        "general.new_size",
        "general",
    )


def test_a_section_new_to_the_app_gets_a_derived_title() -> None:
    record = {"name": "input-capture:new_flag", "description": "x", "default": True}

    schema = supplement(shipped(), (record,), version="0.58.0")

    assert schema.section_title("input-capture") == "Input capture"


def test_an_option_added_to_a_hidden_section_stays_hidden() -> None:
    """The Section's curated tier is a floor for every Option in it, known or not."""
    record = {"name": "debug:new_crash", "description": "x", "default": False}

    added = supplement(shipped(), (record,), version="0.58.0")["debug:new_crash"]

    assert added.visibility is Visibility.HIDDEN


# --- what is not -----------------------------------------------------------------------


def test_plugin_records_are_left_for_the_plugin_supplement() -> None:
    plugin = {"name": "plugin:hyprbars:bar_height", "description": "x", "default": 15}

    schema = supplement(shipped(), (plugin, BOOL), version="0.58.0")

    assert "plugin:hyprbars:bar_height" not in schema
    assert "general:snap_new" in schema


def test_the_plugin_kind_takes_only_plugin_records_and_stamps_no_version() -> None:
    plugin = {"name": "plugin:hyprbars:bar_height", "description": "x", "default": 15}

    schema = supplement(shipped(), (plugin, BOOL), version="0.58.0", kind=SupplementKind.PLUGIN)

    added = schema["plugin:hyprbars:bar_height"]
    assert added.supplement == Supplement(SupplementKind.PLUGIN, "0.58.0")
    assert added.added_in is None
    assert "general:snap_new" not in schema


def test_a_record_whose_shape_cannot_be_read_is_skipped_not_fatal() -> None:
    """A newer compositor may print a type this app has never seen; the rest still show."""
    odd = {"name": "general:new_thing", "description": "x", "default": {"a": 1}}
    bare = {"name": "general:no_default"}

    schema = supplement(shipped(), (odd, bare, BOOL), version="0.58.0")

    assert "general:new_thing" not in schema
    assert "general:no_default" not in schema
    assert "general:snap_new" in schema


# --- when ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("live", "newer"),
    [("0.58.1", True), ("0.58.0", False), ("0.57.0", False), ("0.56.1", False)],
)
def test_only_a_hyprland_newer_than_every_shipped_schema_is_supplemented(
    tmp_path: Path, live: str, newer: bool
) -> None:
    directory = synthetic_schema_dir(tmp_path, "0.56.2", "0.58.0")

    assert newer_than_shipped(live, directory) is newer
