"""Turning a `PagePlan` into the `Adw.PreferencesPage` the Config view shows.

The split is the point: `plan.py` decided *what* goes on the Page and can be checked without
a display; this only builds widgets for it. So "does every Section still produce every one
of its Options?" is a unit test, and the UI smoke tier is left with the one question it is
good for -- does the toolkit still assemble it (spec #48, seam 5).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw  # noqa: E402

from hyprtweaker.ui.pages.plan import PagePlan  # noqa: E402
from hyprtweaker.ui.rows.factory import OptionRow, RowFactory  # noqa: E402


class ConfigPage:
    """One Section's Page, plus the Rows on it, so they can be refreshed as a set."""

    def __init__(self, plan: PagePlan, factory: RowFactory) -> None:
        self._plan = plan
        self._rows: list[OptionRow] = []

        self._page = Adw.PreferencesPage(title=plan.title)
        for group_plan in plan.groups:
            group = Adw.PreferencesGroup(title=group_plan.title)
            for option in group_plan.options:
                row = factory.build(option)
                self._rows.append(row)
                group.add(row.widget)
            self._page.add(group)

        if not plan.groups:
            self._page.add(_withheld_group(plan))

    @property
    def plan(self) -> PagePlan:
        return self._plan

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def rows(self) -> tuple[OptionRow, ...]:
        return tuple(self._rows)

    def refresh(self) -> None:
        """Re-read the model into every control on this Page."""
        for row in self._rows:
            row.refresh()

    def set_editable(self, editable: bool) -> None:
        """Make only the controls insensitive, never the Rows (ADR-0013 §3).

        A read-only session still has to be *readable*: dimming whole Rows takes their
        titles and descriptions down to near-unreadable, which is the same mistake the ADR
        rejects for dependency-disabled Rows.
        """
        for row in self._rows:
            row.control.set_sensitive(editable)


def _withheld_group(plan: PagePlan) -> Adw.PreferencesGroup:
    """What a Section shows when the Advanced switch has emptied it.

    Five Sections are entirely `advanced` or `hidden` -- `debug`, `quirks`, `experimental`,
    `input-capture`, `opengl`. Their Pages still exist, because the sidebar is the map of
    the config surface, and a Page that renders nothing at all reads as a broken app rather
    than as a deliberately quiet one.
    """
    group = Adw.PreferencesGroup()
    if plan.withheld:
        row = Adw.ActionRow(
            title=f"{plan.withheld} advanced settings",
            subtitle="Turn on “Show advanced settings” in the main menu to see them.",
        )
    else:
        row = Adw.ActionRow(
            title="Nothing to configure here",
            subtitle=f"Hyprland {plan.section} has no options in this version.",
        )
    row.set_sensitive(False)
    group.add(row)
    return group
