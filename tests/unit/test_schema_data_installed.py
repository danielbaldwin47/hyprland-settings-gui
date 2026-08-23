"""The shipped schema files: installed, self-consistent, and byte-stable.

Two failure modes, both invisible from a checkout:

- A schema file missing from `data/meson.build` still works locally (the checkout path in
  `schema_dir()` finds it) and produces an installed app with no options at all.
- A hand-edited `hyprland-<ver>.json` still loads, but the next release check regenerates
  it and silently reverts whatever was edited in. The Generated schema is machine output;
  corrections belong in the Overlay.

The end-to-end reproducibility check -- rerunning the generator against a live compositor
-- is integration tier and lives in `tests/integration/` (ADR-0011).
"""

from __future__ import annotations

import pytest
from _support import ROOT, SCHEMA_DIR, assert_lists_match, meson_quoted_names

from hyprtweaker.engine.schema import generated as generated_module
from hyprtweaker.engine.schema.resolve import available_versions

DATA_MESON = ROOT / "data" / "meson.build"
_SCHEMA_FILES_BLOCK = r"schema_files\s*=\s*files\((.*?)\)"


def test_every_schema_file_is_installed_and_no_more() -> None:
    declared = meson_quoted_names(DATA_MESON.read_text(), _SCHEMA_FILES_BLOCK)
    actual = {
        str(path.relative_to(ROOT / "data"))
        for path in SCHEMA_DIR.iterdir()
        if path.suffix == ".json"
    }
    assert_lists_match(declared, actual, DATA_MESON)


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_the_committed_schema_is_exactly_what_the_serialiser_produces(version: str) -> None:
    """Re-serialising the committed file must reproduce it byte for byte.

    The generator's only output path is `dumps`, so a file that survives a load/dump round
    trip unchanged is a file the generator would write. A hand edit, a reordered key, or a
    formatting drift all fail here -- without needing a compositor.
    """
    path = SCHEMA_DIR / f"hyprland-{version}.json"
    committed = path.read_text(encoding="utf-8")
    assert generated_module.dumps(generated_module.loads(committed)) == committed


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_schema_records_agree_with_their_own_filename(version: str) -> None:
    schema = generated_module.load(SCHEMA_DIR / f"hyprland-{version}.json")
    assert schema.hyprland_version == version
    assert schema.provenance.get("hyprland_version") == version


@pytest.mark.parametrize("version", available_versions(SCHEMA_DIR))
def test_provenance_names_the_tree_the_schema_came_from(version: str) -> None:
    """Provenance exists so a release check can regenerate the file and diff it.

    A local input path names one machine's scratch directory and reproduces nothing; the
    Hyprland version, its build commit, and the source ref are what actually identify the
    inputs.
    """
    schema = generated_module.load(SCHEMA_DIR / f"hyprland-{version}.json")
    provenance = schema.provenance

    assert provenance.get("hyprland_commit"), "no Hyprland build commit recorded"
    assert provenance.get("source_ref"), "no Hyprland source ref recorded"
    assert provenance.get("option_count") == len(schema.options)

    for key, value in provenance.items():
        assert not str(value).startswith("/tmp"), (
            f"provenance {key!r} points at a scratch path ({value!r}) -- "
            "regenerate with --source-ref so the record identifies the inputs"
        )


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


def test_support_window_is_latest_plus_previous() -> None:
    """`data/schema/` carries at most two schemas (ADR-0012 support window)."""
    versions = available_versions(SCHEMA_DIR)
    assert versions, f"no hyprland-<ver>.json files in {SCHEMA_DIR}"
    assert len(versions) <= 2, (
        f"shipping {len(versions)} schemas {versions}, but the support window is "
        "latest + previous -- delete the older files, git history keeps them"
    )


def test_the_python_sources_and_the_schema_data_use_the_same_check() -> None:
    """Guards the shared helper: a parser that finds nothing must not read as agreement."""
    declared = meson_quoted_names(
        (ROOT / "src" / "meson.build").read_text(),
        r"package_sources\s*=\s*\{(.*?)^\}",
    )
    assert any(name.endswith("resolve.py") for name in declared)


def test_the_schema_directory_is_not_a_python_package() -> None:
    """The Schema is data on the install prefix, not importable module content."""
    assert not list(SCHEMA_DIR.glob("*.py"))
