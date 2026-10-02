"""The font-weight Rows offer exactly the names Hyprland knows, in weight order (#194).

The Overlay's `labels` give each name its display text; `FONT_WEIGHT_NAMES` gives it its
weight, and `tests/static/test_font_weight_names.py` proves Hyprland accepts each one. A name
in one table and not the other is a choice the Row cannot match to a weight, or a weight it
never offers.
"""

from __future__ import annotations

import pytest
from _support import SCHEMA_DIR

from hyprtweaker.engine.model import FONT_WEIGHT_NAMES
from hyprtweaker.engine.schema import OptionType, available_versions, load_schema


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_every_font_weight_setting_labels_hyprlands_names_in_weight_order(version: str) -> None:
    schema = load_schema(version, SCHEMA_DIR)
    settings = [option for option in schema if option.type is OptionType.FONT_WEIGHT]

    assert [option.name for option in settings] == [
        "group:groupbar:font_weight_active",
        "group:groupbar:font_weight_inactive",
    ]
    for option in settings:
        assert list(option.labels or {}) == list(FONT_WEIGHT_NAMES), option.name
