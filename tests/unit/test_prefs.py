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


def write(tmp_path: Path, payload: object) -> None:
    """Put a hand-made Prefs file where `store(tmp_path)` reads it."""
    path = store(tmp_path).path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# --- round trip -------------------------------------------------------------------------------


def test_a_saved_preference_is_the_one_that_comes_back(tmp_path: Path) -> None:
    """AC 4 of #71, reduced to its one honest question: does the choice survive a restart?"""
    saved = store(tmp_path)
    assert saved.save(Prefs(view="config", show_advanced=True))

    assert store(tmp_path).load() == Prefs(view="config", show_advanced=True)


def test_the_app_opens_in_the_tasks_view_until_told_otherwise(tmp_path: Path) -> None:
    """Tasks is the default (#7): the curated view is the one the audience is here for."""
    assert store(tmp_path).load() == Prefs(view="tasks", show_advanced=False)


def test_the_app_follows_the_system_style_and_remembers_nothing_until_told(
    tmp_path: Path,
) -> None:
    """ADR-0019: the Theme override defaults to System; no dialog answer is assumed."""
    loaded = store(tmp_path).load()

    assert loaded.theme == "system"
    assert dict(loaded.remembered) == {}


def test_a_file_from_before_the_theme_override_keeps_its_other_choices(
    tmp_path: Path,
) -> None:
    """Additive: a file written by #71's app has no `theme` or `remembered` key at all."""
    write(tmp_path, {"format_version": FORMAT_VERSION, "view": "config", "show_advanced": True})

    assert store(tmp_path).load() == Prefs(view="config", show_advanced=True)


def test_saving_creates_the_state_directory(tmp_path: Path) -> None:
    """First run has no state dir at all -- the first preference is what creates it."""
    saved = store(tmp_path)

    assert saved.save(Prefs(view="config"))
    assert saved.path == tmp_path / "state" / PREFS_FILENAME
    assert saved.path.is_file()


def test_every_preference_survives_a_restart_in_one_file(tmp_path: Path) -> None:
    """#78: theme, View, the Advanced switch and remembered dialog answers, one round trip."""
    chosen = (
        Prefs(view="config", show_advanced=True)
        .with_theme("dark")
        .with_remembered("import-overwrite", "replace")
        .with_remembered("leave-unsaved", "discard")
    )
    assert store(tmp_path).save(chosen)

    assert store(tmp_path).load() == Prefs(
        view="config",
        show_advanced=True,
        theme="dark",
        remembered={"import-overwrite": "replace", "leave-unsaved": "discard"},
    )


def test_the_file_is_plain_readable_json(tmp_path: Path) -> None:
    """Not GSettings, and not an opaque blob: a user can read and delete it (ADR-0019)."""
    saved = store(tmp_path)
    saved.save(Prefs(view="config", show_advanced=True).with_remembered("import", "keep"))

    payload = json.loads(saved.path.read_text(encoding="utf-8"))

    assert payload == {
        "format_version": FORMAT_VERSION,
        "view": "config",
        "show_advanced": True,
        "theme": "system",
        "remembered": {"import": "keep"},
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


def test_a_wrong_typed_theme_keeps_the_default_and_spares_the_rest(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "format_version": FORMAT_VERSION,
            "view": "config",
            "show_advanced": True,
            "theme": ["dark"],
            "remembered": {"import": "keep"},
        },
    )

    assert store(tmp_path).load() == Prefs(
        view="config", show_advanced=True, theme="system", remembered={"import": "keep"}
    )


def test_a_junk_remembered_value_loads_as_empty_and_spares_the_rest(tmp_path: Path) -> None:
    """The #170 shape: a mapping of dialog id to choice, or nothing."""
    for junk in (["import", "keep"], "keep", 3, None):
        write(
            tmp_path,
            {
                "format_version": FORMAT_VERSION,
                "view": "config",
                "show_advanced": True,
                "theme": "light",
                "remembered": junk,
            },
        )

        assert store(tmp_path).load() == Prefs(
            view="config", show_advanced=True, theme="light", remembered={}
        ), junk


def test_a_remembered_answer_that_is_not_a_string_is_dropped_alone(tmp_path: Path) -> None:
    """JSON keys are always strings, so only a value can be wrong. The bad answer goes; the
    good one beside it, which the user gave and never corrupted, stays."""
    write(
        tmp_path,
        {
            "format_version": FORMAT_VERSION,
            "theme": "dark",
            "remembered": {"import": "keep", "leave": 1, "quit": None},
        },
    )

    assert store(tmp_path).load() == Prefs(theme="dark", remembered={"import": "keep"})


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


def test_an_unknown_theme_is_carried_rather_than_rejected(tmp_path: Path) -> None:
    """Like the View: the window maps a name it does not know to System."""
    saved = store(tmp_path)
    saved.save(Prefs().with_theme("high-contrast"))

    assert saved.load().theme == "high-contrast"


# --- immutability -----------------------------------------------------------------------------


def test_changing_a_preference_makes_a_new_value(tmp_path: Path) -> None:
    """`Prefs` is frozen so a window cannot drift from the file without one of them saving."""
    original = Prefs()

    assert original.with_view("config").view == "config"
    assert original.with_show_advanced(True).show_advanced is True
    assert original.with_theme("light").theme == "light"
    assert original == Prefs()


def test_remembering_an_answer_leaves_the_earlier_value_untouched() -> None:
    """A fresh mapping per change: the value the window held before is still what it was."""
    before = Prefs().with_remembered("import", "keep")
    after = before.with_remembered("import", "replace").with_remembered("leave", "discard")

    assert dict(before.remembered) == {"import": "keep"}
    assert dict(after.remembered) == {"import": "replace", "leave": "discard"}


def test_one_remembered_answer_can_be_forgotten() -> None:
    both = Prefs().with_remembered("import", "keep").with_remembered("leave", "discard")

    assert dict(both.without_remembered("import").remembered) == {"leave": "discard"}
    assert dict(both.remembered) == {"import": "keep", "leave": "discard"}
    assert both.without_remembered("never-asked") == both


def test_every_remembered_answer_can_be_forgotten_at_once() -> None:
    """The "Forget remembered choices" menu item: every key, nothing else."""
    both = (
        Prefs(view="config")
        .with_theme("dark")
        .with_remembered("import", "keep")
        .with_remembered("leave", "discard")
    )

    assert both.without_any_remembered() == Prefs(view="config", theme="dark")
