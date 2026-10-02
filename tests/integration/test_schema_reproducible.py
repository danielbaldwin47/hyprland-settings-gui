"""Integration tier: the generator actually reproduces the committed schema.

ADR-0011 tier 3 -- needs a real Hyprland, marked `-m hyprland`, auto-skipped without one,
and deliberately outside `testpaths` so a per-commit run never reaches for the network.

The unit tier proves the committed file is what `dumps` produces. Only this proves it is
what the *generator* produces, because only here are the three real inputs available: a
running compositor of the matching version, its installed Lua stub, and the Hyprland
source at the release tag. That is the machine a release check runs on
(`docs/agents/hyprland-release-check.md` step 1), which is exactly when it matters.

The compositor is a nested one from `guarded_hyprland` (#201), and both `hyprctl` and the
generator run with its guarded environment. Inheriting the shell's environment would read
the owner's desktop session, since the generator runs `hyprctl` itself.

Run it explicitly::

    HARNESS_DRM_CARD=/dev/dri/card0 pytest tests/integration/test_schema_reproducible.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from harness import GuardedInstance

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.schema import generated as generated_module  # noqa: E402

SCHEMA_DIR = ROOT / "data" / "schema"

pytestmark = pytest.mark.hyprland


def running_hyprland_version(hyprland: GuardedInstance) -> str | None:
    result = subprocess.run(
        ["hyprctl", "version"], capture_output=True, text=True, env=hyprland.env
    )
    if result.returncode != 0:
        return None
    match = re.search(r"Hyprland (\d+(?:\.\d+)*)", result.stdout)
    return match.group(1) if match else None


def regenerate(
    hyprland: GuardedInstance, version: str, out: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    """The generator against the nested compositor, as a release check runs it."""
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "gen_schema.py"),
            "--source-ref",
            f"v{version}",
            "--version",
            version,
            *extra,
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        env=hyprland.env,
    )
    if result.returncode != 0:
        pytest.skip(f"generator could not run here: {result.stderr.strip()[:200]}")
    return result


def committed_schema(hyprland: GuardedInstance) -> tuple[str, Path]:
    version = running_hyprland_version(hyprland)
    assert version is not None, "the nested Hyprland did not answer `hyprctl version`"

    committed = SCHEMA_DIR / f"hyprland-{version}.json"
    if not committed.is_file():
        pytest.skip(f"no committed schema for the running Hyprland {version}")
    return version, committed


def test_the_generator_reproduces_the_committed_schema(
    tmp_path: Path, guarded_hyprland: GuardedInstance
) -> None:
    version, committed = committed_schema(guarded_hyprland)

    # `added_in` is relative to a predecessor, so the file says which one it was stamped
    # against (provenance, #123) and the regeneration is handed that same file.
    predecessor_flags: list[str] = []
    predecessor = generated_module.load(committed).provenance.get("predecessor")
    if predecessor is not None:
        predecessor_file = SCHEMA_DIR / f"hyprland-{predecessor}.json"
        if not predecessor_file.is_file():
            pytest.skip(
                f"{committed.name} was stamped against {predecessor}, which is not shipped"
            )
        predecessor_flags = ["--predecessor", str(predecessor_file)]

    regenerated = tmp_path / f"hyprland-{version}.json"
    regenerate(guarded_hyprland, version, regenerated, *predecessor_flags)

    # Provenance records the build commit, which differs between machines running the
    # same release. Everything describing the Options themselves must match exactly.
    assert (
        generated_module.load(regenerated).options == generated_module.load(committed).options
    )


def test_the_generator_stamps_exactly_the_options_its_predecessor_lacks(
    tmp_path: Path, guarded_hyprland: GuardedInstance
) -> None:
    version, committed = committed_schema(guarded_hyprland)

    # A synthetic older release: the committed schema minus three Options, so those three
    # are "added" by the real generator run and nothing else is.
    current = generated_module.load(committed)
    dropped = {option.name for option in current.options[-3:]}
    older = replace(
        current,
        hyprland_version="0.0.1",
        options=tuple(option for option in current.options if option.name not in dropped),
    )
    predecessor = tmp_path / "hyprland-0.0.1.json"
    predecessor.write_text(generated_module.dumps(older), encoding="utf-8")

    regenerated = tmp_path / f"hyprland-{version}.json"
    regenerate(guarded_hyprland, version, regenerated, "--predecessor", str(predecessor))

    result = generated_module.load(regenerated)
    assert {o.name for o in result.options if o.added_in == version} == dropped
    assert all(o.added_in is None for o in result.options if o.name not in dropped)
    assert result.provenance["predecessor"] == "0.0.1"


def test_a_missing_predecessor_means_no_stamps_and_no_crash(
    tmp_path: Path, guarded_hyprland: GuardedInstance
) -> None:
    version, _ = committed_schema(guarded_hyprland)

    regenerated = tmp_path / f"hyprland-{version}.json"
    result = regenerate(
        guarded_hyprland, version, regenerated, "--predecessor", str(tmp_path / "absent.json")
    )

    loaded = generated_module.load(regenerated)
    assert all(option.added_in is None for option in loaded.options)
    assert "predecessor" not in loaded.provenance
    assert "no `added_in` stamps" in result.stdout
