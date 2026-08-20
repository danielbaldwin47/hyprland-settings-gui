#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Which construct makes the hyprlang engine reserve
monitor space that the Lua engine does not?

Bisects a .conf by top-level chunk (a `name { ... }` block or a bare line) using
"monitor.reserved is non-zero" as the oracle. Comment lines never open or close a
block (hyprlang config.cpp:674 strips them first), which a naive splitter gets
wrong on the stock config's commented-out `# ecosystem {`.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nested import Nested  # noqa: E402

TMP = "/home/daniel/.claude/jobs/8df8d093/tmp/probe"


def chunks(path):
    out, buf, depth = [], [], 0
    for line in open(path, errors="replace"):
        s = line.strip()
        buf.append(line.rstrip("\n"))
        if not s.startswith("#"):
            if s.endswith("{") and "=" not in s:
                depth += 1
            elif s == "}":
                depth = max(0, depth - 1)
        if depth == 0:
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return [c for c in out if c.strip()]


def reserved_for(cs, tag):
    p = os.path.join(TMP, f"bisect-{tag}.conf")
    open(p, "w").write("\n".join(cs) + "\n")
    try:
        with Nested(p) as n:
            mons = n.ctl("monitors")
            return mons[0]["reserved"] if mons else None
    except Exception as exc:
        return f"ERR {exc}"


def fires(r):
    return isinstance(r, list) and any(v != 0 for v in r)


def main():
    src = sys.argv[1]
    cs = chunks(src)
    print(f"{len(cs)} top-level chunks")
    base = reserved_for(cs, "all")
    print("all:", base)
    if not fires(base):
        print("oracle never fires; nothing to bisect")
        return
    cur = cs
    step = 0
    while len(cur) > 1:
        mid = len(cur) // 2
        a, b = cur[:mid], cur[mid:]
        ra = reserved_for(a, f"s{step}a")
        if fires(ra):
            cur = a
            print(f"  -> first half ({len(a)} chunks) still fires: {ra}")
        else:
            rb = reserved_for(b, f"s{step}b")
            if fires(rb):
                cur = b
                print(f"  -> second half ({len(b)} chunks) still fires: {rb}")
            else:
                print(f"  neither half fires alone (a={ra} b={rb}); "
                      f"needs a combination — stopping at {len(cur)} chunks")
                break
        step += 1
    print("\n=== minimal firing set ===")
    for c in cur:
        print(c)


if __name__ == "__main__":
    main()
