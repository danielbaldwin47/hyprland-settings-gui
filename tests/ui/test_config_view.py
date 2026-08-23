"""UI smoke tier: every Section really does build a Page, in a real toolkit.

Deliberately shallow (spec #48, "Testing Decisions"). What each Page *contains* is settled
in `tests/unit/test_ui_page_plan.py`, where it can be asserted without a display; the only
question left for this tier is whether GTK and libadwaita assemble the plan without
complaint -- which is a question no headless tier can answer, and which a widget-state
assertion here would not answer any better.

The toolkit imports sit inside the test functions on purpose: importing ``gi`` at module
scope would raise during collection on a machine without PyGObject, which pytest reports as
an error rather than the skip this tier is supposed to produce.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"


def build_window(tmp_path: Path) -> Any:
    """A window over a read-only session rooted in a throwaway directory."""
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def test_every_section_builds_a_page(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    assert [page.plan.section for page in window.pages] == list(session.schema.section_names)
    assert all(page.page.get_title() for page in window.pages)


def test_every_visible_option_becomes_a_row(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    built = [row.option.name for page in window.pages for row in page.rows]
    planned = [option.name for page in window.pages for option in _planned_options(page.plan)]

    assert built == planned
    assert len(built) == len(set(built))
    assert len(built) < len(session.schema), "advanced options start hidden"


def test_the_advanced_switch_reveals_every_remaining_option(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    window.activate_action("win.show-advanced")

    assert window.show_advanced
    built = {row.option.name for page in window.pages for row in page.rows}
    assert built == {option.name for option in session.schema}
    assert len(window.pages) == len(session.schema.section_names)


def test_a_read_only_session_leaves_controls_insensitive_but_rows_readable(
    tmp_path: Path,
) -> None:
    """ADR-0013 §3: only the control is dimmed, so titles and subtitles stay legible."""
    _session, window = build_window(tmp_path)

    rows = [row for page in window.pages for row in page.rows]
    assert rows
    assert all(not row.control.get_sensitive() for row in rows)
    assert all(row.widget.get_sensitive() for row in rows)
    assert all(row.widget.get_title() for row in rows)


def _planned_options(plan: Any) -> list[Any]:
    return [option for group in plan.groups for option in group.options]
