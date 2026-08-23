"""Mapping every corpus rice: stable snapshots, full accounting, and the fixpoint.

The corpus is seven real third-party rices. Mapping them is the only check that covers the
combinations nobody writes into a fixture -- a `bindd` inside a submap inside a sourced
file, a monitor line with six trailing pairs, a rule whose effect the wiki documents and
the binary does not.

Three properties are asserted over them:

1. **Stable snapshots.** `end-4` has a golden; a change to any mapping rule shows up as a
   diff to read rather than a number that moved.
2. **Full accounting.** Every keyword in the stream either reaches the model or is named in
   the Loss report. This is the mapping-level counterpart of the Grammar core's
   losslessness: the parser cannot report what it discarded, and the mapper cannot report
   what it dropped on the floor.
3. **The fixpoint.** See `TestFixpoint` for what is and is not checkable before the Lua
   importer of #62 exists.
"""

from __future__ import annotations

import pytest
from _golden import assert_matches_golden
from _support import CORPUS_DIR, GOLDEN_DIR

from hyprtweaker.engine.importer import import_config, parse
from hyprtweaker.engine.importer.keywords import (
    Assignment,
    Handler,
    SpecialCategory,
    UnparsedLine,
)
from hyprtweaker.engine.importer.lua import Consent, import_lua, lua_binary
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer.modules import render_module

RICES = ("end-4", "hyde", "hyprland-default", "hyprv", "jakoolit", "local", "ml4w")

GOLDEN_RICE = "end-4"
"""The rice with a golden: reproducible, richest in constructs, and it ships upstream's own
hand-written Lua port, so the mapping has something to be checked against."""

CORPUS_ENV = {
    "HOME": "/home/tester",
    "XDG_CONFIG_HOME": "/home/tester/.config",
    "XDG_DATA_HOME": "/home/tester/.local/share",
    "XDG_STATE_HOME": "/home/tester/.local/state",
    "XDG_CACHE_HOME": "/home/tester/.cache",
}
"""The same synthetic environment the Grammar core's corpus test uses, for the same reason:
hyprlang seeds `$var` from the environment and bakes `# hyprlang if` against it, so the real
one would make every snapshot depend on who ran it."""


_CONTROL_FLOW = frozenset({"source", "submap"})
"""Keywords that steer the walk instead of producing content: `source` was already inlined
by the Grammar core, and `submap` selects which Submap the following binds belong to. Both
are accounted for by their *effect* on other records, which is checked separately."""

_MERGING = frozenset({"animation", "monitor", "workspace", "windowrule", "layerrule"})
"""Keywords whose identity Hyprland itself merges on -- one animation per leaf, one monitor
rule per output, one workspace rule per selector. A later declaration supersedes an earlier
one, so the earlier record's *origin* legitimately disappears while its identity does not.
These are checked by identity below instead of by origin."""

_IDENTITY_OF = {
    "animation": lambda value: value.split(",")[0].strip(),
    "monitor": lambda value: value.split(",")[0].strip(),
    "workspace": lambda value: value.split(",")[0].strip(),
}


def _entry(rice: str):  # type: ignore[no-untyped-def]
    return CORPUS_DIR / rice / "hyprland.conf"


pytestmark = pytest.mark.skipif(
    not _entry(GOLDEN_RICE).is_file(),
    reason="the rice corpus is not checked out (see tests/corpus/fetch.sh)",
)


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema("0.56.2")


@pytest.fixture(scope="module")
def imports(schema):  # type: ignore[no-untyped-def]
    """Every checked-out rice, imported once for the whole module."""
    return {
        rice: import_config(_entry(rice), schema, env=CORPUS_ENV)
        for rice in RICES
        if _entry(rice).is_file()
    }


def _relative(origin, root):  # type: ignore[no-untyped-def]
    try:
        return f"{origin.file.relative_to(root)}:{origin.line}"
    except ValueError:
        return f"{origin.file}:{origin.line}"


class TestSnapshots:
    def test_the_golden_rice_maps_to_a_stable_snapshot(self, imports) -> None:  # type: ignore[no-untyped-def]
        result = imports[GOLDEN_RICE]
        assert_matches_golden(
            result.snapshot(),
            GOLDEN_DIR / "importer" / f"{GOLDEN_RICE}.model.txt",
            f"the mapped model of {GOLDEN_RICE}",
        )

    def test_every_rice_imports_without_raising(self, imports) -> None:  # type: ignore[no-untyped-def]
        # The contract with the wizard: it always gets a model and a report, never an
        # exception, however strange the config it was pointed at.
        assert set(imports) == {rice for rice in RICES if _entry(rice).is_file()}

    def test_the_richest_rices_produce_substantial_models(self, imports) -> None:  # type: ignore[no-untyped-def]
        result = imports[GOLDEN_RICE]
        assert len(result.model) > 50
        counts = result.entities.counts()
        for kind in ("binds", "window_rules", "layer_rules", "curves", "animations"):
            assert counts.get(kind, 0) > 0, f"{kind} came across empty"


