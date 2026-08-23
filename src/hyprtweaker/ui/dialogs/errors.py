"""The config-error dialog: what **Details** opens.

ADR-0016 puts one dialog behind every unhealthy state -- "monospace `file:line` list with
per-class action buttons" -- and #60 builds that one, with the Banner that raises it and the
Ownership-class actions (Restore last good, Open file, Quarantine, Regenerate). What lives
here is the half auto-revert cannot do without: the lines themselves, legibly.

Verbatim and monospace, on purpose. The `file:line` prefix is the only evidence of *whose*
file failed (ADR-0016 attributes ownership by it), and a dialog that reflowed or reworded the
line would throw away the one part a user can act on -- it is what they paste into an editor's
go-to-line box.
"""

from __future__ import annotations

from collections.abc import Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

_HEADING = "Hyprland reported a problem with the config"

_MAX_HEIGHT = 320
"""How tall the error list grows before it scrolls. A reload can produce one line or fifty,
and a dialog that grew to fifty would be taller than the screen it has to fit on."""


def error_dialog(parent: Gtk.Widget, errors: Sequence[str], *, body: str = "") -> Gtk.Widget:
    """Show `errors` over `parent` and return the dialog, so a caller can assert on it.

    Returned rather than merely presented because this tier is testable and the alternative
    is a function whose entire effect is invisible from a test.
    """
    dialog = Adw.AlertDialog(heading=_HEADING, body=body)
    dialog.set_extra_child(_error_list(errors))
    dialog.add_response("close", "Close")
    dialog.set_default_response("close")
    dialog.set_close_response("close")
    dialog.present(parent)
    return dialog


def _error_list(errors: Sequence[str]) -> Gtk.Widget:
    label = Gtk.Label(
        label="\n".join(errors),
        xalign=0.0,
        selectable=True,
        wrap=False,
        css_classes=["monospace"],
    )
    return Gtk.ScrolledWindow(
        child=label,
        propagate_natural_height=True,
        max_content_height=_MAX_HEIGHT,
        hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
    )
