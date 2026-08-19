"""PROTOTYPE — throwaway. Event stream -> hyprland.lua.

Mapping transcribed from docs/research/hyprlang-to-lua.md sections 2.1-2.11.
Warning codes L1..L28 are that document's lossy-case index; VIMDIR / BADDISP /
DEADOPT / EXTGEN are prototype-local additions.
"""
import re

import dispatchers
import keysyms
import opttypes
from hyprlang import Diag, truthy
from lua import Raw, insert, key as lua_ident, lit, nested_tbl, tbl, val

# ---------------------------------------------------------------------------
# mods (research 2.1: legacy stringToModMask is a case-insensitive substring test)
# ---------------------------------------------------------------------------
MOD_ORDER = ["SUPER", "CTRL", "ALT", "SHIFT", "CAPS", "MOD2", "MOD3", "MOD5"]
MOD_ALIASES = [
    ("SUPER", ("SUPER", "WIN", "LOGO", "MOD4", "META")),
    ("CTRL", ("CTRL", "CONTROL")),
    ("ALT", ("ALT", "MOD1")),
    ("SHIFT", ("SHIFT",)),
    ("CAPS", ("CAPS",)),
    ("MOD2", ("MOD2",)),
    ("MOD3", ("MOD3",)),
    ("MOD5", ("MOD5",)),
]

KEY_ALIASES = {"enter": "Return", "esc": "Escape", "del": "Delete",
               "pgup": "Prior", "pgdn": "Next", "ins": "Insert"}

SPECIAL_KEY_PREFIXES = ("mouse:", "switch:", "code:")
SPECIAL_KEY_NAMES = ("mouse_up", "mouse_down", "mouse_left", "mouse_right", "catchall")

BIND_FLAG_OPTS = {
    "l": "locked", "r": "release", "e": "repeating", "n": "non_consuming",
    "a": "auto_consuming", "t": "transparent", "i": "ignore_mods",
    "o": "long_press", "p": "dont_inhibit", "c": "click", "g": "drag",
    "u": "submap_universal", "x": "allow_input_capture",
}

# ---------------------------------------------------------------------------
# rules (research 2.5 / 2.6 / 2.7)
# ---------------------------------------------------------------------------
WR_BOOL_EFFECTS = {
    "float", "tile", "fullscreen", "maximize", "center", "pseudo",
    "no_initial_focus", "pin", "persistent_size", "allows_input", "dim_around",
    "decorate", "focus_on_activate", "keep_aspect_ratio", "nearest_neighbor",
    "no_anim", "no_blur", "no_dim", "no_focus", "no_follow_mouse", "no_max_size",
    "no_shadow", "no_shortcuts_inhibit", "opaque", "force_rgbx", "sync_fullscreen",
    "immediate", "xray", "render_unfocused", "no_screen_share", "no_vrr",
    "no_auto_hdr", "confine_pointer", "stay_focused",
}
WR_INT_EFFECTS = {"no_close_for", "rounding", "border_size"}
WR_FLOAT_EFFECTS = {"scrolling_width", "rounding_power", "scroll_mouse", "scroll_touchpad"}
WR_VEC2_EFFECTS = {"move", "size", "max_size", "min_size"}
WR_GRADIENT_EFFECTS = {"border_color"}
WR_STRING_EFFECTS = {
    "fullscreen_state", "monitor", "workspace", "group", "suppress_event",
    "content", "animation", "idle_inhibit", "opacity", "tag", "tonemap",
}
WR_ALL_EFFECTS = (WR_BOOL_EFFECTS | WR_INT_EFFECTS | WR_FLOAT_EFFECTS
                  | WR_VEC2_EFFECTS | WR_GRADIENT_EFFECTS | WR_STRING_EFFECTS)

MATCH_BOOL = {"xwayland", "float", "fullscreen", "pin", "focus", "group", "modal"}
MATCH_INT = {"fullscreen_state_internal", "fullscreen_state_client"}
MATCH_ALL = (MATCH_BOOL | MATCH_INT | {"class", "title", "initial_class",
             "initial_title", "tag", "workspace", "content", "xdg_tag", "namespace"})

LR_BOOL_EFFECTS = {"no_anim", "blur", "blur_popups", "dim_around", "xray", "no_screen_share"}
LR_FLOAT_EFFECTS = {"ignore_alpha"}
LR_INT_EFFECTS = {"order", "above_lock"}
LR_STRING_EFFECTS = {"animation"}

# legacy <=0.53 window-rule names -> v3 snake_case (research L13: table is
# unverified against primary sources; flagged on every use)
V1_EFFECT_RENAMES = {
    "noanim": "no_anim", "noblur": "no_blur", "nodim": "no_dim",
    "noshadow": "no_shadow", "nofocus": "no_focus", "noborder": "border_size",
    "nomaxsize": "no_max_size", "noinitialfocus": "no_initial_focus",
    "noscreenshare": "no_screen_share", "nofollowmouse": "no_follow_mouse",
    "noshortcutsinhibit": "no_shortcuts_inhibit", "keepaspectratio": "keep_aspect_ratio",
    "focusonactivate": "focus_on_activate", "forcergbx": "force_rgbx",
    "syncfullscreen": "sync_fullscreen", "nearestneighbor": "nearest_neighbor",
    "renderunfocused": "render_unfocused", "noclosefor": "no_close_for",
    "persistentsize": "persistent_size", "scrollmouse": "scroll_mouse",
    "scrolltouchpad": "scroll_touchpad", "dimaround": "dim_around",
    "idleinhibit": "idle_inhibit", "suppressevent": "suppress_event",
    "bordercolor": "border_color", "maxsize": "max_size", "minsize": "min_size",
    "fullscreenstate": "fullscreen_state", "allowsinput": "allows_input",
    "stayfocused": "stay_focused", "roundingpower": "rounding_power",
    "immediate": "immediate", "opacity": "opacity", "size": "size",
    "move": "move", "workspace": "workspace", "monitor": "monitor",
    "float": "float", "tile": "tile", "pin": "pin", "center": "center",
    "opaque": "opaque", "pseudo": "pseudo", "group": "group", "tag": "tag",
    "xray": "xray", "rounding": "rounding", "bordersize": "border_size",
    "animation": "animation", "content": "content", "decorate": "decorate",
    "fullscreen": "fullscreen", "maximize": "maximize", "unset": None,
    "windowdance": None, "prop": None,
}
V1_MATCH_RENAMES = {
    "class": "class", "title": "title", "initialclass": "initial_class",
    "initialtitle": "initial_title", "tag": "tag", "xwayland": "xwayland",
    "floating": "float", "fullscreen": "fullscreen", "pinned": "pin",
    "focus": "focus", "workspace": "workspace", "onworkspace": "workspace",
    "fullscreenstate": None, "content": "content",
}

