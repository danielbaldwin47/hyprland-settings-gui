"""PROTOTYPE — throwaway. Keysym validation through the same libxkbcommon call
Hyprland's Lua bind parser uses (`xkb_keysym_from_name` with
XKB_KEYSYM_CASE_INSENSITIVE — LuaBindingsToplevel.cpp:109).

Under the legacy engine an unresolvable key name was accepted and silently never
matched (KeybindManager.cpp:713-722); under Lua it is a hard config error, so the
importer has to detect it up front.
"""
import ctypes
import ctypes.util

XKB_KEYSYM_CASE_INSENSITIVE = 1

_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        path = ctypes.util.find_library("xkbcommon")
        if not path:
            return False
        lib = ctypes.CDLL(path)
        lib.xkb_keysym_from_name.restype = ctypes.c_uint32
        lib.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
        _LIB = lib
    return _LIB


def is_keysym(name):
    """None when libxkbcommon is unavailable (caller should not warn)."""
    lib = _lib()
    if lib is False:
        return None
    try:
        return bool(lib.xkb_keysym_from_name(name.encode("utf-8"),
                                             XKB_KEYSYM_CASE_INSENSITIVE))
    except Exception:
        return None
