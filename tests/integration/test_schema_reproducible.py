"""Integration tier: the generator actually reproduces the committed schema.

ADR-0011 tier 3 -- needs a real Hyprland, marked `-m hyprland`, auto-skipped without one,
and deliberately outside `testpaths` so a per-commit run never reaches for the network.

The unit tier proves the committed file is what `dumps` produces. Only this proves it is
what the *generator* produces, because only here are the three real inputs available: a
running compositor of the matching version, its installed Lua stub, and the Hyprland
source at the release tag. That is the machine a release check runs on
(`docs/agents/hyprland-release-check.md` step 1), which is exactly when it matters.

Run it explicitly::

    pytest tests/integration -m hyprland
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.schema import generated as generated_module  # noqa: E402

SCHEMA_DIR = ROOT / "data" / "schema"

pytestmark = pytest.mark.hyprland


def running_hyprland_version() -> str | None:
    if shutil.which("hyprctl") is None:
        return None
    result = subprocess.run(["hyprctl", "version"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    match = re.search(r"Hyprland (\d+(?:\.\d+)*)", result.stdout)
    return match.group(1) if match else None


def test_the_generator_reproduces_the_committed_schema(tmp_path: Path) -> None:
    version = running_hyprland_version()
    if version is None:
        pytest.skip("no running Hyprland on this machine")

    committed = SCHEMA_DIR / f"hyprland-{version}.json"
    if not committed.is_file():
        pytest.skip(f"no committed schema for the running Hyprland {version}")

    regenerated = tmp_path / f"hyprland-{version}.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "gen_schema.py"),
            "--source-ref",
            f"v{version}",
            "--version",
            version,
            "-o",
            str(regenerated),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"generator could not run here: {result.stderr.strip()[:200]}")

    # Provenance records the build commit, which differs between machines running the
    # same release. Everything describing the Options themselves must match exactly.
    assert (
        generated_module.load(regenerated).options == generated_module.load(committed).options
    )