WS_RULE_FIELDS = {
    "monitor": ("monitor", "string"), "default": ("default", "bool"),
    "persistent": ("persistent", "bool"), "gapsin": ("gaps_in", "cssgap"),
    "gapsout": ("gaps_out", "cssgap"), "bordersize": ("border_size", "int"),
    "border": ("no_border", "invbool"), "shadow": ("no_shadow", "invbool"),
    "rounding": ("no_rounding", "invbool"), "decorate": ("decorate", "bool"),
    "defaultName": ("default_name", "string"), "defaultname": ("default_name", "string"),
    "on-created-empty": ("on_created_empty", "string"),
    "layout": ("layout", "string"), "animation": ("animation", "string"),
}

DEVICE_RENAMES = {"tap-to-click": "tap_to_click", "tap-and-drag": "tap_and_drag"}
DEVICE_DROPPED = {"eraser_button_mode", "eraser_button_override",
                  "pressure_range_min", "pressure_range_max"}
DEVICE_BOOL = {
    "natural_scroll", "numlock_by_default", "resolve_binds_by_sym",
    "disable_while_typing", "middle_button_emulation", "tap_to_click",
    "tap_and_drag", "drag_lock", "left_handed", "scroll_button_lock",
    "enabled", "relative_input", "flip_x", "flip_y", "drag_3fg", "keybinds",
    "share_states", "release_pressed_on_close", "clickfinger_behavior",
}
DEVICE_INT = {"repeat_rate", "repeat_delay", "scroll_button", "transform",
              "accel_profile", "tap_button_map", "rotation"}
DEVICE_FLOAT = {"sensitivity", "scroll_factor"}
DEVICE_VEC2 = {"region_position", "absolute_region_position", "region_size",
               "active_area_position", "active_area_size"}

MONITOR_TRAILING = {
    "mirror": ("mirror", "string"), "bitdepth": ("bitdepth", "int"),
    "cm": ("cm", "string"), "sdrsaturation": ("sdrsaturation", "float"),
    "sdrbrightness": ("sdrbrightness", "float"), "transform": ("transform", "int"),
    "vrr": ("vrr", "int"), "icc": ("icc", "string"),
}
MONITORV2_FIELDS = {
    "output": "string", "mode": "string", "position": "string", "scale": "scale",
    "disabled": "bool", "transform": "int", "mirror": "string", "bitdepth": "int",
    "cm": "string", "sdr_eotf": "sdr_eotf", "sdrbrightness": "float",
    "sdrsaturation": "float", "vrr": "int", "icc": "string",
    "supports_wide_color": "int", "supports_hdr": "int",
    "sdr_min_luminance": "float", "sdr_max_luminance": "int",
    "min_luminance": "float", "max_luminance": "int", "max_avg_luminance": "int",
    "addreserved": "reserved",
}

GESTURE_DIRECTIONS = {"swipe", "horizontal", "vertical", "left", "right", "up",
                      "down", "pinch", "pinchin", "pinchout"}


# --------------------------------------------------------------------------- #
# value helpers
# --------------------------------------------------------------------------- #
def _int(text, default=None):
    t = (text or "").strip()
    b = truthy(t)
    if b is not None and not re.match(r"^-?\d", t):
        return 1 if b else 0
    try:
        if t.lower().startswith("0x"):
            return int(t, 16)
        return int(t)
    except ValueError:
        try:
            return int(float(t))
        except ValueError:
            return default


def _float(text, default=None):
    t = (text or "").strip()
    m = re.match(r"^-?\d*\.?\d+([eE][-+]?\d+)?", t)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return default
    b = truthy(t)
    if b is not None:
        return 1.0 if b else 0.0
    return default


