"""The finder's second group: Binds, Rules, Workspace rules, Monitor rules, Monitor profiles
and Presets (ADR-0017, #75, #172).

The Option half of the index is pinned by `test_ui_search.py` and its golden, which this
ticket must not move. This file pins the Entity half the same way -- a golden over a small
hand-built config whose every entry is there to show one rule -- plus the two things a
golden cannot show: that the entries follow the model as it changes (S2b: pulled at query
time, never pushed), and that a hit still finds its entity after the list moved under it.

Hand-built rather than a corpus rice, so the golden reads in one screen and does not skip
on a machine without `tests/corpus`; the corpus is the startup budget's input instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from _golden import assert_matches_golden
from _support import CORPUS_DIR, GOLDEN_DIR, SAMPLE_VERSION, SCHEMA_DIR, entity_session

from hyprtweaker.engine.dispatchers import EXEC_PATH
from hyprtweaker.engine.model.entities import (
    Bind,
    DispatcherCall,
    EntitySet,
    LayerRule,
    MonitorRule,
    Submap,
    WindowRule,
    WorkspaceRule,
)
from hyprtweaker.engine.model.options import ConfigModel
from hyprtweaker.engine.presets import CaptureScope, Preset
from hyprtweaker.engine.profiles import MonitorProfile
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.ui.search import (
    ENTITIES_GROUP,
    SETTINGS_GROUP,
    EntityHit,
    EntityKind,
    OptionHit,
    SearchIndex,
    resolve,
)

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)


def exec_bind(keys: str, command: str, *, enabled: bool = True) -> Bind:
    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path=EXEC_PATH, args={"command": command}),
        enabled=enabled,
    )


def sample_entities() -> EntitySet:
    """One entry per rule the golden pins; the comments say which."""
    return EntitySet(
        submaps=[Submap(name="resize")],
        binds=[
            # Found by its Trigger, and by the dispatcher's name.
            Bind(keys="SUPER + Q", dispatcher=DispatcherCall(path="window.close")),
            # Found by its command.
            exec_bind("SUPER + Return", "kitty"),
            # The row's badge words: a dead keysym, a multi-key Trigger, a user-disabled
            # bind, a function action, and a bind into a submap with no binds (#139, #208).
            exec_bind("SUPER + notakey", "firefox", enabled=False),
            exec_bind("SUPER + A&B", "rofi -show drun", enabled=False),
            exec_bind("SUPER + E", "nautilus", enabled=False),
            Bind(keys="SUPER + F", dispatcher=None),
            Bind(
                keys="SUPER + R",
                dispatcher=DispatcherCall(path="submap", positional=("resize",)),
            ),
            # `code:N` reads as the row reads it.
            exec_bind("SUPER + code:10", "kitty --class scratch"),
        ],
        window_rules=[
            WindowRule(match={"class": "kitty"}, effects={"opacity": 0.9}),
            WindowRule(
                name="Float pavucontrol",
                match={"class": "pavucontrol"},
                effects={"float": True},
                enabled=False,
            ),
        ],
        layer_rules=[LayerRule(match={"namespace": "waybar"}, effects={"blur": True})],
        workspace_rules=[
            # Found by its selector, worded as the Workspaces Page words its row.
            WorkspaceRule(workspace="special:scratch", fields={"gaps_out": 20}),
            WorkspaceRule(workspace="w[tv1]", fields={"monitor": "DP-1", "decorate": False}),
        ],
        monitors=[
            MonitorRule(output="DP-1", fields={"mode": "2560x1440@144"}),
            MonitorRule(output="desc:Dell Inc. DELL U2720Q", fields={"scale": 1.5}),
            MonitorRule(output="", fields={"mode": "preferred"}),
        ],
    )


SAMPLE_PROFILES = (
    ("desk", MonitorProfile(name="Desk", monitors=(MonitorRule(output="DP-1"),))),
    ("travel", MonitorProfile(name="Travel")),
)


def preset(name: str, *scopes: CaptureScope) -> Preset:
    return Preset(
        name=name,
        created=datetime(2026, 9, 14),
        scopes=frozenset(scopes),
        options={},
        app_version=None,
        hyprland_version=None,
    )


SAMPLE_PRESETS = (
    ("desk-dark", preset("Desk dark", CaptureScope.COLORS, CaptureScope.GAPS_LAYOUT)),
    ("nord", preset("Nord", CaptureScope.COLORS)),
)


@dataclass
class Source:
    """The things the index reads from a Session, held in the open for a test."""

    model: ConfigModel
    profiles: tuple[tuple[str, MonitorProfile], ...] = ()
    monitor_profiles_revision: int = 0
    listed: int = field(default=0)
    """How many times the index read the profiles directory."""
    preset_list: tuple[tuple[str, Preset], ...] = ()
    presets_revision: int = 0
    presets_listed: int = field(default=0)
    """How many times the index read the presets directory."""

    def monitor_profiles(self) -> tuple[tuple[str, MonitorProfile], ...]:
        self.listed += 1
        return self.profiles

    def presets(self) -> tuple[tuple[str, Preset], ...]:
        self.presets_listed += 1
        return self.preset_list


def source_over(  # type: ignore[no-untyped-def]
    entities: EntitySet, profiles=SAMPLE_PROFILES, presets=SAMPLE_PRESETS
) -> Source:
    model = ConfigModel(SCHEMA)
    model.adopt_entities(entities)
    return Source(model=model, profiles=profiles, preset_list=presets)


@pytest.fixture
def xkb_knows_all_but_notakey(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stand-in for libxkbcommon, so the dead-keysym badge is pinned without a skip."""
    monkeypatch.setattr(
        "hyprtweaker.engine.importer.binds.known_keysym", lambda name: name != "notakey"
    )


