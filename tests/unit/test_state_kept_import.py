"""The kept-import record and its seeding into the Journal (#259, review m1 F10).

`seed` turns the record into one boundary entry; it withdraws Confirmed whenever which bytes
were imported is unknown, and records a Module the import left absent as deleted.
"""

from __future__ import annotations

from pathlib import Path

from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.state import Journal, Manifest, ModuleChange, kept_import
from hyprtweaker.engine.state.manifest import ModuleRecord

GENERAL = "options/general.lua"
DECORATION = "options/decoration.lua"
IMPORTED = "return { general = { border_size = 4 } }\n"


def put(paths: ConfigPaths, module: str, text: str) -> None:
    path = paths.file_for(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def imported(paths: ConfigPaths) -> Manifest:
    """What the wizard left: `general.lua` written and recorded with its Option."""
    put(paths, GENERAL, IMPORTED)
    record = ModuleRecord.of(IMPORTED, ("general:border_size",))
    return Manifest(app_version="x", schema_version="y", modules={GENERAL: record})


def seeded(paths: ConfigPaths, manifest: Manifest, *, confirmed: bool = True):
    journal = Journal(paths)
    record = kept_import.read(paths)
    assert record is not None
    entry = kept_import.seed(
        journal, paths, record, manifest, confirmed=confirmed, outcome="ok"
    )
    assert entry is not None
    return journal, entry


def test_an_intact_import_read_back_clean_is_a_confirmed_boundary(tmp_path: Path) -> None:
    paths = ConfigPaths.rooted_at(tmp_path)
    manifest = imported(paths)
    kept_import.write(paths, manifest)

    _journal, entry = seeded(paths, manifest)

    assert entry.confirmed
    assert entry.boundary
    assert entry.modules == (GENERAL,)
    assert kept_import.read(paths) is None


def test_an_unparseable_record_is_unconfirmed_and_takes_the_manifest_s_modules(
    tmp_path: Path,
) -> None:
    paths = ConfigPaths.rooted_at(tmp_path)
    manifest = imported(paths)
    paths.kept_import.parent.mkdir(parents=True, exist_ok=True)
    paths.kept_import.write_text("{ not json", encoding="utf-8")

    _journal, entry = seeded(paths, manifest)

    assert not entry.confirmed
    assert entry.boundary
    assert entry.modules == (GENERAL,)
    assert entry.keys == ("general:border_size",)


def test_a_module_edited_since_keep_makes_the_import_unconfirmed(tmp_path: Path) -> None:
    paths = ConfigPaths.rooted_at(tmp_path)
    manifest = imported(paths)
    kept_import.write(paths, manifest)
    put(paths, GENERAL, "-- edited before the first read-back\n")

    _journal, entry = seeded(paths, manifest)

    assert not entry.confirmed
    assert entry.modules == (GENERAL,)


def test_a_module_the_import_left_absent_is_recorded_as_deleted(tmp_path: Path) -> None:
    paths = ConfigPaths.rooted_at(tmp_path)
    journal = Journal(paths)
    put(paths, DECORATION, "return { decoration = { rounding = 8 } }\n")
    draft = journal.begin([DECORATION])
    draft.commit(
        keys=("decoration:rounding",), outcome="ok", confirmed=True, changed=[DECORATION]
    )
    paths.file_for(DECORATION).unlink()
    manifest = imported(paths)
    kept_import.write(paths, manifest)

    record = kept_import.read(paths)
    assert record is not None
    entry = kept_import.seed(journal, paths, record, manifest, confirmed=True, outcome="ok")

    assert entry is not None
    change = entry.change(DECORATION)
    assert isinstance(change, ModuleChange)
    assert change.after is None
    assert journal.last_known_good(DECORATION) is None
