#!/usr/bin/env python3
"""PROTOTYPE — throwaway. What does `hyprctl dispatch` accept under each engine?

Under a hyprlang config the legacy dispatcher names work (DispatcherTranslator).
Under a Lua config the argument is evaluated as Lua source instead, so every
external tool / script / bar that shells out to `hyprctl dispatch movefocus l`
has to be rewritten. This measures exactly that.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402

TMP = "/home/daniel/.claude/jobs/8df8d093/tmp/probe"

CASES = [
    ("legacy: movefocus l", ["dispatch", "movefocus", "l"]),
    ("legacy: focusmonitor HEADLESS-1", ["dispatch", "focusmonitor", "HEADLESS-1"]),
    ("legacy: workspace 3", ["dispatch", "workspace", "3"]),
    ("legacy: exec true", ["dispatch", "exec", "true"]),
    ("lua:    hl.dsp.focus({direction='l'})",
     ["dispatch", "hl.dsp.focus({direction='l'})"]),
    ("lua:    hl.dsp.focus({monitor='HEADLESS-1'})",
     ["dispatch", "hl.dsp.focus({monitor='HEADLESS-1'})"]),
    ("lua:    hl.dsp.exec_cmd('true')", ["dispatch", "hl.dsp.exec_cmd('true')"]),
    ("legacy: keyword general:border_size 4",
     ["keyword", "general:border_size", "4"]),
]


def main():
    os.makedirs(TMP, exist_ok=True)
    open(f"{TMP}/d.conf", "w").write("general {\n    border_size = 2\n}\n")
    open(f"{TMP}/d.lua", "w").write("hl.config({ general = { border_size = 2 } })\n")
    for cfg in (f"{TMP}/d.conf", f"{TMP}/d.lua"):
        print("==", os.path.basename(cfg))
        with Nested(cfg) as n:
            n.ctl("output", "create", "headless", js=False)
            time.sleep(1.5)
            for label, args in CASES:
                out = n.ctl(*args, js=False).strip().replace("\n", " ")[:110]
                verdict = "OK" if out.lower() in ("ok", "") else "FAIL"
                print(f"   {verdict:4} {label:44} -> {out}")


if __name__ == "__main__":
    main()
