"""Is this key name one xkb actually knows?

The question matters because the two engines disagree about the answer's consequences.
hyprlang resolved a key name at *press* time and, finding nothing, simply never fired the
bind -- so a typo lived in a config for years, silently. Lua resolves at *bind* time and
raises a config error (`LuaBindingsToplevel.cpp:111-118`). Converting a config with a dead
keysym therefore turns a bind that quietly did nothing into a config that fails to load,
which is precisely the kind of surprise the Loss report exists to pre-empt (ADR-0009 lists
"dead keysyms" under Needs review).

Answered by asking libxkbcommon, the same library Hyprland asks, through `ctypes` -- no new
dependency, and no second opinion to drift from the compositor's. Hyprland links it, so on
any machine that runs Hyprland it is present.

Degrades to "no opinion" rather than to a guess: if the library cannot be loaded, every name
is reported as unknown-but-unvalidated, and no finding is raised. A validator that guessed
would file false Breakage against perfectly good configs on the machines it failed on.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from functools import lru_cache

__all__ = ["known_keysym", "validator_available"]

XKB_KEYSYM_NO_FLAGS = 0
XKB_KEYSYM_CASE_INSENSITIVE = 1
"""The flag Hyprland passes, which is why `Q` and `q` both bind lowercase q."""

XKB_KEY_NoSymbol = 0


@lru_cache(maxsize=1)
def _library() -> ctypes.CDLL | None:
    """libxkbcommon, or None where it cannot be loaded."""
    name = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
    try:
        library = ctypes.CDLL(name)
    except OSError:  # pragma: no cover -- a machine without libxkbcommon
        return None
    try:
        function = library.xkb_keysym_from_name
    except AttributeError:  # pragma: no cover -- an xkbcommon without the symbol
        return None
    function.restype = ctypes.c_uint32
    function.argtypes = (ctypes.c_char_p, ctypes.c_uint32)
    return library


def validator_available() -> bool:
    """Whether keysym names can be checked at all on this machine."""
    return _library() is not None


@lru_cache(maxsize=2048)
def known_keysym(name: str) -> bool | None:
    """True/False if xkb can be asked, None if it cannot.

    Three-valued on purpose: "I could not check" is not "this is fine", and collapsing the
    two would either hide real dead keysyms or invent imaginary ones.
    """
    library = _library()
    if library is None:
        return None
    stripped = name.strip()
    if not stripped:
        return False
    keysym = library.xkb_keysym_from_name(stripped.encode("utf-8"), XKB_KEYSYM_CASE_INSENSITIVE)
    return bool(keysym != XKB_KEY_NoSymbol)
