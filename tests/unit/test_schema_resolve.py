"""Merging the Overlay onto the Generated schema, and picking a schema for a version."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyprtweaker.engine.schema import (
    GeneratedOption,
    KnownValues,
    OptionType,
    Overlay,
    OverlayEntry,
    Range,
    Schema,
    SectionOverlay,
    Visibility,
    Widget,
    derive_title,
    load_schema,
    resolve_option,
    select_version,
)
from hyprtweaker.engine.schema import generated as generated_module
from hyprtweaker.engine.schema import overlay as overlay_module
from hyprtweaker.engine.schema.resolve import version_key


def option(name: str = "general:border_size", **fields: object) -> GeneratedOption:
    defaults: dict[str, object] = {
        "name": name,
        "lua_key": name.replace(":", "."),
        "section": name.split(":", 1)[0],
        "path": tuple(name.replace(":", ".").split(".")),
        "order": 0,
        "type": OptionType.INT,
        "widget": Widget.INT_RANGE,
        "description": "size of the border",
        "default": 1,
        "default_raw": 1,
        "sentinel_default": False,
        "getoption_key": "int",
    }
    defaults.update(fields)
    return GeneratedOption(**defaults)  # type: ignore[arg-type]


# --- overriding ------------------------------------------------------------------------


def test_the_overlay_overrides_title_widget_and_help() -> None:
    resolved = resolve_option(
        option(),
        OverlayEntry(title="Border size", widget=Widget.SEGMENTED, help="Curated help"),
        None,
    )
    assert resolved.title == "Border size"
    assert resolved.widget is Widget.SEGMENTED
    assert resolved.description == "Curated help"


def test_without_curated_help_the_subtitle_is_the_generated_description() -> None:
    """ADR-0013: the Row subtitle is the description, not the dotted key."""
    resolved = resolve_option(option(), OverlayEntry(title="Border size"), None)
    assert resolved.description == "size of the border"
    assert resolved.dotted_key == "general.border_size"


def test_a_sentinel_default_is_nullable_without_being_asked() -> None:
    resolved = resolve_option(
        option(sentinel_default=True, default=None, default_raw="[[EMPTY]]"),
        OverlayEntry(title="Layout", nullable=True, null_label="Device default"),
        None,
    )
    assert resolved.nullable is True
    assert resolved.null_label == "Device default"


def test_the_overlay_can_deny_nullability_a_sentinel_implies() -> None:
    """`misc:force_default_wallpaper` defaults to -1, but -1 means "random", not "unset"."""
    resolved = resolve_option(
        option(sentinel_default=True, default=None),
        OverlayEntry(title="Default wallpaper", nullable=False),
        None,
    )
    assert resolved.nullable is False


def test_null_value_defaults_to_the_printed_sentinel_and_can_be_overridden() -> None:
    implied = resolve_option(
        option(sentinel_default=True, default=None, default_raw="[[EMPTY]]"),
        OverlayEntry(title="x", nullable=True, null_label="None"),
        None,
    )
    assert implied.null_value == "[[EMPTY]]"

    curated = resolve_option(
        option(sentinel_default=True, default=None, default_raw="[[EMPTY]]"),
        OverlayEntry(title="x", nullable=True, null_label="None", null_value=""),
        None,
    )
    assert curated.null_value == ""


def test_overlay_bounds_win_but_do_not_erase_generated_ones() -> None:
    resolved = resolve_option(
        option(min=0, max=2147483647),
        OverlayEntry(title="Drag threshold", range=Range(soft_max=100)),
        None,
    )
    assert resolved.range == Range(min=0, max=2147483647, step=None, soft_max=100)


def test_generated_choices_become_known_values() -> None:
    resolved = resolve_option(
        option(type=OptionType.STRING, widget=Widget.ENUM_STRING, choices=("a", "b")),
        OverlayEntry(title="x"),
        None,
    )
    assert resolved.known_values == KnownValues(values=("a", "b"))


def test_curated_known_values_replace_the_generated_ones() -> None:
    """`general:layout`'s description lists `lua:<name>`, which is a shape, not a value."""
    resolved = resolve_option(
        option(type=OptionType.STRING, choices=("dwindle", "lua:<name>")),
        OverlayEntry(title="Layout", known_values=KnownValues(("dwindle",), open=True)),
        None,
    )
    assert resolved.known_values == KnownValues(values=("dwindle",), open=True)


# --- visibility ------------------------------------------------------------------------


def test_a_section_sets_the_visibility_floor() -> None:
    resolved = resolve_option(
        option("debug:overlay"),
        OverlayEntry(title="Debug overlay"),
        SectionOverlay(visibility=Visibility.HIDDEN),
    )
    assert resolved.visibility is Visibility.HIDDEN


def test_a_per_option_tier_overrides_its_section() -> None:
    resolved = resolve_option(
        option("misc:anr_missed_pings"),
        OverlayEntry(title="x", visibility=Visibility.ADVANCED),
        SectionOverlay(visibility=None),
    )
    assert resolved.visibility is Visibility.ADVANCED