def _split_top(text, seps=" \t"):
    """Split on whitespace but keep rgba(...) / rgb(...) intact."""
    out, buf, depth = [], [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch in seps and depth == 0:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _css_gap(text, warns, where):
    parts = [p for p in re.split(r"[,\s]+", (text or "").strip()) if p]
    nums = [_int(p, 0) for p in parts]
    if not nums:
        return 0
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        t, r, b, l = nums[0], nums[1], nums[0], nums[1]
    elif len(nums) == 3:
        t, r, b, l = nums[0], nums[1], nums[2], nums[1]
    else:
        t, r, b, l = nums[0], nums[1], nums[2], nums[3]
    if len(nums) in (2, 3):
        warns.append(("L24", f"{where}: CSS-gap {len(nums)}-value shorthand expanded to "
                             f"top/right/bottom/left"))
    return {"top": t, "right": r, "bottom": b, "left": l}


def _gradient(text, warns, where):
    toks = _split_top((text or "").strip())
    angle = None
    if toks and re.match(r"^-?\d+(\.\d+)?deg$", toks[-1], re.I):
        angle = _float(toks[-1][:-3])
        toks = toks[:-1]
    if not toks:
        return None
    if len(toks) == 1 and angle in (None, 0):
        return toks[0]
    if len(toks) > 10:
        warns.append(("L14", f"{where}: {len(toks)} gradient stops (legacy capped at 10)"))
    fields = [("colors", [Raw(str(lit(c))) for c in toks])]
    if angle is not None:
        fields.append(("angle", angle))
    return Raw(tbl(fields))


def _vec2(text, warns, where):
    parts = [p for p in re.split(r"[,\s]+", (text or "").strip()) if p]
    if len(parts) < 2:
        warns.append(("L24", f"{where}: expected two numbers, got {text!r}"))
        return None
    return [_float(parts[0], 0), _float(parts[1], 0)]


def _config_value(rec, raw, warns, where):
    t = rec["type"]
    if t == "bool":
        b = truthy(raw)
        if b is None:
            warns.append(("L24", f"{where}: {raw!r} is not a bool word; "
                                 f"legacy prefix rule gives false"))
            b = False
        elif raw.strip().lower() not in ("true", "false", "0", "1"):
            warns.append(("L24", f"{where}: bool word {raw.strip()!r} normalised to "
                                 f"{str(b).lower()}"))
        return b
    if t in ("int", "int_map"):
        if t == "int_map" and rec.get("map"):
            names = {k.lower() for m in rec["map"] for k in m}
            if raw.strip().lower() in names:
                return raw.strip().lower()
        v = _int(raw)
        if v is None:
            warns.append(("L24", f"{where}: cannot parse int from {raw!r}"))
            return None
        return v
    if t == "float":
        v = _float(raw)
        if v is None:
            warns.append(("L24", f"{where}: cannot parse number from {raw!r}"))
            return None
        return int(v) if v == int(v) and abs(v) < 1e15 else v
    if t == "vec2":
        return _vec2(raw, warns, where)
    if t == "css_gaps":
        return _css_gap(raw, warns, where)
    if t == "gradient":
        return _gradient(raw, warns, where)
    if t == "font_weight":
        v = _int(raw)
        return v if v is not None else raw.strip()
    return raw


# --------------------------------------------------------------------------- #
class Converter:
    def __init__(self, opts=None):
        self.opts = opts or {}
        self.lines = []
        self.warns = []          # Diag
        self.pending = {}        # buffered hl.config tree
        self.submap = None       # (name, reset, [lines])
        self.table = opttypes.table()
        self.stats = {}
        self.cur_file = None
        self.cur_line = 0
        self.key_dead = False
        self.named_rules_seen = 0
        self.anon_rules_seen = 0
        self.anon_before_named = False

    # -- output -----------------------------------------------------------
    def _out(self, text):
        if self.submap is not None:
            self.submap[2].append(text)
        else:
            self.lines.append(text)

    def _warn(self, code, msg, level="warn"):
        self.warns.append(Diag(level, code, msg, self.cur_file, self.cur_line))

    def _bump(self, name):
        self.stats[name] = self.stats.get(name, 0) + 1

    def _collect(self):
        w = []
        return w

    def _drain(self, w, prefix=""):
        for code, msg in w:
            self._warn(code, f"{prefix}{msg}" if prefix else msg)

    # -- pending hl.config buffer -----------------------------------------
    def _flush(self):
        if not self.pending:
            return
        body = nested_tbl(self.pending)
        self._out(f"hl.config({body})")
        self.pending = {}

    def _set_config(self, path, value):
        if not insert(self.pending, path, value):
            self._flush()
            insert(self.pending, path, value)

    # -- driver -----------------------------------------------------------
    def run(self, parser):
        for ev in parser.events:
            self.cur_file = ev.file or self.cur_file
            self.cur_line = ev.line
            fn = getattr(self, "_ev_" + ev.kind, None)
            if fn:
                fn(ev)
        self._close_submap()
        self._flush()
        return self

    def text(self):
        header = [
            "-- Generated by the hyprlang->Lua importer prototype (issue #9).",
            "-- Source tree converted with variables fully expanded, statements in",
            "-- hyprlang parse order (source= inlined).",
            "",
        ]
        return "\n".join(header + self.lines) + "\n"

    # -- events -----------------------------------------------------------
    def _ev_source_enter(self, ev):
        self._flush()
        self._out(f"-- >>> {ev.file}")

    def _ev_source_leave(self, ev):
        self._flush()
        self._out(f"-- <<< {ev.file}")

    def _ev_var(self, ev):
        pass  # variables are expanded at parse time

    def _ev_set(self, ev):
        legacy = ev.key
        if ev.extra.get("orphan"):
            self._flush()
            self._warn("DEADOPT", f"orphan key `{legacy}` outside any category — invalid "
                                  f"in hyprlang too; commented out")
            self._out(f"-- [dead] {legacy} = {ev.value}")
            self._bump("dead_option")
            return
        rec = self.table.lookup(legacy)
        if rec is None:
            self._flush()
            self._warn("L25", f"unknown option `{legacy}` — not in 0.56.2 `descriptions`; "
                              f"commented out")
            self._out(f"-- [dead] {legacy} = {ev.value}")
            self._bump("dead_option")
            return
        w = []
        value = _config_value(rec, ev.value, w, legacy)
        self._drain(w)
        if value is None:
            self._out(f"-- [skipped] {legacy} = {ev.value}")
            self._bump("skipped_option")
            return
        self._set_config(rec["lua"].split("."), value)
        self._bump("option")

    def _ev_handler(self, ev):
        name = ev.key
        fn = getattr(self, "_h_" + name.replace("-", "_"), None)
        if fn is None:
            self._flush()
            self._warn("UNHANDLED", f"no rule for keyword `{name}`")
            self._out(f"-- [unhandled] {name} = {ev.value}")
            return
        self._flush()
        fn(ev)

    def _ev_special(self, ev):
        self._flush()
        cat = ev.key
        fields = ev.value
        if cat == "device":
            self._special_device(fields)
        elif cat == "windowrule":
            self._special_rule(fields, "window")
        elif cat == "layerrule":
            self._special_rule(fields, "layer")
        elif cat == "monitorv2":
            self._special_monitorv2(fields)
        elif cat == "plugin":
            self._special_plugin(fields)

    # ---------------- binds ----------------------------------------------
    def _mods(self, text):
        up = (text or "").upper()
        found = []
        for canon, aliases in MOD_ALIASES:
            if any(a in up for a in aliases):
                found.append(canon)
        if text.strip() and not found:
            self._warn("L1", f"mods {text.strip()!r} produce an empty mask "
                             f"(a config error under both engines)")
        return [m for m in MOD_ORDER if m in found]

    def _keystr(self, mods_text, key_text, multikey=False):
        self.key_dead = False
        mods = self._mods(mods_text)
        keys = []
        raw = (key_text or "").strip()
        if raw.lower() == "catchall":
            if mods:
                self._warn("N1", f"`catchall` cannot carry modifiers under Lua "
                                 f"(hl.bind rejects the key string); dropped the "
                                 f"{'+'.join(mods)} requirement")
            return "catchall"
        if multikey:
            parts = [p.strip() for p in raw.split("&") if p.strip()]
            if len(parts) > 1:
                self._warn("L4", f"`binds` multi-key {raw!r} approximated as a "
                                 f"`+`-joined key string; the Lua matcher differs")
        else:
            parts = [raw] if raw else []
        for p in parts:
            keys.append(self._one_key(p))
        return " + ".join(mods + [k for k in keys if k])

    def _one_key(self, k):
        if not k:
            return ""
        low = k.lower()
        if low in SPECIAL_KEY_NAMES or low.startswith(SPECIAL_KEY_PREFIXES):
            return k
        if re.match(r"^\d+$", k):
            n = int(k)
            if n > 9:
                self._warn("L2", f"bare keycode {k} rewritten as `code:{k}`")
                return f"code:{k}"
            return k
        if low in KEY_ALIASES:
            self._warn("L3", f"key name {k!r} is not an xkb keysym; rewritten as "
                             f"{KEY_ALIASES[low]!r}")
            return KEY_ALIASES[low]
        if keysyms.is_keysym(k) is False:
            self.key_dead = True
            self._warn("N4", f"key name {k!r} resolves to no xkb keysym, so this bind "
                             f"never fired under the legacy engine either; under Lua it "
                             f"is a hard config error, so the bind is commented out")
        return k

    def _h_bind(self, ev):
        flags = ev.extra.get("flags", "")
        unknown = [c for c in flags if c not in "lrenatiodpcgukxsm"]
        if unknown:
            self._warn("BADFLAG", f"bind flags {flags!r}: unknown letter(s) "
                                  f"{''.join(unknown)}")
        mouse = "m" in flags
        nargs = 3 if mouse else 4 + ("d" in flags) + ("k" in flags)
        parts = [p.strip() for p in ev.value.split(",", nargs - 1)]
        while len(parts) < nargs:
            parts.append("")
        idx = 2
        desc = devices = None
        if "d" in flags and not mouse:
            desc = parts[idx]
            idx += 1
        if "k" in flags and not mouse:
            devices = parts[idx]
            idx += 1

        keystr = self._keystr(parts[0], parts[1], multikey="s" in flags)
        opts = []
        for c in flags:
            if c in BIND_FLAG_OPTS and c not in ("d",):
                if c == "c":
                    opts.append(("click", True))
                elif c == "g":
                    opts.append(("drag", True))
                else:
                    opts.append((BIND_FLAG_OPTS[c], True))
        if desc:
            opts.append(("description", desc))
        if devices:
            inclusive = not devices.strip().startswith("!")
            names = devices.strip().lstrip("!").split()
            opts.append(("device", Raw(tbl([("inclusive", inclusive),
                                            ("list", [Raw(str(lit(n))) for n in names])]))))

        if mouse:
            action = parts[2]
            self._warn("L5", "bindm has no Lua `mouse` opt; encoded through the "
                             "hl.dsp.window.drag()/resize() dispatchers")
            w = []
            expr = dispatchers.translate("mouse", action, w)
            self._drain(w, "bindm: ")
        else:
            disp = parts[idx]
            args = parts[idx + 1] if len(parts) > idx + 1 else ""
            w = []
            expr = dispatchers.translate(disp, args, w)
            self._drain(w, f"bind {keystr!r}: ")
        if expr is None:
            self._out(f"-- [skipped bind] {ev.extra.get('raw', ev.value)}")
            self._bump("skipped_bind")
            return
        opt_txt = f", {tbl(opts)}" if opts else ""
        stmt = f"hl.bind({lit(keystr)}, {expr}{opt_txt})"
        if self.key_dead:
            self._out(f"-- [dead key] {stmt}")
            self._bump("dead_bind")
            return
        self._out(stmt)
        self._bump("bind")

    def _h_unbind(self, ev):
        v = ev.value.strip()
        if v.lower() == "all":
            self._out('hl.unbind("all")')
            self._bump("unbind")
            return
        parts = [p.strip() for p in v.split(",", 1)]
        keystr = self._keystr(parts[0], parts[1] if len(parts) > 1 else "")
        self._warn("L6", f"`unbind` is string-matched under Lua; it only cancels a bind "
                         f"emitted with the identical canonical key string ({keystr!r})")
        self._out(f"hl.unbind({lit(keystr)})")
        self._bump("unbind")

    def _h_submap(self, ev):
        v = ev.value.strip()
        parts = [p.strip() for p in v.split(",")]
        name = parts[0]
        if name.lower() == "reset":
            self._close_submap()
            return
        self._close_submap()
        reset = parts[1] if len(parts) > 1 and parts[1] else None
        self.submap = (name, reset, [])
        self._bump("submap")

    def _close_submap(self):
        if self.submap is None:
            return
        name, reset, body = self.submap
        self.submap = None
        args = [str(lit(name))]
        if reset:
            args.append(str(lit(reset)))
        inner = "\n".join("  " + b for b in body)
        self.lines.append(f"hl.define_submap({', '.join(args)}, function()\n{inner}\nend)")

    # ---------------- env / exec -----------------------------------------
    def _h_env(self, ev):
        dbus = "d" in ev.extra.get("flags", "")
        k, _, v = ev.value.partition(",")
        if not k.strip():
            self._warn("PARSE", "env with empty name")
            return
        args = [str(lit(k.strip())), str(lit(v.strip()))]
        if dbus:
            args.append("true")
        self._out(f"hl.env({', '.join(args)})")
        self._bump("env")

    def _h_exec_once(self, ev):
        self._out(f'hl.on("hyprland.start", function() hl.exec_cmd({lit(ev.value)}) end)')
        self._bump("exec")

    def _h_execr_once(self, ev):
        self._out('hl.on("hyprland.start", function() '
                  f'hl.dispatch(hl.dsp.exec_raw({lit(ev.value)})) end)')
        self._bump("exec")

    def _h_exec(self, ev):
        self._warn("L22", "`exec` ran on the start event at first launch under the legacy "
                          "engine; top-level hl.exec_cmd spawns during config parse")
        self._out(f"hl.exec_cmd({lit(ev.value)})")
        self._bump("exec")

    def _h_execr(self, ev):
        self._warn("L22", "`execr` timing shift (see L22); emitted as a top-level dispatch")
        self._out(f"hl.dispatch(hl.dsp.exec_raw({lit(ev.value)}))")
        self._bump("exec")

    def _h_exec_shutdown(self, ev):
        self._out(f'hl.on("hyprland.shutdown", function() hl.exec_cmd({lit(ev.value)}) end)')
        self._bump("exec")

    def _h_permission(self, ev):
        parts = [p.strip() for p in ev.value.split(",")]
        if len(parts) < 3:
            self._warn("PARSE", f"permission needs 3 fields: {ev.value!r}")
            return
        regex = ",".join(parts[:-2]) if len(parts) > 3 else parts[0]
        self._out(f"hl.permission({lit(regex)}, {lit(parts[-2])}, {lit(parts[-1])})")
        self._bump("permission")

    def _h_plugin(self, ev):
        self._out(f"hl.plugin.load({lit(ev.value.strip())})")
        self._bump("plugin")

    def _h_plugin_value(self, ev):
        path = ev.extra["path"].split(":")
        if len(path) < 3:
            return
        ns = path[1]
        self._warn("L21", f"`{ev.extra['path']}` errors under Lua when plugin {ns!r} is not "
                          f"loaded; wrapped in an `if hl.plugin.{ns}` guard")
        tree = {}
        insert(tree, ["plugin"] + [p.replace("-", "_") for p in path[1:]], ev.value)
        self._out(f"if hl.plugin.{ns} ~= nil then hl.config({nested_tbl(tree)}) end")
        self._bump("plugin_value")

    def _h_source_missing(self, ev):
        self._warn("L23", f"`source = {ev.value}` matched no file at conversion time; "
                          f"no require emitted")

    def _h_source(self, ev):
        self._warn("L23", f"`source = {ev.value}` left unresolved")

    def _h_windowrulev2(self, ev):
        self._legacy_v1_rule(ev.value, "window", v2=True)

    def _h_layerrulev2(self, ev):
        self._legacy_v1_rule(ev.value, "layer", v2=True)

    # ---------------- rules -----------------------------------------------
    def _h_windowrule(self, ev):
        self._keyword_rule(ev.value, "window")

    def _h_layerrule(self, ev):
        self._keyword_rule(ev.value, "layer")

    def _keyword_rule(self, value, kind):
        """v3 form: `effect value, match:prop value, ...` (every element has a space)."""
        elements = [e.strip() for e in value.split(",") if e.strip()]
        if not elements:
            return
        if not all(" " in e for e in elements):
            self._legacy_v1_rule(value, kind)
            return
        match, effects, warns = {}, [], []
        for e in elements:
            token, _, v = e.partition(" ")
            token, v = token.strip(), v.strip()
            if token.startswith("match:"):
                prop = token[6:]
                match[prop] = self._match_value(prop, v, warns)
            else:
                self._effect(kind, token, v, effects, warns)
        self._drain(warns, f"{kind}rule: ")
        self._emit_rule(kind, None, match, effects, anonymous=True)

    def _legacy_v1_rule(self, value, kind, v2=False):
        """<=0.53 `windowrule[v2] = EFFECT[ VAL], class:re, title:re` (research L13)."""
        self._warn("L13", f"pre-0.54 {kind}rule{'v2' if v2 else ''} syntax "
                          f"({value!r}) — 0.56.2 rejects it outright; converted with an "
                          f"UNVERIFIED rename table")
        parts = [p.strip() for p in value.split(",")]
        if not parts:
            return
        head = parts[0]
        rest = parts[1:]
        match, effects, warns = {}, [], []
        name, _, v = head.partition(" ")
        canon = V1_EFFECT_RENAMES.get(name.lower().replace("_", ""), None)
        if canon is None:
            canon = V1_EFFECT_RENAMES.get(name.lower(), None)
        if canon is None:
            self._warn("L13", f"pre-0.54 effect {name!r} has no 0.56.2 counterpart; dropped")
        else:
            if name.lower() == "noborder":
                effects.append(("border_size", 0))
            else:
                self._effect(kind, canon, v, effects, warns, default_true=True)
        for r in rest:
            if ":" in r:
                p, _, pv = r.partition(":")
                canon_m = V1_MATCH_RENAMES.get(p.strip().lower().replace("_", ""))
                if canon_m:
                    match[canon_m] = self._match_value(canon_m, pv.strip(), warns)
                else:
                    self._warn("L13", f"pre-0.54 matcher {p!r} dropped")
            elif r:
                match["class"] = r          # v1 bare regex == class regex
        self._drain(warns, f"{kind}rule(v1): ")
        self._emit_rule(kind, None, match, effects, anonymous=True)

    def _match_value(self, prop, v, warns):
        if prop not in MATCH_ALL:
            warns.append(("L13", f"unknown match prop {prop!r} passed through"))
        if prop in MATCH_BOOL:
            b = truthy(v)
            return b if b is not None else v
        if prop in MATCH_INT:
            return _int(v, v)
        return v

    def _effect(self, kind, name, v, effects, warns, default_true=False):
        name = name.strip()
        if kind == "layer":
            if name == "ignorezero":
                warns.append(("L17", "`ignorezero` was removed in 0.56.2; emitted as "
                                     "ignore_alpha = 0"))
                effects.append(("ignore_alpha", 0))
                return
            if name == "unset":
                warns.append(("L17", "`unset` has no 0.56.2 counterpart; dropped"))
                return
            if name in LR_BOOL_EFFECTS:
                b = truthy(v) if v.strip() else True
                effects.append((name, b if b is not None else True))
            elif name in LR_FLOAT_EFFECTS:
                effects.append((name, _float(v, 0)))
            elif name in LR_INT_EFFECTS:
                effects.append((name, _int(v, 0)))
            elif name in LR_STRING_EFFECTS:
                effects.append((name, v))
            else:
                warns.append(("L17", f"unknown layerrule effect {name!r} passed through "
                                     f"as a string"))
                effects.append((name, v))
            return
        if name in WR_BOOL_EFFECTS:
            b = truthy(v) if v.strip() else (True if default_true else None)
            if b is None:
                warns.append(("L14", f"effect {name!r}: {v!r} is not a bool; assumed true"))
                b = True
            elif v.strip() and v.strip().lower() not in ("true", "false", "0", "1"):
                warns.append(("L14", f"effect {name!r}: bool word {v.strip()!r} normalised "
                                     f"to {str(b).lower()} (Lua rejects on/yes)"))
            effects.append((name, b))
        elif name in WR_INT_EFFECTS:
            n = _int(v, 0)
            if name == "rounding" and n is not None and n > 20:
                warns.append(("L14", f"rounding {n} exceeds the Lua range 0..20 and will "
                                     f"be rejected"))
            effects.append((name, n))
        elif name in WR_FLOAT_EFFECTS:
            effects.append((name, _float(v, 0)))
        elif name in WR_VEC2_EFFECTS:
            effects.append((name, v))          # expression strings pass through
        elif name in WR_GRADIENT_EFFECTS:
            toks = _split_top(v)
            degs = [t for t in toks if re.match(r"^-?\d+(\.\d+)?deg$", t, re.I)]
            if len(toks) - len(degs) > 1 and degs:
                warns.append(("L14", "border_color active+inactive pair kept as the raw "
                                     "legacy string (one gradient per Lua table)"))
                effects.append((name, v))
            else:
                effects.append((name, _gradient(v, warns, name)))
        elif name in WR_STRING_EFFECTS:
            effects.append((name, v))
        else:
            warns.append(("L14", f"unknown windowrule effect {name!r} passed through as a "
                                 f"string (dynamic/plugin effect?)"))
            effects.append((name, v))

    def _emit_rule(self, kind, name, match, effects, anonymous):
        if anonymous:
            self.anon_rules_seen += 1
            if self.named_rules_seen == 0:
                pass
        else:
            self.named_rules_seen += 1
            if self.anon_rules_seen:
                self.anon_before_named = True
        fields = []
        if name:
            fields.append(("name", name))
        if match:
            fields.append(("match", Raw(tbl(list(match.items())))))
        fields.extend(effects)
        fn = "hl.window_rule" if kind == "window" else "hl.layer_rule"
        self._out(f"{fn}({tbl(fields)})")
        self._bump(f"{kind}_rule")

    def _special_rule(self, fields, kind):
        name, match, effects, warns = None, {}, [], []
        enabled = None
        for k, v, _ln in fields:
            if k == "name":
                name = v
            elif k == "enable":
                enabled = truthy(v)
            elif k.startswith("match:"):
                prop = k[6:]
                match[prop] = self._match_value(prop, v, warns)
            else:
                self._effect(kind, k, v, effects, warns)
        self._drain(warns, f"{kind}rule block: ")
        if enabled is False:
            effects.insert(0, ("enabled", False))
        self._emit_rule(kind, name, match, effects, anonymous=False)

    # ---------------- workspace rules -------------------------------------
    def _h_workspace(self, ev):
        parts = [p.strip() for p in ev.value.split(",")]
        if not parts:
            return
        selector = parts[0]
        fields = [("workspace", selector)]
        layout_opts = {}
        for r in parts[1:]:
            if not r:
                continue
            k, _, v = r.partition(":")
            k, v = k.strip(), v.strip()
            if k == "layoutopt":
                lk, _, lv = v.partition(":")
                layout_opts[lk.strip()] = lv.strip()
                continue
            spec = WS_RULE_FIELDS.get(k) or WS_RULE_FIELDS.get(k.lower())
            if spec is None:
                self._warn("L16", f"unknown workspace rule `{r}` dropped")
                continue
            luakey, typ = spec
            if typ == "bool":
                b = truthy(v)
                fields.append((luakey, b if b is not None else True))
            elif typ == "invbool":
                b = truthy(v)
                b = True if b is None else b
                self._warn("L16", f"workspace `{k}:{v}` inverted to `{luakey} = "
                                  f"{str(not b).lower()}`")
                fields.append((luakey, not b))
            elif typ == "int":
                fields.append((luakey, _int(v, 0)))
            elif typ == "cssgap":
                w = []
                fields.append((luakey, _css_gap(v, w, f"workspace {k}")))
                self._drain(w)
            else:
                fields.append((luakey, v))
        if layout_opts:
            fields.append(("layout_opts", Raw(tbl(list(layout_opts.items())))))
        self._out(f"hl.workspace_rule({tbl(fields)})")
        self._bump("workspace_rule")

    # ---------------- monitors --------------------------------------------
    def _h_monitor(self, ev):
        parts = [p.strip() for p in ev.value.split(",")]
        name = parts[0]
        rest = parts[1:]
        fields = [("output", name)]
        if rest and rest[0].lower() in ("disable", "disabled"):
            fields.append(("disabled", True))
            self._out(f"hl.monitor({tbl(fields)})")
            self._bump("monitor")
            return
        if rest and rest[0].lower() == "transform":
            fields.append(("transform", _int(rest[1] if len(rest) > 1 else "0", 0)))
            self._warn("L18", "`monitor = NAME, transform, N` updated an existing rule "
                              "in place; hl.monitor merges into the same output rule")
            self._out(f"hl.monitor({tbl(fields)})")
            self._bump("monitor")
            return
        if rest and rest[0].lower() == "addreserved":
            nums = [_int(x, 0) for x in rest[1:5]] + [0, 0, 0, 0]
            top, bottom, left, right = nums[0], nums[1], nums[2], nums[3]
            self._warn("L18", "`addreserved TOP,BOTTOM,LEFT,RIGHT` reordered into the "
                              "named css-gap keys {top,right,bottom,left}")
            fields.append(("reserved", Raw(tbl([("top", top), ("right", right),
                                                ("bottom", bottom), ("left", left)]))))
            self._out(f"hl.monitor({tbl(fields)})")
            self._bump("monitor")
            return
        mode = rest[0] if len(rest) > 0 else ""
        pos = rest[1] if len(rest) > 1 else ""
        scale = rest[2] if len(rest) > 2 else ""
        if mode:
            if "X" in mode and "x" not in mode:
                self._warn("L18", f"mode {mode!r} uses a capital X; 0.56.2's parser only "
                                  f"accepts lowercase `x` — lowercased")
                mode = mode.replace("X", "x")
            fields.append(("mode", mode))
        if pos:
            fields.append(("position", pos))
        if scale:
            if scale.strip() == "-1":
                self._warn("L18", "`scale = -1` shorthand is rejected by parseScale; "
                                  "emitted as \"auto\"")
                scale = "auto"
            fields.append(("scale", scale))
        i = 3
        while i < len(rest):
            tok = rest[i].strip().lower()
            spec = MONITOR_TRAILING.get(tok)
            if spec is None:
                if tok:
                    self._warn("L18", f"unknown monitor trailing token {rest[i]!r} dropped")
                i += 1
                continue
            luakey, typ = spec
            v = rest[i + 1].strip() if i + 1 < len(rest) else ""
            fields.append((luakey, _int(v, 0) if typ == "int" else
                           (_float(v, 0) if typ == "float" else v)))
            i += 2
        self._out(f"hl.monitor({tbl(fields)})")
        self._bump("monitor")

    def _special_monitorv2(self, fields):
        out = []
        for k, v, _ln in fields:
            typ = MONITORV2_FIELDS.get(k)
            if typ is None:
                self._warn("L18", f"unknown monitorv2 field {k!r} dropped")
                continue
            if typ == "bool":
                b = truthy(v)
                out.append((k, b if b is not None else True))
            elif typ == "int":
                out.append((k, _int(v, 0)))
            elif typ == "float":
                out.append((k, _float(v, 0)))
            elif typ == "scale":
                out.append((k, "auto" if v.strip() == "-1" else v))
            elif typ == "sdr_eotf":
                if re.match(r"^\d+$", v.strip()):
                    names = {"0": "default", "1": "srgb", "2": "gamma22"}
                    self._warn("L18", f"monitorv2 sdr_eotf numeric code {v!r} has no Lua "
                                      f"form; mapped to {names.get(v.strip(), 'default')!r}")
                    out.append((k, names.get(v.strip(), "default")))
                else:
                    out.append((k, v))
            elif typ == "reserved":
                nums = [_int(x, 0) for x in re.split(r"[,\s]+", v.strip()) if x] + [0, 0, 0, 0]
                self._warn("L18", "monitorv2 addreserved reordered into {top,right,bottom,left}")
                out.append(("reserved", Raw(tbl([("top", nums[0]), ("right", nums[3]),
                                                 ("bottom", nums[1]), ("left", nums[2])]))))
            else:
                out.append((k, v))
        self._out(f"hl.monitor({tbl(out)})")
        self._bump("monitor")

    # ---------------- animations ------------------------------------------
    def _h_bezier(self, ev):
        parts = [p.strip() for p in ev.value.split(",")]
        if len(parts) < 5:
            self._warn("PARSE", f"bezier needs name + 4 coords: {ev.value!r}")
            return
        name = parts[0]
        coords = [_float(p, 0) for p in parts[1:5]]
        for i, c in enumerate(coords):
            if c is not None and not (-1 <= c <= 2):
                clamped = max(-1.0, min(2.0, c))
                self._warn("L19", f"bezier {name!r} coordinate {c} is outside the Lua "
                                  f"range -1..2; clamped to {clamped} — the curve shape "
                                  f"changes")
                coords[i] = clamped
        pts = Raw("{ { %s, %s }, { %s, %s } }" % tuple(val(c) for c in coords))
        self._out(f"hl.curve({lit(name)}, {tbl([('type', 'bezier'), ('points', pts)])})")
        self._bump("curve")

    def _h_animation(self, ev):
        parts = [p.strip() for p in ev.value.split(",")]
        if not parts or not parts[0]:
            return
        leaf = parts[0]
        on = truthy(parts[1]) if len(parts) > 1 else True
        if on is None:
            on = True
        if not on:
            self._out(f"hl.animation({tbl([('leaf', leaf), ('enabled', False)])})")
            self._bump("animation")
            return
        speed = _float(parts[2], None) if len(parts) > 2 else None
        curve = parts[3] if len(parts) > 3 else ""
        style = parts[4] if len(parts) > 4 else ""
        if speed is not None and not (0 < speed <= 100):
            clamped = 100.0 if speed > 100 else 0.1
            self._warn("L19", f"animation {leaf!r} speed {speed} is outside the Lua range "
                              f"0<x<=100; clamped to {clamped} — the animation will run at "
                              f"a different rate than it did under hyprlang")
            speed = clamped
        fields = [("leaf", leaf), ("enabled", True)]
        if speed is not None:
            fields.append(("speed", speed))
        if curve:
            fields.append(("bezier", curve))
        else:
            self._warn("L19", f"animation {leaf!r} has no curve; Lua requires "
                              f"`bezier` or `spring`")
        if style:
            fields.append(("style", style))
        self._out(f"hl.animation({tbl(fields)})")
        self._bump("animation")

    # ---------------- gestures --------------------------------------------
    def _h_gesture(self, ev):
        flags = ev.extra.get("flags", "")
        parts = [p.strip() for p in ev.value.split(",")]
        if len(parts) < 3:
            self._warn("PARSE", f"gesture needs fingers, direction, action: {ev.value!r}")
            return
        fields = [("fingers", _int(parts[0], 3))]
        direction = parts[1]
        if direction.lower() not in GESTURE_DIRECTIONS:
            self._warn("PARSE", f"gesture direction {direction!r} is not a known name")
        fields.append(("direction", direction))
        rest = parts[2:]
        mods = scale = None
        while rest and (rest[0].lower().startswith("mod:") or
                        rest[0].lower().startswith("scale:")):
            tokk, _, tokv = rest[0].partition(":")
            if tokk.lower() == "mod":
                mods = tokv.strip()
            else:
                scale = _float(tokv, None)
            rest = rest[1:]
        if not rest:
            self._warn("PARSE", "gesture has no action")
            return
        action = rest[0].strip()
        extra = [r.strip() for r in rest[1:]]
        low = action.lower()
        if low == "dispatcher":
            if not extra:
                self._warn("L12", "gesture dispatcher action with no dispatcher name")
                return
            w = []
            expr = dispatchers.translate(extra[0], ",".join(extra[1:]), w)
            self._drain(w, "gesture: ")
            if expr is None:
                self._out(f"-- [skipped gesture] {ev.value}")
                return
            self._warn("L12", "gesture `dispatcher` action has no Lua string form; "
                              "converted to a callback around hl.dispatch")
            fields.append(("action", Raw(f"function() hl.dispatch({expr}) end")))
        else:
            canon = {"cursorzoom": "cursor_zoom", "scrollmove": "scroll_move"}.get(low, low)
            fields.append(("action", canon))
            if canon == "special" and extra:
                fields.append(("workspace_name", extra[0]))
            elif canon in ("float", "fullscreen") and extra:
                fields.append(("mode", extra[0]))
            elif canon == "cursor_zoom" and extra:
                fields.append(("zoom_level", _float(extra[0], 1)))
                if len(extra) > 1:
                    fields.append(("mode", extra[1]))
        if mods:
            fields.append(("mods", mods))
        if scale is not None:
            fields.append(("scale", scale))
        if "p" in flags:
            fields.append(("disable_inhibit", True))
        self._out(f"hl.gesture({tbl(fields)})")
        self._bump("gesture")

    # ---------------- devices ---------------------------------------------
    def _special_device(self, fields):
        out = []
        name = None
        for k, v, _ln in fields:
            k = k.strip()
            if k == "name":
                name = v.strip().replace(" ", "-")
                continue
            if k in DEVICE_DROPPED:
                self._warn("L20", f"device field {k!r} is not settable per-device under "
                                  f"Lua (absent from DEVICE_FIELDS); dropped")
                continue
            lk = DEVICE_RENAMES.get(k, k)
            if lk != k:
                self._warn("L20", f"device field {k!r} renamed to {lk!r}")
            if lk in DEVICE_BOOL:
                b = truthy(v)
                out.append((lk, b if b is not None else True))
            elif lk in DEVICE_INT:
                out.append((lk, _int(v, 0)))
            elif lk in DEVICE_FLOAT:
                out.append((lk, _float(v, 0)))
            elif lk in DEVICE_VEC2:
                w = []
                out.append((lk, _vec2(v, w, f"device {lk}")))
                self._drain(w)
            else:
                out.append((lk, v))
        if name is None:
            self._warn("PARSE", "device block without a name")
            return
        self._out(f"hl.device({tbl([('name', name)] + out)})")
        self._bump("device")

    # ---------------- plugin blocks ---------------------------------------
    def _special_plugin(self, fields):
        by_ns = {}
        for k, v, _ln in fields:
            ns, _, rest = k.partition(":")
            if not rest:
                continue
            by_ns.setdefault(ns, []).append((rest, v))
        for ns, items in by_ns.items():
            tree = {}
            for k, v in items:
                insert(tree, ["plugin", ns] + [p.replace("-", "_") for p in k.split(":")], v)
            self._warn("L21", f"plugin block {ns!r}: unknown keys error under Lua when the "
                              f"plugin is not loaded; wrapped in an `if hl.plugin.{ns}` guard")
            self._out(f"if hl.plugin.{ns} ~= nil then hl.config({nested_tbl(tree)}) end")
            self._bump("plugin_value")
