"""Snapshots and the Journal: what a write replaced, and which writes were good.

Nothing is mocked -- a `Journal` over a real temp state dir, real files on both sides. The
questions worth asserting are all about *bounds* and *pins*: history that grows forever is a
bug, and history that prunes away the one Snapshot recovery needs is a worse one (ADR-0016:
"pruning must never drop the newest confirmed Snapshot of a Module").
"""

from __future__ import annotations

import json
from pathlib import Path

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

    assert journal.last_known_good(GENERAL) == b"-- second\n"


def test_an_unconfirmed_write_never_becomes_last_known_good(tmp_path: Path) -> None:
    """`ok` is not `confirmed` (ADR-0010): a transaction that could not read its keys back
    has verified nothing, and promoting its bytes would make "good" a state nobody checked."""
    journal, paths = journal_for(tmp_path)

    transaction(journal, paths, GENERAL, "-- good\n", confirmed=True)
    transaction(journal, paths, GENERAL, "-- unverified\n", confirmed=False)

    assert journal.last_known_good(GENERAL) == b"-- good\n"


def test_last_known_good_is_per_module(tmp_path: Path) -> None:
    journal, paths = journal_for(tmp_path)

    transaction(journal, paths, GENERAL, "-- general\n", confirmed=True)
    transaction(journal, paths, DECORATION, "-- decoration\n", confirmed=False)

    assert journal.last_known_good(GENERAL) == b"-- general\n"
    assert journal.last_known_good(DECORATION) is None


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

    assert journal.last_known_good(DECORATION) == b"-- the good one\n"
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
    assert journal.last_known_good(GENERAL) == b"-- good\n"


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
