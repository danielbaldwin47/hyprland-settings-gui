#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Does a `code:N` bind still FIRE under the Lua engine?

`hyprctl binds` reports keycode 0 for a Lua `code:N` bind, but that could be an
IPC reporting gap rather than a real regression. This drives a virtual keyboard
(wtype) into a nested Hyprland and checks whether the bind's command ran.
A named key (SUPER+Q) in the same config is the positive control.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402

HIT = "/home/daniel/.claude/jobs/8df8d093/tmp/hit"
TMP = "/home/daniel/.claude/jobs/8df8d093/tmp/probe"

CONF = """bind = SUPER, code:10, exec, touch {h}/conf_code
bind = SUPER, Q, exec, touch {h}/conf_named
"""
LUA = """hl.bind("SUPER + code:10", hl.dsp.exec_cmd("touch {h}/lua_code"))
hl.bind("SUPER + Q", hl.dsp.exec_cmd("touch {h}/lua_named"))
"""


def run(path, tag):
    with Nested(path) as n:
        env = n.env()
        binds = n.ctl("binds")
        time.sleep(1.5)
        for mods, k in ((["super"], "1"), (["super"], "q")):
            cmd = ["wtype"]
            for m in mods:
                cmd += ["-M", m]
            cmd += ["-k", k]
            for m in mods:
                cmd += ["-m", m]
            subprocess.run(cmd, env=env, capture_output=True, timeout=30)
            time.sleep(0.8)
        time.sleep(1.5)
    return {
        "reported": [(b["modmask"], b["key"], b["keycode"]) for b in binds],
        "code_fired": os.path.exists(f"{HIT}/{tag}_code"),
        "named_fired": os.path.exists(f"{HIT}/{tag}_named"),
    }


def main():
    os.makedirs(HIT, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    for f in os.listdir(HIT):
        os.remove(os.path.join(HIT, f))
    open(f"{TMP}/hit.conf", "w").write(CONF.format(h=HIT))
    open(f"{TMP}/hit.lua", "w").write(LUA.format(h=HIT))
    for path, tag in ((f"{TMP}/hit.conf", "conf"), (f"{TMP}/hit.lua", "lua")):
        r = run(path, tag)
        print(f"{tag}: binds={r['reported']} code_fired={r['code_fired']} "
              f"named_fired={r['named_fired']}")


if __name__ == "__main__":
    main()
