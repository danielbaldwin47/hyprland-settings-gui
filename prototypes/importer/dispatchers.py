"""PROTOTYPE — throwaway. Legacy dispatcher name + arg string -> `hl.dsp.*` call.

Transcribed from docs/research/hyprlang-to-lua.md section 2.2, which cites
src/config/legacy/DispatcherTranslator.cpp and
src/config/lua/bindings/LuaBindingsDispatchers.cpp of Hyprland v0.56.2.
Returns (lua_expression | None, [warnings]).
"""
import re

from lua import lit, tbl

# Math::fromChar — src/helpers/math/Direction.hpp:14-24
LEGACY_DIRS = {"l": "l", "r": "r", "t": "u", "u": "u", "b": "d", "d": "d"}
# vim letters that were already broken under the legacy engine (see research 4.3)
VIM_DIRS = {"h": "l", "j": "d", "k": "u"}


def _dir(arg, warns, what):
    a = (arg or "").strip().lower()
    if not a:
        warns.append(("L11", f"{what}: empty direction"))
        return None
    c = a[0]
    if c in LEGACY_DIRS:
        return LEGACY_DIRS[c]
    if c in VIM_DIRS:
        warns.append(("VIMDIR", f"{what}: direction {arg!r} is invalid in both engines "
                                f"(legacy Math::fromChar rejects it); remapped to "
                                f"{VIM_DIRS[c]!r}"))
        return VIM_DIRS[c]
    warns.append(("L11", f"{what}: unsupported direction {arg!r}"))
    return None


def _resize_params(arg, warns, what):
    """DT:427-437 parseWindowVectorArgsRelative. `exact W H` | `dx dy`, `%` forms."""
    toks = (arg or "").split()
    relative = True
    if toks and toks[0].lower() == "exact":
        relative = False
        toks = toks[1:]
    if len(toks) < 2:
        warns.append(("L8", f"{what}: cannot parse resize params {arg!r}"))
        return None
    vals = []
    for t in toks[:2]:
        if t.endswith("%"):
            warns.append(("L8", f"{what}: percentage {t!r} has no Lua representation; "
                                f"emitted as the bare number"))
            t = t[:-1]
        vals.append(t)
    fields = [("x", _num(vals[0])), ("y", _num(vals[1])), ("relative", relative)]
    return fields


def _num(s):
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return lit(s)


def _sel(arg):
    """Window selector strings pass through verbatim (research 2.3)."""
    return arg.strip() if arg and arg.strip() and arg.strip() != "active" else None


def _win(arg, fields):
    s = _sel(arg)
    if s:
        fields.append(("window", s))
    return fields


def _call(path, fields=None):
    if not fields:
        return f"hl.dsp.{path}()"
    return f"hl.dsp.{path}({tbl(fields)})"


def translate(name, args, warns):
    """name: legacy dispatcher; args: everything after it (commas intact)."""
    n = (name or "").strip().lower()
    a = args if args is not None else ""
    a_s = a.strip()
    fn = _TABLE.get(n)
    if fn is None:
        warns.append(("BADDISP", f"unknown dispatcher {name!r}"))
        return None
    return fn(a_s, warns)


# --------------------------------------------------------------------------- #
# individual dispatchers
# --------------------------------------------------------------------------- #
def _d_exec(a, w):
    return f"hl.dsp.exec_cmd({lit(a)})"


def _d_execr(a, w):
    return f"hl.dsp.exec_raw({lit(a)})"


def _d_killactive(a, w):
    return _call("window.close")


def _d_forcekillactive(a, w):
    return _call("window.kill")


def _d_closewindow(a, w):
    return _call("window.close", _win(a, []))


def _d_killwindow(a, w):
    return _call("window.kill", _win(a, []))


def _d_signal(a, w):
    return _call("window.signal", [("signal", _num(a))])


def _d_signalwindow(a, w):
    win, _, sig = a.partition(",")
    return _call("window.signal", _win(win, [("signal", _num(sig.strip()))]))


def _d_togglefloating(a, w):
    return _call("window.float", _win(a, []))


