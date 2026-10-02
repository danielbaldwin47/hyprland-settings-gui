"""The Theme archive import preview, as real widgets (#169).

What the preview means is proven headless (`tests/unit/test_session_presets.py`,
`tests/unit/test_presets_archive.py`). What only this tier can check is that the dialog
shows it: the Sections, each Option's before and after in the Rows' own words, the
wallpaper decoded from the archive's bytes, a default that changes nothing, and that
confirming adds the Preset and hands its slug to the apply it was given.

Toolkit imports stay inside the test functions, as everywhere in this tier.
"""

from __future__ import annotations

import struct
import sys
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import main_loop
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

APP_ID = "io.github.danielbaldwin47.Hyprtweaker.Test"


def png(width: int = 4, height: int = 3) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + crc

    rows = b"".join(b"\x00" + b"\x2e\x34\x40" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def build(tmp_path: Path, *, wallpaper: bool = True, **options: Any):  # type: ignore[no-untyped-def]
    """A window over an offline session, and a Theme archive of a Preset "Nord" to import."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.presets import CaptureScope, Preset
    from hyprtweaker.engine.presets_archive import export_archive
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    Adw.init()
    app = Adw.Application(application_id=APP_ID)

    def no_compositor():  # type: ignore[no-untyped-def]
        raise NoInstance("no compositor in the test tier")

    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path / "config"),
        app_version="0.0.0-test",
        connect=no_compositor,
    )
    session.model.set("general:border_size", 2)
    image = tmp_path / "fjord.png"
    image.write_bytes(png())
    archive = tmp_path / "nord.hyprtweaker-theme"
    export_archive(
        Preset(
            name="Nord",
            created=datetime(2026, 10, 2, 9, 30, tzinfo=UTC),
            scopes=frozenset({CaptureScope.GAPS_LAYOUT}),
            options=options
            or {
                "general:border_size": 3,
                "general:gaps_in": "5 10 5 10",
                "decoration:rounding": 0,
                "general:sparkle": 1,
            },
            app_version="0.1.0",
            hyprland_version="0.56.2",
            wallpaper=str(image) if wallpaper else None,
        ),
        archive,
    )
    return MainWindow(session, application=app), session, archive


def walk(widget: Any) -> list[Any]:
    found = [widget]
    child = widget.get_first_child()
    while child is not None:
        found.extend(walk(child))
        child = child.get_next_sibling()
    return found


def rows(dialog: Any) -> list[tuple[str, str, str]]:
    """Each Row under each group, as (group title, row title, row subtitle)."""
    from gi.repository import Adw

    listed = []
    for group in walk(dialog):
        if isinstance(group, Adw.PreferencesGroup):
            for row in walk(group):
                if isinstance(row, Adw.ActionRow):
                    listed.append(
                        (group.get_title(), row.get_title(), row.get_subtitle() or "")
                    )
    return listed


def button(dialog: Any, label: str) -> Any:
    from gi.repository import Gtk

    [found] = [w for w in walk(dialog) if isinstance(w, Gtk.Button) and w.get_label() == label]
    return found


def test_the_preview_shows_each_change_and_what_is_left_out(tmp_path: Path) -> None:
    from gi.repository import Gtk

    from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog

    window, session, archive = build(tmp_path)
    dialog = ThemeImportDialog(session, archive)
    dialog.present(window)
    main_loop.settle("the dialog")

    assert dialog.get_title() == "Import theme"
    assert rows(dialog) == [
        ("General", "Border size", "2 → 3"),
        ("General", "Inner gaps", "5 5 5 5 → 5 10 5 10"),
        ("Already the same", "Corner rounding", "0"),
        ("Left out", "general:sparkle", "This version of Hyprland does not have it"),
    ]
    [picture] = [w for w in walk(dialog) if isinstance(w, Gtk.Picture)]
    assert picture.get_paintable().get_intrinsic_width() == 4
    assert dialog.get_default_widget() is button(dialog, "Cancel")
    assert button(dialog, "Add to Presets").get_sensitive()
    assert session.presets() == ()  # nothing written by showing it


def test_without_a_wallpaper_there_is_no_picture(tmp_path: Path) -> None:
    from gi.repository import Gtk

    from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog

    window, session, archive = build(tmp_path, wallpaper=False)
    dialog = ThemeImportDialog(session, archive)
    dialog.present(window)

    assert [w for w in walk(dialog) if isinstance(w, Gtk.Picture)] == []


def test_adding_while_offline_keeps_the_preset_and_says_it_was_not_applied(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.presets import PresetImported
    from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog

    window, session, archive = build(tmp_path)
    finished: list[object] = []
    dialog = ThemeImportDialog(session, archive, on_finished=finished.append)
    dialog.present(window)

    button(dialog, "Add to Presets").emit("clicked")
    main_loop.settle("the dialog")

    [(slug, preset)] = session.presets()
    assert (slug, preset.name) == ("nord", "Nord")
    assert finished == [PresetImported(slug, preset)]
    assert Path(preset.wallpaper or "").read_bytes() == png()


def test_confirming_while_live_imports_then_applies_through_the_given_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hyprtweaker.engine.presets import PresetApplied
    from hyprtweaker.session import Session
    from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog

    window, session, archive = build(tmp_path)
    monkeypatch.setattr(Session, "live", property(lambda _self: True))
    applied: list[str] = []

    def apply(slug: str) -> PresetApplied:
        applied.append(slug)
        return PresetApplied(applied=("general:border_size",), skipped=())

    dialog = ThemeImportDialog(session, archive, apply=apply)
    dialog.present(window)
    assert dialog.get_default_widget() is button(dialog, "Cancel")

    button(dialog, "Import and Apply").emit("clicked")
    main_loop.settle("the dialog")

    assert applied == ["nord"]
    assert [slug for slug, _ in session.presets()] == ["nord"]


def test_a_colour_conflict_adds_the_preset_and_says_where_to_choose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Until #171 puts the choice in this dialog, a conflict applies nothing (#170)."""
    from gi.repository import Adw

    from hyprtweaker.engine.bridge import Wallpaper
    from hyprtweaker.engine.presets import PresetColorConflict
    from hyprtweaker.session import Session
    from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog

    window, session, archive = build(tmp_path)
    monkeypatch.setattr(Session, "live", property(lambda _self: True))
    dialog = ThemeImportDialog(
        session, archive, apply=lambda _slug: PresetColorConflict(Wallpaper("matugen"))
    )
    dialog.present(window)

    button(dialog, "Import and Apply").emit("clicked")
    main_loop.settle("the dialog")

    [group] = [
        w
        for w in walk(dialog)
        if isinstance(w, Adw.PreferencesGroup) and w.get_title() == "Added, but not applied"
    ]
    assert group.get_description() == (
        "Nord is in your presets. Its colors and the ones your wallpaper sets would "
        "compete, so apply it from your presets to choose which win."
    )
    assert [slug for slug, _ in session.presets()] == ["nord"]


def test_a_hostile_archive_is_a_sentence_and_nothing_to_confirm(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog

    window, session, _ = build(tmp_path)
    bogus = tmp_path / "bogus.hyprtweaker-theme"
    bogus.write_bytes(b"PK\x03\x04 a zip, not a theme")
    dialog = ThemeImportDialog(session, bogus)
    dialog.present(window)

    assert rows(dialog) == []
    assert "This file is not a theme archive." in [
        w.get_description() for w in walk(dialog) if hasattr(w, "get_description")
    ]
    assert dialog.get_default_widget() is button(dialog, "Close")
    assert session.presets() == ()


def test_the_slot_holds_what_the_caller_adds(tmp_path: Path) -> None:
    from gi.repository import Gtk

    from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog

    window, session, archive = build(tmp_path)
    dialog = ThemeImportDialog(session, archive)
    marker = Gtk.Label(label="colour conflict row goes here")
    dialog.slot.append(marker)
    dialog.present(window)
    main_loop.settle("the dialog")

    assert marker in walk(dialog)
    assert dialog.slot.get_visible()


def test_an_empty_slot_is_hidden(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog

    window, session, archive = build(tmp_path)
    dialog = ThemeImportDialog(session, archive)
    dialog.present(window)
    main_loop.settle("the dialog")

    assert not dialog.slot.get_visible()
