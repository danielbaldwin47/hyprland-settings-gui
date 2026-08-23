#!/usr/bin/env python3
"""Build a Generated schema for one Hyprland release.

Run on a machine with the target Hyprland available (release-check step 1,
`docs/agents/hyprland-release-check.md`)::

    tools/gen_schema.py --source-ref v0.56.2 -o data/schema/hyprland-0.56.2.json

The three sources and how each is reached:

1. `hyprctl -j descriptions` -- from the running compositor, or `--descriptions FILE`.
2. `hl.meta.lua` -- `/usr/share/hypr/stubs/hl.meta.lua`, or `--stub FILE`.
3. `ConfigValues.{cpp,hpp}` at the release tag -- `--source DIR` for a checkout, or
   `--source-ref TAG` to fetch the two files from GitHub. Both optional: without either,
   the generator degrades (Color falls back to string, `vec2Range` bounds and refresh bits
   are absent) and says so in the output's provenance block, which the release-check PR
   must mention. The Overlay then has to carry what was lost.

The tool is deliberately thin. Every rule it applies lives in `hyprtweaker.engine.schema`,
because ADR-0012 makes the app run the same inference at runtime against a Hyprland newer
than any shipped schema.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hyprtweaker.engine.schema import sources  # noqa: E402
from hyprtweaker.engine.schema.generated import GeneratedSchema, dumps  # noqa: E402
from hyprtweaker.engine.schema.infer import build_option  # noqa: E402

DEFAULT_STUB = Path("/usr/share/hypr/stubs/hl.meta.lua")
RAW_SOURCE_URL = "https://raw.githubusercontent.com/hyprwm/Hyprland/{ref}/{path}"
SOURCE_FILES = ("src/config/values/ConfigValues.cpp", "src/config/values/ConfigValues.hpp")


def hyprland_version() -> str:
    """The running compositor's version, e.g. `0.56.2` from `hyprctl version`."""
    output = subprocess.run(
        ["hyprctl", "version"], capture_output=True, text=True, check=True
    ).stdout
    match = re.search(r"Hyprland (\d+(?:\.\d+)*)", output)
    if match is None:
        raise SystemExit(f"could not parse a version out of `hyprctl version`:\n{output}")
    return match.group(1)


def hyprland_commit() -> str | None:
    """The compositor's build commit, so a schema names the exact tree it came from.

    Recorded rather than the local input paths: provenance exists so a release check can
    reproduce the file, and one machine's `/tmp` scratch directory tells nobody anything.
    """
    try:
        output = subprocess.run(
            ["hyprctl", "version"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"at commit ([0-9a-f]{7,40})", output)
    return match.group(1) if match else None


def read_descriptions(path: Path | None) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8")
    return subprocess.run(
        ["hyprctl", "-j", "descriptions"], capture_output=True, text=True, check=True
    ).stdout


def read_source(directory: Path | None, ref: str | None) -> tuple[str, str] | None:
    """The two `ConfigValues` files, from a checkout or from GitHub at a tag."""
    if directory is not None:
        return tuple(  # type: ignore[return-value]
            (directory / name).read_text(encoding="utf-8") for name in SOURCE_FILES
        )
    if ref is not None:
        fetched: list[str] = []
        for name in SOURCE_FILES:
            url = RAW_SOURCE_URL.format(ref=ref, path=name)
            with urllib.request.urlopen(url, timeout=30) as response:
                fetched.append(response.read().decode("utf-8"))
        return fetched[0], fetched[1]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--descriptions", type=Path, help="`hyprctl -j descriptions` JSON")
    parser.add_argument("--stub", type=Path, default=DEFAULT_STUB, help="hl.meta.lua")
    parser.add_argument("--source", type=Path, help="a Hyprland checkout at the release tag")
    parser.add_argument("--source-ref", help="fetch ConfigValues.* from GitHub at this tag")
    parser.add_argument("--version", help="Hyprland version (default: `hyprctl version`)")
    parser.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args(argv)

    version = args.version or hyprland_version()

    records = sources.parse_descriptions(read_descriptions(args.descriptions))
    stub_text = args.stub.read_text(encoding="utf-8")
    stub_types = sources.parse_stub_types(stub_text)
    stub_keys = sources.parse_stub_keys(stub_text)

    source_text = read_source(args.source, args.source_ref)
    facts = (
        sources.parse_source(*source_text) if source_text is not None else sources.SourceFacts()
    )

    # Every option must exist in both `descriptions` and the stub. A name in one but not
    # the other means the stub was generated from a different Hyprland than the one
    # running -- which would silently mistype Gradient and CssGap options as plain strings.
    described = {sources.lua_key_for(str(record["name"])) for record in records}
    if missing := described - stub_keys:
        raise SystemExit(
            f"{len(missing)} option(s) in descriptions are absent from the stub's "
            f"HL.ConfigKey list -- stub/compositor version mismatch: {sorted(missing)[:5]}"
        )

    options = tuple(
        build_option(record, order, stub_types, facts) for order, record in enumerate(records)
    )

    provenance: dict[str, Any] = {
        "hyprland_version": version,
        "hyprland_commit": hyprland_commit(),
        "descriptions": "hyprctl -j descriptions",
        "stub": "hl.meta.lua",
        "source_ref": args.source_ref or ("local checkout" if args.source else None),
        "degraded": facts.is_empty,
        "option_count": len(options),
    }
    if facts.is_empty:
        provenance["degradation"] = (
            "no Hyprland source consulted: Color options fall back to string, and "
            "vec2Range bounds, strChoice lists and refresh bits are absent. "
            "The Overlay must carry them."
        )

    schema = GeneratedSchema(hyprland_version=version, options=options, provenance=provenance)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dumps(schema), encoding="utf-8")

    counts: dict[str, int] = {}
    for option in options:
        counts[option.widget.value] = counts.get(option.widget.value, 0) + 1
    flagged = sum(1 for option in options if option.curation_flags)

    print(f"wrote {args.out} -- Hyprland {version}, {len(options)} options")
    print(f"  widgets: {json.dumps(dict(sorted(counts.items())))}")
    print(f"  needing curation: {flagged}")
    if facts.is_empty:
        print("  WARNING: degraded run, no Hyprland source consulted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