class TestFullAccounting:
    """Nothing is dropped in silence: every record is in the model or in the report."""

    def test_every_handler_produces_an_entity_or_a_finding(self, imports, schema) -> None:  # type: ignore[no-untyped-def]
        for rice, result in imports.items():
            root = result.root
            parsed = parse(_entry(rice), env=CORPUS_ENV)
            reported = {item.origin for item in result.loss}
            produced = {
                item.origin
                for _, items in result.entities.kinds()
                for item in items
                if item.origin
            }
            unaccounted = [
                _relative(keyword.origin, root)
                for keyword in parsed.keywords
                if isinstance(keyword, Handler)
                and keyword.name not in _CONTROL_FLOW | _MERGING
                and _relative(keyword.origin, root) not in reported | produced
            ]
            assert not unaccounted, f"{rice}: handlers vanished at {unaccounted[:5]}"

    def test_every_merged_identity_survives_to_the_entity_set(self, imports) -> None:  # type: ignore[no-untyped-def]
        """The kinds Hyprland merges keep one record per identity, so an earlier
        declaration is superseded rather than lost -- but the identity itself must still be
        there. A leaf that is declared and then vanishes is a real drop."""
        surviving = {
            "animation": lambda result: {a.leaf for a in result.entities.animations},
            "monitor": lambda result: {m.output for m in result.entities.monitors},
            "workspace": lambda r: {w.workspace for w in r.entities.workspace_rules},
        }
        for rice, result in imports.items():
            parsed = parse(_entry(rice), env=CORPUS_ENV)
            reported = {item.origin for item in result.loss}
            root = result.root
            for name, identity in _IDENTITY_OF.items():
                declared = {
                    identity(keyword.value)
                    for keyword in parsed.keywords
                    if isinstance(keyword, Handler)
                    and keyword.name == name
                    and _relative(keyword.origin, root) not in reported
                }
                missing = declared - surviving[name](result)
                assert not missing, f"{rice}: {name} identities vanished: {sorted(missing)[:5]}"

    def test_every_declared_submap_is_in_the_entity_set(self, imports) -> None:  # type: ignore[no-untyped-def]
        """`submap` is control flow, not content: a second `submap = global` re-enters the
        block rather than declaring a new one, so it is accounted for by *name* rather than
        by producing another entity."""
        for rice, result in imports.items():
            parsed = parse(_entry(rice), env=CORPUS_ENV)
            declared = {
                keyword.value.split(",")[0].strip()
                for keyword in parsed.keywords
                if isinstance(keyword, Handler) and keyword.name == "submap"
            }
            declared -= {"reset", ""}
            assert declared <= {s.name for s in result.entities.submaps}, rice

    def test_every_assignment_reaches_the_model_or_the_report(self, imports) -> None:  # type: ignore[no-untyped-def]
        for rice, result in imports.items():
            root = result.root
            parsed = parse(_entry(rice), env=CORPUS_ENV)
            reported = {item.origin for item in result.loss}
            unaccounted = [
                keyword.key
                for keyword in parsed.keywords
                if isinstance(keyword, Assignment)
                and keyword.key not in result.model
                and _relative(keyword.origin, root) not in reported
            ]
            assert not unaccounted, f"{rice}: options vanished: {unaccounted[:5]}"

    def test_every_unparsed_line_is_named_in_the_report(self, imports) -> None:  # type: ignore[no-untyped-def]
        for rice, result in imports.items():
            root = result.root
            parsed = parse(_entry(rice), env=CORPUS_ENV)
            expected = {
                _relative(keyword.origin, root)
                for keyword in parsed.keywords
                if isinstance(keyword, UnparsedLine)
            }
            reported = {item.origin for item in result.loss if str(item.code) == "L31"}
            assert expected <= reported, f"{rice}: unparsed lines went unreported"

    def test_every_keyed_category_produces_an_entity_or_a_finding(self, imports) -> None:  # type: ignore[no-untyped-def]
        for rice, result in imports.items():
            root = result.root
            parsed = parse(_entry(rice), env=CORPUS_ENV)
            reported = {item.origin for item in result.loss}
            produced = {
                item.origin
                for _, items in result.entities.kinds()
                for item in items
                if item.origin
            }
            unaccounted = [
                _relative(keyword.origin, root)
                for keyword in parsed.keywords
                if isinstance(keyword, SpecialCategory)
                and _relative(keyword.origin, root) not in reported | produced
            ]
            assert not unaccounted, f"{rice}: category blocks vanished at {unaccounted[:3]}"


