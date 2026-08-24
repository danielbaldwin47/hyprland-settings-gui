"""The Prefs file: remembered app preferences, and every way reading one can go wrong.

Most of this file is failure modes, and that is the point. A preference store is written on
every switch flip and read once at startup, on machines whose `$XDG_STATE_HOME` may be
read-only, full, or holding a file some earlier version wrote. None of that may stop the app
from opening (ADR-0019), so "what happens when the file is nonsense" is the behaviour worth
pinning -- the happy path is one `json.dumps`.
"""

from __future__ import annotations

import json
from pathlib import Path

from hyprtweaker.engine.prefs import FORMAT_VERSION, PREFS_FILENAME, Prefs, PrefsStore


def store(tmp_path: Path) -> PrefsStore:
    return PrefsStore(tmp_path / "state")


# --- round trip -------------------------------------------------------------------------------


def test_a_saved_preference_is_the_one_that_comes_back(tmp_path: Path) -> None:
    """AC 4 of #71, reduced to its one honest question: does the choice survive a restart?"""
    saved = store(tmp_path)
    assert saved.save(Prefs(view="config", show_advanced=True))

    assert store(tmp_path).load() == Prefs(view="config", show_advanced=True)


def test_the_app_opens_in_the_tasks_view_until_told_otherwise(tmp_path: Path) -> None:
    """Tasks is the default (#7): the curated view is the one the audience is here for."""
    assert store(tmp_path).load() == Prefs(view="tasks", show_advanced=False)


def test_saving_creates_the_state_directory(tmp_path: Path) -> None:
    """First run has no state dir at all -- the first preference is what creates it."""
    saved = store(tmp_path)

    assert saved.save(Prefs(view="config"))
    assert saved.path == tmp_path / "state" / PREFS_FILENAME
    assert saved.path.is_file()


def test_the_file_is_plain_readable_json(tmp_path: Path) -> None:
    """Not GSettings, and not an opaque blob: a user can read and delete it (ADR-0019)."""
    saved = store(tmp_path)
    saved.save(Prefs(view="config", show_advanced=True))

    payload = json.loads(saved.path.read_text(encoding="utf-8"))

    assert payload == {
        "format_version": FORMAT_VERSION,
        "view": "config",
        "show_advanced": True,
    }


# --- nothing here may stop the app opening ----------------------------------------------------


def test_an_absent_file_reads_as_the_defaults(tmp_path: Path) -> None:
    assert store(tmp_path).load() == Prefs()


def test_a_corrupt_file_reads_as_the_defaults(tmp_path: Path) -> None:
    """A truncated write from a crash, or a hand-edit gone wrong. Losing a preference is a
    recoverable annoyance; refusing to start over one is not."""
    saved = store(tmp_path)
    saved.path.parent.mkdir(parents=True)
    saved.path.write_text("{not json", encoding="utf-8")

    assert saved.load() == Prefs()


def test_a_file_that_is_not_an_object_reads_as_the_defaults(tmp_path: Path) -> None:
    saved = store(tmp_path)
    saved.path.parent.mkdir(parents=True)
    saved.path.write_text("[1, 2, 3]", encoding="utf-8")

    assert saved.load() == Prefs()


def test_a_newer_format_version_reads_as_the_defaults(tmp_path: Path) -> None:
    """A downgrade must not reinterpret keys whose meaning may have changed."""
    saved = store(tmp_path)
    saved.path.parent.mkdir(parents=True)
    saved.path.write_text(
        json.dumps({"format_version": FORMAT_VERSION + 1, "view": "config"}),
        encoding="utf-8",
    )

    assert saved.load() == Prefs()


def test_one_field_of_the_wrong_type_does_not_reset_the_other(tmp_path: Path) -> None:
    """Partial recovery: the user should not lose preferences they never corrupted."""
    saved = store(tmp_path)
    saved.path.parent.mkdir(parents=True)
    saved.path.write_text(
        json.dumps({"format_version": FORMAT_VERSION, "view": 17, "show_advanced": True}),
        encoding="utf-8",
    )

    assert saved.load() == Prefs(view="tasks", show_advanced=True)


def test_an_unwritable_state_dir_reports_failure_instead_of_raising(
    tmp_path: Path,
) -> None:
    """Clicking a switch on a read-only `$XDG_STATE_HOME` must not take the window down."""
    blocked = tmp_path / "state"
    blocked.write_text("I am a file where a directory should be", encoding="utf-8")

    assert PrefsStore(blocked).save(Prefs()) is False


def test_an_unknown_view_is_carried_rather_than_rejected(tmp_path: Path) -> None:
    """The engine does not own the View vocabulary -- the UI decides what it recognises,
    so a value from a newer version survives a round trip through an older store."""
    saved = store(tmp_path)
    saved.save(Prefs(view="something-new"))

    assert saved.load().view == "something-new"


# --- immutability -----------------------------------------------------------------------------


def test_changing_a_preference_makes_a_new_value(tmp_path: Path) -> None:
    """`Prefs` is frozen so a window cannot drift from the file without one of them saving."""
    original = Prefs()

    assert original.with_view("config").view == "config"
    assert original.with_show_advanced(True).show_advanced is True
    assert original == Prefs()
