"""Export, preferences and monitor profiles are written whole or not at all (#251).

Each site goes through `engine/files.write_atomic`. The fault injected is the one a full
disk or a crash produces: the new bytes never reach the destination. Whatever was there
before must still be there, byte for byte, with no stray temporary beside it. The loss
report and the recovery marker are #268's and are proven in its tests.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from hyprtweaker.engine.migration.export import ExportResult
from hyprtweaker.engine.paths import ConfigPaths

OLD = "-- the user's working config\nhl.config({ general = { gaps_in = 5 } })\n"


@pytest.fixture
def full_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rename onto a destination fails as a full disk would."""

    def refuse(src: object, dst: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "replace", refuse)


def entries(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


class TestExport:
    def test_a_failed_export_over_the_entrypoint_keeps_its_old_bytes(
        self, tmp_path: Path, full_disk: None
    ) -> None:
        entrypoint = ConfigPaths.rooted_at(tmp_path).entrypoint
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text(OLD, encoding="utf-8")

        with pytest.raises(OSError):
            ExportResult(text="-- exported\n", inlined=(), missing=()).write(entrypoint)

        assert entrypoint.read_text(encoding="utf-8") == OLD
        assert entries(entrypoint.parent) == ["hyprland.lua"]

    def test_an_export_over_the_entrypoint_lands_whole(self, tmp_path: Path) -> None:
        entrypoint = ConfigPaths.rooted_at(tmp_path).entrypoint
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text(OLD, encoding="utf-8")
        new = "-- Hyprland config exported\n" + "hl.config({})\n" * 2000

        written = ExportResult(text=new, inlined=(), missing=()).write(entrypoint)

        assert written == entrypoint
        assert entrypoint.read_text(encoding="utf-8") == new
        assert entries(entrypoint.parent) == ["hyprland.lua"]
