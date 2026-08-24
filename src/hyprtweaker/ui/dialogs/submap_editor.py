"""Creating and editing a Submap: name and reset target (#66, ADR-0007).

Small on purpose. A Submap has exactly two facts of its own -- its name and where leaving
it lands -- and its binds are edited where they live, on the Binds page. The dialog never
writes: it hands the two strings back and the caller (`session.save_submap`) does the
cascade, because a rename touches binds this dialog never sees.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.binds_analysis import RESET  # noqa: E402

# `submap("reset")` is the exit gesture (`binds_analysis.RESET`), so a submap *named*
# "reset" could never be entered by name -- the dispatcher call that should enter it
# exits instead. Blocked here, at the only place a name is chosen.

NAME_HELP = "The name binds enter it by, as in: switch to a submap → {name}"
RESET_HELP = (
    "Where leaving this submap lands. Empty means back to the root keybinds; "
    "naming another submap chains into it."
)


class SubmapEditor(Adw.Dialog):
    """Add or edit one Submap. Calls `on_done(name, reset_target)`, or never."""

    def __init__(
        self,
        *,
        on_done: Callable[[str, str], None],
        taken: Sequence[str] = (),
        name: str = "",
        reset_target: str = "",
    ) -> None:
        """`taken` is every other submap's name -- duplicates merge silently in the model,
        so a second submap by the same name would be an edit wearing a create's clothes."""
        super().__init__(
            title="Edit submap" if name else "Add submap",
            content_width=480,
            content_height=360,
        )
        self._on_done = on_done
        self._taken = {existing for existing in taken if existing != name}

        group = Adw.PreferencesGroup(
            title="Submap",
            description="A named mode: while it is active, only its keybinds fire.",
        )
        self._name = Adw.EntryRow(title="Name")
        self._name.set_text(name)
        group.add(self._name)

        self._reset_target = Adw.EntryRow(title="Reset target (optional)")
        self._reset_target.set_text(reset_target)
        self._reset_target.set_tooltip_text(RESET_HELP)
        group.add(self._reset_target)

        self._error = Gtk.Label(css_classes=["error"], visible=False, wrap=True)

        save = Gtk.Button(label="Save", css_classes=["suggested-action"], halign=Gtk.Align.END)
        save.connect("clicked", lambda _button: self._save())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(group)
        box.append(self._error)
        box.append(save)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(Adw.HeaderBar())
        clamp = Adw.Clamp(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        clamp.set_child(box)
        body.append(clamp)
        self.set_child(body)

    def _validate(self) -> str:
        name = self._name.get_text().strip()
        if not name:
            return "A submap needs a name."
        if name == RESET:
            return (
                'A submap cannot be named "reset": that is the word that *leaves* a '
                "submap, so nothing could ever enter this one."
            )
        if name in self._taken:
            return f'There is already a submap named "{name}".'
        if self._reset_target.get_text().strip() == name:
            return "A submap cannot be its own reset target."
        return ""

    def _save(self) -> None:
        if problem := self._validate():
            self._error.set_text(problem)
            self._error.set_visible(True)
            return
        self._on_done(self._name.get_text().strip(), self._reset_target.get_text().strip())
        self.close()
