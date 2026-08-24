"""The rules catalog against the Importer's typed tables -- the drift guard (#67).

The catalog answers "what can the user add"; `importer/rules.py` answers "how do I coerce
this legacy string". Both enumerate the same published surface (`lua-api-surface.md` §5),
and these tests are what keeps an effect added to one from silently missing the other.
"""

from __future__ import annotations

from hyprtweaker.engine import rules_catalog as catalog
from hyprtweaker.engine.importer import rules as importer_rules


def _catalog_names(effects, type_):  # type: ignore[no-untyped-def]
    return {effect.name for effect in effects if effect.type is type_}


class TestWindowEffectsAgreeWithTheImporter:
    def test_bool_effects(self) -> None:
        assert _catalog_names(catalog.WINDOW_EFFECTS, catalog.EffectType.BOOL) == set(
            importer_rules._BOOL_EFFECTS
        )

    def test_int_effects(self) -> None:
        assert _catalog_names(catalog.WINDOW_EFFECTS, catalog.EffectType.INT) == set(
            importer_rules._INT_EFFECTS
        )

    def test_float_effects(self) -> None:
        assert _catalog_names(catalog.WINDOW_EFFECTS, catalog.EffectType.FLOAT) == set(
            importer_rules._FLOAT_EFFECTS
        )

    def test_string_effects(self) -> None:
        assert _catalog_names(catalog.WINDOW_EFFECTS, catalog.EffectType.STRING) == set(
            importer_rules._STRING_EFFECTS
        )

    def test_the_57_static_effects_are_all_here(self) -> None:
        """The published count (`lua-api-surface.md` §5, ADR-0008)."""
        assert len(catalog.WINDOW_EFFECTS) == 57


class TestMatchPropsAgreeWithTheImporter:
    def test_bool_match_props(self) -> None:
        bools = {
            prop.name
            for prop in catalog.WINDOW_MATCH_PROPS
            if prop.kind is catalog.MatchKind.BOOL
        }
        assert bools == set(importer_rules._BOOL_MATCH)

    def test_int_match_props(self) -> None:
        ints = {
            prop.name
            for prop in catalog.WINDOW_MATCH_PROPS
            if prop.kind is catalog.MatchKind.INT
        }
        assert ints == set(importer_rules._INT_MATCH)

    def test_the_18_match_props_are_all_here(self) -> None:
        """17 window props plus the layer-only `namespace` (ADR-0008)."""
        assert len(catalog.WINDOW_MATCH_PROPS) + len(catalog.LAYER_MATCH_PROPS) == 18

    def test_no_duplicates(self) -> None:
        names = [prop.name for prop in catalog.WINDOW_MATCH_PROPS]
        assert len(names) == len(set(names))


class TestLayerEffectsAgreeWithTheImporter:
    def test_bool_effects(self) -> None:
        assert _catalog_names(catalog.LAYER_EFFECTS, catalog.EffectType.BOOL) == set(
            importer_rules._LAYER_BOOL
        )

    def test_int_effects(self) -> None:
        assert _catalog_names(catalog.LAYER_EFFECTS, catalog.EffectType.INT) == set(
            importer_rules._LAYER_INT
        )

    def test_the_10_layer_effects_are_all_here(self) -> None:
        assert len(catalog.LAYER_EFFECTS) == 10


class TestShape:
    def test_every_effect_has_a_known_category(self) -> None:
        for effect in (*catalog.WINDOW_EFFECTS, *catalog.LAYER_EFFECTS):
            assert effect.category in catalog.CATEGORIES

    def test_no_duplicate_effect_names_per_kind(self) -> None:
        for effects in (catalog.WINDOW_EFFECTS, catalog.LAYER_EFFECTS):
            names = [effect.name for effect in effects]
            assert len(names) == len(set(names))

    def test_lookup_answers_and_declines(self) -> None:
        found = catalog.find_effect("window", "rounding")
        assert found is not None and found.type is catalog.EffectType.INT
        assert catalog.find_effect("window", "plugin:whatever") is None
        prop = catalog.find_match_prop("layer", "namespace")
        assert prop is not None and prop.kind is catalog.MatchKind.REGEX

    def test_negatable_kinds_are_the_string_valued_ones(self) -> None:
        assert catalog.MatchKind.REGEX in catalog.NEGATABLE_KINDS
        assert catalog.MatchKind.BOOL not in catalog.NEGATABLE_KINDS

    def test_an_unknown_kind_raises_everywhere_the_same(self) -> None:
        """A typo answering with the window surface would be a picker quietly offering
        props the other kind rejects -- so both raise, matching `Session.rules`."""
        import pytest

        with pytest.raises(ValueError, match="unknown rule kind"):
            catalog.match_props("monitor")
        with pytest.raises(ValueError, match="unknown rule kind"):
            catalog.effects("monitor")
