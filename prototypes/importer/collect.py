#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Copy the run artifacts into the repo, rewriting the
throwaway staging paths back to `~/.config/hypr` so the checked-in Lua is readable."""
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
TMP = "/home/daniel/.claude/jobs/8df8d093/tmp"
RICES = ["hyprland-default", "end-4", "hyde", "jakoolit", "ml4w", "hyprv", "local"]

STAGE_RE = re.compile(re.escape(TMP) + r"/[a-z]+/[^/]+/home(-[a-z]+)?/\.config/hypr")
HOME_RE = re.compile(re.escape(TMP) + r"/[a-z]+/[^/]+/home(-[a-z]+)?")


def clean(text):
    text = STAGE_RE.sub("~/.config/hypr", text)
    return HOME_RE.sub("~", text)


def main():
    os.makedirs(os.path.join(RESULTS, "lua"), exist_ok=True)
    os.makedirs(os.path.join(RESULTS, "shots"), exist_ok=True)
    for rice in RICES:
        src = os.path.join(TMP, "out", rice, "hyprland.lua")
        if os.path.exists(src):
            open(os.path.join(RESULTS, "lua", f"{rice}.lua"), "w").write(
                clean(open(src).read()))
        rep = os.path.join(TMP, "out", rice, "report.json")
        if os.path.exists(rep):
            d = json.loads(clean(open(rep).read()))
            with open(os.path.join(RESULTS, "lua", f"{rice}.report.json"), "w") as fh:
                json.dump(d, fh, indent=1)
    for name, path in (("corpus-summary.json", f"{TMP}/out/corpus-summary.json"),
                       ("structural-summary.json",
                        f"{TMP}/struct/structural-summary.json"),
                       ("visual-summary.json", f"{TMP}/visual/visual-summary.json"),
                       ("groundtruth.json", f"{TMP}/gt/groundtruth.json")):
        if os.path.exists(path):
            with open(os.path.join(RESULTS, name), "w") as fh:
                fh.write(clean(open(path).read()))
    for rice in RICES:
        d = os.path.join(TMP, "struct", rice, "diff.json")
        if not os.path.exists(d):
            continue
        full = json.loads(clean(open(d).read()))
        trimmed = {
            "options": full["options"],
            "binds": {k: v for k, v in full["binds"].items()
                      if k in ("conf_total", "lua_total", "order_identical")},
            "bind_only_conf_sample": full["binds"]["only_conf"][:3],
            "bind_only_lua_sample": full["binds"]["only_lua"][:3],
            "monitors": full["monitors"],
            "animations": full["animations"],
            "beziers": full["beziers"],
            "workspacerules": full["workspacerules"],
            "devices": full["devices"],
        }
        with open(os.path.join(RESULTS, f"struct-{rice}.json"), "w") as fh:
            json.dump(trimmed, fh, indent=1)
    for src, dst in (("visual/local/conf-tiled.png", "local-conf-tiled.png"),
                     ("visual/local/lua-tiled.png", "local-lua-tiled.png")):
        p = os.path.join(TMP, src)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(RESULTS, "shots", dst))
    print("collected into", RESULTS)


if __name__ == "__main__":
    main()
