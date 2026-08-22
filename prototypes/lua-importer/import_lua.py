#!/usr/bin/env python3
# PROTOTYPE (wayfinder #30) — throwaway. Drives runner.lua, summarizes the capture.
import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent


def run_import(entry: pathlib.Path, basedir: pathlib.Path, out_json: pathlib.Path,
               policy: str = "block", home: pathlib.Path | None = None) -> dict:
    env = dict(os.environ)
    if home:
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["XDG_DATA_HOME"] = str(home / ".local/share")
        env["XDG_STATE_HOME"] = str(home / ".local/state")
        env["XDG_CACHE_HOME"] = str(home / ".cache")
    r = subprocess.run(
        ["lua5.5", str(HERE / "runner.lua"), str(entry), str(basedir), str(out_json), policy],
        env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print("runner crashed:", r.stderr[-2000:], file=sys.stderr)
        raise SystemExit(1)
    if r.stderr.strip():
        print("runner stderr:", r.stderr[-500:], file=sys.stderr)
    return json.loads(out_json.read_text())


def summarize(rec: dict, name: str) -> None:
    calls = rec.get("calls", [])
    by_type = collections.Counter(c["call"] for c in calls)
    scripts = rec.get("scripts", [])
    hybrid = sum(1 for c in calls if json.dumps(c.get("args", {})).find("__fn") >= 0)
    print(f"== {name} ==")
    print(f"calls: {len(calls)}  ({dict(sorted(by_type.items(), key=lambda kv: -kv[1]))})")
    print(f"scripts (functions captured): {len(scripts)}   hybrid calls (fn inside args): {hybrid}")
    ups = [s for s in scripts if s.get("upvalues")]
    print(f"scripts with upvalues: {len(ups)}")
    for s in ups[:10]:
        uvs = ", ".join(f"{u['name']}:{u['type']}" for u in s["upvalues"])
        print(f"   {s['source']}:{s['from']}-{s['to']}  [{uvs}]  ctx={s.get('context')}")
    print(f"queries: {len(rec.get('queries', []))}  shell: {len(rec.get('shell', []))}  "
          f"iowrites: {len(rec.get('iowrites', []))}  requires: {len(rec.get('requires', []))}")
    for s in rec.get("shell", [])[:8]:
        print(f"   shell[{s['kind']}]: {s['cmd'][:110]}")
    errs = rec.get("errors", [])
    print(f"errors: {len(errs)}")
    for e in errs[:10]:
        print(f"   ERR: {e[:160]}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("entry")
    ap.add_argument("basedir")
    ap.add_argument("--policy", default="block", choices=["block", "record"])
    ap.add_argument("--home", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--name", default=None)
    args = ap.parse_args()
    entry = pathlib.Path(args.entry).resolve()
    basedir = pathlib.Path(args.basedir).resolve()
    out = pathlib.Path(args.out) if args.out else HERE / "results" / (
        (args.name or entry.parent.name) + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    rec = run_import(entry, basedir, out, args.policy,
                     pathlib.Path(args.home).resolve() if args.home else None)
    summarize(rec, args.name or str(entry))


if __name__ == "__main__":
    main()
