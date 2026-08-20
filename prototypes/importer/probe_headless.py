#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Can we add a fixed-size headless output inside the
nested Hyprland, so screenshots do not depend on how big the host tiled the
nested window? Also checks that it works under BOTH config engines."""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402

TMP = "/home/daniel/.claude/jobs/8df8d093/tmp/probe"


def main():
    for name, body in (("h.conf", "general {\n    border_size = 2\n}\n"),
                       ("h.lua", "hl.config({ general = { border_size = 2 } })\n")):
        p = os.path.join(TMP, name)
        open(p, "w").write(body)
        with Nested(p) as n:
            print("==", name, "before:",
                  [(m["name"], m["width"], m["height"]) for m in n.ctl("monitors")])
            out = n.ctl("output", "create", "headless", js=False)
            print("   create ->", out.strip()[:120])
            time.sleep(1.5)
            mons = n.ctl("monitors")
            print("   after:", [(m["name"], m["width"], m["height"], m["scale"])
                                for m in mons])
            hl = [m for m in mons if m["name"].lower().startswith("headless")]
            if hl:
                shot = os.path.join(TMP, f"headless-{name}.png")
                ok, err = n.grim_output(hl[0]["name"], shot)
                print("   grim:", ok, err.strip()[:120],
                      os.path.getsize(shot) if os.path.exists(shot) else "-")


if __name__ == "__main__":
    main()