def entity_hits(index: SearchIndex, query: str) -> list[EntityHit]:
    return [hit for hit in index.query(query) if isinstance(hit, EntityHit)]


# --- findable by salient text ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "kind", "title"),
    [
        ("super + q", EntityKind.BIND, "SUPER + Q"),
        ("window.close", EntityKind.BIND, "SUPER + Q"),
        ("close the window", EntityKind.BIND, "SUPER + Q"),
        ("nautilus", EntityKind.BIND, "SUPER + E"),
        ("key code 10", EntityKind.BIND, "SUPER + key code 10"),
        ("float pavu", EntityKind.WINDOW_RULE, "Float pavucontrol"),
        ("class pavucontrol", EntityKind.WINDOW_RULE, "Float pavucontrol"),
        ("waybar", EntityKind.LAYER_RULE, "namespace waybar → blur"),
        ("DP-1", EntityKind.MONITOR_RULE, "DP-1"),
        ("u2720q", EntityKind.MONITOR_RULE, "desc:Dell Inc. DELL U2720Q"),
        ("any other", EntityKind.MONITOR_RULE, "Any other display"),
        ("travel", EntityKind.MONITOR_PROFILE, "Travel"),
        ("special:scr", EntityKind.WORKSPACE_RULE, "special:scratch"),
        ("w[tv1]", EntityKind.WORKSPACE_RULE, "w[tv1]"),
        ("nord", EntityKind.PRESET, "Nord"),
        ("desk dark", EntityKind.PRESET, "Desk dark"),
    ],
)
def test_each_kind_is_findable_by_its_salient_text(
    query: str, kind: EntityKind, title: str
) -> None:
    index = SearchIndex.build(SCHEMA, source_over(sample_entities()))
    assert (kind, title) in [(hit.kind, hit.title) for hit in entity_hits(index, query)]