class TestLossClassification:
    def test_the_legacy_dispatch_breakage_is_found_where_it_exists(self, imports) -> None:  # type: ignore[no-untyped-def]
        """Four of the seven rices shell out to `hyprctl dispatch`. That is the engine-swap
        breakage this Importer owns, and it is invisible to any syntax check."""
        found = {
            rice
            for rice, result in imports.items()
            if any(str(item.code) == "L29" for item in result.loss)
        }
        assert "end-4" in found
        assert "hyprland-default" in found

    def test_a_pre_054_rice_reports_its_rule_syntax_as_breakage(self, imports) -> None:  # type: ignore[no-untyped-def]
        if "hyprv" not in imports:
            pytest.skip("hyprv is not checked out")
        codes = {str(item.code) for item in imports["hyprv"].loss}
        assert "L13" in codes

    def test_no_rice_reports_a_finding_with_an_unknown_code(self, imports) -> None:  # type: ignore[no-untyped-def]
        from hyprtweaker.engine.importer.loss import LOSS_CODES

        for rice, result in imports.items():
            for item in result.loss:
                assert item.code in LOSS_CODES, f"{rice}: {item.code} is not a known code"

    def test_findings_carry_an_origin_the_user_can_open(self, imports) -> None:  # type: ignore[no-untyped-def]
        # A report the user cannot act on is a report they will ignore. Only the
        # whole-file findings (rule precedence) are allowed to have no line.
        for rice, result in imports.items():
            for item in result.loss:
                if str(item.code) == "L15":
                    continue
                assert item.origin, f"{rice}: {item.code} has no origin"


class TestFixpoint:
    """import -> write -> re-import, closed.

    All three legs are checkable now that the Lua importer of #62 exists: the mapping is
    deterministic, the model renders to byte-identical Lua however many times it is
    written, and reading that Lua back gives the same model it was written from.

    The last leg covers Options only, and will until Entity Modules land (#64). The Writer
    renders `hl.config` and nothing else today, so there are no bind or rule modules to
    read back yet -- what the Lua importer does with those is asserted in
    `test_importer_lua_mapping.py` against configs written by hand.
    """

    def test_mapping_the_same_tree_twice_gives_the_same_model(self, imports, schema) -> None:  # type: ignore[no-untyped-def]
        for rice, first in imports.items():
            second = import_config(_entry(rice), schema, env=CORPUS_ENV)
            assert first.snapshot() == second.snapshot(), (
                f"{rice} did not map deterministically"
            )

    def test_the_tree_hash_is_stable_across_imports(self, imports, schema) -> None:  # type: ignore[no-untyped-def]
        for rice, first in imports.items():
            second = import_config(_entry(rice), schema, env=CORPUS_ENV)
            assert first.tree_hash() == second.tree_hash()

    def test_an_imported_model_renders_to_byte_identical_lua_twice(self, imports) -> None:  # type: ignore[no-untyped-def]
        for rice, result in imports.items():
            by_section: dict[str, list] = {}
            for option, value in result.model.set_options():
                by_section.setdefault(option.section, []).append((option, value))
            for section, items in by_section.items():
                first = render_module(items, app_version="0.0.0-test")
                second = render_module(items, app_version="0.0.0-test")
                assert first == second, f"{rice}/{section} did not render stably"

    @pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter installed")
    def test_writing_a_model_and_reading_it_back_gives_the_same_model(  # type: ignore[no-untyped-def]
        self, imports, schema, tmp_path
    ) -> None:
        """The leg this class waited on #62 for: Lua out, Lua in, nothing moved.

        The one that would catch a writer emitting a value shape its own reader cannot
        take -- a gradient written as text, a css-gap written as four bare numbers -- which
        no amount of write-side determinism would show, because both writes would be
        identically wrong.
        """
        for rice, result in imports.items():
            sections = [
                render_module(result.model.section(section), app_version="0.0.0-test")
                for section in result.model.sections()
            ]
            entry = tmp_path / rice / "hyprland.lua"
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text("\n".join(sections), encoding="utf-8")

            reimported = import_lua(
                entry, schema, consent=Consent(evaluate=True), env=dict(CORPUS_ENV)
            )
            assert reimported.loss.clean, (
                f"{rice}: re-reading our own output reported "
                f"{[item.code for item in reimported.loss]}"
            )
            written = {option.name: value for option, value in result.model.set_options()}
            read_back = {option.name: value for option, value in reimported.model.set_options()}
            assert read_back == written, f"{rice}: the model did not survive the round trip"

    def test_entity_order_is_the_source_order_every_time(self, imports, schema) -> None:  # type: ignore[no-untyped-def]
        """Position is identity for Binds and Rules (ADR-0007, ADR-0008), so an order that
        varies between imports is a different config, not a cosmetic difference."""
        for rice, first in imports.items():
            second = import_config(_entry(rice), schema, env=CORPUS_ENV)
            assert [b.keys for b in first.entities.binds] == [
                b.keys for b in second.entities.binds
            ], f"{rice}: bind order moved"
            assert [r.name for r in first.entities.ordered_window_rules()] == [
                r.name for r in second.entities.ordered_window_rules()
            ], f"{rice}: window rule order moved"
