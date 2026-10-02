"""ADR-0012's one-time Info notices: what a toast says, and what its **Details** opens.

Two notices, one shape. A release that removed settings the user had set (Retired), and a
release that renamed them (the value moved on its own). Neither is a fault, so neither is
the Banner's (ADR-0016 §Surfacing): a toast says it once, and the dialog lists the settings
so the user can see exactly which.

A retired setting may have no schema entry left -- the app's update shipped a schema
without it -- so a row falls back to the option's own name, which is also what the user
would search their config for.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.schema import Schema  # noqa: E402
from hyprtweaker.engine.state.retirement import (  # noqa: E402
    RenamedNotice,
    RetiredNotice,
    UnkeptNotice,
)

Notice = RetiredNotice | UnkeptNotice | RenamedNotice

_MAX_HEIGHT = 320
"""How tall the list grows before it scrolls, as in the config-error dialog."""


def notice_title(notice: Notice) -> str:
    """The toast's one line."""
    if isinstance(notice, RetiredNotice | UnkeptNotice):
        count = len(notice.names)
        what = "a setting" if count == 1 else f"{count} settings"
        title = f"Hyprland {notice.release} removed {what} you had set"
        if isinstance(notice, RetiredNotice):
            return title
        return title + (
            "; its value could not be kept"
            if count == 1
            else "; their values could not be kept"
        )
    if len(notice.renames) == 1:
        return "Hyprland renamed a setting you had set; your value moved with it"
    return f"Hyprland renamed {len(notice.renames)} settings you had set; your values moved"


def notice_dialog(parent: Gtk.Widget, notice: Notice, schema: Schema) -> Adw.AlertDialog:
    """Show the settings `notice` is about over `parent`, and return the dialog."""
    if isinstance(notice, RetiredNotice | UnkeptNotice):
        dialog = Adw.AlertDialog(
            heading=f"Settings removed in Hyprland {notice.release}",
            body=(
                "The app no longer writes these, so they cause no config errors. "
                + (
                    "Your values are kept and come back if the settings return."
                    if isinstance(notice, RetiredNotice)
                    else "It could not read their values back, so if Hyprland brings "
                    "these settings back, set them again."
                )
            ),
        )
        # A name with no label left is its own title; repeating it underneath says nothing.
        rows = [(_label(name, schema), name) for name in notice.names]
        rows = [(title, "" if title == name else name) for title, name in rows]
    else:
        dialog = Adw.AlertDialog(
            heading="Renamed settings",
            body="Hyprland gave these settings new names. The app moved your values to them.",
        )
        rows = [(_label(new, schema), f"{old} → {new}") for old, new in notice.renames]

    dialog.set_extra_child(_list(rows))
    dialog.add_response("close", "Close")
    dialog.set_default_response("close")
    dialog.set_close_response("close")
    dialog.present(parent)
    return dialog


def _label(name: str, schema: Schema) -> str:
    option = schema.get(name)
    return option.title if option is not None else name


def _list(rows: list[tuple[str, str]]) -> Gtk.Widget:
    box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
    for title, subtitle in rows:
        row = Adw.ActionRow(title=title, subtitle=subtitle, subtitle_selectable=True)
        row.set_use_markup(False)
        box.append(row)
    return Gtk.ScrolledWindow(
        child=box,
        propagate_natural_height=True,
        max_content_height=_MAX_HEIGHT,
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
    )
