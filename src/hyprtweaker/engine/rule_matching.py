"""How many open windows (or layer surfaces) a rule's Match would catch (#113, ADR-0008).

The Rule editor's "Matches N open windows" badge: a client-side approximation over what
`hyprctl -j clients` (or `layers`) answered, never the compositor's own verdict. Three
honest limits, each handled by saying less rather than guessing:

- **Regex dialects.** Hyprland matches with RE2's `FullMatch`; this uses Python's
  `re.fullmatch`. They agree on every pattern a rule normally carries and differ on
  exotic ones; ADR-0008 accepts that. A pattern Python cannot compile gives no count.
- **Props the payload cannot answer.** `modal` has no field in the payload, `focus` is
  only a history position, a workspace selector (`r[1-3]`) or a numeric `content` id
  needs the compositor's own state. Such a prop is never counted as a match: the count
  covers the props that can be checked and names the ones that were not
  (`MatchCount.skipped`). A rule whose every prop was skipped has no count at all.
- **Absent compositor.** Callers pass the answer only when there is one; this module
  never sees "offline", so it cannot turn it into zero.

The field mapping was read off a nested Hyprland's `j/clients` and the match semantics
checked there with `hyprctl -j clients` (`_fake_hyprland.CLIENTS`, #113): `float`/`pin`
read `floating`/`pinned`, `fullscreen` is "any fullscreen state", the two
`fullscreen_state_*` props read `fullscreen` and `fullscreenClient`, a `tag` compares names
with the trailing `*` Hyprland adds to a rule-set tag stripped from both sides, and a
`workspace` is an id or `name:<name>`.

Toolkit-free, so it is typed and tested without GTK.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hyprtweaker.engine.rules_catalog import is_negated, prop_title, strip_negation

Matcher = Callable[[Mapping[str, Any]], bool | None]
"""One prop's test on one target: `None` when the target lacks the field to answer it."""

Builder = Callable[[Any], Matcher | None]
"""A prop's matcher for one value, or `None` when this value cannot be checked here."""


@dataclass(frozen=True, slots=True)
class MatchCount:
    """What a Match catches: how many targets, and which props were not checked."""

    count: int
    skipped: tuple[str, ...]


def _regex(field: str, *, numeric_is_unanswerable: bool = False) -> Builder:
    def build(value: Any) -> Matcher | None:
        if not isinstance(value, str):
            return None
        negated = is_negated(value)
        pattern_text = strip_negation(value)
        if numeric_is_unanswerable and pattern_text.isdigit():
            return None
        pattern = re.compile(pattern_text)

        def matches(target: Mapping[str, Any]) -> bool | None:
            text = target.get(field)
            if not isinstance(text, str):
                return None
            return (pattern.fullmatch(text) is not None) != negated

        return matches

    return build


def _flag(read: Callable[[Mapping[str, Any]], bool | None]) -> Builder:
    def build(value: Any) -> Matcher | None:
        if not isinstance(value, bool):
            return None
        return lambda target: None if (state := read(target)) is None else state == value

    return build


def _field_flag(field: str) -> Builder:
    def read(target: Mapping[str, Any]) -> bool | None:
        state = target.get(field)
        return state if isinstance(state, bool) else None

    return _flag(read)


def _state_flag(field: str, *, nonzero: bool = False, nonempty: bool = False) -> Builder:
    def read(target: Mapping[str, Any]) -> bool | None:
        state = target.get(field)
        if nonzero and isinstance(state, int) and not isinstance(state, bool):
            return state != 0
        if nonempty and isinstance(state, list):
            return bool(state)
        return None

    return _flag(read)


def _int_field(field: str) -> Builder:
    def build(value: Any) -> Matcher | None:
        if not isinstance(value, int) or isinstance(value, bool):
            return None

        def matches(target: Mapping[str, Any]) -> bool | None:
            state = target.get(field)
            if not isinstance(state, int) or isinstance(state, bool):
                return None
            return state == value

        return matches

    return build


def _workspace(value: Any) -> Matcher | None:
    if not isinstance(value, str) or is_negated(value):
        return None
    name = value.removeprefix("name:") if value.startswith("name:") else None
    identifier = int(value) if value.isdecimal() else None
    if name is None and identifier is None:
        return None  # a selector (`r[1-3]`, `w[t1]`): the compositor's own state decides

    def matches(target: Mapping[str, Any]) -> bool | None:
        workspace = target.get("workspace")
        if not isinstance(workspace, Mapping):
            return None
        if name is not None:
            return workspace.get("name") == name
        return workspace.get("id") == identifier

    return matches


def _tag(value: Any) -> Matcher | None:
    if not isinstance(value, str) or is_negated(value):
        return None
    wanted = value.removesuffix("*")

    def matches(target: Mapping[str, Any]) -> bool | None:
        tags = target.get("tags")
        if not isinstance(tags, list):
            return None
        return any(isinstance(tag, str) and tag.removesuffix("*") == wanted for tag in tags)

    return matches


_WINDOW: dict[str, Builder] = {
    "class": _regex("class"),
    "title": _regex("title"),
    "initial_class": _regex("initialClass"),
    "initial_title": _regex("initialTitle"),
    "xdg_tag": _regex("xdgTag"),
    "content": _regex("contentType", numeric_is_unanswerable=True),
    "float": _field_flag("floating"),
    "xwayland": _field_flag("xwayland"),
    "pin": _field_flag("pinned"),
    "fullscreen": _state_flag("fullscreen", nonzero=True),
    "group": _state_flag("grouped", nonempty=True),
    "fullscreen_state_internal": _int_field("fullscreen"),
    "fullscreen_state_client": _int_field("fullscreenClient"),
    "workspace": _workspace,
    "tag": _tag,
}
"""Window match props the client payload can answer. Absent on purpose: `modal` (no
field) and `focus` (the payload only has a focus history position, and a live rule
evaluates it at map time, before the window is focused)."""

_LAYER: dict[str, Builder] = {"namespace": _regex("namespace")}

_BUILDERS = {"window": _WINDOW, "layer": _LAYER}


def count_matches(
    kind: str, match: Mapping[str, Any], targets: Sequence[Mapping[str, Any]]
) -> MatchCount | None:
    """How many `targets` the `match` catches, or `None` when there is nothing to say.

    `None` for an empty Match, an invalid regex, or a Match none of whose props can be
    checked. `targets` is whatever the compositor answered, and `()` is a real answer:
    zero windows open.
    """
    if kind not in _BUILDERS:
        raise ValueError(f"unknown rule kind {kind!r}")
    builders = _BUILDERS[kind]

    checks: list[Matcher] = []
    skipped: list[str] = []
    for name, value in match.items():
        build = builders.get(name)
        try:
            matcher = build(value) if build is not None else None
        except re.error:
            return None
        if matcher is None or any(matcher(target) is None for target in targets):
            skipped.append(name)
            continue
        checks.append(matcher)

    if not checks:
        return None
    count = sum(all(check(target) for check in checks) for target in targets)
    return MatchCount(count, tuple(skipped))


def badge_text(kind: str, result: MatchCount) -> str:
    """The badge's words: `Matches 3 open windows, not checking tag`."""
    if kind == "layer":
        noun, nouns = "layer surface", "layer surfaces"
    else:
        noun, nouns = "open window", "open windows"
    text = f"Matches {result.count} {noun if result.count == 1 else nouns}"
    if result.skipped:
        names = [prop_title(name).lower() for name in result.skipped]
        listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        text += f", not checking {listed}"
    return text
