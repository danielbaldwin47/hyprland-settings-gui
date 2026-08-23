"""Shared helpers for the unit tier.

A plain module rather than `conftest.py`: `tests/unit` is not a package, so a conftest is
reachable as fixtures but not as an import. pytest puts this directory on `sys.path`,
which makes `from _support import ...` the working form.

Two things several schema tests need: where the shipped schema files are, and a way to
read the explicit file lists out of a `meson.build`. Both `src/meson.build` (Python
sources) and `data/meson.build` (schema data) list their files by hand -- meson says
nothing about a file no list names -- so both need the same "declared vs on disk" check,
and it is one helper rather than two copies of the same parser.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SCHEMA_DIR = ROOT / "data" / "schema"


def meson_quoted_names(text: str, block: str) -> set[str]:
    """Every single-quoted name inside the `block` construct of a meson.build.

    `block` is a regex with one capturing group around the region to scan, e.g. a
    `foo = files(...)` call or a `foo = {...}` dict.
    """
    found = re.search(block, text, re.DOTALL | re.MULTILINE)
    assert found is not None, f"could not find /{block}/ in the meson.build"

    names = set(re.findall(r"'([^']+)'", found.group(1)))
    assert names, f"matched /{block}/ but found no quoted names -- the parser is broken"
    return names


def assert_lists_match(declared: set[str], actual: set[str], meson_file: Path) -> None:
    """Both directions: nothing uninstalled on disk, nothing installed that is gone."""
    missing = actual - declared
    assert not missing, (
        f"these files exist but {meson_file.name} does not install them "
        f"(add them to its list): {sorted(missing)}"
    )

    stale = declared - actual
    assert not stale, f"{meson_file.name} installs files that no longer exist: {sorted(stale)}"
