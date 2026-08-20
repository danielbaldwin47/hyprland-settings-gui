#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Compare our conversion against upstream's OWN Lua port.

end-4 and ML4W were captured mid-migration (tests/corpus/README.md): each ships a
hand-written .lua beside every .conf at the same commit. Those ports are the only
human ground truth that exists for a hyprlang->Lua migration, so this loads three
configs in a nested Hyprland — the original .conf, our generated .lua, and
upstream's .lua — and compares the resulting bind sets and option values.

Upstream's port is NOT a mechanical translation (they reorganise, drop, and
rewrite things), so a difference here is a lead to inspect, not a defect.
"""
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from nested import Nested  # noqa: E402
from structural import (BIND_KEYS, diff_options, env_for, option_names,  # noqa: E402
                        stage)

PAIRS = {"end-4": "hyprland.lua", "ml4w": "hyprland.lua"}


def canon(b):
    return tuple((k, b.get(k)) for k in BIND_KEYS)


def keyset(binds):
    return [(b.get("modmask"), b.get("key"), b.get("keycode"), b.get("submap"))
            for b in binds]


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "gt")
    os.makedirs(root, exist_ok=True)
    options = option_names()
    out = {}
    for rice, upstream_name in PAIRS.items():
        outdir = os.path.join(root, rice)
        os.makedirs(outdir, exist_ok=True)
        home, entry = stage(rice, root)
        env = env_for(home)
        ours = os.path.join(outdir, "hyprland.lua")
        p = subprocess.run([sys.executable, os.path.join(HERE, "convert.py"), entry,
                            "-o", ours, "--quiet"], capture_output=True, text=True,
                           env=env, timeout=300)
        if p.returncode != 0:
            out[rice] = {"error": "convert failed"}
            continue
        upstream = os.path.join(home, ".config", "hypr", upstream_name)
        if not os.path.exists(upstream):
            out[rice] = {"error": f"no upstream lua at {upstream}"}
            continue
        states = {}
        for tag, cfg in (("conf", entry), ("ours", ours), ("upstream", upstream)):
            try:
                with Nested(cfg, home=home,
                            log=os.path.join(outdir, f"{tag}.log")) as n:
                    states[tag] = {"binds": n.ctl("binds"),
                                   "options": n.getoptions(options),
                                   "errors": [e for e in (n.ctl("configerrors") or [])
                                              if str(e).strip()]}
            except Exception as exc:
                states[tag] = {"error": f"{type(exc).__name__}: {exc}"}
        rec = {}
        for tag in ("conf", "ours", "upstream"):
            s = states.get(tag, {})
            rec[tag] = {"binds": len(s.get("binds", [])) if "binds" in s else None,
                        "errors": len(s.get("errors", [])) if "errors" in s else None,
                        "error": s.get("error")}
        for tag, s in states.items():
            if "binds" in s:
                with open(os.path.join(outdir, f"state-{tag}.json"), "w") as fh:
                    json.dump(s, fh, indent=1, sort_keys=True)
        if all("binds" in states.get(t, {}) for t in ("conf", "ours", "upstream")):
            ks = {t: collections.Counter(keyset(states[t]["binds"])) for t in states}
            rec["binds_by_submap"] = {
                t: dict(collections.Counter(b.get("submap")
                                            for b in states[t]["binds"]))
                for t in states}
            rec["key_overlap_ours_vs_conf"] = sum((ks["ours"] & ks["conf"]).values())
            rec["key_overlap_upstream_vs_conf"] = sum(
                (ks["upstream"] & ks["conf"]).values())
            rec["key_overlap_ours_vs_upstream"] = sum(
                (ks["ours"] & ks["upstream"]).values())
            rec["option_diffs_ours_vs_conf"] = len(
                diff_options(states["conf"]["options"], states["ours"]["options"]))
            rec["option_diffs_upstream_vs_conf"] = len(
                diff_options(states["conf"]["options"], states["upstream"]["options"]))
            rec["option_diffs_ours_vs_upstream"] = len(
                diff_options(states["upstream"]["options"], states["ours"]["options"]))
        out[rice] = rec
        print(rice, json.dumps(rec))
    with open(os.path.join(root, "groundtruth.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print("wrote", os.path.join(root, "groundtruth.json"))


if __name__ == "__main__":
    main()