@pytest.mark.usefixtures("xkb_knows_all_but_notakey")
@pytest.mark.parametrize(
    ("query", "badge"),
    [
        ("notakey", 'Unknown key "notakey"'),
        ("a&b", "Multi-key: Hyprland can't load it"),
        ("nautilus", "Disabled"),
        ("super + f", "Defined by a Lua function in user.lua"),
        ("submap", "Submap has no enabled keybinds"),
        ("pavucontrol", "Disabled"),
        ("super + q", None),
    ],
)
def test_a_bind_entry_carries_its_rows_badge_words(query: str, badge: str | None) -> None:
    """The salient state reads as the row's badge does (#139's vocabulary, #208's flag).

    `SUPER + R` enters `resize`, which has no binds: the flag needs the whole bind list,
    which is why the entries are built over the list and not one bind at a time.
    """
    index = SearchIndex.build(SCHEMA, source_over(sample_entities()))
    assert entity_hits(index, query)[0].badge == badge


@pytest.mark.usefixtures("xkb_knows_all_but_notakey")
def test_entity_index_matches_golden() -> None:
    index = SearchIndex.build(SCHEMA, source_over(sample_entities()))
    assert_matches_golden(
        render_entities(index, ENTITY_GOLDEN_QUERIES),
        GOLDEN_DIR / "search-entities.txt",
        f"the {ENTITIES_GROUP} group of the search index",
    )


ENTITY_GOLDEN_QUERIES = ("super", "kitty", "class", "display", "d", "desk", "special", "nord")
"""`super` -- every bind in list order, each with its row's badge. `kitty` -- one text found
by a rule's title and two binds' commands: field order across kinds. `class` -- an unlabelled
rule by its summary title, a labelled one by its Match. `display` -- the catch-all by the
name its row shows. `d` -- a one-letter query reaching every kind at once. `desk` -- a profile
by name, with its row's summary, then a Preset named alike: Presets come last. `special` -- a
Workspace rule by its selector, with its row's field summary. `nord` -- a Preset by name, with
its row's scopes and date."""


def render_entities(index: SearchIndex, queries: tuple[str, ...]) -> str:
    lines = [f"# search index -- the {ENTITIES_GROUP} group"]
    for query in queries:
        hits = entity_hits(index, query)
        lines += ["", f"## query: {query} ({len(hits)} hits)"]
        lines.append("field | match | kind | title | subtitle | badge")
        lines += [
            " | ".join(
                (
                    hit.field.value,
                    hit.match.name.lower().replace("_", "-"),
                    hit.kind.value,
                    hit.title,
                    hit.subtitle,
                    hit.badge or "-",
                )
            )
            for hit in hits
        ]
    return "\n".join(lines) + "\n"


# --- groups ---------------------------------------------------------------------------------


def test_settings_come_first_then_rules_and_entities() -> None:
    """ADR-0017's group order: `opacity` names Options and a window rule's Effect."""
    index = SearchIndex.build(SCHEMA, source_over(sample_entities()))
    groups = [hit.group for hit in index.query("opacity")]

    assert groups[0] == SETTINGS_GROUP
    assert groups[-1] == ENTITIES_GROUP
    assert groups == sorted(groups, key=[SETTINGS_GROUP, ENTITIES_GROUP].index)


def test_the_limit_caps_each_group_separately() -> None:
    """Fifty Option hits must not starve the entity group off the end of the list."""
    index = SearchIndex.build(SCHEMA, source_over(sample_entities()))
    hits = index.query("o", limit=2)

    assert [type(hit) for hit in hits] == [OptionHit, OptionHit, EntityHit, EntityHit]


def test_an_index_without_entities_lists_options_only() -> None:
    hits = SearchIndex.build(SCHEMA).query("opacity")
    assert hits and all(isinstance(hit, OptionHit) for hit in hits)


# --- freshness (settled S2b) ----------------------------------------------------------------


def test_nothing_entity_is_built_until_the_first_query() -> None:
    """Startup pays for the Schema only; the first query builds the entity entries."""
    source = source_over(sample_entities())
    index = SearchIndex.build(SCHEMA, source)
    assert (index.entity_count, source.listed, source.presets_listed) == (None, 0, 0)

    index.query("kitty")
    assert (index.entity_count, source.listed, source.presets_listed) == (20, 1, 1)


