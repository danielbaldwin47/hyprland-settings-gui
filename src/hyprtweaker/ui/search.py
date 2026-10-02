"""The finder's index: which Options and Entities a query names, and in what order (ADR-0017).

Search is view-independent and all-indexing. It sees every Option of the Schema --
including the `hidden` tier the Config view alone renders -- because the whole point of
typing `manual_crash` is to reach a Row no amount of browsing would show you. Nothing here
filters by visibility; deciding what a *hit* costs to reveal is the window's job
(`One-off reveal`, ADR-0013 §5), not the index's.

Nothing here imports `gi`. The index is a decision about text -- which is exactly the kind
of question worth a golden file and a machine with no display, the same bargain `plan.py`
makes for the shape of a Page.

**Substring, never fuzzy** (ADR-0017): over a corpus this size fuzzy matching is noise, and
dotted keys are what an expert types precisely so that the match can be exact. The one
refinement is the word-prefix boost -- `round` should reach "Rounding" before it reaches
"Blur passes (rounding aware)" -- and it is a *tie-break within a field*, not a rank of its
own, so the field order below still decides first.

**Two groups** (ADR-0017 §Index scope): Settings, then Keybinds, rules & displays. The Options are
indexed once, at construction; the Entities are not, because they change under the window
-- an edit, an undo, a foreign reload, a profile saved. Rather than a change signal fired
from every site that mutates a list (and forgotten at the next one), the index *pulls*: each
query compares the model's lists with the ones its entity entries were built from and
rebuilds only when they differ (settled S2b, amended into ADR-0017 §Index build during #75).
Monitor profiles live in files, not the model, so their store's revision counter stands in
for the comparison there.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol

from hyprtweaker.engine.binds_analysis import empty_submaps
from hyprtweaker.engine.model.entities import (
    Bind,
    EntitySet,
    LayerRule,
    MonitorRule,
    WindowRule,
)
from hyprtweaker.engine.model.options import ConfigModel
from hyprtweaker.engine.monitors_catalog import CATCH_ALL_OUTPUT
from hyprtweaker.engine.profiles import MonitorProfile
from hyprtweaker.engine.schema import ResolvedOption, Schema
from hyprtweaker.ui.pages.entity_text import (
    action_text,
    bind_badge,
    effects_text,
    match_text,
    profile_summary,
    rule_subtitle,
    rule_summary,
    rule_title,
    trigger_text,
)

SETTINGS_GROUP = "Settings"
ENTITIES_GROUP = "Keybinds, rules & displays"
"""ADR-0017's two result groups, in the order the finder lists them."""


class Field(enum.StrEnum):
    """The three texts an Option is findable by, best first.

    ADR-0017's ranking is stated over these: "title prefix > title substring > dotted-key
    substring > any other field". Title first because it is what the Row shows; the dotted
    key next because it is unambiguous and the expert's address for an Option; the
    description last because it is prose, and prose matches a great many queries weakly.
    """

    TITLE = "title"
    KEY = "key"
    DESCRIPTION = "description"


_FIELD_ORDER = (Field.TITLE, Field.KEY, Field.DESCRIPTION)


class Match(enum.IntEnum):
    """How well a query met one field. Lower sorts first.

    An `IntEnum` because it is half of a sort key and reads as one: `Match.PREFIX <
    Match.SUBSTRING` is the ranking rule itself rather than a lookup table beside it.
    """

    PREFIX = 0
    """The field begins with the query -- typing `round` into "Rounding"."""

    WORD_PREFIX = 1
    """A word inside the field begins with the query.

    The boost ADR-0017 asks for, and the reason a dotted key is worth indexing whole:
    `rounding` is a word-prefix of `decoration:rounding` because `:` ends a word, so the
    expert who types the leaf of a key gets it ranked as though they typed the start."""

    SUBSTRING = 2
    """The query appears, but mid-word: `ound` in "Rounding"."""


def match_kind(haystack: str, needle: str) -> Match | None:
    """How `needle` occurs in `haystack`, or `None` when it does not. Both pre-folded.

    Every occurrence is considered, not just the first, and that is the whole subtlety: in
    "background rounding" the first `round` sits mid-word inside "background", and stopping
    there would rank the Option as a bare substring hit when the word-prefix it also has is
    the better answer. Scanning on costs a `str.find` per occurrence over a field of a few
    dozen characters.
    """
    index = haystack.find(needle)
    if index < 0:
        return None
    if index == 0:
        return Match.PREFIX

    while index > 0:
        if not haystack[index - 1].isalnum():
            return Match.WORD_PREFIX
        index = haystack.find(needle, index + 1)
    return Match.SUBSTRING