def _d_setfloating(a, w):
    return _call("window.float", _win(a, [("action", "on")]))


def _d_settiled(a, w):
    return _call("window.float", _win(a, [("action", "off")]))


def _d_pseudo(a, w):
    return _call("window.pseudo", _win(a, []))


def _d_workspace(a, w):
    return _call("focus", [("workspace", a)])


def _d_movetoworkspace(a, w, silent=False):
    ws, sep, win = a.rpartition(",")
    if not sep:
        ws, win = a, ""
    fields = [("workspace", ws.strip())]
    if silent:
        fields.append(("follow", False))
    return _call("window.move", _win(win, fields))


def _d_movetoworkspacesilent(a, w):
    return _d_movetoworkspace(a, w, silent=True)


def _d_renameworkspace(a, w):
    ident, _, newname = a.partition(" ")
    fields = [("workspace", ident.strip())]
    if newname.strip():
        fields.append(("name", newname.strip()))
    return _call("workspace.rename", fields)


def _d_fullscreen(a, w):
    toks = a.split()
    fields = []
    if toks:
        fields.append(("mode", toks[0]))
    if len(toks) > 1:
        fields.append(("action", toks[1]))
    return _call("window.fullscreen", fields)


def _d_fullscreenstate(a, w):
    toks = a.replace(",", " ").split()
    if len(toks) < 2:
        w.append(("L9", f"fullscreenstate: needs internal+client, got {a!r}"))
        return None
    if "-1" in toks[:2]:
        w.append(("L9", "fullscreenstate: `-1` (keep current) has no Lua form; "
                        "emitted verbatim, Hyprland will reject it"))
    fields = [("internal", _num(toks[0])), ("client", _num(toks[1]))]
    if len(toks) > 2:
        w.append(("L9", f"fullscreenstate: trailing action token {toks[2]!r} is ignored "
                        f"by the legacy translator too"))
    return _call("window.fullscreen_state", fields)


def _d_movefocus(a, w):
    d = _dir(a, w, "movefocus")
    return _call("focus", [("direction", d)]) if d else None


def _d_movewindow(a, w):
    if a.lower().startswith("mon:"):
        rest = a[4:].strip()
        silent = rest.lower().endswith(" silent")
        if silent:
            rest = rest[: -len(" silent")].strip()
        fields = [("monitor", rest)]
        if silent:
            fields.append(("follow", False))
        return _call("window.move", fields)
    d = _dir(a, w, "movewindow")
    return _call("window.move", [("direction", d)]) if d else None


def _d_swapwindow(a, w):
    a_l = a.strip().lower()
    if a_l and a_l[0] in LEGACY_DIRS and len(a_l) <= 6 and a_l.isalpha():
        return _call("window.swap", [("direction", LEGACY_DIRS[a_l[0]])])
    if a_l and a_l[0] in VIM_DIRS:
        d = _dir(a, w, "swapwindow")
        return _call("window.swap", [("direction", d)])
    return _call("window.swap", [("target", a.strip())])


def _d_centerwindow(a, w):
    if a.strip():
        w.append(("L11", "centerwindow: the `1` (respect reserved) argument is dropped "
                         "by the legacy translator too"))
    return _call("window.center")


def _d_togglegroup(a, w):
    return _call("group.toggle")


def _d_changegroupactive(a, w):
    v = a.strip().lower()
    if v in ("b", "prev", "back"):
        return _call("group.prev")
    if v in ("", "f", "next", "forward"):
        return _call("group.next")
    try:
        return _call("group.active", [("index", int(v))])
    except ValueError:
        return _call("group.next")


def _d_movegroupwindow(a, w):
    back = a.strip().lower() in ("b", "prev", "back")
    return _call("group.move_window", [("forward", not back)])


def _d_focusmonitor(a, w):
    return _call("focus", [("monitor", a.strip())])


def _d_movecursortocorner(a, w):
    return _call("cursor.move_to_corner", [("corner", _num(a.strip()))])


