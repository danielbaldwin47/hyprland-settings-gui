#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Inspect the nested compositor log while injecting keys."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402

HIT = "/home/daniel/.claude/jobs/8df8d093/tmp/hit"
TMP = "/home/daniel/.claude/jobs/8df8d093/tmp/probe"
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")

LUA = """hl.config({{ debug = {{ disable_logs = false }} }})
hl.bind("Q", hl.dsp.exec_cmd("touch {h}/plain_q"))
"""


def main():
    os.makedirs(HIT, exist_ok=True)
    for f in os.listdir(HIT):
        os.remove(os.path.join(HIT, f))
    open(f"{TMP}/w2.lua", "w").write(LUA.format(h=HIT))
    with Nested(f"{TMP}/w2.lua") as n:
        env = n.env()
        log = os.path.join(RUNTIME, "hypr", n.sig, "hyprland.log")
        time.sleep(2)
        before = os.path.getsize(log) if os.path.exists(log) else 0
        for args in (["-k", "q"], ["-P", "q", "-p", "q"], ["hello"]):
            p = subprocess.run(["wtype"] + args, env=env, capture_output=True,
                               text=True, timeout=30)
            print("wtype", args, "rc", p.returncode, p.stderr.strip()[:120])
            time.sleep(1.2)
        time.sleep(2)
        if os.path.exists(log):
            with open(log, errors="replace") as fh:
                fh.seek(before)
                tail = fh.read()
            hits = [l for l in tail.splitlines()
                    if any(w in l.lower() for w in
                           ("keyboard", "keybind", "key press", "virtual", "keysym"))]
            print("log lines matched:", len(hits))
            for l in hits[:25]:
                print("  ", l[:180])
        else:
            print("no log at", log)
    print("plain_q fired:", os.path.exists(f"{HIT}/plain_q"))


if __name__ == "__main__":
    main()
