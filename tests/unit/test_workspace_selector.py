"""The workspace selector validator and its simple/advanced split (#160).

The grammar is the Hyprland wiki's (`naming-conventions.md` § Workspace filters), not
something `Hyprland --verify-config` judges: it loads `x[1]` and `w[tv1` as happily as `3`
(checked during #160), so these fixtures are the wiki's own examples and the validator
is the only check a typed selector gets.
"""

from __future__ import annotations

import pytest

from hyprtweaker.engine.workspace_selector import (
    Severity,
    SimpleKind,
    check_selector,
    compose_simple,
    parse_simple,
)


@pytest.mark.parametrize(
    "selector",
    [
        "3",
        "-99",
        "name:coding",
        "name:Better anime",
        "special",
        "special:scratchpad",
        "w[tv1]",
        "w[t1-2]",
        "w[1]",
        "w[gp3]",
        "r[2-4]",
        "f[-1]",
        "f[2]",
        "s[true]",
        "s[false]",
        "n[true]",
        "n[s:web]",
        "n[e:dev]",
        "m[DP-1]",
        "m[desc:Chimei Innolux Corporation 0x150C]",
        "f[1]s[false]",
        "r[2-4] w[t1]",
        "w[tv1]s[false]",
    ],
)
def test_the_wikis_own_examples_are_clean(selector: str) -> None:
    assert check_selector(selector) is None


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        ("", "Type a workspace selector, such as 5 or name:web."),
        ("   ", "Type a workspace selector, such as 5 or name:web."),
        ("x[1]", "“x[” is not a workspace filter. Filters start with w, r, f, s, n or m."),
        ("W[1]", "“W[” is not a workspace filter. Filters start with w, r, f, s, n or m."),
        ("w[tv1", "This selector is missing a closing ]."),
        ("w[1]]", "This selector has a ] with no [ before it."),
        ("w[[1]]", "This selector has a [ inside another [ ]."),
        ("w[1] foo", "“foo” sits outside any filter. Write each filter as a letter and [ ]."),
        ("name:", "A name: selector needs a name after the colon."),
        (
            "special:",
            "A special: selector needs a name after the colon, or just write special.",
        ),
    ],
)
def test_a_selector_hyprland_cannot_read_blocks_with_a_sentence(
    selector: str, message: str
) -> None:
    issue = check_selector(selector)

    assert issue is not None
    assert issue.severity is Severity.ERROR
    assert issue.message == message


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        ("w[]", "w[ ] is empty."),
        ("r[5]", "r[5] takes a range such as r[2-4]."),
        ("s[yes]", "s[yes] takes true or false."),
        ("n[x:web]", "n[x:web] takes true, false, s:text or e:text."),
        ("f[7]", "f[7] takes -1, 0, 1 or 2."),
        (
            "w[tx1]",
            "w[tx1] takes flags t, f, g, v or p, then a count such as w[tv1] or w[t1-2].",
        ),
        ("Web", "Hyprland reads “Web” as a name. Write name:Web to say so."),
    ],
)
def test_a_selector_that_may_not_mean_what_it_says_warns_without_blocking(
    selector: str, message: str
) -> None:
    issue = check_selector(selector)

    assert issue is not None
    assert issue.severity is Severity.WARNING
    assert issue.message == message


def test_the_first_error_wins_over_an_earlier_warning() -> None:
    issue = check_selector("w[] x[1]")

    assert issue is not None
    assert issue.severity is Severity.ERROR


@pytest.mark.parametrize(
    ("selector", "kind", "value"),
    [
        ("5", SimpleKind.NUMBER, "5"),
        ("name:web", SimpleKind.NAME, "web"),
        ("name:5", SimpleKind.NAME, "5"),
        ("name:Better anime", SimpleKind.NAME, "Better anime"),
        ("special:scratch", SimpleKind.SPECIAL, "scratch"),
    ],
)
def test_the_simple_pickers_express_an_id_a_name_and_a_special(
    selector: str, kind: SimpleKind, value: str
) -> None:
    assert parse_simple(selector) == (kind, value)
    assert compose_simple(kind, value) == selector


@pytest.mark.parametrize("selector", ["w[tv1]", "r[2-4]", "-99", "special", "name:", "Web", ""])
def test_a_selector_the_pickers_cannot_express_has_no_simple_form(selector: str) -> None:
    assert parse_simple(selector) is None


def test_composing_trims_what_the_user_typed() -> None:
    assert compose_simple(SimpleKind.NAME, "  web ") == "name:web"
    assert compose_simple(SimpleKind.NUMBER, " 7 ") == "7"


def test_a_selector_with_stray_spaces_stays_advanced_so_opening_it_rewrites_nothing() -> None:
    assert parse_simple(" 5") is None
    assert parse_simple("name:web ") is None