def _d_movecursor(a, w):
    toks = a.replace(",", " ").split()
    if len(toks) < 2:
        w.append(("L11", f"movecursor: bad args {a!r}"))
        return None
    return _call("cursor.move", [("x", _num(toks[0])), ("y", _num(toks[1]))])


def _d_dead(name):
    def inner(a, w):
        w.append(("L11", f"`{name}` is deprecated/removed in 0.56.2 — no Lua call emitted"))
        return None
    return inner


def _d_exit(a, w):
    return _call("exit")


def _d_movecurrentworkspacetomonitor(a, w):
    return _call("workspace.move", [("monitor", a.strip())])


def _d_focusworkspaceoncurrentmonitor(a, w):
    return _call("focus", [("workspace", a.strip()), ("on_current_monitor", True)])


def _d_moveworkspacetomonitor(a, w):
    ws, _, mon = a.strip().partition(" ")
    return _call("workspace.move", [("workspace", ws.strip()), ("monitor", mon.strip())])


def _d_togglespecialworkspace(a, w):
    if a.strip():
        return f"hl.dsp.workspace.toggle_special({lit(a.strip())})"
    return _call("workspace.toggle_special")


def _d_forcerendererreload(a, w):
    return _call("force_renderer_reload")


def _d_resizeactive(a, w):
    f = _resize_params(a, w, "resizeactive")
    return _call("window.resize", f) if f else None


def _d_moveactive(a, w):
    f = _resize_params(a, w, "moveactive")
    return _call("window.move", f) if f else None


def _d_resizewindowpixel(a, w):
    params, _, win = a.partition(",")
    f = _resize_params(params, w, "resizewindowpixel")
    return _call("window.resize", _win(win, f)) if f else None


def _d_movewindowpixel(a, w):
    params, _, win = a.partition(",")
    f = _resize_params(params, w, "movewindowpixel")
    return _call("window.move", _win(win, f)) if f else None


def _d_cyclenext(a, w):
    fields = []
    for t in a.replace(",", " ").split():
        t = t.lower()
        if t in ("prev", "p", "last", "l"):
            fields.append(("next", False))
        elif t in ("next", "n"):
            fields.append(("next", True))
        elif t in ("tile", "tiled"):
            fields.append(("tiled", True))
        elif t in ("float", "floating"):
            fields.append(("floating", True))
        elif t in ("visible", "hist"):
            w.append(("L11", f"cyclenext: `{t}` is dropped by the legacy translator too"))
    return _call("window.cycle_next", fields)


def _d_focuswindow(a, w):
    return _call("focus", _win(a, []))


def _d_tagwindow(a, w):
    tag, _, win = a.strip().partition(" ")
    return _call("window.tag", _win(win, [("tag", tag)]))


def _d_toggleswallow(a, w):
    return _call("window.toggle_swallow")


def _d_submap(a, w):
    return f"hl.dsp.submap({lit(a.strip())})"


def _d_pass(a, w):
    s = _sel(a)
    if not s:
        w.append(("L11", "pass: Lua requires an explicit window selector"))
        return None
    return _call("pass", [("window", s)])


def _d_sendshortcut(a, w):
    parts = a.split(",", 2)
    mods = parts[0].strip() if parts else ""
    key = parts[1].strip() if len(parts) > 1 else ""
    fields = [("mods", mods), ("key", key)]
    if len(parts) > 2 and parts[2].strip():
        fields.append(("window", parts[2].strip()))
    return _call("send_shortcut", fields)


def _d_sendkeystate(a, w):
    parts = a.split(",", 3)
    fields = []
    if parts:
        fields.append(("mods", parts[0].strip()))
    if len(parts) > 1:
        fields.append(("key", parts[1].strip()))
    if len(parts) > 2:
        fields.append(("state", parts[2].strip()))
    if len(parts) > 3 and parts[3].strip():
        fields.append(("window", parts[3].strip()))
    return _call("send_key_state", fields)


def _d_layoutmsg(a, w):
    return f"hl.dsp.layout({lit(a)})"