def test_an_edit_to_the_model_reaches_the_next_query() -> None:
    entities = sample_entities()
    index = SearchIndex.build(SCHEMA, source_over(entities))
    assert entity_hits(index, "thunar") == []

    entities.binds.append(exec_bind("SUPER + T", "thunar"))
    assert [hit.title for hit in entity_hits(index, "thunar")] == ["SUPER + T"]

    entities.binds[-1] = exec_bind("SUPER + T", "dolphin")
    assert entity_hits(index, "thunar") == []


def test_a_replaced_entity_set_reaches_the_next_query() -> None:
    """A foreign reload adopts a new `EntitySet`; the index reads the model's current one."""
    source = source_over(sample_entities())
    index = SearchIndex.build(SCHEMA, source)
    assert entity_hits(index, "waybar")

    source.model.adopt_entities(EntitySet())
    assert entity_hits(index, "waybar") == []


def test_profiles_are_relisted_only_when_their_revision_moves() -> None:
    source = source_over(sample_entities(), profiles=())
    index = SearchIndex.build(SCHEMA, source)
    assert entity_hits(index, "travel") == []

    source.profiles = SAMPLE_PROFILES
    assert entity_hits(index, "travel") == [], "re-listed with no revision bump"
    assert source.listed == 1

    source.monitor_profiles_revision += 1
    assert [hit.title for hit in entity_hits(index, "travel")] == ["Travel"]
    assert source.listed == 2


def test_saving_updating_and_deleting_a_profile_change_the_next_query(tmp_path: Path) -> None:
    """Through the real Session and its profile store: the revision is the change signal."""
    session, _applier = entity_session(tmp_path)
    index = SearchIndex.build(session.schema, session)
    assert entity_hits(index, "studio") == []

    slug = session.save_monitor_profile("Studio")
    [saved] = entity_hits(index, "studio")
    assert (saved.title, saved.subtitle, saved.target) == ("Studio", "0 display rules", slug)

    session.model.entities.monitors.append(MonitorRule(output="HDMI-A-1"))
    assert session.update_monitor_profile(slug)
    [updated] = entity_hits(index, "studio")
    assert updated.subtitle == "1 display rule"

    session.delete_monitor_profile(slug)
    assert entity_hits(index, "studio") == []


def test_presets_are_relisted_only_when_their_revision_moves() -> None:
    """Presets are files, not model state: their store's revision stands in (settled S7)."""
    source = source_over(sample_entities(), presets=())
    index = SearchIndex.build(SCHEMA, source)
    assert entity_hits(index, "nord") == []

    source.preset_list = SAMPLE_PRESETS
    assert entity_hits(index, "nord") == [], "re-listed with no revision bump"
    assert source.presets_listed == 1

    source.presets_revision += 1
    assert [hit.title for hit in entity_hits(index, "nord")] == ["Nord"]
    assert source.presets_listed == 2


def test_saving_and_deleting_a_preset_change_the_next_query(tmp_path: Path) -> None:
    """Through the real Session and its Preset store, worded as the Presets group's row."""
    session, _applier = entity_session(tmp_path)
    index = SearchIndex.build(session.schema, session)
    assert entity_hits(index, "evening") == []

    session.model.set("general:border_size", 3)
    results: list[object] = []
    session.save_preset("Nord evening", (CaptureScope.GAPS_LAYOUT,), done=results.append)
    assert [type(result).__name__ for result in results] == ["PresetSaved"]
    [saved] = entity_hits(index, "evening")
    assert (saved.kind, saved.title, saved.target) == (
        EntityKind.PRESET,
        "Nord evening",
        "nord-evening",
    )
    assert saved.subtitle.startswith("Gaps & layout · saved ")

    session.delete_preset("nord-evening")
    assert entity_hits(index, "evening") == []


