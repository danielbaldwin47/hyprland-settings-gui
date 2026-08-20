#!/usr/bin/env python3
"""PROTOTYPE — counts behind the #8 answer.

Two independent measures:
  1. machine-checkable defects of the purely generated row (app.defects)
  2. what the hand overlay actually had to say, split into
     STRUCTURAL (title / group / order — mechanical, needed on every option) and
     SEMANTIC   (widget / labels / known_values / nullable / depends_on / range /
                 unit / visibility / help — needs a human who knows Hyprland)
"""
import collections, json, sys

sys.path.insert(0, ".")
import schema, app

STRUCTURAL = {"title", "group", "order", "help_url"}
SEMANTIC = {"widget", "labels", "known_values", "nullable", "null_label", "depends_on",
            "range", "unit", "visibility", "help", "restart", "placeholder"}
# the subset without which the row is WRONG (not merely plain)
CRITICAL = {"widget", "labels", "known_values", "nullable", "null_label", "depends_on",
            "range", "visibility"}

CURATED_SECTIONS = ["input", "decoration", "general"]


def main():
    raw = {r["name"]: r for r in schema.build(curated=False)}
    cur = {r["name"]: r for r in schema.build(curated=True)}
    overlay = schema.load_overlay()

    rows = []
    defect_hist = collections.Counter()
    for name, r in raw.items():
        d = app.defects(r)
        defect_hist.update(d)
        fields = set(cur[name]["curated_fields"])
        rows.append({
            "name": name, "section": r["section"], "widget": r["widget"],
            "defects": d,
            "structural": sorted(fields & STRUCTURAL),
            "semantic": sorted(fields & SEMANTIC),
            "critical": sorted(fields & CRITICAL),
        })

    print("== all 353 options: machine-checkable defects of the generated row ==")
    clean = [x for x in rows if not x["defects"]]
    print("no defect: %d / %d (%.0f%%)" % (len(clean), len(rows), 100 * len(clean) / len(rows)))
    for k, v in defect_hist.most_common():
        print("  %-28s %3d" % (k, v))

    print("\n== per section (all 353) ==")
    print("%-14s %5s %7s %7s" % ("section", "opts", "clean", "defect"))
    for s, items in sorted(collections.Counter(x["section"] for x in rows).items()):
        sec = [x for x in rows if x["section"] == s]
        c = len([x for x in sec if not x["defects"]])
        print("%-14s %5d %7d %7d" % (s, len(sec), c, len(sec) - c))

    print("\n== the three hand-curated sections: what the overlay had to say ==")
    print("%-12s %5s %9s %9s %9s %s" % ("section", "opts", "clean-raw", "structural", "semantic", "semantic fields"))
    for s in CURATED_SECTIONS:
        sec = [x for x in rows if x["section"] == s]
        clean_raw = len([x for x in sec if not x["defects"]])
        st = len([x for x in sec if x["structural"]])
        se = len([x for x in sec if x["semantic"]])
        fh = collections.Counter(f for x in sec for f in x["semantic"])
        print("%-12s %5d %9d %9d %9d %s" % (s, len(sec), clean_raw, st, se,
                                            ", ".join("%s=%d" % kv for kv in fh.most_common())))

    print("\n== options in the curated sections that needed NO semantic override ==")
    for s in CURATED_SECTIONS:
        sec = [x for x in rows if x["section"] == s]
        none = [x for x in sec if not x["semantic"]]
        nocrit = [x for x in sec if not x["critical"]]
        print("%-12s no-semantic %2d/%-3d (%3.0f%%)   no-correctness-critical %2d/%-3d (%3.0f%%)"
              % (s, len(none), len(sec), 100 * len(none) / len(sec),
                 len(nocrit), len(sec), 100 * len(nocrit) / len(sec)))
    allsec = [x for x in rows if x["section"] in CURATED_SECTIONS]
    nocrit = [x for x in allsec if not x["critical"]]
    print("%-12s no-correctness-critical %d/%d (%.0f%%)"
          % ("TOTAL", len(nocrit), len(allsec), 100 * len(nocrit) / len(allsec)))

    print("\n== widget kinds that never needed a semantic override ==")
    per_widget = collections.defaultdict(lambda: [0, 0])
    for x in rows:
        if x["section"] in CURATED_SECTIONS:
            per_widget[x["widget"]][0] += 1
            if x["semantic"]:
                per_widget[x["widget"]][1] += 1
    for w, (n, over) in sorted(per_widget.items(), key=lambda kv: -kv[1][0]):
        print("  %-14s %3d options, %3d needed a semantic override" % (w, n, over))

    json.dump(rows, open("report.json", "w"), indent=1)


if __name__ == "__main__":
    main()
