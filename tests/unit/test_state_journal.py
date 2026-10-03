"""Snapshots and the Journal: what a write replaced, and which writes were good.

Nothing is mocked -- a `Journal` over a real temp state dir, real files on both sides. The
questions worth asserting are all about *bounds* and *pins*: history that grows forever is a
bug, and history that prunes away the one Snapshot recovery needs is a worse one (ADR-0016:
"pruning must never drop the newest confirmed Snapshot of a Module").
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hyprtweaker.engine.paths import ENTRYPOINT_NAME, ConfigPaths
from hyprtweaker.engine.state import Journal, JournalEntry, ModuleChange, content_hash

GENERAL = "options/general.lua"
DECORATION = "options/decoration.lua"


def journal_for(tmp_path: Path, **kwargs: int) -> tuple[Journal, ConfigPaths]:
    paths = ConfigPaths.rooted_at(tmp_path)
    return Journal(paths, **kwargs), paths


def put(paths: ConfigPaths, module: str, text: str) -> None:
    path = paths.app_dir / module
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def transaction(
    journal: Journal,
    paths: ConfigPaths,
    module: str,
    text: str | None,
    *,
    confirmed: bool = True,
    keys: tuple[str, ...] = ("general:gaps_in",),
) -> JournalEntry | None:
    """One write of `module`, snapshotted and journalled the way a transaction does it."""
    draft = journal.begin([module])
    if text is None:
        (paths.app_dir / module).unlink(missing_ok=True)
    else:
        put(paths, module, text)
    return draft.commit(
        keys=keys,
        outcome="ok" if confirmed else "read-back-mismatch",
        confirmed=confirmed,
        changed=[module],
    )


# --- one transaction ------------------------------------------------------------------------


def test_a_write_records_the_bytes_it_replaced_and_the_bytes_it_left(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)
    put(paths, GENERAL, "-- old\n")

    entry = transaction(journal, paths, GENERAL, "-- new\n")

    assert entry is not None
    change = entry.change(GENERAL)
    assert change is not None
    assert journal.snapshot(change.before) == b"-- old\n"
    assert journal.snapshot(change.after) == b"-- new\n"


def test_a_module_that_did_not_exist_before_the_write_snapshots_as_absent(
    tmp_path: Path,
) -> None:
    """`None` is a real prior state, not a missing one.

    A Module is created when its Section gains its first set Option, and an undo of that
    gesture has to delete the file again -- which it cannot know to do if "was not there"
    and "was not recorded" are the same answer.
    """
    journal, paths = journal_for(tmp_path)

    entry = transaction(journal, paths, GENERAL, "-- new\n")

    assert entry is not None
    change = entry.change(GENERAL)
    assert change is not None and change.before is None
    assert journal.snapshot(change.after) == b"-- new\n"


def test_a_deleted_module_records_its_bytes_and_an_absent_after(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)
    put(paths, GENERAL, "-- old\n")

    entry = transaction(journal, paths, GENERAL, None)

    assert entry is not None
    change = entry.change(GENERAL)
    assert change is not None and change.after is None
    assert journal.snapshot(change.before) == b"-- old\n"


def test_a_transaction_that_changed_nothing_writes_no_entry_and_no_snapshot(
    tmp_path: Path,
) -> None:
    """`NOTHING_TO_DO` has nothing to say that the Manifest does not already record."""
    journal, paths = journal_for(tmp_path)
    put(paths, GENERAL, "-- same\n")

    draft = journal.begin([GENERAL])
    assert draft.commit(keys=(), outcome="nothing-to-do", confirmed=False, changed=()) is None

    assert journal.entries() == ()
    assert not paths.journal.exists()
    assert not paths.snapshots_dir.exists()


def test_discarding_a_draft_leaves_no_trace(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)
    put(paths, GENERAL, "-- old\n")

    draft = journal.begin([GENERAL])
    draft.discard()

    assert draft.before_bytes(GENERAL) is None
    assert journal.entries() == ()


# --- a crash between the write and the commit -----------------------------------------------


def crashed_write(paths: ConfigPaths, module: str, text: str | None) -> None:
    """One write that got as far as replacing `module`, and then the process died.

    The Draft is preserved before the replace, the way the Writer does it, and then simply
    abandoned: never committed, never discarded. The next process opens a fresh Journal.
    """
    draft = Journal(paths).begin([module])
    draft.preserve(paths.file_for(module))
    if text is not None:
        put(paths, module, text)


def test_a_crash_after_the_write_recovers_the_pre_write_bytes_from_the_journal(
    tmp_path: Path,
) -> None:
    """ADR-0010: the snapshot is taken *before* each write, so it outlives the process.

    Held in memory until the commit, a hand edit the zero-binds restore overwrote would be
    gone for good if the app died before the reload answered -- and ADR-0016 spends that
    edit only because it promises it back.
    """
    paths = ConfigPaths.rooted_at(tmp_path)
    put(paths, GENERAL, "-- hand edit\n")
    crashed_write(paths, GENERAL, "-- restored\n")

    journal = Journal(paths)
    # The next process's next write: its own Snapshot GC runs here, and must spare it.
    transaction(journal, paths, DECORATION, "-- unrelated\n")

    recovered = [entry for entry in journal.entries() if GENERAL in entry.modules]
    assert len(recovered) == 1
    entry = recovered[0]
    assert entry.outcome == "interrupted"
    assert not entry.confirmed, "nothing confirmed bytes the app never saw reload"
    change = entry.change(GENERAL)
    assert change is not None
    assert journal.snapshot(change.before) == b"-- hand edit\n"
    assert journal.snapshot(change.after) == b"-- restored\n"


def test_a_crash_before_the_replace_landed_records_nothing(tmp_path: Path) -> None:
    """The file still holds its old bytes, so there is no history to tell -- and no blob."""
    paths = ConfigPaths.rooted_at(tmp_path)
    put(paths, GENERAL, "-- untouched\n")
    crashed_write(paths, GENERAL, None)

    journal = Journal(paths)
    transaction(journal, paths, DECORATION, "-- unrelated\n")

    assert [entry.modules for entry in journal.entries()] == [(DECORATION,)]
    assert sorted(p.name for p in paths.snapshots_dir.iterdir()) == [
        content_hash(b"-- unrelated\n")
    ]


def test_a_committed_write_is_not_recovered_a_second_time(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)
    put(paths, GENERAL, "-- old\n")
    draft = journal.begin([GENERAL])
    draft.preserve(paths.file_for(GENERAL))
    put(paths, GENERAL, "-- new\n")
    draft.commit(keys=(), outcome="ok", confirmed=True, changed=[GENERAL])

    fresh = Journal(paths)
    transaction(fresh, paths, DECORATION, "-- unrelated\n")

    assert [entry.outcome for entry in fresh.entries()] == ["ok", "ok"]


def test_a_crash_between_the_append_and_the_release_is_not_journalled_twice(
    tmp_path: Path,
) -> None:
    """The commit appends before it releases, so a crash in between leaves both the entry
    and the pending record. Recovering that is a no-op, not a second, `interrupted`, copy."""
    journal, paths = journal_for(tmp_path)
    put(paths, GENERAL, "-- old\n")
    draft = journal.begin([GENERAL])
    draft.preserve(paths.file_for(GENERAL))
    put(paths, GENERAL, "-- new\n")
    draft.commit(keys=(), outcome="ok", confirmed=True, changed=[GENERAL])
    journal.hold({GENERAL: content_hash(b"-- old\n")})  # the release that never happened

    assert Journal(paths).recover() is None
    assert [entry.outcome for entry in journal.entries()] == ["ok"]
    assert not paths.journal_pending.exists()


def test_an_unreadable_pending_record_is_dropped_by_the_next_begin(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)
    paths.journal_pending.parent.mkdir(parents=True, exist_ok=True)
    paths.journal_pending.write_text("{not json", encoding="utf-8")

    journal.begin([GENERAL])

    assert not paths.journal_pending.exists()
    assert journal.entries() == ()


def test_pruning_reads_a_pending_record_without_deleting_it(tmp_path: Path) -> None:
    """Collection only asks which Snapshots are spoken for; dropping a bad record is
    `recover`'s call, made where the reader expects a write."""
    journal, paths = journal_for(tmp_path)
    paths.journal_pending.parent.mkdir(parents=True, exist_ok=True)
    paths.journal_pending.write_text("{not json", encoding="utf-8")
    journal.store(b"-- a blob, so collection has a store to walk\n")

    journal.prune()

    assert paths.journal_pending.read_text(encoding="utf-8") == "{not json"