def _d_dpms(a, w):
    toks = a.replace(",", " ").split()
    if not toks:
        w.append(("L10", "dpms with no argument meant OFF under the legacy engine but "
                         "TOGGLE under Lua; emitted action=\"off\""))
        return _call("dpms", [("action", "off")])
    action = toks[0].lower()
    if not (action.startswith("on") or action.startswith("toggle")):
        action = "off"
    elif action.startswith("toggle"):
        action = "toggle"
    else:
        action = "on"
    fields = [("action", action)]
    if len(toks) > 1:
        fields.append(("monitor", toks[1]))
    return _call("dpms", fields)


def _d_swapnext(a, w):
    prev = a.strip().lower() in ("l", "last", "prev", "b", "back")
    return _call("window.swap", [("prev", True)] if prev else [("next", True)])


def _d_swapactiveworkspaces(a, w):
    toks = a.replace(",", " ").split()
    if len(toks) < 2:
        w.append(("L11", f"swapactiveworkspaces: bad args {a!r}"))
        return None
    return _call("workspace.swap_monitors",
                 [("monitor1", toks[0]), ("monitor2", toks[1])])


def _d_pin(a, w):
    return _call("window.pin", _win(a, []))


def _d_bringactivetotop(a, w):
    return _call("window.bring_to_top")


def _d_alterzorder(a, w):
    mode, _, win = a.partition(",")
    return _call("window.alter_zorder", _win(win, [("mode", mode.strip())]))


def _d_focusurgentorlast(a, w):
    return _call("focus", [("urgent_or_last", True)])


def _d_focuscurrentorlast(a, w):
    return _call("focus", [("last", True)])


def _lock_action(a, w, what):
    v = a.strip().lower()
    if v in ("", "lock", "lockgroups", "lockactivegroup"):
        return "on"
    if v == "toggle":
        return "toggle"
    if v == "unlock":
        w.append(("L10", f"{what}: legacy `unlock` means OFF, but Lua maps unknown "
                         f"action strings to TOGGLE; emitted action=\"off\""))
    return "off"


def _d_lockgroups(a, w):
    return _call("group.lock", [("action", _lock_action(a, w, "lockgroups"))])


def _d_lockactivegroup(a, w):
    v = a.strip().lower()
    if not v:
        w.append(("L10", "lockactivegroup with no argument meant OFF under the legacy "
                         "engine but TOGGLE under Lua; emitted action=\"off\""))
        return _call("group.lock_active", [("action", "off")])
    return _call("group.lock_active", [("action", _lock_action(a, w, "lockactivegroup"))])


def _d_moveintogroup(a, w):
    d = _dir(a, w, "moveintogroup")
    return _call("window.move", [("into_group", d)]) if d else None


def _d_moveintoorcreategroup(a, w):
    d = _dir(a, w, "moveintoorcreategroup")
    return _call("window.move", [("into_or_create_group", d)]) if d else None


def _d_moveoutofgroup(a, w):
    s = _sel(a)
    if s:
        return _call("window.move", [("out_of_group", True), ("window", s)])
    return _call("window.move", [("out_of_group", True)])


def _d_movewindoworgroup(a, w):
    d = _dir(a, w, "movewindoworgroup")
    return _call("window.move", [("direction", d), ("group_aware", True)]) if d else None


def _d_denywindowfromgroup(a, w):
    v = a.strip().lower()
    action = "on" if v.startswith("on") else ("toggle" if v == "toggle" else "off")
    return _call("window.deny_from_group", [("action", action)])


def _d_event(a, w):
    return f"hl.dsp.event({lit(a)})"


def _d_global(a, w):
    return f"hl.dsp.global({lit(a)})"


def _d_setprop(a, w):
    toks = a.split()
    if len(toks) < 3:
        w.append(("L11", f"setprop: needs `window prop value`, got {a!r}"))
        return None
    return _call("window.set_prop",
                 [("prop", toks[1]), ("value", " ".join(toks[2:])), ("window", toks[0])])


def _d_forceidle(a, w):
    return f"hl.dsp.force_idle({_num(a.strip()) if a.strip() else 0})"


