"""The workspace-rule field catalog against the Importer's tables -- the drift guard (#160).

The catalog answers "which fields can the editor offer"; `importer/rules.py` answers "how
do I read a legacy `workspace = ...` line". Both enumerate the 16 fields of
`hl.workspace_rule` (`lua-api-surface.md` §9), and these tests keep one from growing
a field the other lacks. A subset, not equality: `float_gaps` and `enabled` have no
legacy spelling, so the Importer names 14 of the 16.
"""

from __future__ import annotations

from hyprtweaker.engine import workspace_catalog as catalog
from hyprtweaker.engine.importer import rules as importer_rules
from hyprtweaker.engine.workspace_catalog import WorkspaceFieldType as T


def _names(kind: T) -> set[str]:
    return {field.name for field in catalog.WORKSPACE_FIELDS if field.type is kind}


def test_the_sixteen_published_fields_are_all_here() -> None:
    assert [field.name for field in catalog.WORKSPACE_FIELDS] == [
        "monitor",
        "default",
        "persistent",
        "default_name",
        "on_created_empty",
        "enabled",
        "gaps_in",
        "gaps_out",
        "float_gaps",
        "border_size",
        "no_border",
        "no_rounding",
        "no_shadow",
        "decorate",
        "animation",
        "layout",
    ]


def test_every_field_the_importer_produces_is_in_the_catalog() -> None:
    produced = set(importer_rules._WORKSPACE_FIELDS.values()) | set(
        importer_rules._WORKSPACE_INVERTED.values()
    )

    assert produced <= {field.name for field in catalog.WORKSPACE_FIELDS}
    assert {field.name for field in catalog.WORKSPACE_FIELDS} - produced == {
        "float_gaps",
        "enabled",
    }


def test_the_importers_bool_fields_are_bools_here() -> None:
    importer_bools = {
        importer_rules._WORKSPACE_FIELDS[k] for k in importer_rules._WORKSPACE_BOOL
    }
    importer_bools |= set(importer_rules._WORKSPACE_INVERTED.values())

    assert importer_bools <= _names(T.BOOL)


def test_the_gap_fields_are_the_ones_the_importer_expands_to_four_sides() -> None:
    assert {"gaps_in", "gaps_out"} <= _names(T.GAPS)
    assert _names(T.GAPS) == {"gaps_in", "gaps_out", "float_gaps"}


def test_no_field_is_listed_twice_and_each_sits_on_a_known_shelf() -> None:
    names = [field.name for field in catalog.WORKSPACE_FIELDS]

    assert len(names) == len(set(names))
    assert {field.shelf for field in catalog.WORKSPACE_FIELDS} <= set(catalog.SHELVES)
    assert all(
        any(field.shelf == shelf for field in catalog.WORKSPACE_FIELDS)
        for shelf in catalog.SHELVES
    )


def test_a_field_added_from_the_picker_starts_at_the_value_its_user_came_for() -> None:
    starts = {field.name: field.starts_at for field in catalog.WORKSPACE_FIELDS}

    assert starts["persistent"] is True  # nobody adds "keep alive" to switch it off
    assert starts["decorate"] is False  # decorations are on already
    assert starts["gaps_in"] == 0
    assert starts["monitor"] == ""


def test_find_field_answers_by_lua_key_and_knows_layout_opts_is_not_a_field() -> None:
    assert catalog.find_field("float_gaps") is not None
    assert catalog.find_field("layout_opts") is None
    assert catalog.find_field("gapsout") is None  # a legacy spelling: raw, never dropped


def test_text_typed_over_a_held_value_keeps_the_type_the_value_had() -> None:
    assert catalog.retype_like(5, "6") == 6
    assert catalog.retype_like(True, "false") is False
    assert catalog.retype_like(0.5, "0.25") == 0.25
    assert catalog.retype_like("top", " bottom ") == "bottom"


def test_text_that_no_longer_reads_as_the_held_type_stays_text() -> None:
    assert catalog.retype_like(5, "five") == "five"
    assert catalog.retype_like(True, "maybe") == "maybe"
