"""UI smoke tier: ADR-0012's one-time notices, as the user meets them (#178).

When a notice is owed and which releases it covers is settled headless, in
`tests/unit/test_session_retirement.py`. What is left here is the toolkit half: the toast
says what happened and offers Details, Details lists the settings, and the release is
recorded as seen when the toast goes away -- not when it appears.

Toolkit imports sit inside the functions so a machine without PyGObject skips this tier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"
RESIZE = "general:resize_on_border"
GONE = "misc:removed_long_ago"
"""A retired Option the loaded Schema no longer describes: trigger (a), no Row, no label."""


def build_window(tmp_path: Path) -> tuple[Any, Any]:
    """A window over an offline Session whose Manifest keeps two retired values."""
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.state import Manifest, RetiredValue
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    paths = ConfigPaths.rooted_at(tmp_path)
    paths.app_dir.mkdir(parents=True)
    manifest = Manifest(
        app_version=APP_VERSION,
        schema_version="0.56.2",
        retired={
            RESIZE: RetiredValue("0.57.0", True),
            GONE: RetiredValue("0.57.0", 3),
        },
    )
    paths.manifest.write_text(manifest.render(), encoding="utf-8")

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=paths,
        app_version=APP_VERSION,
        connect=no_compositor,
        read_live=lambda: None,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def seen(tmp_path: Path) -> tuple[str, ...]:
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.state import Manifest

    path = ConfigPaths.rooted_at(tmp_path).manifest
    return Manifest.load(path, app_version="x", schema_version="x").retired_notices


def rows(dialog: Any) -> list[tuple[str, str]]:
    from gi.repository import Adw, Gtk

    found: list[tuple[str, str]] = []
    stack: list[Gtk.Widget] = [dialog.get_extra_child()]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Adw.ActionRow):
            found.append((widget.get_title(), widget.get_subtitle()))
            continue
        child = widget.get_last_child()
        while child is not None:
            stack.append(child)
            child = child.get_prev_sibling()
    return found


def test_a_retired_notice_says_which_release_and_offers_details(tmp_path: Path) -> None:
    from hyprtweaker.engine.state.retirement import RetiredNotice

    _, window = build_window(tmp_path)
    notice = RetiredNotice("0.57.0", (GONE, RESIZE))

    toast = window.show_notice(notice)
    dialog = window.notice_details(notice)

    assert toast.get_title() == "Hyprland 0.57.0 removed 2 settings you had set"
    assert toast.get_button_label() == "Details"
    assert dialog.get_heading() == "Settings removed in Hyprland 0.57.0"
    assert rows(dialog) == [(GONE, ""), ("Resize windows by dragging their border", RESIZE)]


def test_the_release_is_recorded_when_the_toast_goes_not_when_it_comes(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.state.retirement import RetiredNotice

    _, window = build_window(tmp_path)

    toast = window.show_notice(RetiredNotice("0.57.0", (GONE, RESIZE)))
    shown = seen(tmp_path)
    toast.dismiss()

    assert shown == ()
    assert seen(tmp_path) == ("0.57.0",)


def test_a_rename_notice_lists_old_and_new_names(tmp_path: Path) -> None:
    from hyprtweaker.engine.state.retirement import RenamedNotice

    _, window = build_window(tmp_path)
    notice = RenamedNotice(((GONE, RESIZE),))

    toast = window.show_notice(notice)
    dialog = window.notice_details(notice)
    toast.dismiss()

    assert toast.get_title() == (
        "Hyprland renamed a setting you had set; your value moved with it"
    )
    assert rows(dialog) == [("Resize windows by dragging their border", f"{GONE} → {RESIZE}")]
    assert seen(tmp_path) == (), "a rename notice records nothing"