def test_visibility_defaults_to_the_default_tier() -> None:
    assert (
        resolve_option(option(), OverlayEntry(title="x"), None).visibility is Visibility.DEFAULT
    )


def test_a_section_help_url_is_inherited() -> None:
    resolved = resolve_option(
        option(), OverlayEntry(title="x"), SectionOverlay(help_url="https://wiki/#general")
    )
    assert resolved.help_url == "https://wiki/#general"


# --- derived titles --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("general:border_size", "Border size"),
        ("input:kb_layout", "Keyboard layout"),
        ("general:col.active_border", "Active border color"),
    ],
)
def test_derive_title_is_a_readable_last_resort(name: str, expected: str) -> None:
    """Only reachable for an Option a newer Hyprland added (ADR-0012 supplement path)."""
    assert derive_title(option(name)) == expected


# --- version selection -----------------------------------------------------------------


def test_version_key_orders_numerically_not_lexically() -> None:
    assert version_key("0.56.10") > version_key("0.56.2")


def test_an_exact_version_wins() -> None:
    assert select_version("0.56.2", ("0.55.0", "0.56.2")) == "0.56.2"


def test_an_unseen_newer_version_degrades_to_the_nearest_lower_schema() -> None:
    assert select_version("0.57.1", ("0.55.0", "0.56.2")) == "0.56.2"


def test_never_degrade_onto_a_higher_schema() -> None:
    """Offering options the running compositor lacks is a config error on next reload."""
    with pytest.raises(ValueError, match="older than every shipped schema"):
        select_version("0.54.0", ("0.55.0", "0.56.2"))


def test_no_shipped_schemas_is_an_error() -> None:
    with pytest.raises(FileNotFoundError):
        select_version("0.56.2", ())


# --- round trip ------------------------------------------------------------------------


def test_generated_schema_survives_a_serialisation_round_trip() -> None:
    schema = generated_module.GeneratedSchema(
        hyprland_version="0.56.2",
        options=(option(map={"off": 0}, choices=("a",), refresh=("REFRESH_ALL",)),),
        provenance={"degraded": False},
    )
    assert generated_module.loads(generated_module.dumps(schema)) == schema


def test_duplicate_options_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate options"):
        generated_module.GeneratedSchema(
            hyprland_version="0.56.2", options=(option(), option()), provenance={}
        )


def test_a_schema_from_a_future_format_version_fails_loudly() -> None:
    with pytest.raises(ValueError, match="format version"):
        generated_module.loads(json.dumps({"format_version": 99, "hyprland_version": "x"}))


# --- overlay parsing -------------------------------------------------------------------


def test_an_unknown_overlay_field_is_rejected() -> None:
    """A typo in a 353-entry hand-edited file is otherwise invisible."""
    text = json.dumps(
        {"format_version": 1, "options": {"a:b": {"title": "A", "nulllabel": "None"}}}
    )
    with pytest.raises(ValueError, match="unknown overlay field"):
        overlay_module.loads(text)


def test_nullable_without_a_label_is_rejected_at_load_time() -> None:
    text = json.dumps({"format_version": 1, "options": {"a:b": {"nullable": True}}})
    with pytest.raises(ValueError, match="null_label"):
        overlay_module.loads(text)


def test_an_unknown_widget_is_rejected() -> None:
    text = json.dumps({"format_version": 1, "options": {"a:b": {"widget": "spinner"}}})
    with pytest.raises(ValueError):
        overlay_module.loads(text)


# --- the Schema container --------------------------------------------------------------


def test_schema_lookup_and_sections() -> None:
    schema = Schema.merge(
        generated_module.GeneratedSchema(
            hyprland_version="0.56.2",
            options=(
                option("general:border_size", order=0),
                option("debug:overlay", order=1),
            ),
            provenance={},
        ),
        Overlay(
            sections={"debug": SectionOverlay(visibility=Visibility.HIDDEN)},
            options={"general:border_size": OverlayEntry(title="Border size")},
        ),
    )

    assert len(schema) == 2
    assert "general:border_size" in schema
    assert schema["general:border_size"].title == "Border size"
    assert schema.get("nope:missing") is None
    assert schema.section_names == ("general", "debug")
    assert [o.name for o in schema.section("debug")] == ["debug:overlay"]
    assert schema.section("debug")[0].visibility is Visibility.HIDDEN


def test_load_schema_reads_the_shipped_files(tmp_path: Path) -> None:
    """End to end through the public entry point the app actually calls."""
    repo_schema = Path(__file__).resolve().parents[2] / "data" / "schema"
    schema = load_schema("0.56.2", repo_schema)

    assert schema.hyprland_version == "0.56.2"
    assert len(schema) == 353

    profile = schema["input:accel_profile"]
    assert profile.title == "Pointer acceleration"
    assert profile.widget is Widget.ENUM_STRING
    assert profile.nullable is True
    assert profile.null_label == "Device default"
    assert profile.default is None


def test_missing_schema_directory_names_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_schema("0.56.2", tmp_path)
