"""The confirm shown before the app touches another program's files (settled S3, #164).

Every file a `WirePlan` writes outside the App dir is listed with its path in `~/` form,
whether it is new or changed, and, under "Show changes", the exact text before and after.
Where the copies go is said, and so is any command the action runs. The verb button names
the action ("Switch to wallust", "Set up DMS"); Cancel is the default and what Escape
answers, and nothing is remembered: the question is asked every time.

`plan_view` is the list on its own, for a page that shows a plan without a dialog (the
Migration wizard's Bridge step, #187).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

from hyprtweaker.engine.bridge.wire import Change, WirePlan  # noqa: E402

CANCEL = "cancel"
SHOW_CHANGES = "Show changes"
COPY_PATCH = "Copy patch"


def _label(text: str, *, dim: bool = False) -> Gtk.Label:
    """Plain text: a path or a stanza may hold `&` or `<`, which markup would eat."""
    # Not selectable: the first selectable label takes focus and shows itself selected.
    # The paths and texts worth copying are in `_code`, which is.
    label = Gtk.Label(xalign=0.0, wrap=True)
    label.set_use_markup(False)
    label.set_label(text)
    if dim:
        label.add_css_class("dim-label")
    return label


def _code(text: str, *, wrap: bool = False) -> Gtk.Widget:
    """Exact text in a box of its own. A stanza stays unwrapped: wrapped at a dialog's width
    it no longer reads as the file it is, so long text scrolls rather than stretching the
    dialog. A command line wraps (`wrap`), so all of it is in view without scrolling: the
    user agrees to the whole command, not to the part that fits."""
    label = Gtk.Label(xalign=0.0, yalign=0.0, selectable=True)
    label.set_use_markup(False)
    label.set_label(text)
    label.add_css_class("monospace")
    if wrap:
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    scroller = Gtk.ScrolledWindow(
        child=label, propagate_natural_height=True, max_content_height=220
    )
    scroller.set_policy(
        Gtk.PolicyType.NEVER if wrap else Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
    )
    scroller.add_css_class("card")
    label.set_margin_start(8)
    label.set_margin_end(8)
    label.set_margin_top(6)
    label.set_margin_bottom(6)
    return scroller


class PlanView:
    """The files a plan changes, the copies, the command and the patch, as widgets.

    `lines` is every text shown, in order: what a probe or test reads.
    """

    def __init__(
        self, plan: WirePlan | None, *, command: Sequence[str] | None = None, intro: str = ""
    ) -> None:
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.lines: list[str] = []
        self.copy_button: Gtk.Button | None = None
        if intro:
            self._add(_label(intro), intro)
        if plan is not None:
            self._files(plan)
        if command:
            lead = "This runs once, to make the colors:"
            self._add(_label(lead), lead)
            shown = shlex.join(command)
            self._add(_code(shown, wrap=True), shown)
        if plan is not None and plan.patch:
            self._patch(plan)

    def _files(self, plan: WirePlan) -> None:
        for edit in plan.files:
            heading = f"{edit.shown} ({'new' if edit.change is Change.NEW else 'changed'})"
            self._add(_label(heading), heading)
            changes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            if edit.excerpt_before:
                changes.append(_label("Before", dim=True))
                changes.append(_code(edit.excerpt_before.rstrip("\n")))
                self.lines.append(edit.excerpt_before.rstrip("\n"))
            changes.append(_label("After", dim=True))
            changes.append(_code(edit.excerpt_after.rstrip("\n")))
            self.lines.append(edit.excerpt_after.rstrip("\n"))
            expander = Gtk.Expander(label=SHOW_CHANGES, child=changes)
            self.widget.append(expander)
        if any(edit.change is not Change.NEW for edit in plan.files):
            copies = f"A copy of each changed file is kept in {plan.backups_shown}."
            self._add(_label(copies, dim=True), copies)
        elif plan.files:
            # Only new files: there is nothing to keep a copy of (#148 hand-test 9).
            fresh = (
                f"Every file is new, and removing {plan.title} deletes them again."
                if len(plan.files) > 1
                else f"The file is new, and removing {plan.title} deletes it again."
            )
            self._add(_label(fresh, dim=True), fresh)

    def _patch(self, plan: WirePlan) -> None:
        lead = f"Then change your own {plan.title} script as below. This app never edits it."
        self._add(_label(lead), lead)
        patch = plan.patch.rstrip("\n")
        self._add(_code(patch), patch)
        button = Gtk.Button(label=COPY_PATCH, halign=Gtk.Align.START)
        button.connect("clicked", _copy, plan.patch)
        self.widget.append(button)
        self.copy_button = button

    def _add(self, widget: Gtk.Widget, line: str) -> None:
        self.widget.append(widget)
        self.lines.append(line)


def _copy(button: Gtk.Button, text: str) -> None:
    button.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))
    button.set_label("Copied")


def plan_view(plan: WirePlan, *, command: Sequence[str] | None = None) -> PlanView:
    return PlanView(plan, command=command)


class ConsentDialog(Adw.AlertDialog):
    """Asks before an action that touches another program's files or runs it.

    `on_agree` runs only on the verb response; Cancel, Escape and closing do nothing.
    """

    def __init__(
        self,
        *,
        heading: str,
        body: str,
        verb: str,
        on_agree: Callable[[], None],
        plan: WirePlan | None = None,
        intro: str = "",
        command: Sequence[str] | None = None,
        destructive: bool = False,
    ) -> None:
        super().__init__()
        self.set_heading(heading)
        self.set_body(body)
        self.set_prefer_wide_layout(True)
        self.view = PlanView(plan, command=command, intro=intro)
        if self.view.lines:
            self.set_extra_child(self.view.widget)
        self.add_response(CANCEL, "Cancel")
        self.add_response("agree", verb)
        self.set_response_appearance(
            "agree",
            Adw.ResponseAppearance.DESTRUCTIVE
            if destructive
            else Adw.ResponseAppearance.SUGGESTED,
        )
        self.set_default_response(CANCEL)
        self.set_close_response(CANCEL)
        self._on_agree = on_agree
        self.connect("response", self._on_response)

    @property
    def lines(self) -> tuple[str, ...]:
        """The heading, the body and every line of the plan, as the user reads them."""
        return (self.get_heading() or "", self.get_body() or "", *self.view.lines)

    def _on_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response == "agree":
            self._on_agree()


__all__ = ["CANCEL", "COPY_PATCH", "SHOW_CHANGES", "ConsentDialog", "PlanView", "plan_view"]
