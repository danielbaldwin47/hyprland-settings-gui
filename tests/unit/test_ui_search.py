"""What the finder finds, and in what order (ADR-0017, ticket #72).

`search.py` is toolkit-free for the same reason `plan.py` is: ranking is a decision about
text, and "does `round` still reach Corner rounding before Blur passes?" is a question worth
asking on a machine with no display. The UI smoke tier then only has to check that a hit
navigates -- not that it was the right hit.

The golden is the corpus-level half. Ranking rules stated as asserts can each be satisfied
by a plausible-looking implementation that is wrong about the other 350 Options; a golden
over the real shipped Schema is what makes a change in *any* of them show up as a diff a
human reads before it ships.
"""

from __future__ import annotations

import pytest
from _golden import assert_matches_golden
from _support import GOLDEN_DIR, SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.schema import Visibility, load_schema
from hyprtweaker.ui.search import Field, Match, SearchIndex, match_kind

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)
INDEX = SearchIndex.build(SCHEMA)

GOLDEN_QUERIES = (
    "border",
    "size",
    "group",
    "tile",
    "kb_layout",
    "manual_crash",
)
"""Six queries chosen to pin one rule each, so a golden diff says *which* rule moved.

`border` -- the field's three tiers in order. `size` -- word-prefix ahead of bare substring,
which is ticket #72's ranking criterion. `group` -- a title substring still beating a dotted
key that matches from its first character, which is ADR-0017's field order read literally.
`tile` -- Options reachable by their help text alone. `kb_layout` -- the expert's exact key.
`manual_crash` -- the `hidden` tier, which no browsing in either View would reach."""


# --- the index covers everything --------------------------------------------------------------


def test_indexes_every_option() -> None:
    """All visibility tiers, unfiltered (ADR-0017): the index is the whole Schema."""
    assert len(INDEX) == len(SCHEMA)


def test_finds_the_hidden_tier() -> None:
    """`debug:manual_crash` is Config-view-only and never browsable in Tasks.

    Reaching it by name is the entire reason the index ignores visibility; a finder that
    respected the Advanced switch would make the hidden tier unreachable by any route.
    """
    hits = INDEX.query("manual_crash")
    assert [hit.name for hit in hits] == ["debug:manual_crash"]
    assert SCHEMA["debug:manual_crash"].visibility is Visibility.HIDDEN


def test_finds_by_dotted_key() -> None:
    """The key lives in the Help popover and the index, never the subtitle (ADR-0013)."""
    hits = INDEX.query("kb_layout")
    assert [hit.name for hit in hits] == ["input:kb_layout"]
    assert hits[0].field is Field.KEY


def test_finds_by_help_text() -> None:
    """Description/curated help is indexed, so an Option is reachable by what it does."""
    hits = INDEX.query("tile")
    assert any(hit.field is Field.DESCRIPTION for hit in hits)


def test_is_case_insensitive() -> None:
    assert [hit.name for hit in INDEX.query("BORDER SIZE")] == [
        hit.name for hit in INDEX.query("border size")
    ]


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_empty_query_finds_nothing(query: str) -> None:
    """No query is the nav-list state, not a request for all 353 Rows (ADR-0017)."""
    assert INDEX.query(query) == ()


def test_limit_keeps_the_best() -> None:
    """A capped query is the head of the uncapped one, never a different set."""
    assert INDEX.query("border", limit=5) == INDEX.query("border")[:5]


# --- ranking ----------------------------------------------------------------------------------


def test_word_prefix_outranks_bare_substring() -> None:
    """Ticket #72's ranking criterion, over every title `size` matches.

    Stated as a partition rather than as an expected list: the rule is "every word-prefix
    before every bare substring", and asserting the list would also pin the 14 tie-breaks
    between them, which is the golden's job and not this test's.
    """
    titles = [hit for hit in INDEX.query("size") if hit.field is Field.TITLE]
    kinds = [hit.match for hit in titles]

    assert Match.WORD_PREFIX in kinds and Match.SUBSTRING in kinds
    assert kinds == sorted(kinds), "a bare substring outranked a word-prefix"


def test_title_outranks_a_stronger_key_match() -> None:
    """ADR-0017's field order beats match quality: title substring > dotted-key prefix.

    `group` is the case that makes the rule visible -- 34 Options have keys *beginning*
    `group.`, and the two whose titles merely contain "group" still come first, because
    someone typing a word is naming a setting rather than addressing a Section.
    """
    hits = INDEX.query("group")
    last_title = max(i for i, hit in enumerate(hits) if hit.field is Field.TITLE)
    first_key = min(i for i, hit in enumerate(hits) if hit.field is Field.KEY)

    assert hits[last_title].match is Match.SUBSTRING
    assert hits[first_key].match is Match.PREFIX
    assert last_title < first_key


def test_ties_break_by_page_order() -> None:
    """Equal field and match quality resolve the way scrolling would (ADR-0017)."""
    hits = [
        hit
        for hit in INDEX.query("border")
        if hit.field is Field.TITLE and hit.match is Match.WORD_PREFIX
    ]
    orders = [hit.option.order for hit in hits]
    assert orders == sorted(orders)


# --- match_kind -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("haystack", "needle", "expected"),
    [
        ("rounding", "round", Match.PREFIX),
        ("corner rounding", "round", Match.WORD_PREFIX),
        ("decoration.rounding", "rounding", Match.WORD_PREFIX),
        ("rounding", "ound", Match.SUBSTRING),
        ("rounding", "blur", None),
    ],
)
def test_match_kind(haystack: str, needle: str, expected: Match | None) -> None:
    assert match_kind(haystack, needle) == expected


def test_match_kind_prefers_a_later_word_boundary() -> None:
    """The subtle one: the *first* occurrence is not always the best one.

    In "background rounding" the first `round` sits inside "background". Ranking on that
    occurrence alone would call the whole field a bare substring and bury an Option whose
    second occurrence starts a word -- so the scan has to keep going after a mid-word hit.
    """
    assert match_kind("background rounding", "round") is Match.WORD_PREFIX


# --- golden -----------------------------------------------------------------------------------


def render_index(index: SearchIndex, queries: tuple[str, ...]) -> str:
    """Every query's full result list, as reviewable text.

    Full rather than capped: a truncated golden hides exactly the regression that pushes one
    Option off the end of a list, and these six queries together come to under a hundred rows.
    """
    lines = [
        f"# search index -- Hyprland {index_version()}",
        f"options indexed: {len(index)}",
    ]
    for query in queries:
        hits = index.query(query)
        lines += ["", f"## query: {query} ({len(hits)} hits)"]
        lines.append("field | match | tier | key | title")
        lines += [
            " | ".join(
                (
                    hit.field.value,
                    hit.match.name.lower().replace("_", "-"),
                    hit.option.visibility.value,
                    hit.dotted_key,
                    hit.title,
                )
            )
            for hit in hits
        ]
    return "\n".join(lines) + "\n"


def index_version() -> str:
    return SCHEMA.hyprland_version


def test_index_matches_golden() -> None:
    assert_matches_golden(
        render_index(INDEX, GOLDEN_QUERIES),
        GOLDEN_DIR / f"search-{SAMPLE_VERSION}.txt",
        "the search index",
    )
