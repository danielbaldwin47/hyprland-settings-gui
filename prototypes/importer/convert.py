#!/usr/bin/env python3
"""PROTOTYPE — throwaway. hyprlang .conf tree -> hyprland.lua.

    convert.py <hyprland.conf> [-o out.lua] [--json report.json]
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hyprlang  # noqa: E402
from emit import Converter  # noqa: E402


def convert(path, env=None):
    p = hyprlang.parse(path, env=env)
    c = Converter().run(p)
    diags = [d.as_dict() for d in p.diags] + [d.as_dict() for d in c.warns]
    files = sorted({e.file for e in p.events if e.kind == "source_enter"})
    var_names = [n for n, _v, _f, _l in p._var_defs]
    redefined = [n for n, cnt in collections.Counter(var_names).items() if cnt > 1]
    report = {
        "config": path,
        "files": files,
        "counts": c.stats,
        "vars": {"definitions": len(var_names), "redefined": sorted(redefined)},
        "rule_order": {
            "named": c.named_rules_seen,
            "anonymous": c.anon_rules_seen,
            "anonymous_before_named": c.anon_before_named,
        },
        "diagnostics": diags,
        "codes": dict(collections.Counter(d["code"] for d in diags)),
    }
    return c.text(), report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("-o", "--out")
    ap.add_argument("--json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    text, report = convert(args.config)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=1)
    if not args.quiet:
        sys.stderr.write(json.dumps({"counts": report["counts"],
                                     "codes": report["codes"]}, indent=1) + "\n")


if __name__ == "__main__":
    main()
