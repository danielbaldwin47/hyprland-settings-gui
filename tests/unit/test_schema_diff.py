"""The release check's schema diff: what changed between two Generated schemas.

Layer 1 of `docs/agents/hyprland-release-check.md` step 2. The reviewer reads the output, so
each class is pinned with a pair of schemas that differ in exactly that way, and the one
rule the diff shares with the generator (what counts as *added*) is pinned against
`stamp_added_in` itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from _support import ROOT, SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.schema import (
    GeneratedOption,
    GeneratedSchema,
    GetOptionKey,
    OptionType,
    Overlay,
    OverlayEntry,
    Vec2Range,
    Widget,
    stamp_added_in,
)
from hyprtweaker.engine.schema import generated as generated_module
from hyprtweaker.engine.schema.diff import diff_schemas, dumps

TOOL = ROOT / "tools" / "diff_schema.py"


def option(name: str = "general:border_size", **fields: object) -> GeneratedOption:
    defaults: dict[str, object] = {
        "name": name,
        "lua_key": name.replace(":", "."),
        "section": name.split(":", 1)[0],
        "path": tuple(name.replace(":", ".").split(".")),
        "order": 0,
        "type": OptionType.INT,
        "widget": Widget.INT_RANGE,
        "description": f"description of {name}",
        "default": 1,
        "default_raw": 1,
        "sentinel_default": False,
        "getoption_key": GetOptionKey.INT,
    }
    defaults.update(fields)
    return GeneratedOption(**defaults)  # type: ignore[arg-type]


def schema(version: str, *options: GeneratedOption) -> GeneratedSchema:
    numbered = tuple(replace(item, order=index) for index, item in enumerate(options))
    return GeneratedSchema(hyprland_version=version, options=numbered, provenance={})


OLD = "0.56.1"
NEW = "0.56.2"


def test_an_option_only_the_new_schema_has_is_added() -> None:
    before = schema(OLD, option("general:gaps_in"))
    after = schema(NEW, option("general:gaps_in"), option("general:gaps_out"))

    diff = diff_schemas(after, before)

    assert diff.added == ("general:gaps_out",)
    assert diff.removed == ()


def test_an_option_only_the_old_schema_has_is_removed() -> None:
    before = schema(OLD, option("general:gaps_in"), option("general:gaps_out"))
    after = schema(NEW, option("general:gaps_in"))

    diff = diff_schemas(after, before)

    assert diff.removed == ("general:gaps_out",)
    assert diff.added == ()


def test_added_is_the_rule_the_generator_stamps_by() -> None:
    before = schema(OLD, option("general:gaps_in"), option("general:old"))
    after = schema(NEW, option("general:gaps_in"), option("general:new_b"), option("a:new_a"))

    stamped = {
        item.name for item in stamp_added_in(after, before).options if item.added_in == NEW
    }

    assert set(diff_schemas(after, before).added) == stamped == {"general:new_b", "a:new_a"}


def test_a_curated_rename_is_renamed_and_in_neither_added_nor_removed() -> None:
    before = schema(OLD, option("general:old_name"))
    after = schema(NEW, option("general:new_name"))
    overlay = Overlay(
        sections={}, options={"general:new_name": OverlayEntry(renamed_from="general:old_name")}
    )

    diff = diff_schemas(after, before, overlay)

    assert [(pair.old, pair.new) for pair in diff.renamed] == [
        ("general:old_name", "general:new_name")
    ]
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.rename_candidates == ()


def test_renamed_from_naming_an_option_the_new_schema_still_has_is_not_a_rename() -> None:
    before = schema(OLD, option("general:keeps"), option("general:other"))
    after = schema(NEW, option("general:keeps"), option("general:other"))
    overlay = Overlay(
        sections={}, options={"general:other": OverlayEntry(renamed_from="general:keeps")}
    )

    assert diff_schemas(after, before, overlay).renamed == ()


def test_a_removed_and_added_pair_with_one_description_and_default_is_a_candidate() -> None:
    before = schema(OLD, option("general:old_name", description="same words", default=3))
    after = schema(NEW, option("general:new_name", description="same words", default=3))

    diff = diff_schemas(after, before)

    assert [(pair.old, pair.new) for pair in diff.rename_candidates] == [
        ("general:old_name", "general:new_name")
    ]
    # The tool cannot know it is a rename, so the pair is still a removal and an addition.
    assert diff.renamed == ()
    assert diff.removed == ("general:old_name",)
    assert diff.added == ("general:new_name",)


@pytest.mark.parametrize(
    ("old_fields", "new_fields"),
    [
        ({"description": "one", "default": 3}, {"description": "two", "default": 3}),
        (
            {"description": "one", "default": 3, "default_raw": 3},
            {"description": "one", "default": 4, "default_raw": 4},
        ),
        ({"description": "", "default": 3}, {"description": "", "default": 3}),
    ],
    ids=["other description", "other default", "no description to match on"],
)
def test_a_pair_that_differs_in_description_or_default_is_not_a_candidate(
    old_fields: dict[str, object], new_fields: dict[str, object]
) -> None:
    before = schema(OLD, option("general:old_name", **old_fields))
    after = schema(NEW, option("general:new_name", **new_fields))

    assert diff_schemas(after, before).rename_candidates == ()


def test_a_pair_the_overlay_already_maps_is_not_also_a_candidate() -> None:
    before = schema(OLD, option("general:old_name", description="same", default=3))
    after = schema(NEW, option("general:new_name", description="same", default=3))
    overlay = Overlay(
        sections={}, options={"general:new_name": OverlayEntry(renamed_from="general:old_name")}
    )

    assert diff_schemas(after, before, overlay).rename_candidates == ()


def test_a_changed_type_is_retyped() -> None:
    before = schema(OLD, option("misc:x", type=OptionType.INT))
    after = schema(NEW, option("misc:x", type=OptionType.FLOAT))

    changes = diff_schemas(after, before).retyped

    assert [(c.name, c.before, c.after) for c in changes] == [("misc:x", "int", "float")]


def test_a_changed_bound_is_a_range_change() -> None:
    before = schema(OLD, option("misc:x", min=0, max=10))
    after = schema(NEW, option("misc:x", min=0, max=20))

    changes = diff_schemas(after, before).range_changed

    assert [(c.name, c.before, c.after) for c in changes] == [
        (
            "misc:x",
            {"min": 0, "max": 10, "vec2_range": None},
            {"min": 0, "max": 20, "vec2_range": None},
        )
    ]


def test_a_changed_vec2_range_is_a_range_change() -> None:
    before = schema(OLD, option("misc:v", vec2_range=Vec2Range(0, 0, 1, 1)))
    after = schema(NEW, option("misc:v", vec2_range=Vec2Range(0, 0, 1, 2)))

    (change,) = diff_schemas(after, before).range_changed

    assert (change.before["vec2_range"], change.after["vec2_range"]) == (
        [0.0, 0.0, 1.0, 1.0],
        [0.0, 0.0, 1.0, 2.0],
    )


def test_a_changed_enum_map_is_an_enum_map_change() -> None:
    before = schema(OLD, option("misc:mode", map={"a": 0, "b": 1}))
    after = schema(NEW, option("misc:mode", map={"a": 0, "b": 1, "c": 2}))

    (change,) = diff_schemas(after, before).enum_map_changed

    assert change.name == "misc:mode"
    assert change.before == {"map": {"a": 0, "b": 1}, "choices": None}
    assert change.after == {"map": {"a": 0, "b": 1, "c": 2}, "choices": None}


def test_a_changed_choice_list_is_an_enum_map_change() -> None:
    before = schema(OLD, option("misc:pick", choices=("x", "y")))
    after = schema(NEW, option("misc:pick", choices=("x", "y", "z")))

    (change,) = diff_schemas(after, before).enum_map_changed

    assert change.after == {"map": None, "choices": ["x", "y", "z"]}


def test_a_changed_default_is_a_default_change() -> None:
    before = schema(OLD, option("misc:x", default=1, default_raw=1))
    after = schema(NEW, option("misc:x", default=2, default_raw=2))

    changes = diff_schemas(after, before).default_changed

    assert [(c.name, c.before, c.after) for c in changes] == [("misc:x", 1, 2)]


def test_a_default_that_becomes_a_sentinel_is_a_default_change() -> None:
    before = schema(OLD, option("misc:x", default=0, default_raw=0))
    after = schema(
        NEW, option("misc:x", default=None, default_raw="[[EMPTY]]", sentinel_default=True)
    )

    (change,) = diff_schemas(after, before).default_changed

    assert (change.before, change.after) == (0, "[[EMPTY]]")


def test_a_renamed_option_that_also_changed_is_still_compared() -> None:
    before = schema(OLD, option("general:old_name", type=OptionType.INT))
    after = schema(NEW, option("general:new_name", type=OptionType.FLOAT))
    overlay = Overlay(
        sections={}, options={"general:new_name": OverlayEntry(renamed_from="general:old_name")}
    )

    (change,) = diff_schemas(after, before, overlay).retyped

    assert change.name == "general:new_name"


def test_an_unchanged_option_appears_in_no_class() -> None:
    both = [option("general:gaps_in"), option("misc:x", min=0, max=5, map={"a": 0})]

    diff = diff_schemas(schema(NEW, *both), schema(OLD, *both))

    assert diff.counts == {
        "added": 0,
        "removed": 0,
        "renamed": 0,
        "rename_candidates": 0,
        "retyped": 0,
        "range_changed": 0,
        "enum_map_changed": 0,
        "default_changed": 0,
        "new_sections": 0,
    }


def test_a_new_section_and_a_new_subsection_are_reported_once_each() -> None:
    before = schema(OLD, option("general:gaps_in"), option("decoration:shadow:range"))
    after = schema(
        NEW,
        option("general:gaps_in"),
        option("decoration:shadow:range"),
        option("decoration:shadow:power"),
        option("general:snap:enabled"),
        option("cursor:hide_on_key_press"),
        option("cursor:inactive_timeout"),
    )

    assert diff_schemas(after, before).new_sections == ("cursor", "general:snap")


def test_no_predecessor_is_an_empty_diff() -> None:
    diff = diff_schemas(schema(NEW, option("general:gaps_in")), None)

    assert diff.predecessor is None
    assert sum(diff.counts.values()) == 0


def test_the_same_inputs_give_byte_identical_output_whatever_their_order() -> None:
    before = schema(OLD, option("a:one"), option("b:two", default=1, default_raw=1))
    after = schema(NEW, option("b:two", default=2, default_raw=2), option("c:three"))
    shuffled = schema(NEW, option("c:three"), option("b:two", default=2, default_raw=2))

    first = dumps(diff_schemas(after, before))

    assert first == dumps(diff_schemas(after, before))
    assert first == dumps(diff_schemas(shuffled, before))
    assert first.endswith("}\n")
    payload = json.loads(first)
    assert payload["hyprland_version"] == NEW
    assert payload["predecessor"] == OLD
    assert payload["counts"]["added"] == 1
    assert payload["added"] == ["c:three"]
    assert payload["removed"] == ["a:one"]
    assert payload["default_changed"] == [{"name": "b:two", "before": 1, "after": 2}]


# --- the tool, over the shipped schema and a synthetic predecessor ----------------------


def synthetic_predecessor() -> GeneratedSchema:
    """0.56.2 as a hypothetical 0.56.1: three Options not yet added, one since removed, one
    retyped, one with another default, and one under its old name (a rename candidate)."""
    shipped = generated_module.load(SCHEMA_DIR / f"hyprland-{SAMPLE_VERSION}.json")
    kept = list(shipped.options)
    by_name = {item.name: index for index, item in enumerate(kept)}

    def edit(target: str, **fields: object) -> None:
        kept[by_name[target]] = replace(kept[by_name[target]], **fields)

    edit("general:border_size", type=OptionType.FLOAT, default=2, default_raw=2)
    edit("general:gaps_in", name="general:gaps_inside", lua_key="general.gaps_inside")
    gone = ("general:gaps_out", "misc:font_family", "decoration:rounding")
    options = [item for item in kept if item.name not in gone]
    options.append(
        replace(shipped.options[0], name="general:long_gone", lua_key="general.gone")
    )
    return replace(
        shipped,
        hyprland_version="0.56.1",
        options=tuple(replace(item, order=index) for index, item in enumerate(options)),
    )


def run_tool(tmp_path: Path, predecessor: Path | None, overlay: Path | None = None) -> Path:
    out = tmp_path / f"hyprland-{SAMPLE_VERSION}.diff.json"
    command = [
        sys.executable,
        str(TOOL),
        str(SCHEMA_DIR / f"hyprland-{SAMPLE_VERSION}.json"),
        "-o",
        str(out),
    ]
    if predecessor is not None:
        command += ["--predecessor", str(predecessor)]
    if overlay is not None:
        command += ["--overlay", str(overlay)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return out


def test_the_tool_classifies_the_shipped_schema_against_a_synthetic_predecessor(
    tmp_path: Path,
) -> None:
    predecessor = tmp_path / "hyprland-0.56.1.json"
    predecessor.write_text(generated_module.dumps(synthetic_predecessor()), encoding="utf-8")
    out = run_tool(tmp_path, predecessor)

    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["hyprland_version"] == SAMPLE_VERSION
    assert payload["predecessor"] == "0.56.1"
    assert payload["added"] == [
        "decoration:rounding",
        "general:gaps_in",
        "general:gaps_out",
        "misc:font_family",
    ]
    assert payload["removed"] == ["general:gaps_inside", "general:long_gone"]
    assert [(c["name"], c["before"], c["after"]) for c in payload["retyped"]] == [
        ("general:border_size", "float", "int")
    ]
    assert payload["default_changed"][0]["name"] == "general:border_size"
    assert payload["rename_candidates"] == [
        {"from": "general:gaps_inside", "to": "general:gaps_in"}
    ]
    # Run twice: the same inputs make the same bytes.
    assert run_tool(tmp_path, predecessor).read_bytes() == out.read_bytes()


def test_the_tool_without_a_predecessor_writes_an_empty_diff(tmp_path: Path) -> None:
    out = run_tool(tmp_path, None)

    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["predecessor"] is None
    assert sum(payload["counts"].values()) == 0


def test_the_tool_with_a_predecessor_path_that_does_not_exist_writes_an_empty_diff(
    tmp_path: Path,
) -> None:
    out = run_tool(tmp_path, tmp_path / "hyprland-9.9.9.json")

    assert sum(json.loads(out.read_text(encoding="utf-8"))["counts"].values()) == 0
