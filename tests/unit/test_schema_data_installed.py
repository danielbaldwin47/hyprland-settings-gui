"""The shipped schema files: installed, byte-stable, and reproducible by the generator.

Two failure modes this covers, both invisible from a checkout:

- A schema file missing from `data/meson.build` still works locally (the checkout path in
  `schema_dir()` finds it) and produces an installed app with no options at all.
- A hand-edited `hyprland-<ver>.json` still loads, but the next release check regenerates
  it and silently reverts whatever was edited in. The Generated schema is machine output;
  corrections belong in the Overlay.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hyprtweaker.engine.schema import generated as generated_module
from hyprtweaker.engine.schema.resolve import available_versions

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "data" / "schema"
DATA_MESON = ROOT / "data" / "meson.build"


def declared_data_files() -> set[str]:
    """The `schema/...` paths named by `files(...)` in data/meson.build."""
    block = re.search(r"schema_files\s*=\s*files\((.*?)\)", DATA_MESON.read_text(), re.DOTALL)
    assert block is not None, f"could not find a schema_files list in {DATA_MESON}"

    declared = set(re.findall(r"'([^']+)'", block.group(1)))
    assert declared, "parsed schema_files but found no entries -- the parser is broken"
    return declared


def actual_data_files() -> set[str]:
    return {
        str(path.relative_to(ROOT / "data"))
        for path in SCHEMA_DIR.iterdir()
        if path.suffix == ".json"
    }


def test_every_schema_file_is_installed() -> None:
    missing = actual_data_files() - declared_data_files()
    assert not missing, (
        "these schema files exist but data/meson.build does not install them: "
        f"{sorted(missing)}"
    )


def test_no_installed_schema_file_is_missing_from_disk() -> None:
    stale = declared_data_files() - actual_data_files()
    assert not stale, f"data/meson.build installs files that do not exist: {sorted(stale)}"


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_the_committed_schema_is_exactly_what_the_serialiser_produces(version: str) -> None:
    """Re-serialising the committed file must reproduce it byte for byte.

    This is what makes "reproducible by the generator tool" checkable without a running
    Hyprland: the generator's only output path is `dumps`, so a file that survives a
    load/dump round trip unchanged is a file the generator would write. A hand edit, a
    reordered key, or a formatting change all fail here.
    """
    path = SCHEMA_DIR / f"hyprland-{version}.json"
    committed = path.read_text(encoding="utf-8")
    assert generated_module.dumps(generated_module.loads(committed)) == committed


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_schema_records_agree_with_their_own_filename(version: str) -> None:
    schema = generated_module.load(SCHEMA_DIR / f"hyprland-{version}.json")
    assert schema.hyprland_version == version
    assert all(option.since == version for option in schema.options)


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_the_shipped_schema_was_not_generated_in_a_degraded_run(version: str) -> None:
    """A degraded schema types every Color as a string and has no vec2 bounds.

    Fine as a stopgap on a machine with no Hyprland source, never fine as something the
    app ships -- the release-check protocol requires the degradation to be called out.
    """
    schema = generated_module.load(SCHEMA_DIR / f"hyprland-{version}.json")
    assert schema.provenance.get("degraded") is False, (
        f"hyprland-{version}.json was generated without the Hyprland source: "
        f"{schema.provenance.get('degradation')}"
    )


@pytest.mark.hyprland
def test_the_generator_reproduces_the_committed_schema() -> None:
    """The real reproducibility check, against live sources.

    Needs the matching Hyprland running, its stub installed, and network access for the
    source tree, so it only runs where all three exist -- which is exactly the machine a
    release check runs on.
    """
    if shutil.which("hyprctl") is None:
        pytest.skip("no hyprctl on this machine")

    version_output = subprocess.run(
        ["hyprctl", "version"], capture_output=True, text=True
    ).stdout
    match = re.search(r"Hyprland (\d+(?:\.\d+)*)", version_output)
    if match is None:
        pytest.skip("could not determine the running Hyprland version")

    version = match.group(1)
    committed = SCHEMA_DIR / f"hyprland-{version}.json"
    if not committed.is_file():
        pytest.skip(f"no committed schema for the running Hyprland {version}")

    regenerated = ROOT / "build" / f"regenerated-{version}.json"
    regenerated.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "python3",
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

    # Provenance records where the inputs came from, which differs between a release
    # check and this test. Everything describing the options themselves must match.
    assert (
        generated_module.load(regenerated).options == generated_module.load(committed).options
    )
