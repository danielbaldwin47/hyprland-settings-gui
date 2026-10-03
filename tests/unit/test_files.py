"""`keep_edited_copy`: a hand-edited file is copied where the user can find it before the
app replaces it (S3 of spec m1: Roll back (#268) and Restore (#266) share it)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyprtweaker.engine import files
from hyprtweaker.engine.files import keep_edited_copy
from hyprtweaker.engine.paths import ConfigPaths


@pytest.fixture
def paths(tmp_path: Path) -> ConfigPaths:
    config = ConfigPaths.rooted_at(tmp_path)
    config.hypr_dir.mkdir(parents=True)
    return config


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def test_the_copy_holds_the_exact_bytes_under_a_dated_folder(paths: ConfigPaths) -> None:
    source = paths.entrypoint
    source.write_bytes(b"-- mine\n\xff marker-268\n")

    copy = keep_edited_copy(paths, source, "hyprland.lua")

    assert copy is not None
    assert copy.read_bytes() == b"-- mine\n\xff marker-268\n"
    assert copy.name == "hyprland.lua"
    assert copy.parent.parent == paths.edited_copies_dir


def test_a_second_copy_in_the_same_second_never_lands_on_the_first(
    paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files, "datetime", _Frozen)
    source = paths.entrypoint
    source.write_text("first\n")
    first = keep_edited_copy(paths, source, "hyprland.lua")
    source.write_text("second\n")
    second = keep_edited_copy(paths, source, "hyprland.lua")

    assert first == paths.edited_copies_dir / "20261002-120000" / "hyprland.lua"
    assert second == paths.edited_copies_dir / "20261002-120000-2" / "hyprland.lua"
    assert first.read_text() == "first\n"
    assert second.read_text() == "second\n"


def test_a_missing_source_has_nothing_to_copy(paths: ConfigPaths) -> None:
    assert keep_edited_copy(paths, paths.entrypoint, "hyprland.lua") is None
    assert not paths.edited_copies_dir.exists()


def test_a_copy_that_cannot_be_written_raises(paths: ConfigPaths) -> None:
    paths.entrypoint.write_text("mine\n")
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.edited_copies_dir.write_text("in the way")

    with pytest.raises(OSError):
        keep_edited_copy(paths, paths.entrypoint, "hyprland.lua")
