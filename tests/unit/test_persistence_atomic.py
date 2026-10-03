"""Export, preferences and monitor profiles are written whole or not at all (#251).

Each site goes through `engine/files.write_atomic`. The fault injected is the one a full
disk or a crash produces: the new bytes never reach the destination. Whatever was there
before must still be there, byte for byte, with no stray temporary beside it. The loss
report and the recovery marker are #268's and are proven in its tests.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from hyprtweaker.engine.migration.export import ExportResult
from hyprtweaker.engine.model.entities import MonitorRule
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.prefs import Prefs, PrefsStore
from hyprtweaker.engine.profiles import MonitorProfile, ProfileStore

OLD = "-- the user's working config\nhl.config({ general = { gaps_in = 5 } })\n"


@pytest.fixture
def full_disk(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Arms a full disk: from the call on, every rename onto a destination fails."""

    def refuse(src: object, dst: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    return lambda: monkeypatch.setattr(os, "replace", refuse)


def entries(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


class TestExport:
    def test_a_failed_export_over_the_entrypoint_keeps_its_old_bytes(
        self, tmp_path: Path, full_disk: Callable[[], None]
    ) -> None:
        entrypoint = ConfigPaths.rooted_at(tmp_path).entrypoint
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text(OLD, encoding="utf-8")

        full_disk()
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


class TestPrefs:
    def test_a_failed_save_keeps_the_old_prefs_and_says_so(
        self, tmp_path: Path, full_disk: Callable[[], None]
    ) -> None:
        store = PrefsStore(tmp_path / "state")
        assert store.save(Prefs(view="config"))
        old = store.path.read_bytes()

        full_disk()
        assert store.save(Prefs(view="tasks", theme="dark")) is False

        assert store.path.read_bytes() == old
        assert entries(store.path.parent) == ["prefs.json"]

    def test_a_save_keeps_its_format(self, tmp_path: Path) -> None:
        store = PrefsStore(tmp_path / "state")

        assert store.save(Prefs(view="config", theme="dark", remembered={"quit": "keep"}))

        assert store.path.read_text(encoding="utf-8") == (
            "{\n"
            '  "format_version": 1,\n'
            '  "view": "config",\n'
            '  "show_advanced": false,\n'
            '  "theme": "dark",\n'
            '  "remembered": {\n'
            '    "quit": "keep"\n'
            "  }\n"
            "}\n"
        )


DOCKED = MonitorProfile(
    name="Docked", monitors=(MonitorRule(output="eDP-1", fields={"mode": "preferred"}),)
)


class TestMonitorProfiles:
    def test_a_failed_update_keeps_the_old_profile(
        self, tmp_path: Path, full_disk: Callable[[], None]
    ) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")
        slug = store.save(DOCKED)
        old = (tmp_path / "monitor-profiles" / "docked.json").read_bytes()

        full_disk()
        with pytest.raises(OSError):
            store.replace(slug, MonitorProfile(name="Docked", monitors=()))

        assert (tmp_path / "monitor-profiles" / "docked.json").read_bytes() == old
        assert entries(tmp_path / "monitor-profiles") == ["docked.json"]

    def test_a_failed_activation_keeps_the_old_pointer(
        self, tmp_path: Path, full_disk: Callable[[], None]
    ) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")
        store.set_active(store.save(DOCKED))
        store.save(MonitorProfile(name="Desk", monitors=()))

        full_disk()
        with pytest.raises(OSError):
            store.set_active("desk")

        assert store.active_slug() == "docked"
        assert entries(tmp_path / "monitor-profiles") == [
            "active.json",
            "desk.json",
            "docked.json",
        ]

    def test_profile_and_pointer_keep_their_format(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")

        store.set_active(store.save(DOCKED))

        assert (tmp_path / "monitor-profiles" / "active.json").read_text(
            encoding="utf-8"
        ) == '{"slug": "docked"}\n'
        assert (
            (tmp_path / "monitor-profiles" / "docked.json")
            .read_text(encoding="utf-8")
            .startswith('{\n  "connected": [],\n')
        )
