"""The Lua importer against real hand-written Lua.

Two of the seven corpus rices ship an upstream Lua port beside their `.conf` -- `end-4`
wrote theirs by hand, and it is the richest thing available to point this importer at:
seventeen files, requires nested three deep, submaps, and handlers full of `exec_cmd`.

`end-4` has goldens, so a change to any mapping rule shows up as a diff to read rather than
a count that moved. The other rice is checked by property, the same way the hyprlang corpus
test treats the rices that have no golden.
"""

from __future__ import annotations

import pytest
from _golden import assert_matches_golden
from _support import CORPUS_DIR, GOLDEN_DIR

from hyprtweaker.engine.importer.loss import LossClass, LossReport
from hyprtweaker.engine.importer.lua import Consent, import_lua, lua_binary
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema

pytestmark = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter installed")

LUA_RICES = ("end-4", "ml4w")
"""The rices with a hand-written `hyprland.lua` beside the `.conf`."""

GOLDEN_RICE = "end-4"

CORPUS_ENV = {
    "HOME": "/home/tester",
    "XDG_CONFIG_HOME": "/home/tester/.config",
    "XDG_DATA_HOME": "/home/tester/.local/share",
    "XDG_STATE_HOME": "/home/tester/.local/state",
    "XDG_CACHE_HOME": "/home/tester/.cache",
}
"""The same synthetic environment the other corpus tests use: these configs read `$HOME`,
and an import that varied with whose machine ran it would have no golden worth keeping."""

GRANTED = Consent(evaluate=True)


def _entry(rice: str):  # type: ignore[no-untyped-def]
    return CORPUS_DIR / rice / "hyprland.lua"


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema("0.56.2")


@pytest.fixture(scope="module")
def imports(schema):  # type: ignore[no-untyped-def]
    return {
        rice: import_lua(_entry(rice), schema, consent=GRANTED, env=dict(CORPUS_ENV))
        for rice in LUA_RICES
        if _entry(rice).is_file()
    }


@pytest.fixture(scope="module")
def golden(imports):  # type: ignore[no-untyped-def]
    if GOLDEN_RICE not in imports:
        pytest.skip(f"{GOLDEN_RICE} is not checked out -- see tests/corpus/fetch.sh")
    return imports[GOLDEN_RICE]


def test_the_golden_rice_maps_to_a_stable_model(golden) -> None:  # type: ignore[no-untyped-def]
    assert_matches_golden(
        golden.snapshot(),
        GOLDEN_DIR / "importer" / "end-4.lua-model.txt",
        "the end-4 Lua import",
    )


def test_the_golden_rice_keeps_a_stable_legacy_file(golden) -> None:  # type: ignore[no-untyped-def]
    """`legacy.lua` is written once and never rewritten, so what goes in it is a promise."""
    assert_matches_golden(
        golden.legacy.replace(str(_entry(GOLDEN_RICE)), "<entry>"),
        GOLDEN_DIR / "importer" / "end-4.lua-legacy.lua",
        "the end-4 legacy.lua",
    )


def test_a_real_rice_declares_real_config(imports) -> None:  # type: ignore[no-untyped-def]
    """The blunt check that the evaluation is doing anything at all."""
    for rice, result in imports.items():
        assert len(result.model.set_options()) > 20, f"{rice}: barely any options"
        assert result.entities.counts().get("binds", 0) > 20, f"{rice}: barely any binds"


def test_every_script_construct_is_accounted_for(imports) -> None:  # type: ignore[no-untyped-def]
    """The Lua counterpart of the hyprlang corpus test's full accounting: a captured
    function either reaches `legacy.lua` or is named in the report. Nothing is dropped on
    the floor quietly."""
    for rice, result in imports.items():
        kept = result.legacy.count("-- script from") + result.legacy.count(
            "-- inline function from"
        )
        reported = sum(1 for item in result.loss if item.code.value == "L32")
        assert kept == reported, f"{rice}: {kept} constructs kept but {reported} reported"


def test_nothing_a_real_rice_does_is_silently_broken(imports) -> None:  # type: ignore[no-untyped-def]
    """Every Breakage finding has to be one of the kinds ADR-0009 predicts, not a surprise.

    A rice that runs a command at load time is genuinely broken by import and says so;
    anything *else* landing in Breakage means the importer failed and blamed the config.
    """
    for rice, result in imports.items():
        for item in result.loss.of_class(LossClass.BREAKAGE):
            assert item.code.value in {"L29", "L34"}, f"{rice}: unexpected breakage {item}"


def test_the_report_survives_being_written_and_read_back(golden, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The Loss report is shared with the hyprlang importer, including its persistence --
    ADR-0009 wants it viewable long after the wizard has closed."""
    paths = ConfigPaths.rooted_at(tmp_path)
    paths.reports_dir.mkdir(parents=True, exist_ok=True)

    written = golden.loss.save(paths)
    reloaded = LossReport.load(written)

    assert [item.code for item in reloaded] == [item.code for item in golden.loss]
    assert reloaded.counts() == golden.loss.counts()
    assert LossReport.latest(paths) is not None
    assert written.with_suffix(".md").is_file(), "the human-readable half was not written"


def test_importing_the_same_rice_twice_gives_the_same_model(imports, schema) -> None:  # type: ignore[no-untyped-def]
    """Evaluation is the risk this covers: a config that reads the clock or a directory
    listing could import differently each time, and a model that moves under the user is
    worse than one that is wrong the same way twice."""
    for rice, first in imports.items():
        second = import_lua(_entry(rice), schema, consent=GRANTED, env=dict(CORPUS_ENV))
        assert first.snapshot() == second.snapshot(), f"{rice} did not import deterministically"
        assert first.legacy == second.legacy, f"{rice}: legacy.lua moved between imports"
