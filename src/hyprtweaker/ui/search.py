"""The finder's index: which Options a query names, and in what order (ADR-0017).

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
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from hyprtweaker.engine.schema import ResolvedOption, Schema


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
class Hit:
    """One Option a query found, and why -- enough to rank it and to render it.

    Carries the Option itself rather than its name alone: ranking needs its declaration
    order for the tie-break, and the window needs the visibility tier to decide whether
    opening the hit costs a One-off reveal. Re-reading those out of the Schema by name is a
    lookup the index has already done.
    """

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


class SearchIndex:
    """Every Option, findable by title, dotted key or description (ADR-0017).

    One in-memory index built at startup -- no persistence and no per-query rebuild. The
    corpus is under a thousand entries, so the whole scan is a few hundred `str.find` calls
    and there is nothing here worth caching harder than this.
    """

    def __init__(self, entries: tuple[_Entry, ...]) -> None:
        self._entries = entries

    @classmethod
    def build(cls, schema: Schema) -> SearchIndex:
        """Index the whole Schema -- every tier, unfiltered.

        Deliberately not given the Advanced switch or the active View: an index that knew
        about either would be an index that goes stale when they change, and ADR-0017's
        first sentence is that search sees everything regardless.
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
            )
        )

    def __len__(self) -> int:
        return len(self._entries)

    def query(self, text: str, *, limit: int | None = None) -> tuple[Hit, ...]:
        """The Options matching `text`, best first.

        An all-whitespace or empty query returns nothing rather than everything: the finder
        shows the ordinary nav list while the entry is empty (ADR-0017), and "no query" is
        that state, not a request for all 353 Rows.
        """
        needle = text.strip().casefold()
        if not needle:
            return ()

        hits = [hit for entry in self._entries if (hit := _best(entry, needle)) is not None]
        hits.sort(key=lambda hit: hit.rank)
        return tuple(hits if limit is None else hits[:limit])


def _best(entry: _Entry, needle: str) -> Hit | None:
    """The strongest field match on one Option, or `None` if the query misses it.

    Strongest by the *field* order first, so an Option whose title merely contains the query
    still outranks one whose description begins with it -- which is ADR-0017's ranking read
    literally, and the reason a search for `blur` does not surface every Option that
    mentions blurring in passing above the Blur switch itself.
    """
    for field, haystack in zip(_FIELD_ORDER, entry.texts, strict=True):
        match = match_kind(haystack, needle)
        if match is not None:
            return Hit(option=entry.option, field=field, match=match)
    return None
