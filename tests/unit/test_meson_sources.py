"""`src/meson.build` must list every Python source, or the install is incomplete.

Meson does not notice an unlisted source file: it installs what the lists name
and says nothing about the rest. The gap is invisible from a checkout, where
`PYTHONPATH=src` picks the module up anyway, and only surfaces as an ImportError
from an installed package. This test closes that gap by comparing the build's
source lists against what is actually on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
MESON_BUILD = SRC / "meson.build"


def declared_sources() -> set[str]:
    """The `src/hyprtweaker/...` paths named by `package_sources` in src/meson.build."""
    text = MESON_BUILD.read_text()

    block = re.search(r"package_sources\s*=\s*\{(.*?)^\}", text, re.DOTALL | re.MULTILINE)
    assert block is not None, f"could not find a package_sources dict in {MESON_BUILD}"

    declared: set[str] = set()
    for package, entries in re.findall(r"'([^']+)'\s*:\s*\[(.*?)\]", block.group(1), re.DOTALL):
        for source in re.findall(r"'([^']+)'", entries):
            declared.add(f"{package}/{source}")

    assert declared, "parsed package_sources but found no entries -- the parser is broken"
    return declared


def actual_sources() -> set[str]:
    """Every .py file under src/hyprtweaker, as a path relative to src/."""
    return {str(path.relative_to(SRC)) for path in (SRC / "hyprtweaker").rglob("*.py")}


def test_every_python_source_is_installed() -> None:
    missing = actual_sources() - declared_sources()
    assert not missing, (
        "these files exist but src/meson.build does not install them "
        f"(add them to package_sources): {sorted(missing)}"
    )


def test_no_installed_source_is_missing_from_disk() -> None:
    stale = declared_sources() - actual_sources()
    assert not stale, f"src/meson.build installs files that no longer exist: {sorted(stale)}"


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
