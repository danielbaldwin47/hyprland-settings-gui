"""Every corpus rule, through the writer and back (ADR-0008, #67).

The corpus carries hundreds of real window and layer rules -- named block rules, regexes
full of escapes, `negative:` matches, expression-valued sizes -- and is the only place the
combinations nobody writes into a fixture occur. Three properties, mirroring the binds
corpus test: everything renders, it is valid Lua, and it survives the round-trip in order
-- which is what both "drag-reorder persists" and "hand edits reflect in the app" rest on.
"""

from __future__ import annotations

import pytest
from _support import CORPUS_DIR

from hyprtweaker.engine.importer import import_config
from hyprtweaker.engine.importer.lua import lua_binary
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import syntax
from hyprtweaker.engine.writer.rules import (
    parse_rules_module,
    render_layer_rules_module,
    render_window_rules_module,
)

RICES = ("end-4", "hyde", "hyprland-default", "hyprv", "jakoolit", "local", "ml4w")

CORPUS_ENV = {
    "HOME": "/home/tester",
    "XDG_CONFIG_HOME": "/home/tester/.config",
    "XDG_DATA_HOME": "/home/tester/.local/share",
    "XDG_STATE_HOME": "/home/tester/.local/state",
    "XDG_CACHE_HOME": "/home/tester/.cache",
}

VERSION = "0.1.0"


def _entry(rice: str):  # type: ignore[no-untyped-def]
    return CORPUS_DIR / rice / "hyprland.conf"


pytestmark = pytest.mark.skipif(not _entry("end-4").is_file(), reason="corpus not checked out")


def _shape(rule):  # type: ignore[no-untyped-def]
    return (dict(rule.match), dict(rule.effects), rule.name, rule.enabled)


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema("0.56.2")


@pytest.fixture(scope="module")
def rendered(schema):  # type: ignore[no-untyped-def]
    """Each rice imported once, and its rule Modules rendered once."""
    out = {}
    for rice in RICES:
        if not _entry(rice).is_file():
            continue
        result = import_config(_entry(rice), schema, env=CORPUS_ENV)
        out[rice] = (
            result.entities,
            render_window_rules_module(result.entities.window_rules, app_version=VERSION),
            render_layer_rules_module(result.entities.layer_rules, app_version=VERSION),
        )
    return out


@pytest.mark.parametrize("rice", RICES)
class TestCorpusRules:
    def test_every_rule_renders(self, rendered, rice) -> None:  # type: ignore[no-untyped-def]
        if rice not in rendered:
            pytest.skip(f"{rice} not checked out")
        entities, window_text, layer_text = rendered[rice]
        if entities.window_rules:
            assert window_text is not None
            assert window_text.count("hl.window_rule(") == len(entities.window_rules)
        if entities.layer_rules:
            assert layer_text is not None
            assert layer_text.count("hl.layer_rule(") == len(entities.layer_rules)

    def test_rendered_modules_are_valid_lua(self, rendered, rice) -> None:  # type: ignore[no-untyped-def]
        if rice not in rendered:
            pytest.skip(f"{rice} not checked out")
        if not syntax.gate_available():
            pytest.skip("no luac to gate with")
        _, window_text, layer_text = rendered[rice]
        if window_text is not None:
            syntax.gate(window_text, "window_rules.lua")
        if layer_text is not None:
            syntax.gate(layer_text, "layer_rules.lua")

    def test_round_trip_preserves_rules_in_order(self, rendered, rice) -> None:  # type: ignore[no-untyped-def]
        if rice not in rendered:
            pytest.skip(f"{rice} not checked out")
        if lua_binary() is None:
            pytest.skip("no Lua interpreter")
        entities, window_text, layer_text = rendered[rice]
        if window_text is not None:
            parsed = parse_rules_module(window_text)
            assert parsed.ok, parsed.errors
            assert [_shape(r) for r in parsed.window_rules] == [
                _shape(r) for r in entities.window_rules
            ]
        if layer_text is not None:
            parsed = parse_rules_module(layer_text, module="layer_rules.lua")
            assert parsed.ok, parsed.errors
            assert [_shape(r) for r in parsed.layer_rules] == [
                _shape(r) for r in entities.layer_rules
            ]


def test_corpus_has_window_rules(rendered) -> None:  # type: ignore[no-untyped-def]
    """Guards the guard: an empty corpus would pass everything while proving nothing."""
    assert any(entities.window_rules for entities, _, _ in rendered.values())


def test_corpus_has_layer_rules(rendered) -> None:  # type: ignore[no-untyped-def]
    assert any(entities.layer_rules for entities, _, _ in rendered.values())


def test_corpus_import_is_named_first(rendered) -> None:  # type: ignore[no-untyped-def]
    """After mapping, the plain list *is* the L15 order -- named before anonymous."""
    for entities, _, _ in rendered.values():
        names = [rule.named for rule in entities.window_rules]
        assert names == sorted(names, reverse=True)
