"""PROTOTYPE — throwaway. Builds a widget schema from live Hyprland data.

Inputs (all primary):
  descriptions.json   `hyprctl -j descriptions`            (353 options)
  hl.meta.lua         installed stub, HL.ConfigValueTypes  (Gradient / FontWeight / Vec2 / CssGap)
  coverage.json       source-derived bits from issue #3    (MS<Color>, strChoice, vec2Range)
  overlay.json        hand curation under test             (this prototype's actual subject)

Rules R1-R13 are transcribed from docs/research/option-schema.md section 1.4.
"""
import json, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
STUB = "/usr/share/hypr/stubs/hl.meta.lua"


def lua_key(name):
    return name.replace(":", ".").replace("-", "_")


def load_descriptions(refresh=False):
    path = os.path.join(HERE, "descriptions.json")
    if refresh or not os.path.exists(path):
        out = subprocess.run(["hyprctl", "-j", "descriptions"], capture_output=True, text=True).stdout
        open(path, "w").write(out)
    return json.load(open(path))


def load_stub_types():
    """HL.ConfigValueTypes: ---@field ['general.border_size'] integer|boolean"""
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


def load_source_bits():
    cov = json.load(open(os.path.join(HERE, "coverage.json")))
    return {o["name"]: o for o in cov["options"]}


def load_overlay():
    return json.load(open(os.path.join(HERE, "overlay.json")))


def infer(o, stub_type, src):
    """Return (type, widget) per R1-R13. `o` is a descriptions record."""
    d = o["default"]
    has_map = "map" in o
    mn, mx = o.get("min"), o.get("max")

    if isinstance(d, bool):                                        # R1
        return "bool", "toggle"
    if isinstance(d, list) and len(d) == 2:                        # R2
        return "vec2", "vec2"
    if isinstance(d, (int, float)) and has_map:
        if o.get("map"):                                           # R3
            return "int", "enum-map"
        if mn is not None and mx is not None and mx < 2**31 - 1:   # R4
            return "int", "int-range"
        return "int", "free-int"                                   # R5
    if isinstance(d, (int, float)) and not has_map:
        if mn is not None and mx is not None:                      # R6
            return "float", "float-range"
        return "float", "free-float"                               # R7
    # strings
    if "min" in o and "max" in o:                                  # R8
        return "css_gaps", "css-gaps"
    if stub_type == "string|HL.Gradient":                          # R9
        return "gradient", "gradient"
    if stub_type == "integer|string":                              # R10
        return "font_weight", "font-weight"
    if src and src.get("source_type") == "Color":                  # R11
        return "color", "color"
    if _choices(o, src):                                           # R12
        return "string", "enum-string"
    return "string", "string"                                      # R13


def _choices(o, src):
    c = (src or {}).get("choices")
    if c:
        return list(c)
    m = re.findall(r"\[([^\]]+/[^\]]+)\]", o.get("description", ""))
    if m:
        return m[0].split("/")
    return None


def title_from_name(name):
    leaf = name.split(":")[-1]
    return leaf.replace("col.", "").replace("_", " ").replace(".", " ").strip().capitalize()


def build(curated=True):
    descs = load_descriptions()
    stub = load_stub_types()
    srcs = load_source_bits()
    overlay = load_overlay() if curated else {}
    out = []
    for o in descs:
        name = o["name"]
        src = srcs.get(name, {})
        st = stub.get(lua_key(name))
        typ, widget = infer(o, st, src)
        rec = {
            "name": name,
            "section": name.split(":")[0],
            "path": name.split(":"),
            "type": typ,
            "widget": widget,
            "title": title_from_name(name),
            "description": o.get("description", ""),
            "default": o["default"],
            "min": o.get("min"),
            "max": o.get("max"),
            "map": {list(m.keys())[0]: list(m.values())[0] for m in (o.get("map") or [])} or None,
            "choices": _choices(o, src),
            "vec2_range": src.get("vec2_range"),
            "device_overridable": src.get("device_overridable", False),
            "getoption_key": src.get("getoption_key"),
            "stub_type": st,
            "source_type": src.get("source_type"),
            "unit": None, "help": None, "labels": None, "known_values": None,
            "nullable": False, "null_label": None, "depends_on": None,
            "restart": None, "visibility": "default", "group": None, "order": None,
            "curated_fields": [],
        }
        if curated:
            sec_ov = overlay.get("@section:" + rec["section"]) or {}
            for k, v in sec_ov.items():
                if k != "groups":
                    rec[k] = v
            ov = overlay.get(name)
            if ov:
                for k, v in ov.items():
                    rec[k] = v
                    rec["curated_fields"].append(k)
        out.append(rec)
    return out


def groups_for(section, curated=True):
    """Ordered [(group title, [option names])] from the overlay, or None."""
    if not curated:
        return None
    ov = load_overlay().get("@section:" + section) or {}
    return ov.get("groups")


if __name__ == "__main__":
    import collections, sys
    recs = build(curated="--raw" not in sys.argv)
    print(len(recs), "options")
    print(collections.Counter(r["widget"] for r in recs).most_common())
