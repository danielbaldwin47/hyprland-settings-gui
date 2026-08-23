"""`src/meson.build` must list every Python source, or the install is incomplete.

Meson does not notice an unlisted source file: it installs what the lists name
and says nothing about the rest. The gap is invisible from a checkout, where
`PYTHONPATH=src` picks the module up anyway, and only surfaces as an ImportError
from an installed package. This test closes that gap by comparing the build's
source lists against what is actually on disk.
"""

from __future__ import annotations

import re

from _support import ROOT, SRC, assert_lists_match

MESON_BUILD = SRC / "meson.build"


def declared_sources() -> set[str]:
    """The `src/hyprtweaker/...` paths named by `package_sources` in src/meson.build.

    A dict of per-package lists, so it needs its own two-level walk rather than
    `_support.meson_quoted_names` -- the package name and the file name both matter, and
    only their join is a path.
    """
    text = MESON_BUILD.read_text()

    block = re.search(r"package_sources\s*=\s*\{(.*?)^\}", text, re.DOTALL | re.MULTILINE)
    assert block is not None, f"could not find a package_sources dict in {MESON_BUILD}"

    declared: set[str] = set()
    for package, entries in re.findall(r"'([^']+)'\s*:\s*\[(.*?)\]", block.group(1), re.DOTALL):
        for source in re.findall(r"'([^']+)'", entries):
            declared.add(f"{package}/{source}")

    assert declared, "parsed package_sources but found no entries -- the parser is broken"
    return declared


INSTALLABLE = ("*.py", "*.lua")
"""Suffixes that have to reach an install.

`.lua` is here for the Lua importer's `runner.lua`: it is not a module, but the importer
subprocesses it by path, so an install that left it behind would fail at the first import
rather than at start-up -- the same invisible-from-a-checkout failure this test exists to
prevent for `.py`.
"""


def actual_sources() -> set[str]:
    """Every installable file under src/hyprtweaker, as a path relative to src/."""
    return {
        str(path.relative_to(SRC))
        for pattern in INSTALLABLE
        for path in (SRC / "hyprtweaker").rglob(pattern)
    }


def test_every_installable_source_is_installed_and_no_more() -> None:
    assert_lists_match(declared_sources(), actual_sources(), MESON_BUILD)


def test_version_matches_between_build_and_package() -> None:
    """The build and the package must agree on the version, or a bump half-lands."""
    meson_version = re.search(
        r"^project\(.*?^\s*version:\s*'([^']+)'",
        (ROOT / "meson.build").read_text(),
        re.DOTALL | re.MULTILINE,
    )
    assert meson_version is not None, "could not find the project version in meson.build"

    package_version = re.search(
        r'^__version__\s*=\s*"([^"]+)"',
        (SRC / "hyprtweaker" / "__init__.py").read_text(),
        re.MULTILINE,
    )
    assert package_version is not None, "could not find __version__ in hyprtweaker/__init__.py"

    assert meson_version.group(1) == package_version.group(1), (
        f"meson.build says {meson_version.group(1)} but "
        f"hyprtweaker.__version__ is {package_version.group(1)}"
    )