def test_adding_and_removing_a_workspace_rule_change_the_next_query(tmp_path: Path) -> None:
    session, _applier = entity_session(tmp_path)
    index = SearchIndex.build(session.schema, session)
    assert entity_hits(index, "special:music") == []

    assert session.save_workspace_rule(
        WorkspaceRule(workspace="special:music", fields={"gaps_in": 0, "decorate": False})
    )
    [added] = entity_hits(index, "special:music")
    assert (added.kind, added.title, added.subtitle, added.target) == (
        EntityKind.WORKSPACE_RULE,
        "special:music",
        "gaps_in 0, decorate off",
        "special:music",
    )

    assert session.remove_workspace_rule("special:music")
    assert entity_hits(index, "special:music") == []


# --- resolving a hit at open ----------------------------------------------------------------


def test_a_hit_follows_its_bind_when_the_list_moves() -> None:
    entities = sample_entities()
    source = source_over(entities)
    index = SearchIndex.build(SCHEMA, source)
    [hit] = entity_hits(index, "nautilus")
    assert resolve(hit, source) == 4

    entities.binds.insert(0, exec_bind("SUPER + T", "thunar"))
    assert resolve(hit, source) == 5, "found by identity, not by its old position"


def test_a_hit_for_a_removed_entity_resolves_to_nothing() -> None:
    entities = sample_entities()
    source = source_over(entities)
    index = SearchIndex.build(SCHEMA, source)
    [rule] = entity_hits(index, "waybar")
    [profile] = entity_hits(index, "travel")
    [workspace] = entity_hits(index, "special:scratch")
    [nord] = entity_hits(index, "nord")
    assert (resolve(workspace, source), resolve(nord, source)) == (0, 1)

    entities.layer_rules.clear()
    entities.workspace_rules.pop(0)
    source.profiles = SAMPLE_PROFILES[:1]
    source.monitor_profiles_revision += 1
    source.preset_list = SAMPLE_PRESETS[:1]
    source.presets_revision += 1

    assert [resolve(hit, source) for hit in (rule, profile, workspace, nord)] == [None] * 4


def test_a_workspace_rule_hit_follows_its_selector_through_an_edit() -> None:
    """A rule edited since the query is a new object with the same selector -- its identity
    (CONTEXT.md, Workspace rule) -- so the hit still opens it, wherever it now sits."""
    entities = sample_entities()
    source = source_over(entities)
    index = SearchIndex.build(SCHEMA, source)
    [hit] = entity_hits(index, "w[tv1]")

    entities.workspace_rules[:] = [
        WorkspaceRule(workspace="w[tv1]", fields={"monitor": "HDMI-A-1"}),
    ]
    assert resolve(hit, source) == 0


def test_an_equal_copy_is_found_and_a_duplicate_keeps_its_place() -> None:
    """After a reload every bind is a new object; equality finds it, and of two identical
    binds (legal, ADR-0007) the one at the hit's own position wins."""
    entities = sample_entities()
    entities.binds.append(exec_bind("SUPER + Return", "kitty"))
    source = source_over(entities)
    index = SearchIndex.build(SCHEMA, source)
    second = next(hit for hit in entity_hits(index, "kitty") if hit.index == 8)

    source.model.adopt_entities(sample_entities())
    source.model.entities.binds.append(exec_bind("SUPER + Return", "kitty"))
    assert resolve(second, source) == 8


# --- the startup budget ---------------------------------------------------------------------

END_4 = CORPUS_DIR / "end-4" / "hyprland.conf"


@pytest.mark.skipif(not END_4.is_file(), reason="the rice corpus is not checked out")
def test_building_the_entries_over_a_big_rice_is_quick() -> None:
    """Settled S2b's budget: under 250 ms over end-4, best of three (#172 re-runs it)."""
    from hyprtweaker.engine.importer import import_config

    entities = import_config(END_4, SCHEMA, env={"HOME": "/home/tester"}).entities
    assert len(entities) > 300

    timings = []
    for _ in range(3):
        index = SearchIndex.build(SCHEMA, source_over(entities))
        started = time.perf_counter()
        index.query("super")
        timings.append(time.perf_counter() - started)

    print(f"entity entries over end-4: best {min(timings) * 1000:.1f} ms")
    assert min(timings) < 0.25
