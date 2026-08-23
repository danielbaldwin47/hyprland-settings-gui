"""The config-error dialog: the one thing the Banner opens (ADR-0016 §Surfacing).

ADR-0016 allows exactly one, and describes it exactly: "monospace `file:line` list with
per-class action buttons". Both halves are load-bearing.

**Monospace and verbatim.** The `file:line` prefix is the only evidence of *whose* file
failed -- attribution is made from it -- and it is also, in the ADR's own words, "what they
paste into an editor's go-to-line box". A dialog that reflowed or reworded the line would
throw away the one part the user can act on.

**Grouped by file, with that file's actions beside it.** Recovery is per file: a Module with
six syntax errors is one broken file with one Restore button. The buttons come from
`recovery.py`'s matrix rather than from anything decided here, so what the dialog offers and
what the ADR's table says cannot drift apart -- this module only knows what each `Action` is
*called*.

A dialog with no `on_action` renders the lines and nothing else. That is the auto-revert
toast's **Details**: by the time it opens, the app has already put the file back and the
reload that did it has cleared `configerrors`, so there is nothing left to act on and
offering a button would be offering to recover from a recovery.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.apply import Action, Problem, Recovery  # noqa: E402

_HEADING = "Hyprland reported a problem with the config"

_MAX_HEIGHT = 320
"""How tall the list grows before it scrolls. A reload can produce one line or fifty, and a
dialog that grew to fifty would be taller than the screen it has to fit on."""

ACTION_LABELS: dict[Action, str] = {
    Action.RESTORE_LAST_GOOD: "Restore last good",
    Action.OPEN_FILE: "Open file",
    Action.QUARANTINE: "Disable until fixed",
    Action.REGENERATE: "Regenerate",
}
"""What each recovery is called on its button.

`AUTO_REVERT` is deliberately absent, and its absence is what keeps it from being drawn: it
is not something the user does, it is something the app has already done. Looking the label
up rather than testing for the class means a future action added to the matrix appears here
the moment it is named, instead of silently rendering nothing.
"""

ActionHandler = Callable[[Action, Problem], None]


def error_dialog(
    parent: Gtk.Widget,
    recovery: Recovery,
    *,
    on_action: ActionHandler | None = None,
    body: str = "",
) -> Adw.AlertDialog:
    """Show `recovery` over `parent` and return the dialog, so a caller can assert on it.

    Returned rather than merely presented because this tier is testable and the alternative
    is a function whose entire effect is invisible from a test.
    """
    dialog = Adw.AlertDialog(heading=_HEADING, body=body)
    dialog.set_extra_child(_problem_list(recovery, dialog, on_action))
    dialog.add_response("close", "Close")
    dialog.set_default_response("close")
    dialog.set_close_response("close")
    dialog.present(parent)
    return dialog


def _problem_list(
    recovery: Recovery, dialog: Adw.AlertDialog, on_action: ActionHandler | None
) -> Gtk.Widget:
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    for problem in recovery.problems:
        body.append(_problem_card(problem, dialog, on_action))
    return Gtk.ScrolledWindow(
        child=body,
        propagate_natural_height=True,
        max_content_height=_MAX_HEIGHT,
        hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
    )


def _problem_card(
    problem: Problem, dialog: Adw.AlertDialog, on_action: ActionHandler | None
) -> Gtk.Widget:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.append(
        Gtk.Label(
            label="\n".join(problem.lines),
            xalign=0.0,
            selectable=True,
            wrap=False,
            css_classes=["monospace"],
        )
    )

    buttons = _buttons(problem, dialog, on_action)
    if buttons is not None:
        card.append(buttons)
    return card


def _buttons(
    problem: Problem, dialog: Adw.AlertDialog, on_action: ActionHandler | None
) -> Gtk.Widget | None:
    if on_action is None:
        return None

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.END)
    offered = 0
    for action in problem.actions:
        label = ACTION_LABELS.get(action)
        if label is None:
            continue
        button = Gtk.Button(label=label)
        # Closing first: every action ends in a reload, and a dialog left open over it would
        # be listing the errors of a config that no longer exists.
        button.connect("clicked", _handler(dialog, on_action, action, problem))
        row.append(button)
        offered += 1
    return row if offered else None


def _handler(
    dialog: Adw.AlertDialog,
    on_action: ActionHandler,
    action: Action,
    problem: Problem,
) -> Callable[[Gtk.Button], None]:
    def clicked(_button: Gtk.Button) -> None:
        dialog.close()
        on_action(action, problem)

    return clicked
