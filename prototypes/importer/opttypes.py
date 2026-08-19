"""PROTOTYPE — throwaway. Per-option value typing for the importer.

Rules R1-R13 are lifted verbatim from prototypes/schema-page/schema.py (issue #8),
which transcribes docs/research/option-schema.md section 1.4. Inputs are all
primary: `hyprctl -j descriptions`, the installed hl.meta.lua stub, and the
source-derived bits captured in coverage.json (issue #3).
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = "/usr/share/hypr/stubs/hl.meta.lua"


def lua_key(name):
    return name.replace(":", ".").replace("-", "_")


def _load_stub_types():
    types = {}
    pat = re.compile(r"---@field \['([^']+)'\]\s+(\S+)")
    inside = False
    for line in open(STUB):
        if "---@class HL.ConfigValueTypes" in line:
            inside = True
            continue
        if inside:
            m = pat.match(line)
            if m:
                types[m.group(1)] = m.group(2)
            elif line.startswith("---@class") or line.startswith("local "):
                break
    return types


def _load_config_keys():
    """HL.ConfigKey alias block — the authoritative set of Lua config keys."""
    keys = set()
    inside = False
    for line in open(STUB):
        if line.startswith("---@alias HL.ConfigKey"):
            inside = True
            continue
        if inside:
            m = re.match(r"---\|\s*\"([^\"]+)\"", line)
            if m:
                keys.add(m.group(1))
            elif not line.startswith("---|"):
                break
    return keys


def _infer(o, stub_type, src):
    d = o["default"]
    has_map = "map" in o
    mn, mx = o.get("min"), o.get("max")
    if isinstance(d, bool):
        return "bool"
    if isinstance(d, list) and len(d) == 2:
        return "vec2"
    if isinstance(d, (int, float)) and has_map:
        if o.get("map"):
            return "int_map"
        return "int"
    if isinstance(d, (int, float)) and not has_map:
        return "float"
    if "min" in o and "max" in o:
        return "css_gaps"
    if stub_type == "string|HL.Gradient":
        return "gradient"
    if stub_type == "integer|string":
        return "font_weight"
    if src and src.get("source_type") == "Color":
        return "color"
    return "string"


class OptionTable:
    def __init__(self):
        descs = json.load(open(os.path.join(HERE, "descriptions.json")))
        cov = json.load(open(os.path.join(HERE, "coverage.json")))
        srcs = {o["name"]: o for o in cov["options"]}
        stub = _load_stub_types()
        self.lua_keys = _load_config_keys()
        self.by_legacy = {}
        for o in descs:
            name = o["name"]
            lk = lua_key(name)
            self.by_legacy[name.lower()] = {
                "legacy": name,
                "lua": lk,
                "type": _infer(o, stub.get(lk), srcs.get(name)),
                "map": o.get("map"),
                "min": o.get("min"),
                "max": o.get("max"),
                "default": o.get("default"),
            }
        # index by lua key too (importer sees legacy colon names, but be forgiving)
        self.by_lua = {v["lua"]: v for v in self.by_legacy.values()}

    def lookup(self, legacy_key):
        """legacy_key like `general:col.active_border` or `decoration:blur:size`."""
        rec = self.by_legacy.get(legacy_key.lower())
        if rec:
            return rec
        return self.by_lua.get(lua_key(legacy_key))

    def known_lua_key(self, key):
        return key in self.lua_keys


_TABLE = None


def table():
    global _TABLE
    if _TABLE is None:
        _TABLE = OptionTable()
    return _TABLE
