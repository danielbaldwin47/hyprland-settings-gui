"""Every corpus bind, through the writer and back (ADR-0007, #64).

The unit tests build binds by hand, which proves the renderer does what it was told and
nothing about whether it was told the right things. The corpus is seven real third-party
rices carrying 865 bind lines between them -- `bindd` inside a submap inside a sourced
file, `code:N` number rows, `bindm` drags, dead keysyms -- and it is the only place the
combinations nobody thinks to write into a fixture actually occur.

Three properties, each one an acceptance criterion of #64:

1. **Everything renders.** Every mapped bind with an action reaches the file.
2. **It is valid Lua.** The syntax gate runs over the rendered Module, so a bind whose
   description contains a quote cannot take the whole config down.
3. **It survives the round-trip.** Rendering and re-reading yields the same binds in the
   same order -- which is what "hand edits reflect in the app" rests on.
"""

from __future__ import annotations

import pytest
from _support import CORPUS_DIR

from hyprtweaker.engine.importer import import_config
from hyprtweaker.engine.importer.lua import lua_binary
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import syntax
from hyprtweaker.engine.writer.binds import parse_binds_module, render_binds_module

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


def _by_submap(binds):  # type: ignore[no-untyped-def]
    """Binds keyed by owning Submap, each group in its own order."""
    groups: dict[str | None, list[tuple[str, str]]] = {}
    for bind in binds:
        path = bind.dispatcher.path if bind.dispatcher else ""
        groups.setdefault(bind.submap, []).append((bind.keys, path))
    return groups


pytestmark = pytest.mark.skipif(not _entry("end-4").is_file(), reason="corpus not checked out")


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema("0.56.2")


@pytest.fixture(scope="module")
def rendered(schema):  # type: ignore[no-untyped-def]
    """Each rice imported once, and its binds Module rendered once."""
    out = {}
    for rice in RICES:
        if not _entry(rice).is_file():
            continue
        result = import_config(_entry(rice), schema, env=CORPUS_ENV)
        out[rice] = (
            result.entities,
            render_binds_module(result.entities, app_version=VERSION),
        )
    return out


@pytest.mark.parametrize("rice", RICES)
class TestCorpusBinds:
    def test_every_actionable_bind_renders(self, rendered, rice) -> None:  # type: ignore[no-untyped-def]
        if rice not in rendered:
            pytest.skip(f"{rice} not checked out")
        entities, text = rendered[rice]
        actionable = [bind for bind in entities.binds if bind.dispatcher is not None]
        if not actionable:
            pytest.skip(f"{rice} has no binds")
        assert text is not None
        assert text.count("hl.bind(") == len(actionable), (
            f"{rice}: {len(actionable)} binds mapped, {text.count('hl.bind(')} rendered"
        )

    def test_rendered_module_is_valid_lua(self, rendered, rice) -> None:  # type: ignore[no-untyped-def]
        if rice not in rendered or rendered[rice][1] is None:
            pytest.skip(f"{rice} has no binds")
        if not syntax.gate_available():
            pytest.skip("no luac to gate with")
        syntax.gate(rendered[rice][1], "binds.lua")

    def test_mouse_flag_is_never_emitted(self, rendered, rice) -> None:  # type: ignore[no-untyped-def]
        """`bindm` is everywhere in the corpus and `mouse = true` is inert (ADR-0007)."""
        if rice not in rendered or rendered[rice][1] is None:
            pytest.skip(f"{rice} has no binds")
        assert "mouse =" not in rendered[rice][1]

    def test_round_trip_preserves_binds_in_order(self, rendered, rice) -> None:  # type: ignore[no-untyped-def]
        """Order is compared *per submap*, which is the order that means anything.

        A submap's binds only exist inside its `hl.define_submap` callback, so a file
        cannot interleave root and submap binds the way the model's flat list does --
        grouping is forced by Lua, not chosen here. What must survive is the order that
        decides which of two duplicates fires first, and duplicates only race inside the
        same submap (ADR-0007).
        """
        if rice not in rendered or rendered[rice][1] is None:
            pytest.skip(f"{rice} has no binds")
        if lua_binary() is None:
            pytest.skip("no Lua interpreter")
        entities, text = rendered[rice]
        parsed = parse_binds_module(text)
        assert parsed.ok, parsed.errors
        assert _by_submap(parsed.binds) == _by_submap(
            [bind for bind in entities.binds if bind.dispatcher is not None]
        )

    def test_round_trip_keeps_every_bind(self, rendered, rice) -> None:  # type: ignore[no-untyped-def]
        if rice not in rendered or rendered[rice][1] is None:
            pytest.skip(f"{rice} has no binds")
        if lua_binary() is None:
            pytest.skip("no Lua interpreter")
        entities, text = rendered[rice]
        actionable = [bind for bind in entities.binds if bind.dispatcher is not None]
        assert len(parse_binds_module(text).binds) == len(actionable)


def test_corpus_has_key_code_binds(rendered) -> None:  # type: ignore[no-untyped-def]
    """The case `hyprctl binds` cannot see, so the file is the only record of it.

    Guards the guard: if the corpus ever stopped containing `code:N` binds, the tests above
    would keep passing while covering nothing.
    """
    texts = [text for _, text in rendered.values() if text]
    assert any("code:" in text for text in texts), "corpus should exercise key-code binds"


def test_corpus_has_descriptions(rendered) -> None:  # type: ignore[no-untyped-def]
    """`bindd` is common in the corpus; descriptions must reach the file."""
    texts = [text for _, text in rendered.values() if text]
    assert any("description = " in text for text in texts)
