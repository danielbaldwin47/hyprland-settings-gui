"""Merging the Overlay onto the Generated schema, and picking a schema for a version."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from _support import SCHEMA_DIR, synthetic_schema_dir

from hyprtweaker.engine.schema import (
    GeneratedOption,
    GetOptionKey,
    KnownValues,
    OptionType,
    Overlay,
    OverlayEntry,
    OverlayGroup,
    Range,
    Schema,
    SectionOverlay,
    Visibility,
    Widget,
    available_versions,
    below_lua_floor,
    derive_title,
    load_schema,
    resolve_option,
    select_version,
    stamp_added_in,
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
        "getoption_key": GetOptionKey.INT,
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


def test_a_lua_hyprland_older_than_every_schema_gets_the_oldest() -> None:
    """ADR-0012 §Support window: every version down to 0.56 degrades, none crashes."""
    assert select_version("0.56.1", ("0.56.2", "0.58.0")) == "0.56.2"
    assert select_version("0.56.0", ("0.56.2",)) == "0.56.2"


def test_each_shipped_schema_is_picked_by_the_release_it_describes() -> None:
    """ADR-0012 §Support window: latest + previous ship, and each loads for its own release."""
    shipped = available_versions(SCHEMA_DIR)

    assert len(shipped) == 2
    for running in shipped:
        assert select_version(running, shipped) == running
        assert load_schema(running, SCHEMA_DIR).hyprland_version == running


def test_a_hyprland_without_a_lua_config_has_no_schema() -> None:
    """Below 0.56 there is no `hl.*` API to write for: no Schema describes it."""
    with pytest.raises(ValueError, match=r"older than 0\.56,"):
        select_version("0.55.9", ("0.56.2", "0.58.0"))
    assert below_lua_floor("0.55.9")
    assert not below_lua_floor("0.56.0")


def test_load_schema_degrades_between_two_shipped_schemas(tmp_path: Path) -> None:
    directory = synthetic_schema_dir(tmp_path, "0.56.2", "0.58.0")

    assert load_schema("0.57.1", directory).hyprland_version == "0.56.2"
    assert load_schema("0.56.0", directory).hyprland_version == "0.56.2"
    assert load_schema(None, directory).hyprland_version == "0.58.0"


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


def test_added_in_survives_the_round_trip_and_is_omitted_when_absent() -> None:
    schema = generated_module.GeneratedSchema(
        hyprland_version="0.58.0",
        options=(
            option("general:border_size", added_in=None),
            option("general:new_thing", order=1, added_in="0.58.0"),
        ),
        provenance={},
    )
    text = generated_module.dumps(schema)

    assert generated_module.loads(text) == schema
    assert text.count('"added_in"') == 1
    assert '"added_in": "0.58.0"' in text


def test_resolution_carries_added_in_from_the_generated_record() -> None:
    assert resolve_option(option(added_in="0.58.0"), None, None).added_in == "0.58.0"
    assert resolve_option(option(), None, None).added_in is None


def _schema(version: str, *records: GeneratedOption) -> generated_module.GeneratedSchema:
    return generated_module.GeneratedSchema(
        hyprland_version=version, options=records, provenance={}
    )


def test_an_option_the_predecessor_lacks_is_stamped_with_the_new_version() -> None:
    old = _schema("0.56.2", option("general:gaps_in", order=0))
    new = _schema("0.58.0", option("general:gaps_in", order=0), option("misc:fresh", order=1))

    stamped = stamp_added_in(new, old)

    assert {o.name: o.added_in for o in stamped.options} == {
        "general:gaps_in": None,
        "misc:fresh": "0.58.0",
    }
    assert stamped.hyprland_version == "0.58.0"


def test_a_stamp_carries_forward_until_the_option_is_curated() -> None:
    """The predecessor's stamp survives: ADR-0012's "New in" group outlives one release."""
    old = _schema("0.58.0", option("misc:fresh", added_in="0.58.0"))
    new = _schema("0.59.0", option("misc:fresh"), option("misc:newer", order=1))

    stamped = stamp_added_in(new, old)

    assert {o.name: o.added_in for o in stamped.options} == {
        "misc:fresh": "0.58.0",
        "misc:newer": "0.59.0",
    }


def test_no_predecessor_means_no_stamps() -> None:
    new = _schema("0.56.2", option("general:gaps_in"))

    assert stamp_added_in(new, None) is new
    assert all(o.added_in is None for o in new.options)


def test_stamping_records_the_predecessor_in_provenance() -> None:
    old = _schema("0.56.2", option("general:gaps_in"))
    new = _schema("0.58.0", option("general:gaps_in"))

    assert stamp_added_in(new, old).provenance["predecessor"] == "0.56.2"
    assert "predecessor" not in stamp_added_in(new, None).provenance


def test_stamping_keeps_the_animation_leaves_block() -> None:
    old = _schema("0.56.2", option("general:gaps_in"))
    new = replace(
        _schema("0.58.0", option("general:gaps_in")), animation_leaves=("fade", "global")
    )

    assert stamp_added_in(new, old).animation_leaves == ("fade", "global")


def test_a_predecessor_that_is_not_older_is_rejected() -> None:
    same = _schema("0.58.0", option("general:gaps_in"))

    with pytest.raises(ValueError, match="not older"):
        stamp_added_in(same, same)


def test_the_animation_leaves_block_round_trips_and_sits_beside_provenance() -> None:
    schema = generated_module.GeneratedSchema(
        hyprland_version="0.56.2",
        options=(option(),),
        provenance={},
        animation_leaves=("fade", "global"),
    )
    text = generated_module.dumps(schema)

    assert json.loads(text)["animation_leaves"] == ["fade", "global"]
    assert generated_module.loads(text) == schema


def test_a_schema_without_the_block_serialises_without_one() -> None:
    """The shipped 0.56.1-style file has no block and must stay byte-for-byte as it is."""
    schema = generated_module.GeneratedSchema(
        hyprland_version="0.56.2", options=(option(),), provenance={}
    )
    text = generated_module.dumps(schema)

    assert "animation_leaves" not in json.loads(text)
    assert generated_module.loads(text).animation_leaves is None


@pytest.mark.parametrize("block", [[], "fade", ["fade", "fade"], ["fade", 3], [""]])
def test_a_malformed_animation_leaves_block_is_refused_at_load(block: object) -> None:
    text = generated_module.dumps(
        generated_module.GeneratedSchema(
            hyprland_version="0.56.2", options=(option(),), provenance={}
        )
    )
    payload = json.loads(text)
    payload["animation_leaves"] = block

    with pytest.raises(ValueError, match="animation_leaves"):
        generated_module.loads(json.dumps(payload))


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


def overlay_with_groups(groups: object, **options: dict[str, object]) -> str:
    """An Overlay whose Section `a` declares `groups`; option names use `__` for `:`."""
    return json.dumps(
        {
            "format_version": 1,
            "sections": {"a": {"title": "A", "groups": groups}},
            "options": {name.replace("__", ":"): entry for name, entry in options.items()},
        }
    )


def test_a_section_reads_its_groups_in_the_order_the_curator_wrote_them() -> None:
    text = overlay_with_groups(
        [{"title": "Typing", "description": "How keys repeat."}, {"title": "Layout"}],
        a__b={"group": "Layout", "order": 1},
    )

    section = overlay_module.loads(text).sections["a"]

    assert section.groups == (
        OverlayGroup(title="Typing", description="How keys repeat."),
        OverlayGroup(title="Layout", description=None),
    )


def test_an_unknown_field_inside_a_group_is_rejected() -> None:
    text = overlay_with_groups([{"title": "Typing", "descripton": "typo"}])
    with pytest.raises(ValueError, match="unknown overlay field"):
        overlay_module.loads(text)


def test_two_groups_with_one_title_in_a_section_are_rejected() -> None:
    text = overlay_with_groups([{"title": "Typing"}, {"title": "Typing"}])
    with pytest.raises(ValueError, match="duplicate group 'Typing'"):
        overlay_module.loads(text)


def test_an_option_naming_a_group_its_section_does_not_declare_is_rejected() -> None:
    """A misspelt group would otherwise render as a stray heading of its own."""
    text = overlay_with_groups([{"title": "Typing"}], a__b={"group": "Typng"})
    with pytest.raises(ValueError, match=r"'a:b'.*'Typng'"):
        overlay_module.loads(text)


def test_an_option_may_name_a_group_only_of_its_own_section() -> None:
    text = json.dumps(
        {
            "format_version": 1,
            "sections": {"a": {"groups": [{"title": "Typing"}]}, "c": {}},
            "options": {"c:d": {"group": "Typing"}},
        }
    )
    with pytest.raises(ValueError, match=r"'c:d'.*'Typing'"):
        overlay_module.loads(text)


# --- the Schema container --------------------------------------------------------------


def test_the_resolved_schema_carries_the_animation_leaves() -> None:
    generated = generated_module.GeneratedSchema(
        hyprland_version="0.56.2",
        options=(option(),),
        provenance={},
        animation_leaves=("fade", "global"),
    )

    assert Schema.merge(generated, Overlay(sections={}, options={})).animation_leaves == (
        "fade",
        "global",
    )
    bare = generated_module.GeneratedSchema(
        hyprland_version="0.56.2", options=(option(),), provenance={}
    )
    assert Schema.merge(bare, Overlay(sections={}, options={})).animation_leaves is None


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


def test_load_schema_reads_the_shipped_files() -> None:
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


def test_a_rename_reaches_the_resolved_option() -> None:
    """ADR-0012: a retired value restores under the new name, and the Session holds a Schema,
    not the Overlay -- so the old name has to survive resolution."""
    schema = Schema.merge(
        generated_module.GeneratedSchema(
            hyprland_version="0.57.0",
            options=(option("general:border_width", order=0), option("general:layout")),
            provenance={},
        ),
        Overlay(
            sections={},
            options={"general:border_width": OverlayEntry(renamed_from="general:border_size")},
        ),
    )

    assert schema["general:border_width"].renamed_from == "general:border_size"
    assert schema["general:layout"].renamed_from is None
