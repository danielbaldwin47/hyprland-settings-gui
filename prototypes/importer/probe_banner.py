#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Confirm that `monitors[].reserved` tracks Hyprland's
on-screen config-error banner, not anything the importer emits."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402

TMP = "/home/daniel/.claude/jobs/8df8d093/tmp/probe"

CLEAN = "general {\n    border_size = 2\n}\n"
CASES = {
    "clean.conf": CLEAN,
    "err1.conf": CLEAN + "dwindle {\n    pseudotile = true\n}\n",
    "err3.conf": (CLEAN + "dwindle {\n    pseudotile = true\n}\n"
                  "misc {\n    vfr = true\n}\n"
                  "render {\n    cm_fs_passthrough = 1\n}\n"),
    "err8.conf": (CLEAN + "dwindle {\n    pseudotile = true\n}\n"
                  + "".join(f"misc {{\n    bogus{i} = 1\n}}\n" for i in range(7))),
    "clean.lua": "hl.config({ general = { border_size = 2 } })\n",
    "err1.lua": ("hl.config({ general = { border_size = 2 } })\n"
                 "hl.config({ general = { nope = 1 } })\n"),
}


def main():
    os.makedirs(TMP, exist_ok=True)
    for name, body in CASES.items():
        p = os.path.join(TMP, name)
        open(p, "w").write(body)
        with Nested(p) as n:
            mons = n.ctl("monitors")
            errs = [e for e in (n.ctl("configerrors") or []) if str(e).strip()]
            res = mons[0]["reserved"] if mons else None
            print(f"{name:12} reserved={res} errors={len(errs)}")


if __name__ == "__main__":
    main()
