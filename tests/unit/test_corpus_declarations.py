"""Every declarative Entity kind, from a real rice to its Module and back (#70).

The ticket's first acceptance criterion end to end: import from the corpus, render to the
Module the kind owns, read that Module back, and get the same entities. Fixtures prove the
renderer handles what the *tests* thought to write down; the corpus proves it handles what
seven real users actually wrote -- a `bezier` with coordinates outside the Lua parser's
clamp, an `env` value full of commas, a `device` block with a dashed key, an `exec-once`
whose command starts with a bracket.

The union of the corpus is used rather than one rice, because no single rice exercises all
six kinds -- only `local` sets a permission, and the animation-heavy rices set no devices.
Which rice each entity came from does not matter here; that it survives the round trip does.
"""

from __future__ import annotations

import pytest
from _support import CORPUS_DIR

from hyprtweaker.engine.importer import import_config
from hyprtweaker.engine.importer.lua import lua_binary
from hyprtweaker.engine.model.entities import EntitySet
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer.animations import render_animations_module
from hyprtweaker.engine.writer.declarations import parse_declarations_module
from hyprtweaker.engine.writer.inputs import render_devices_module, render_gestures_module
from hyprtweaker.engine.writer.session_scope import (
    render_autostart_module,
    render_env_module,
    render_permissions_module,
)

RICES = ("end-4", "hyde", "hyprland-default", "hyprv", "jakoolit", "local", "ml4w")

VERSION = "0.1.0"

CORPUS_ENV = {
    "HOME": "/home/tester",
    "XDG_CONFIG_HOME": "/home/tester/.config",
    "XDG_DATA_HOME": "/home/tester/.local/share",
    "XDG_STATE_HOME": "/home/tester/.local/state",
    "XDG_CACHE_HOME": "/home/tester/.cache",
}
"""The synthetic environment the other corpus tests use, for the same reason: hyprlang
seeds `$var` from the environment, so the real one would make results depend on who ran."""


def _entry(rice: str):  # type: ignore[no-untyped-def]
    return CORPUS_DIR / rice / "hyprland.conf"


pytestmark = [
    pytest.mark.skipif(
        not _entry("end-4").is_file(),
        reason="the rice corpus is not checked out (see tests/corpus/fetch.sh)",
    ),
    pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter"),
]


@pytest.fixture(scope="module")
def corpus() -> EntitySet:
    """Every declarative Entity the whole corpus declares, in one set."""
    schema = load_schema("0.56.2")
    merged = EntitySet()
    for rice in RICES:
        if not _entry(rice).is_file():
            continue
        result = import_config(_entry(rice), schema, env=CORPUS_ENV)
        entities = result.entities
        merged.curves.extend(entities.curves)
        merged.gestures.extend(entities.gestures)
        merged.env.extend(entities.env)
        merged.permissions.extend(entities.permissions)
        merged.startup.extend(entities.startup)
        for animation in entities.animations:
            merged.add_animation(animation)
        for device in entities.devices:
            merged.add_device(device)
    return merged


class TestTheCorpusExercisesEveryKind:
    """If one of these ever goes empty, the round-trip tests below stopped proving anything."""

    @pytest.mark.parametrize(
        "kind",
        ["curves", "animations", "gestures", "devices", "env", "permissions", "startup"],
    )
    def test_the_corpus_declares_at_least_one(self, corpus: EntitySet, kind: str) -> None:
        assert getattr(corpus, kind), f"no rice in the corpus declares a {kind[:-1]}"


class TestRoundTrip:
    def test_curves_and_animations_survive(self, corpus: EntitySet) -> None:
        text = render_animations_module(corpus.curves, corpus.animations, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="animations.lua")

        assert parsed.ok, parsed.errors
        assert [(c.name, dict(c.spec)) for c in parsed.curves] == [
            (c.name, dict(c.spec)) for c in corpus.curves
        ]
        assert [(a.leaf, dict(a.fields)) for a in parsed.animations] == [
            (a.leaf, dict(a.fields)) for a in corpus.animations
        ]

    def test_gestures_survive(self, corpus: EntitySet) -> None:
        text = render_gestures_module(corpus.gestures, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="gestures.lua")

        assert parsed.ok, parsed.errors
        assert [dict(g.fields) for g in parsed.gestures] == [
            dict(g.fields) for g in corpus.gestures
        ]

    def test_devices_survive(self, corpus: EntitySet) -> None:
        text = render_devices_module(corpus.devices, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="devices.lua")

        assert parsed.ok, parsed.errors
        assert [(d.name, dict(d.fields)) for d in parsed.devices] == [
            (d.name, dict(d.fields)) for d in corpus.devices
        ]

    def test_environment_variables_survive_their_commas(self, corpus: EntitySet) -> None:
        text = render_env_module(corpus.env, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="env.lua")

        assert parsed.ok, parsed.errors
        assert [(v.name, v.value, v.dbus) for v in parsed.env] == [
            (v.name, v.value, v.dbus) for v in corpus.env
        ]

    def test_permissions_survive(self, corpus: EntitySet) -> None:
        text = render_permissions_module(corpus.permissions, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="permissions.lua")

        assert parsed.ok, parsed.errors
        assert [(p.binary, p.kind, p.mode) for p in parsed.permissions] == [
            (p.binary, p.kind, p.mode) for p in corpus.permissions
        ]

    def test_autostart_survives_its_handler_block(self, corpus: EntitySet) -> None:
        text = render_autostart_module(corpus.startup, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="autostart.lua")

        assert parsed.ok, parsed.errors
        assert sorted((c.command, c.event, c.raw) for c in parsed.startup) == sorted(
            (c.command, c.event, c.raw) for c in corpus.startup
        )


class TestFixpoint:
    """Re-rendering what was read gives the same bytes -- no phantom write on startup."""

    def test_every_kind_reaches_a_fixpoint(self, corpus: EntitySet) -> None:
        renders = {
            "animations.lua": (
                lambda e: render_animations_module(
                    list(e.curves), list(e.animations), app_version=VERSION
                )
            ),
            "gestures.lua": lambda e: render_gestures_module(
                list(e.gestures), app_version=VERSION
            ),
            "devices.lua": lambda e: render_devices_module(
                list(e.devices), app_version=VERSION
            ),
            "env.lua": lambda e: render_env_module(list(e.env), app_version=VERSION),
            "permissions.lua": lambda e: render_permissions_module(
                list(e.permissions), app_version=VERSION
            ),
            "autostart.lua": lambda e: render_autostart_module(
                list(e.startup), app_version=VERSION
            ),
        }
        for module, render in renders.items():
            first = render(corpus)
            assert first is not None, module

            parsed = parse_declarations_module(first, module=module)
            assert parsed.ok, (module, parsed.errors)

            again = render(parsed)
            assert again == first, f"{module} does not reach a fixpoint"
