"""Capture: record a Trigger by pressing it for real (ADR-0007, #65).

The GNOME Settings shortcut-capture pattern, with the three changes Hyprland needs.

**Capture-phase controllers, no keyboard grab.** A `Gtk.EventControllerKey` in the
capture phase on the dialog sees every key before any widget acts on it, which is what
makes `Tab`, `Return` and arrows recordable instead of being eaten by focus handling.
GNOME does this without grabbing the keyboard, and so do we -- a grab would take input
away from the compositor's own shortcut handling rather than politely asking for it.

**System shortcuts are inhibited, and always restored.** `inhibit_system_shortcuts` asks
the compositor to stop firing its own binds so `SUPER + Q` arrives here rather than
closing a window. Every exit path restores them -- Escape, a successful capture, closing
the dialog, and `unrealize` -- because the failure mode of missing one is a session whose
keybinds stay dead until the app quits.

**Validation is looser than GNOME's, but has one hard block.** Modifier-less binds are
legal in Hyprland; a bare letter warns rather than blocks. What does block is a key name
xkb does not know, because the compositor accepts those silently (`hyprctl` says `ok`,
`configerrors` stays empty, and the bind simply never fires) while Lua rejects the entire
config. Blocking at capture is the only place that asymmetry can be caught cheaply.

The dialog never writes and never touches the model: it hands a canonical trigger string
back through `on_done`, exactly as `BindEditor` does with its `Bind`.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from hyprtweaker.engine.triggers import (  # noqa: E402
    CaptureRecorder,
    Trigger,
    parse_trigger,
    validate_trigger,
)

PROMPT = "Press the keys you want to bind"
SUBPROMPT = "Mouse buttons and the wheel work too. Press Esc to cancel, Backspace to clear."

#: GDK scroll directions we can name. `SMOOTH` carries deltas rather than a direction and
#: is resolved from its delta instead.
_SCROLL_NAMES: dict[Gdk.ScrollDirection, str] = {
    Gdk.ScrollDirection.UP: "up",
    Gdk.ScrollDirection.DOWN: "down",
    Gdk.ScrollDirection.LEFT: "left",
    Gdk.ScrollDirection.RIGHT: "right",
}


class CaptureDialog(Adw.Dialog):
    """Record one Trigger from real input. Calls `on_done` with the string, or never."""

    def __init__(
        self,
        *,
        on_done: Callable[[str], None],
        initial: str = "",
        in_submap: bool = False,
    ) -> None:
        super().__init__(title="Set keybind", content_width=460, content_height=340)
        self._on_done = on_done
        self._in_submap = in_submap
        self._recorder = CaptureRecorder()
        self._initial = initial.strip()
        self._captured: Trigger | None = parse_trigger(self._initial) if self._initial else None
        self._inhibited = False

        self.set_child(self._body())
        self._install_controllers()
        self.connect("unrealize", lambda _dialog: self._restore_shortcuts())
        self.connect("closed", lambda _dialog: self._restore_shortcuts())
        self._refresh()

    # --- layout -------------------------------------------------------------------------

    def _body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self._cancel())
        header.pack_start(cancel)

        self._confirm = Gtk.Button(label="Set", css_classes=["suggested-action"])
        self._confirm.connect("clicked", lambda _button: self._accept())
        header.pack_end(self._confirm)
        box.append(header)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            valign=Gtk.Align.CENTER,
            vexpand=True,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        self._prompt = Gtk.Label(label=PROMPT, css_classes=["title-2"], wrap=True)
        content.append(self._prompt)

        self._shortcut = Gtk.Label(css_classes=["title-1"], selectable=False, wrap=True)
        content.append(self._shortcut)

        self._hint = Gtk.Label(label=SUBPROMPT, css_classes=["dim-label"], wrap=True)
        content.append(self._hint)

        self._problem = Gtk.Label(css_classes=["error"], visible=False, wrap=True)
        content.append(self._problem)

        # Manual entry stays reachable: capture cannot produce a `switch:` trigger, and a
        # key this keyboard does not have still needs a way in (ADR-0007 keeps text entry
        # as the fallback, not as a second-class path).
        group = Adw.PreferencesGroup(title="Or type it")
        self._manual = Adw.EntryRow(title="Trigger")
        if self._initial:
            self._manual.set_text(self._initial)
        self._manual.connect("changed", self._manual_changed_marshal)
        group.add(self._manual)
        content.append(group)

        box.append(content)
        return box

    def _install_controllers(self) -> None:
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        keys.connect("key-released", self._on_key_released)
        self.add_controller(keys)

        click = Gtk.GestureClick(button=0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        # Inhibition needs a realized surface, which a dialog only has once mapped.
        self.connect("map", lambda _dialog: self._inhibit_shortcuts())

    # --- system shortcut inhibition -----------------------------------------------------

    def _toplevel(self) -> Gdk.Toplevel | None:
        native = self.get_native()
        surface = native.get_surface() if native is not None else None
        return surface if isinstance(surface, Gdk.Toplevel) else None

    def _inhibit_shortcuts(self) -> None:
        if self._inhibited:
            return
        toplevel = self._toplevel()
        if toplevel is not None:
            toplevel.inhibit_system_shortcuts(None)
            self._inhibited = True

    def _restore_shortcuts(self) -> None:
        if not self._inhibited:
            return
        # Cleared before the call, so a failure here cannot leave the flag saying we still
        # owe a restore and skip the next one.
        self._inhibited = False
        toplevel = self._toplevel()
        if toplevel is not None:
            toplevel.restore_system_shortcuts()

    # --- input --------------------------------------------------------------------------

    def _on_key_pressed(
        self, _controller: Gtk.EventControllerKey, keyval: int, keycode: int, _state: int
    ) -> bool:
        name = Gdk.keyval_name(keyval) or ""

        # Bare Escape cancels and bare Backspace clears, the GNOME convention. With a
        # modifier held they are ordinary keys -- SUPER + Escape is a bind people really
        # do set, and swallowing it would make it uncapturable.
        if not self._recorder.mods:
            if name == "Escape":
                self._cancel()
                return True
            if name in {"BackSpace", "Delete"}:
                self._clear()
                return True

        captured = self._recorder.press(name, keycode)
        if captured is None:
            self._refresh()  # modifier held: show the chord building up
            return True

        self._settle(captured)
        return True

    def _on_key_released(
        self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: int
    ) -> None:
        self._recorder.release(Gdk.keyval_name(keyval) or "")
        if self._captured is None:
            self._refresh()

    def _on_click(self, gesture: Gtk.GestureClick, _n: int, _x: float, _y: float) -> None:
        self._settle(self._recorder.button(gesture.get_current_button()))

    def _on_scroll(self, controller: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        event = controller.get_current_event()
        direction = None
        if event is not None:
            direction = _SCROLL_NAMES.get(event.get_direction())
        if direction is None:
            # Smooth scrolling reports deltas, not a direction. The dominant axis is the
            # direction the user turned the wheel.
            if abs(dy) >= abs(dx):
                direction = "down" if dy > 0 else "up"
            else:
                direction = "right" if dx > 0 else "left"
        self._settle(self._recorder.wheel(direction))
        return True

    # --- state --------------------------------------------------------------------------

    def _settle(self, trigger: Trigger) -> None:
        self._captured = trigger
        self._manual.handler_block_by_func(self._manual_changed_marshal)
        self._manual.set_text(str(trigger))
        self._manual.handler_unblock_by_func(self._manual_changed_marshal)
        self._recorder.reset()
        self._refresh()

    def _manual_changed_marshal(self, _row: Adw.EntryRow) -> None:
        """Named rather than a lambda so `_settle` can block it while it writes back."""
        self._manual_changed()

    def _manual_changed(self) -> None:
        text = self._manual.get_text().strip()
        self._captured = parse_trigger(text) if text else None
        self._refresh()

    def _clear(self) -> None:
        self._captured = None
        self._recorder.reset()
        self._manual.set_text("")
        self._refresh()

    def _refresh(self) -> None:
        trigger = self._captured
        if trigger is None:
            pending = self._recorder.modifier_only()
            self._shortcut.set_text(f"{pending} + …" if pending else "")
            self._problem.set_visible(False)
            self._confirm.set_sensitive(False)
            return

        self._shortcut.set_text(trigger.display())
        problem = validate_trigger(str(trigger), in_submap=self._in_submap)
        if problem is None:
            self._problem.set_visible(False)
            self._confirm.set_sensitive(True)
            return

        self._problem.set_text(problem.full_text())
        self._problem.set_visible(True)
        self._problem.set_css_classes(["error"] if problem.blocking else ["warning"])
        self._confirm.set_sensitive(not problem.blocking)

    # --- exits --------------------------------------------------------------------------

    def _accept(self) -> None:
        trigger = self._captured
        if trigger is None:
            return
        problem = validate_trigger(str(trigger), in_submap=self._in_submap)
        if problem is not None and problem.blocking:
            self._refresh()
            return
        self._restore_shortcuts()
        self._on_done(str(trigger))
        self.close()

    def _cancel(self) -> None:
        self._restore_shortcuts()
        self.close()
