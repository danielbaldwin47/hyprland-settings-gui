#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Does `ecosystem:no_donation_nag` suppress the donate
screen under BOTH engines? A stray nag window steals focus and moves every other
window, which would wreck any visual comparison."""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402

TMP = "/home/daniel/.claude/jobs/8df8d093/tmp/probe"
HOME = "/home/daniel/.claude/jobs/8df8d093/tmp/naghome"

CASES = {
    "nag-on.conf": "ecosystem {\n    no_donation_nag = 0\n}\n",
    "nag-off.conf": "ecosystem {\n    no_donation_nag = 1\n}\n",
    "nag-on.lua": "hl.config({ ecosystem = { no_donation_nag = false } })\n",
    "nag-off.lua": "hl.config({ ecosystem = { no_donation_nag = true } })\n",
}


def main():
    os.makedirs(TMP, exist_ok=True)
    for name, body in CASES.items():
        home = os.path.join(HOME, name.replace(".", "_"))
        for sub in (".config", ".local/share", ".local/state", ".cache"):
            os.makedirs(os.path.join(home, sub), exist_ok=True)
        p = os.path.join(TMP, name)
        open(p, "w").write(body)
        with Nested(p, home=home) as n:
            time.sleep(6)
            cls = [c.get("class") for c in (n.ctl("clients") or [])]
            print(f"{name:14} clients={cls}")


if __name__ == "__main__":
    main()