def _d_releaseinputcapture(a, w):
    return _call("release_input_capture")


def _d_mouse(a, w):
    """bindm's internal dispatcher — handled by the bind path, kept for completeness."""
    v = a.strip().lower()
    if v.startswith("resizewindow"):
        rest = v[len("resizewindow"):].strip()
        if rest == "1":
            return _call("window.resize", [("keep_aspect_ratio", True)])
        if rest == "2":
            return _call("window.resize", [("keep_aspect_ratio", False)])
        return _call("window.resize")
    return _call("window.drag")


_TABLE = {
    "exec": _d_exec, "execr": _d_execr,
    "killactive": _d_killactive, "forcekillactive": _d_forcekillactive,
    "closewindow": _d_closewindow, "killwindow": _d_killwindow,
    "signal": _d_signal, "signalwindow": _d_signalwindow,
    "togglefloating": _d_togglefloating, "setfloating": _d_setfloating,
    "settiled": _d_settiled, "pseudo": _d_pseudo,
    "workspace": _d_workspace,
    "movetoworkspace": _d_movetoworkspace,
    "movetoworkspacesilent": _d_movetoworkspacesilent,
    "renameworkspace": _d_renameworkspace,
    "fullscreen": _d_fullscreen, "fullscreenstate": _d_fullscreenstate,
    "movefocus": _d_movefocus, "movewindow": _d_movewindow,
    "swapwindow": _d_swapwindow, "centerwindow": _d_centerwindow,
    "togglegroup": _d_togglegroup, "changegroupactive": _d_changegroupactive,
    "movegroupwindow": _d_movegroupwindow, "focusmonitor": _d_focusmonitor,
    "movecursortocorner": _d_movecursortocorner, "movecursor": _d_movecursor,
    "workspaceopt": _d_dead("workspaceopt"), "exit": _d_exit,
    "movecurrentworkspacetomonitor": _d_movecurrentworkspacetomonitor,
    "focusworkspaceoncurrentmonitor": _d_focusworkspaceoncurrentmonitor,
    "moveworkspacetomonitor": _d_moveworkspacetomonitor,
    "togglespecialworkspace": _d_togglespecialworkspace,
    "forcerendererreload": _d_forcerendererreload,
    "resizeactive": _d_resizeactive, "moveactive": _d_moveactive,
    "resizewindowpixel": _d_resizewindowpixel, "movewindowpixel": _d_movewindowpixel,
    "cyclenext": _d_cyclenext,
    "focuswindow": _d_focuswindow, "focuswindowbyclass": _d_focuswindow,
    "tagwindow": _d_tagwindow, "toggleswallow": _d_toggleswallow,
    "submap": _d_submap, "pass": _d_pass,
    "sendshortcut": _d_sendshortcut, "sendkeystate": _d_sendkeystate,
    "layoutmsg": _d_layoutmsg, "dpms": _d_dpms, "swapnext": _d_swapnext,
    "swapactiveworkspaces": _d_swapactiveworkspaces, "pin": _d_pin,
    "mouse": _d_mouse, "bringactivetotop": _d_bringactivetotop,
    "alterzorder": _d_alterzorder,
    "focusurgentorlast": _d_focusurgentorlast,
    "focuscurrentorlast": _d_focuscurrentorlast,
    "lockgroups": _d_lockgroups, "lockactivegroup": _d_lockactivegroup,
    "moveintogroup": _d_moveintogroup,
    "moveintoorcreategroup": _d_moveintoorcreategroup,
    "moveoutofgroup": _d_moveoutofgroup,
    "movewindoworgroup": _d_movewindoworgroup,
    "setignoregrouplock": _d_dead("setignoregrouplock"),
    "denywindowfromgroup": _d_denywindowfromgroup,
    "event": _d_event, "global": _d_global, "setprop": _d_setprop,
    "forceidle": _d_forceidle, "releaseinputcapture": _d_releaseinputcapture,
    "splitratio": _d_dead("splitratio"),
}

KNOWN = set(_TABLE)
