#!/usr/bin/env python3
"""Diff two Generated schemas into `hyprland-<ver>.diff.json`.

Layer 1 of the release check's diff (`docs/agents/hyprland-release-check.md` step 2)::

    tools/diff_schema.py data/schema/hyprland-0.57.0.json \
        --predecessor data/schema/hyprland-0.56.2.json -o /tmp/hyprland-0.57.0.diff.json

Every change is classified: added, removed, renamed, rename candidates, retyped, range,
enum map, default, new Section or subsection. `renamed` is only what the Overlay's
`renamed_from` (`--overlay`, default `data/schema/overlay.json`) maps; a removed and an
added Option with one description and default are listed as a rename candidate to confirm
against the release notes. Without a predecessor, or when the file does not exist, the
diff is empty and says so.

The output is for the release-check PR's reviewer and is not shipped: the app reads nothing
from it, so write it outside `data/schema/`. The rules live in
`hyprtweaker.engine.schema.diff`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hyprtweaker.engine.schema import overlay as overlay_module  # noqa: E402
from hyprtweaker.engine.schema.diff import diff_schemas, dumps  # noqa: E402
from hyprtweaker.engine.schema.generated import load  # noqa: E402

DEFAULT_OVERLAY = REPO_ROOT / "data" / "schema" / "overlay.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("schema", type=Path, help="the new Generated schema")
    parser.add_argument(
        "--predecessor",
        type=Path,
        help="the previous newest Generated schema (absent file: an empty diff)",
    )
    parser.add_argument(
        "--overlay", type=Path, default=DEFAULT_OVERLAY, help="overlay.json, for renamed_from"
    )
    parser.add_argument(
        "-o", "--out", type=Path, required=True, help="hyprland-<ver>.diff.json"
    )
    args = parser.parse_args(argv)

    schema = load(args.schema)
    predecessor = None
    if args.predecessor is not None and args.predecessor.is_file():
        predecessor = load(args.predecessor)
    elif args.predecessor is not None:
        print(f"WARNING: no predecessor at {args.predecessor}, so the diff is empty")
    diff = diff_schemas(schema, predecessor, overlay_module.load(args.overlay))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(diff), encoding="utf-8")

    print(f"wrote {args.out} -- Hyprland {diff.hyprland_version} vs {diff.predecessor}")
    for name, count in diff.counts.items():
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