def test_preserving_syncs_the_snapshot_and_the_pending_record_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`preserve` runs before the Writer's rename, and the rename may survive a power loss.
    The Snapshot it names, and the record naming it, must survive it too."""
    synced: set[Path] = set()
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if path.name.startswith(".") and path.name.endswith(".tmp"):
            path = path.with_name(path.name[1 : -len(".tmp")])  # synced, then renamed
        synced.add(path)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    journal, paths = journal_for(tmp_path)
    put(paths, GENERAL, "-- hand edit\n")

    draft = journal.begin([GENERAL])
    draft.preserve(paths.file_for(GENERAL))

    snapshot = paths.snapshots_dir / content_hash(b"-- hand edit\n")
    assert {
        snapshot,
        paths.snapshots_dir,
        paths.journal_pending,
        paths.journal_pending.parent,
    } <= (synced)


def test_a_half_written_app_dir_is_journalled_from_what_the_disk_says(tmp_path: Path) -> None:
    """The `WRITE_FAILED` path has no `WriteResult` to read, so `dirty()` asks the files."""
    journal, paths = journal_for(tmp_path)
    put(paths, GENERAL, "-- old\n")
    put(paths, DECORATION, "-- untouched\n")

    draft = journal.begin([GENERAL, DECORATION])
    put(paths, GENERAL, "-- half a write\n")

    assert draft.dirty() == (GENERAL,)


def test_identical_bytes_are_stored_once(tmp_path: Path) -> None:
    """Content addressing is what makes snapshotting *every* write affordable."""
    journal, paths = journal_for(tmp_path)

    for text in ("-- a\n", "-- b\n", "-- a\n"):
        transaction(journal, paths, GENERAL, text)

    assert sorted(path.name for path in paths.snapshots_dir.iterdir()) == sorted(
        {content_hash("-- a\n"), content_hash("-- b\n")}
    )


# --- last known good ------------------------------------------------------------------------


def test_last_known_good_is_the_newest_confirmed_write(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)

    transaction(journal, paths, GENERAL, "-- first\n", confirmed=True)
    transaction(journal, paths, GENERAL, "-- second\n", confirmed=True)

    assert journal.last_known_good(GENERAL).data == b"-- second\n"


def test_an_unconfirmed_write_never_becomes_last_known_good(tmp_path: Path) -> None:
    """`ok` is not `confirmed` (ADR-0010): a transaction that could not read its keys back
    has verified nothing, and promoting its bytes would make "good" a state nobody checked."""
    journal, paths = journal_for(tmp_path)

    transaction(journal, paths, GENERAL, "-- good\n", confirmed=True)
    transaction(journal, paths, GENERAL, "-- unverified\n", confirmed=False)

    assert journal.last_known_good(GENERAL).data == b"-- good\n"


def test_last_known_good_is_per_module(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)

    transaction(journal, paths, GENERAL, "-- general\n", confirmed=True)
    transaction(journal, paths, DECORATION, "-- decoration\n", confirmed=False)

    assert journal.last_known_good(GENERAL).data == b"-- general\n"
    assert journal.last_known_good(DECORATION) is None


def test_the_previous_digest_is_the_newest_write_confirmed_or_not(tmp_path: Path) -> None:
    """Auto-revert's question, and deliberately the looser one: the state a rejected write
    replaced was live a moment earlier whether or not the app got to verify it."""
    journal, paths = journal_for(tmp_path)

    transaction(journal, paths, GENERAL, "-- confirmed\n", confirmed=True)
    transaction(journal, paths, GENERAL, "-- never verified\n", confirmed=False)

    assert journal.snapshot(journal.previous_digest(GENERAL)) == b"-- confirmed\n"
    assert journal.last_known_good(GENERAL).data == b"-- confirmed\n"

    transaction(journal, paths, GENERAL, "-- rejected\n", confirmed=False)

    # The revert target moves with every write; Last known good does not.
    assert journal.snapshot(journal.previous_digest(GENERAL)) == b"-- never verified\n"
    assert journal.last_known_good(GENERAL).data == b"-- confirmed\n"


def test_a_module_with_no_confirmed_write_has_no_last_known_good(tmp_path: Path) -> None:
    """`None` means "there is nothing to restore to", never "restore whatever is newest" --
    the newest may be exactly what broke."""
    journal, paths = journal_for(tmp_path)

    transaction(journal, paths, GENERAL, "-- never confirmed\n", confirmed=False)

    assert journal.last_known_good(GENERAL) is None


# --- bounds and pins ------------------------------------------------------------------------


def test_the_journal_keeps_only_its_newest_entries(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path, max_entries=3)

    for index in range(6):
        transaction(journal, paths, GENERAL, f"-- {index}\n", confirmed=False)

    entries = journal.entries()
    assert len(entries) == 3
    assert [journal.snapshot(entry.change(GENERAL).after) for entry in entries] == [  # type: ignore[union-attr]
        b"-- 3\n",
        b"-- 4\n",
        b"-- 5\n",
    ]


def test_pruning_pins_each_modules_newest_confirmed_entry(tmp_path: Path) -> None:
    """ADR-0016's consequence, and the reason the bound alone is not enough.

    A Module nobody has touched in months would otherwise roll out of the window and lose
    its Last known good, leaving Restore-last-good with nothing to restore -- which is the
    one thing pruning is forbidden to do.
    """
    journal, paths = journal_for(tmp_path, max_entries=2)

    transaction(journal, paths, DECORATION, "-- the good one\n", confirmed=True)
    for index in range(5):
        transaction(journal, paths, GENERAL, f"-- {index}\n", confirmed=True)

    assert journal.last_known_good(DECORATION).data == b"-- the good one\n"
    # The pin is *in addition to* the window, not instead of it.
    assert len(journal.entries()) == 3


def test_pruning_collects_the_snapshots_nothing_refers_to(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path, max_entries=2)

    for index in range(6):
        transaction(journal, paths, GENERAL, f"-- {index}\n", confirmed=False)

    referenced = {
        digest
        for entry in journal.entries()
        for change in entry.changes
        for digest in (change.before, change.after)
        if digest is not None
    }
    assert {path.name for path in paths.snapshots_dir.iterdir()} == referenced


def test_a_pinned_snapshot_survives_collection(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path, max_entries=1)

    transaction(journal, paths, DECORATION, "-- pinned\n", confirmed=True)
    for index in range(4):
        transaction(journal, paths, GENERAL, f"-- {index}\n", confirmed=False)

    assert journal.snapshot(journal.last_known_good_digest(DECORATION)) == b"-- pinned\n"


# --- damage ---------------------------------------------------------------------------------


def test_a_truncated_last_line_costs_that_entry_and_nothing_else(tmp_path: Path) -> None:
    """The expected damage: the app was killed mid-append. Everything before it is history."""
    journal, paths = journal_for(tmp_path)
    transaction(journal, paths, GENERAL, "-- good\n", confirmed=True)

    with paths.journal.open("a", encoding="utf-8") as handle:
        handle.write('{"format_version": 1, "at": "2026-')

    assert len(journal.entries()) == 1
    assert journal.last_known_good(GENERAL).data == b"-- good\n"


def test_an_entry_from_an_unknown_format_is_skipped(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)
    paths.journal.parent.mkdir(parents=True, exist_ok=True)
    paths.journal.write_text(json.dumps({"format_version": 99}) + "\n", encoding="utf-8")

    assert journal.entries() == ()


def test_an_unreadable_state_dir_costs_history_not_the_edit(tmp_path: Path) -> None:
    """History is not config: a state dir that cannot be written must not raise into an
    apply. The Snapshot is simply not stored, and `store` says so by answering `None`."""
    journal, paths = journal_for(tmp_path)
    paths.state_dir.parent.mkdir(parents=True, exist_ok=True)
    # A *file* where the state dir should be: every mkdir under it now fails.
    paths.state_dir.write_text("not a directory", encoding="utf-8")

    assert journal.store(b"-- something\n") is None
    journal.append(
        JournalEntry(
            at="2026-08-23T00:00:00+00:00",
            keys=(),
            outcome="ok",
            confirmed=True,
            changes=(ModuleChange(GENERAL, None, None),),
        )
    )
    assert journal.entries() == ()


def test_the_entrypoint_is_snapshotted_from_beside_the_app_dir(tmp_path: Path) -> None:
    """It is app-owned and outside the App dir, so the one path lookup that is not a join."""
    journal, paths = journal_for(tmp_path)
    paths.entrypoint.parent.mkdir(parents=True, exist_ok=True)
    paths.entrypoint.write_text("-- entrypoint\n", encoding="utf-8")

    assert journal.read_module(ENTRYPOINT_NAME) == b"-- entrypoint\n"


# --- a kept import is a boundary (#259) -----------------------------------------------------


def kept_import(
    journal: Journal,
    paths: ConfigPaths,
    modules: dict[str, str | None],
    *,
    confirmed: bool,
) -> JournalEntry | None:
    """An import the user kept: the wizard laid the bytes down, the Session records them."""
    for module, text in modules.items():
        if text is None:
            (paths.app_dir / module).unlink(missing_ok=True)
        else:
            put(paths, module, text)
    draft = journal.begin(modules)
    return draft.commit(
        keys=("general:gaps_in",),
        outcome="ok",
        confirmed=confirmed,
        changed=modules,
        options={module: ("general:gaps_in",) for module in modules},
        boundary=True,
    )


def test_a_confirmed_import_is_the_restore_point_not_the_bytes_before_it(
    tmp_path: Path,
) -> None:
    journal, paths = journal_for(tmp_path)
    transaction(journal, paths, GENERAL, "-- before the import\n")

    kept_import(journal, paths, {GENERAL: "-- imported\n"}, confirmed=True)

    good = journal.last_known_good(GENERAL)
    assert good is not None
    assert (good.data, good.options) == (b"-- imported\n", ("general:gaps_in",))
    assert not journal.unverified_since_import(GENERAL)


def test_an_unconfirmed_import_offers_nothing_from_before_it(tmp_path: Path) -> None:
    """AC4: the pre-import bytes are a config the user replaced, not a restore point."""
    journal, paths = journal_for(tmp_path)
    transaction(journal, paths, GENERAL, "-- before the import\n")

    kept_import(journal, paths, {GENERAL: "-- imported\n"}, confirmed=False)

    assert journal.last_known_good(GENERAL) is None
    assert journal.last_known_good_digest(GENERAL) is None
    assert journal.unverified_since_import(GENERAL)
    assert len(journal.entries()) == 2, "the history before the import is kept"


def test_a_module_the_import_deleted_offers_nothing_from_before_it(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)
    transaction(journal, paths, DECORATION, "-- before the import\n")

    kept_import(journal, paths, {DECORATION: None, GENERAL: "-- imported\n"}, confirmed=True)

    assert journal.last_known_good(DECORATION) is None
    assert not journal.unverified_since_import(DECORATION)


def test_a_confirmed_write_after_an_unconfirmed_import_is_the_restore_point(
    tmp_path: Path,
) -> None:
    journal, paths = journal_for(tmp_path)
    kept_import(journal, paths, {GENERAL: "-- imported\n"}, confirmed=False)

    transaction(journal, paths, GENERAL, "-- edited since\n")

    good = journal.last_known_good(GENERAL)
    assert good is not None and good.data == b"-- edited since\n"
    assert not journal.unverified_since_import(GENERAL)


def test_a_module_the_import_left_alone_keeps_its_restore_point(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)
    transaction(journal, paths, DECORATION, "-- untouched\n")

    kept_import(journal, paths, {GENERAL: "-- imported\n"}, confirmed=False)

    good = journal.last_known_good(DECORATION)
    assert good is not None and good.data == b"-- untouched\n"


def test_the_boundary_survives_a_reopened_journal_and_pruning(tmp_path: Path) -> None:
    """Pruned out of the window, an unconfirmed boundary would let the pinned pre-import
    entry be offered again."""
    journal, paths = journal_for(tmp_path, max_entries=2)
    transaction(journal, paths, GENERAL, "-- before the import\n")
    kept_import(journal, paths, {GENERAL: "-- imported\n"}, confirmed=False)
    for index in range(4):
        transaction(journal, paths, DECORATION, f"-- {index}\n", confirmed=False)

    reopened = Journal(paths, max_entries=2)
    assert reopened.last_known_good(GENERAL) is None
    assert reopened.unverified_since_import(GENERAL)
