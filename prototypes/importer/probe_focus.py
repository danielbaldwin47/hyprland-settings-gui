#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Why do probe windows not land on the headless output
under the Lua engine? Checks `hyprctl dispatch` round-trips step by step."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402


def show(n, label):
    mons = n.ctl("monitors")
    print(f"  {label}: " + ", ".join(
        f"{m['name']}(ws{m['activeWorkspace']['id']},focused={m.get('focused')})"
        for m in mons))
    return mons


def main():
    cfg = sys.argv[1]
    with Nested(cfg) as n:
        print("==", os.path.basename(cfg))
        print("  create:", n.ctl("output", "create", "headless", js=False).strip())
        time.sleep(2)
        show(n, "after create")
        r = n.ctl("dispatch", "focusmonitor", "HEADLESS-1", js=False)
        print("  focusmonitor ->", r.strip()[:120])
        time.sleep(1)
        show(n, "after focusmonitor")
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "winspawn.py"),
                              "probe.one", "0.9,0.2,0.2,1.0"], env=n.env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        cl = n.ctl("clients")
        print("  clients:", [(c["class"], c["workspace"]["id"], c["monitor"])
                             for c in cl])
        if cl:
            ws = [m["activeWorkspace"]["id"] for m in n.ctl("monitors")
                  if m["name"] == "HEADLESS-1"]
            r = n.ctl("dispatch", "movetoworkspacesilent",
                      f"{ws[0]},address:{cl[0]['address']}", js=False)
            print("  movetoworkspacesilent ->", r.strip()[:120])
            time.sleep(1.5)
            print("  clients:", [(c["class"], c["workspace"]["id"], c["monitor"])
                                 for c in n.ctl("clients")])
        p.terminate()


if __name__ == "__main__":
    main()