@dataclass(frozen=True, slots=True)
class OptionHit:
    """One Option a query found, and why -- enough to rank it and to render it.

    Carries the Option itself rather than its name alone: ranking needs its declaration
    order for the tie-break, and the window needs the visibility tier to decide whether
    opening the hit costs a One-off reveal. Re-reading those out of the Schema by name is a
    lookup the index has already done.
    """

    group: ClassVar[str] = SETTINGS_GROUP

    option: ResolvedOption
    field: Field
    """Which text matched -- the better one, when several did."""

    match: Match

    @property
    def name(self) -> str:
        return self.option.name

    @property
    def title(self) -> str:
        return self.option.title

    @property
    def dotted_key(self) -> str:
        return self.option.dotted_key

    @property
    def rank(self) -> tuple[int, int, int]:
        """The sort key: field, then match quality, then the Option's own Page order.

        Declaration order is ADR-0017's tie-break ("ties break by Page order"), and it is
        the honest one: it is the order the user would have scrolled past these Rows in, so
        an exact tie in the text resolves the way browsing would have.
        """
        return (_FIELD_ORDER.index(self.field), int(self.match), self.option.order)


class EntityKind(enum.StrEnum):
    """The Entity kinds the finder indexes, in the order their hits tie-break.

    #172 adds `WORKSPACE_RULE` and `PRESET`; each kind names the Page it opens (fed to
    `tasks.entity_page_id`) and the noun its result row is labelled with.
    """

    BIND = "bind"
    WINDOW_RULE = "window_rule"
    LAYER_RULE = "layer_rule"
    MONITOR_RULE = "monitor_rule"
    MONITOR_PROFILE = "monitor_profile"

    @property
    def page_kind(self) -> str:
        """The Entity Page this kind lives on, as `entity_page_id` takes it."""
        return _PAGE_KINDS[self]

    @property
    def noun(self) -> str:
        """What a result row calls an entity of this kind -- the words the Pages use."""
        return _NOUNS[self]


_PAGE_KINDS = {
    EntityKind.BIND: "binds",
    EntityKind.WINDOW_RULE: "window_rules",
    EntityKind.LAYER_RULE: "layer_rules",
    EntityKind.MONITOR_RULE: "monitors",
    EntityKind.MONITOR_PROFILE: "monitors",
}

_NOUNS = {
    EntityKind.BIND: "Keybind",
    EntityKind.WINDOW_RULE: "Window rule",
    EntityKind.LAYER_RULE: "Layer rule",
    EntityKind.MONITOR_RULE: "Display rule",
    EntityKind.MONITOR_PROFILE: "Display profile",
}

EntityTarget = Bind | WindowRule | LayerRule | MonitorRule | str
"""What a hit points at: the entity object itself, or a Monitor profile's slug."""


@dataclass(frozen=True, slots=True)
class EntityHit:
    """One Entity a query found: what it is, what its row says, and why it matched.

    Carries the entity object rather than its position alone, because the position is
    exactly what goes stale: a hit opened after an insert above it must land on *its* bind,
    not on whatever now sits where it was. `resolve` finds it again at open.
    """

    group: ClassVar[str] = ENTITIES_GROUP

    kind: EntityKind
    target: EntityTarget
    index: int
    """The position the entity had in its list when the entries were built -- the
    tie-break between two identical binds (ADR-0007), never an address on its own."""

    title: str
    subtitle: str
    """The row's own detail line: the Action, the Label's summary, the rule's fields."""

    badge: str | None
    """The row's badge words (#139's vocabulary for a bind, "Disabled" for a rule)."""

    field: Field
    match: Match
    order: int
    """Kind order then list order: the Entity half's "Page order" tie-break."""

    @property
    def rank(self) -> tuple[int, int, int]:
        return (_FIELD_ORDER.index(self.field), int(self.match), self.order)


Hit = OptionHit | EntityHit
"""One result row. Each carries its own `group`, so the finder heads groups by asking."""


@dataclass(frozen=True, slots=True)
class _Entry:
    """One indexed Option: the Option plus its three fields, pre-folded.

    Pre-folded because the alternative is calling `str.casefold` three times per Option per
    keystroke. The corpus is ~350 Options and the entry is built once at startup, so the
    per-query work is three `find` scans over strings that are already the right case.
    """

    option: ResolvedOption
    texts: tuple[str, str, str]
    """Folded title, dotted key and description, in `_FIELD_ORDER`."""


