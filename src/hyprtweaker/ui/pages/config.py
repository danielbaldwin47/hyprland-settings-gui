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

from gi.repository import Adw, GLib  # noqa: E402

from hyprtweaker.ui.pages.plan import PagePlan  # noqa: E402
from hyprtweaker.ui.rows.factory import OptionRow, RowFactory  # noqa: E402


class ConfigPage:
    """One Section's Page, plus the Rows on it, so they can be refreshed as a set."""

    def __init__(self, plan: PagePlan, factory: RowFactory) -> None:
        self._plan = plan
        self._rows: list[OptionRow] = []
        self._by_name: dict[str, OptionRow] = {}

        self._page = Adw.PreferencesPage(title=plan.title)
        for group_plan in plan.groups:
            group = Adw.PreferencesGroup(
                title=escaped(group_plan.title),
                description=escaped(group_plan.description),
            )
            for option in group_plan.options:
                row = factory.build(option)
                self._rows.append(row)
                self._by_name[option.name] = row
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

    def row(self, name: str) -> OptionRow | None:
        """The Row for one Option name, if this Page built it.

        `None` covers the Option the Advanced switch is currently withholding -- which is
        why the dependency badge has to ask rather than assume.
        """
        return self._by_name.get(name)

    def refresh(self) -> None:
        """Re-read the model into every control on this Page, chrome included.

        Abandons any continuous gesture first. A refresh means the model moved under the UI
        -- a foreign reload's re-read, a session going read-only -- and that same reload
        wiped the Eval preview a drag in progress was showing (ADR-0010). Letting the
        gesture live through it would commit the user's half-chosen value on the strength of
        somebody else's reload; dropping it leaves the truth the re-read just fetched.
        """
        for row in self._rows:
            row.abandon_gesture()
            row.refresh()
            row.chrome.refresh()

    def refresh_chrome(self) -> None:
        """Re-decide the suffix strips without touching the controls.

        The cheap half of `refresh`, and the one an edit needs: a write changes which Rows
        count as modified and which dependencies are met, but not what any control should be
        displaying -- and rewriting a control the user is mid-gesture on is how a spinner
        fights back.
        """
        for row in self._rows:
            row.chrome.refresh()


def escaped(text: str) -> str:
    """A Group heading or description, safe to hand to libadwaita.

    `Adw.PreferencesGroup` parses its title as Pango markup, so a curated heading with an
    ampersand in it -- "Splash & wallpaper" -- renders as *nothing at all*, with only a
    `Gtk-WARNING` on stderr to say so. That is the same trap #56 hit with Row titles, and it
    bites here the moment headings become curated data (#71's mapping, and #82's Overlay
    `group`): a curator writing plain English has no reason to expect their heading to
    vanish. Escaping here rather than in the data keeps the plan's titles plain text --
    the sidebar and the tests read the same strings, and `&amp;` never leaks on screen.
    """
    return GLib.markup_escape_text(text)


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
