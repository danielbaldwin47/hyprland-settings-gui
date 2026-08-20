#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Is wtype's virtual keyboard reaching the nested compositor?"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402

HIT = "/home/daniel/.claude/jobs/8df8d093/tmp/hit"
TMP = "/home/daniel/.claude/jobs/8df8d093/tmp/probe"

LUA = """hl.bind("Q", hl.dsp.exec_cmd("touch {h}/plain_q"))
hl.bind("SUPER + Q", hl.dsp.exec_cmd("touch {h}/super_q"))
"""


def main():
    os.makedirs(HIT, exist_ok=True)
    for f in os.listdir(HIT):
        os.remove(os.path.join(HIT, f))
    open(f"{TMP}/w.lua", "w").write(LUA.format(h=HIT))
    with Nested(f"{TMP}/w.lua") as n:
        env = n.env()
        time.sleep(2)
        print("devices:", str(n.ctl("devices"))[:400])
        for args in (["-k", "q"], ["-M", "super", "-k", "q", "-m", "super"], ["q"]):
            p = subprocess.run(["wtype"] + args, env=env, capture_output=True,
                               text=True, timeout=30)
            print("wtype", args, "rc=", p.returncode, "err=", p.stderr.strip()[:200])
            time.sleep(1.0)
        time.sleep(2)
        print("keyboards after:", [d.get("name") for d in
                                   (n.ctl("devices") or {}).get("keyboards", [])])
    print("plain_q:", os.path.exists(f"{HIT}/plain_q"),
          "super_q:", os.path.exists(f"{HIT}/super_q"))


if __name__ == "__main__":
    main()
