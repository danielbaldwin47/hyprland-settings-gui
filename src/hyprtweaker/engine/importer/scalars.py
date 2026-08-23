"""How hyprlang reads a scalar, in one place.

Every mapping module needs the same four readings, and they are hyprlang's rather than
Python's. Keeping one copy matters more than the line count: `truthy` and `bool_prefix`
differ on purpose (one is the rule window rules use, the other the rule config values use),
and three private copies would drift into each other the first time someone "fixed" one.

None of these raise. A value hyprlang would have shrugged at is a Loss finding, decided by
the caller that knows which option it was reading.
"""

from __future__ import annotations

__all__ = ["bool_prefix", "direction", "number", "truthy"]

_TRUE_WORDS: tuple[str, ...] = ("true", "yes", "on")
_FALSE_WORDS: tuple[str, ...] = ("false", "no", "off")


def truthy(text: str) -> bool:
    """hyprlang's `truthy()`: `1`, or a `true`/`yes`/`on` prefix, case-insensitively.

    The rule window rules and dispatcher arguments are read with, so `float on`, `float 1`
    and `float true` all mean the same thing (`MiscFunctions.cpp:829-843`).
    """
    lowered = text.strip().lower()
    return lowered == "1" or lowered.startswith(_TRUE_WORDS)


def bool_prefix(text: str) -> bool | None:
    """hyprlang's `parseInt` truth rule, or None when the text is not a truth word at all.

    Distinct from `truthy` in the one way that matters: it can say "this is not a boolean".
    `truthy` answers False for both `off` and `4`, which is right for a rule effect and
    wrong for a config value, where `4` is a number and `off` is a zero.

    It matches a *prefix* and ignores the rest, which is not sloppiness on our part --
    `animations:enabled = yes, please :)` is a valid `1` to hyprlang, and shipped rices
    contain exactly that (`ParserUtils.cpp:134-150`).
    """
    lowered = text.strip().lower()
    if lowered.startswith(_TRUE_WORDS) or lowered == "1":
        return True
    if lowered.startswith(_FALSE_WORDS) or lowered == "0":
        return False
    return None


def number(text: str) -> int | float | None:
    """An int if it reads as one, else a float, else None. `0x` forms included."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return int(stripped, 0) if stripped.lower().startswith(("0x", "-0x")) else int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return None


def direction(text: str) -> str:
    """A direction argument as the single letter both engines accept.

    hyprlang kept only the first character (`l`/`r`/`u`/`d`), so `left` and `l` were always
    the same argument; Lua accepts that letter too.
    """
    stripped = text.strip().lower()
    return stripped[0] if stripped else ""