@dataclass(frozen=True, slots=True)
class _EntityEntry:
    """One indexed Entity: the hit it becomes, less the match, plus its folded fields.

    Title is what the row shows; key is the text an expert addresses it by (a raw Trigger
    and dispatcher path, a Match); description is everything else worth finding it by.
    """

    kind: EntityKind
    target: EntityTarget
    index: int
    title: str
    subtitle: str
    badge: str | None
    order: int
    texts: tuple[str, str, str]

    def hit(self, field: Field, match: Match) -> EntityHit:
        return EntityHit(
            kind=self.kind,
            target=self.target,
            index=self.index,
            title=self.title,
            subtitle=self.subtitle,
            badge=self.badge,
            field=field,
            match=match,
            order=self.order,
        )


Profiles = tuple[tuple[str, MonitorProfile], ...]


class EntitySource(Protocol):
    """What the index reads Entities from: the Session, or a test's stand-in for one."""

    @property
    def model(self) -> ConfigModel: ...

    @property
    def monitor_profiles_revision(self) -> int: ...

    def monitor_profiles(self) -> Profiles: ...


_Snapshot = tuple[tuple[object, ...], ...]


def _snapshot(entities: EntitySet, profiles: Profiles) -> _Snapshot:
    """The inputs the entity entries are a function of, as comparable tuples.

    Submaps are in it because a bind's empty-submap badge reads them. Comparing is cheap
    where it matters: tuple `==` checks identity per element first, and an unchanged list
    holds the very objects the entries were built from.
    """
    return (
        tuple(entities.binds),
        tuple(entities.submaps),
        tuple(entities.window_rules),
        tuple(entities.layer_rules),
        tuple(entities.monitors),
        profiles,
    )


class SearchIndex:
    """Every Option and Entity, findable by its salient text (ADR-0017).

    One in-memory index, no persistence. The Option entries are built at construction;
    the Entity entries on the first query, and again whenever a query finds the model's
    lists (or the profile store's revision) moved since -- see the module docstring.
    """

    def __init__(self, entries: tuple[_Entry, ...], source: EntitySource | None = None) -> None:
        self._entries = entries
        self._source = source
        self._entity_entries: tuple[_EntityEntry, ...] | None = None
        self._built_from: _Snapshot | None = None
        self._profiles: Profiles = ()
        self._profiles_revision: int | None = None

    @classmethod
    def build(cls, schema: Schema, source: EntitySource | None = None) -> SearchIndex:
        """Index the whole Schema -- every tier, unfiltered -- and Entities from `source`.

        Deliberately not given the Advanced switch or the active View: an index that knew
        about either would be an index that goes stale when they change, and ADR-0017's
        first sentence is that search sees everything regardless. No `source`, no Entity
        group: the Options alone, which is what the Option golden pins.
        """
        return cls(
            tuple(
                _Entry(
                    option=option,
                    texts=(
                        option.title.casefold(),
                        option.dotted_key.casefold(),
                        option.description.casefold(),
                    ),
                )
                for option in schema
            ),
            source,
        )

    def __len__(self) -> int:
        """How many Options are indexed."""
        return len(self._entries)

    @property
    def entity_count(self) -> int | None:
        """How many Entity entries the last query built over, `None` before the first."""
        return None if self._entity_entries is None else len(self._entity_entries)

    def query(self, text: str, *, limit: int | None = None) -> tuple[Hit, ...]:
        """The Options then the Entities matching `text`, each group best first.

        `limit` caps each group separately, so a query that names fifty Options still lists
        the bind it also names. An all-whitespace or empty query returns nothing rather than
        everything: the finder shows the ordinary nav list while the entry is empty
        (ADR-0017), and "no query" is that state, not a request for all 353 Rows.
        """
        needle = text.strip().casefold()
        if not needle:
            return ()

        options = [hit for entry in self._entries if (hit := _best(entry, needle)) is not None]
        options.sort(key=lambda hit: hit.rank)
        entities = [
            hit
            for entry in self._current_entities()
            if (hit := _best(entry, needle)) is not None
        ]
        entities.sort(key=lambda hit: hit.rank)
        if limit is not None:
            options, entities = options[:limit], entities[:limit]
        return (*options, *entities)

    def _current_entities(self) -> tuple[_EntityEntry, ...]:
        """The entity entries as of now, rebuilt only when their inputs moved."""
        if self._source is None:
            return ()
        revision = self._source.monitor_profiles_revision
        if revision != self._profiles_revision:
            self._profiles = self._source.monitor_profiles()
            self._profiles_revision = revision
        entities = self._source.model.entities
        snapshot = _snapshot(entities, self._profiles)
        if self._entity_entries is None or snapshot != self._built_from:
            self._entity_entries = entity_entries(entities, self._profiles)
            self._built_from = snapshot
        return self._entity_entries


