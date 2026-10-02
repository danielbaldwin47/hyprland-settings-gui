"""What a workspace rule's selector may say, headless (ADR-0008 § Workspace rules, #160).

A selector is either *simple* -- a workspace id (`5`), a name (`name:web`) or a special
workspace (`special:scratch`) -- or a *filter* (`w[tv1]`, `r[2-4] f[1]s[false]`): one or
more `letter[body]` expressions that match workspaces by their windows, id, monitor or
state. The grammar is the Hyprland wiki's (`naming-conventions.md` § Workspace filters,
cited by `docs/research/lua-api-surface.md` §9).

`Hyprland --verify-config` does not judge a selector: it loads `x[1]` and `w[tv1` as it
loads `3`, so this module is the only check a typed one gets. It therefore blocks only
what cannot be a selector at all (an unknown letter, an unbalanced bracket, text outside
any filter) and warns about a body that does not match its letter's documented shape,
because the grammar is prose and a newer Hyprland may read more than the wiki says.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

FILTER_LETTERS = "wrfsnm"
"""The six filter letters: windows, id range, fullscreen state, special, named, monitor."""

BLANK_MESSAGE = "Type a workspace selector, such as 5 or name:web."


class Severity(enum.Enum):
    ERROR = "error"
    """Cannot be a selector: Save refuses a selector the user typed."""
    WARNING = "warning"
    """Probably not what was meant: shown, never blocking."""


@dataclass(frozen=True, slots=True)
class SelectorIssue:
    severity: Severity
    message: str


class SimpleKind(enum.Enum):
    NUMBER = "number"
    NAME = "name"
    SPECIAL = "special"


_NUMBER = re.compile(r"\d+")
_BODY_CHECKS: dict[str, tuple[re.Pattern[str], str]] = {
    "r": (re.compile(r"\d+-\d+"), "takes a range such as r[2-4]."),
    "s": (re.compile(r"true|false"), "takes true or false."),
    "n": (re.compile(r"true|false|[se]:.+"), "takes true, false, s:text or e:text."),
    "f": (re.compile(r"-1|0|1|2"), "takes -1, 0, 1 or 2."),
    "w": (
        re.compile(r"[tfgvp]*\d+(-\d+)?"),
        "takes flags t, f, g, v or p, then a count such as w[tv1] or w[t1-2].",
    ),
}


def parse_simple(selector: str) -> tuple[SimpleKind, str] | None:
    """The picker form of a selector, or `None` when only the advanced entry can say it.

    Only a selector the pickers would write back byte for byte: one with stray spaces
    around it stays in the advanced entry, so opening a rule never rewrites its selector.
    """
    if selector != selector.strip():
        return None
    if _NUMBER.fullmatch(selector):
        return SimpleKind.NUMBER, selector
    for kind, prefix in ((SimpleKind.NAME, "name:"), (SimpleKind.SPECIAL, "special:")):
        if selector.startswith(prefix) and len(selector) > len(prefix):
            return kind, selector[len(prefix) :]
    return None


def compose_simple(kind: SimpleKind, value: str) -> str:
    """The selector a picker choice stands for."""
    text = value.strip()
    if kind is SimpleKind.NUMBER:
        return text
    return f"{kind.value}:{text}"


def check_selector(selector: str) -> SelectorIssue | None:
    """Why a selector is wrong (blocking) or doubtful (not), or `None` when it is clean."""
    text = selector.strip()
    if not text:
        return SelectorIssue(Severity.ERROR, BLANK_MESSAGE)
    if _NUMBER.fullmatch(text) or re.fullmatch(r"-\d+", text) or text == "special":
        return None
    for prefix, advice in (
        ("name:", "A name: selector needs a name after the colon."),
        (
            "special:",
            "A special: selector needs a name after the colon, or just write special.",
        ),
    ):
        if text.startswith(prefix):
            return None if len(text) > len(prefix) else SelectorIssue(Severity.ERROR, advice)
    if "[" not in text and "]" not in text:
        return SelectorIssue(
            Severity.WARNING,
            f"Hyprland reads “{text}” as a name. Write name:{text} to say so.",
        )
    return _check_filters(text)


def _check_filters(text: str) -> SelectorIssue | None:
    warning: SelectorIssue | None = None
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        opening = text.find("[", position)
        stray = text[position : opening if opening != -1 else len(text)]
        if "]" in stray:
            return SelectorIssue(Severity.ERROR, "This selector has a ] with no [ before it.")
        if opening == -1:
            return SelectorIssue(
                Severity.ERROR,
                f"“{text[position:].strip()}” sits outside any filter. "
                "Write each filter as a letter and [ ].",
            )
        head = stray.strip()
        if head != text[opening - 1 : opening] or head not in tuple(FILTER_LETTERS):
            if len(head) == 1 and head not in FILTER_LETTERS:
                return SelectorIssue(
                    Severity.ERROR,
                    f"“{head}[” is not a workspace filter. "
                    "Filters start with w, r, f, s, n or m.",
                )
            return SelectorIssue(
                Severity.ERROR,
                f"“{head}” sits outside any filter. Write each filter as a letter and [ ].",
            )
        closing = text.find("]", opening)
        if closing == -1:
            return SelectorIssue(Severity.ERROR, "This selector is missing a closing ].")
        body = text[opening + 1 : closing]
        if "[" in body:
            return SelectorIssue(Severity.ERROR, "This selector has a [ inside another [ ].")
        warning = warning or _check_body(head, body)
        position = closing + 1
    return warning


def _check_body(letter: str, body: str) -> SelectorIssue | None:
    if not body:
        return SelectorIssue(Severity.WARNING, f"{letter}[ ] is empty.")
    check = _BODY_CHECKS.get(letter)
    if check is None or check[0].fullmatch(body):
        return None
    return SelectorIssue(Severity.WARNING, f"{letter}[{body}] {check[1]}")
