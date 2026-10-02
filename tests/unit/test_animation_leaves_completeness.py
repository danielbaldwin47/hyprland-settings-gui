"""Every shipped schema's animation tree resolves in the catalogue, or the build fails (#121).

The Generated schema carries the animation tree's leaf names so a release's leaves are
offered without a hand edit. What the catalogue then has to be able to do with them is
checked here, per shipped version: serve them to the Page, find nothing wrong with any of
them, and keep the compositor's internal nodes out. A schema without the block resolves to
the shipped list instead, and that path is held to the same rules.
"""

from __future__ import annotations

import re

import pytest
from _support import SCHEMA_DIR

from hyprtweaker.engine.entities_catalog import (
    SHIPPED_ANIMATION_LEAVES,
    animation_findings,
    animation_leaves,
)
from hyprtweaker.engine.model.entities import Animation
from hyprtweaker.engine.schema import generated as generated_module
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.schema.resolve import available_versions
from hyprtweaker.ui.pages.declaration_kinds import BY_KIND

VERSIONS = available_versions(SCHEMA_DIR)

LEAF_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*")
"""What a leaf looks like in `hl.animation{leaf = ...}`; `__internal_*` nodes do not."""


@pytest.mark.parametrize("version", VERSIONS)
def test_the_catalogue_resolves_every_leaf_the_schema_records(version: str) -> None:
    schema = load_schema(version, SCHEMA_DIR)
    leaves = animation_leaves(schema)

    assert leaves, f"hyprland-{version}.json resolves to no animation leaves"
    assert len(set(leaves)) == len(leaves)
    for leaf in leaves:
        assert LEAF_NAME.fullmatch(leaf), f"{leaf!r} is not a name config can use"
        assert animation_findings(Animation(leaf, {"enabled": False}), leaves) == (), leaf


@pytest.mark.parametrize("version", VERSIONS)
def test_the_page_offers_exactly_what_the_catalogue_resolves(version: str) -> None:
    schema = load_schema(version, SCHEMA_DIR)

    offered = BY_KIND["animations"].choices_from(schema)["leaf"]

    assert offered == animation_leaves(schema)


def test_the_0_56_2_schema_records_the_same_34_leaves_the_app_shipped_by_hand() -> None:
    """Parity before the switch: the generated block is the hand list, order aside."""
    schema = generated_module.load(SCHEMA_DIR / "hyprland-0.56.2.json")

    assert schema.animation_leaves is not None
    assert len(schema.animation_leaves) == 34
    assert set(schema.animation_leaves) == set(SHIPPED_ANIMATION_LEAVES)