def _best(entry: _Entry | _EntityEntry, needle: str) -> Hit | None:
    """The strongest field match on one entry, or `None` if the query misses it.

    Strongest by the *field* order first, so an Option whose title merely contains the query
    still outranks one whose description begins with it -- which is ADR-0017's ranking read
    literally, and the reason a search for `blur` does not surface every Option that
    mentions blurring in passing above the Blur switch itself.
    """
    for field, haystack in zip(_FIELD_ORDER, entry.texts, strict=True):
        match = match_kind(haystack, needle)
        if match is not None:
            if isinstance(entry, _Entry):
                return OptionHit(option=entry.option, field=field, match=match)
            return entry.hit(field, match)
    return None


# --- the Entity entries ----------------------------------------------------------------------


def entity_entries(
    entities: EntitySet, profiles: Sequence[tuple[str, MonitorProfile]]
) -> tuple[_EntityEntry, ...]:
    """Every indexed Entity, in kind order then list order, worded as its row words it.

    The words come from `entity_text`, the functions the Pages build their rows with, so a
    bind found here reads exactly as the Binds Page lists it -- Trigger, Action and badge.
    """
    empty = frozenset(empty_submaps(entities))
    texts: list[
        tuple[EntityKind, EntityTarget, int, str, str, str | None, tuple[str, ...]]
    ] = []

    for index, bind in enumerate(entities.binds):
        badge = bind_badge(bind, empty_submaps=empty)
        call = bind.dispatcher
        texts.append(
            (
                EntityKind.BIND,
                bind,
                index,
                trigger_text(bind),
                action_text(bind),
                badge.text if badge is not None else None,
                (
                    f"{bind.keys}\n{call.path if call is not None else ''}",
                    f"{action_text(bind)}\n{bind.options.description}\n{bind.submap or ''}",
                ),
            )
        )
    rule_lists: tuple[tuple[EntityKind, Sequence[WindowRule | LayerRule]], ...] = (
        (EntityKind.WINDOW_RULE, entities.window_rules),
        (EntityKind.LAYER_RULE, entities.layer_rules),
    )
    for kind, rules in rule_lists:
        for index, rule in enumerate(rules):
            texts.append(
                (
                    kind,
                    rule,
                    index,
                    rule_title(rule),
                    rule_subtitle(rule),
                    None if rule.enabled else "Disabled",
                    (match_text(rule), effects_text(rule)),
                )
            )
    for index, monitor in enumerate(entities.monitors):
        catch_all = monitor.output == CATCH_ALL_OUTPUT
        texts.append(
            (
                EntityKind.MONITOR_RULE,
                monitor,
                index,
                "Any other display" if catch_all else monitor.output,
                rule_summary(monitor),
                None,
                (monitor.output, rule_summary(monitor)),
            )
        )
    for index, (slug, profile) in enumerate(profiles):
        texts.append(
            (
                EntityKind.MONITOR_PROFILE,
                slug,
                index,
                profile.name,
                profile_summary(profile),
                None,
                ("", ""),
            )
        )

    return tuple(
        _EntityEntry(
            kind=kind,
            target=target,
            index=index,
            title=title,
            subtitle=subtitle,
            badge=badge,
            order=order,
            texts=(title.casefold(), key.casefold(), description.casefold()),
        )
        for order, (
            kind,
            target,
            index,
            title,
            subtitle,
            badge,
            (key, description),
        ) in enumerate(texts)
    )


def resolve(hit: EntityHit, source: EntitySource) -> int | None:
    """Where `hit`'s entity sits in its list *now*, or `None` when it is gone.

    By identity first: an unchanged entity is the very object the hit holds, wherever an
    insert or a move has put it. Then by equality, which is what survives a reload that
    re-read every entity as a new object; of several equal ones (two identical binds are
    legal, ADR-0007) the one still at the hit's own position wins. Never by position alone,
    which is how a stale hit would open the wrong row.
    """
    items: Sequence[object]
    match hit.kind:
        case EntityKind.BIND:
            items = source.model.entities.binds
        case EntityKind.WINDOW_RULE:
            items = source.model.entities.window_rules
        case EntityKind.LAYER_RULE:
            items = source.model.entities.layer_rules
        case EntityKind.MONITOR_RULE:
            items = source.model.entities.monitors
        case EntityKind.MONITOR_PROFILE:
            items = [slug for slug, _profile in source.monitor_profiles()]

    for position, item in enumerate(items):
        if item is hit.target:
            return position
    equal = [position for position, item in enumerate(items) if item == hit.target]
    if hit.index in equal:
        return hit.index
    return equal[0] if equal else None
