"""Adding and editing a Bind: the two-door flow (ADR-0007).

**Two doors, not one picker.** "Run command" is its own door because exec is the majority
bind type in every corpus rice -- burying it behind a list of 51 dispatchers would put the
common case one extra decision deep to buy consistency nobody asked for. The second door,
"Hyprland action", is the dispatcher picker grouped by namespace.

**Trigger entry is text here.** Recording a Trigger from real input is Capture (#65), which
needs shortcut inhibition and held-modifier tracking to do properly. Until it lands this
dialog takes the canonical `"SUPER + SHIFT + Q"` string, which is what `hl.bind` takes and
what the file already shows.

The dialog never writes. It hands a finished `Bind` back and the caller decides where it
goes in the list -- because position is identity, and only the list knows the position.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.dispatchers import (  # noqa: E402
    EXEC_PATH,
    NAMESPACE_LABELS,
    Dispatcher,
    lookup,
    namespaces,
)
from hyprtweaker.engine.model.entities import Bind, BindOptions, DispatcherCall  # noqa: E402
from hyprtweaker.engine.triggers import parse_trigger, validate_trigger  # noqa: E402
from hyprtweaker.ui.dialogs.capture import CaptureDialog  # noqa: E402

TRIGGER_HELP = "Modifiers and one key, joined by +. For example: SUPER + SHIFT + Q"

FLAGS: tuple[tuple[str, str, str], ...] = (
    ("locked", "Works on the lock screen", ""),
    ("release", "Fires when the key is released", ""),
    ("repeating", "Repeats while held", ""),
    ("non_consuming", "Lets the key through to the app", ""),
    ("transparent", "Does not block other binds", ""),
    ("ignore_mods", "Ignores extra modifiers", ""),
    ("long_press", "Fires on a long press", ""),
    ("dont_inhibit", "Works while shortcuts are inhibited", ""),
    ("allow_input_capture", "Works during input capture", ""),
)
"""The flags the editor offers, in the order they read best.

Not the whole of `BindOptions`: `click` and `drag` imply `release` and are mutually
exclusive with it (ADR-0007), `submap_universal` belongs to the Submap editor (#66), and
`auto_consuming` is absent from the stub though the code parses it. Those need constraint
handling rather than a switch, and a switch that silently produced an invalid combination
would be worse than not offering it yet.
"""

INCOMPATIBLE = (("long_press", "repeating"), ("release", "repeating"))
"""Pairs the compositor rejects. Enforced as the editor's own validation (ADR-0007)."""


class BindEditor(Adw.Dialog):
    """Add or edit one Bind. Calls `on_done` with the finished Bind, or never."""

    def __init__(
        self,
        *,
        on_done: Callable[[Bind], None],
        bind: Bind | None = None,
    ) -> None:
        super().__init__(
            title="Edit keybind" if bind else "Add keybind",
            content_width=560,
            content_height=520,
        )
        self._on_done = on_done
        self._original = bind
        self._chosen: Dispatcher | None = None
        self._arg_entries: dict[str, Gtk.Widget] = {}
        self._flag_switches: dict[str, Adw.SwitchRow] = {}

        self._view = Adw.NavigationView()
        self.set_child(self._view)

        if bind is None:
            self._view.push(self._door_page())
        else:
            path = bind.dispatcher.path if bind.dispatcher else EXEC_PATH
            self._chosen = lookup(path) or Dispatcher(
                path=path, label=f"hl.dsp.{path}", free_form=True
            )
            self._view.push(self._form_page())

    # --- door 1: which kind of action -----------------------------------------------------

    def _door_page(self) -> Adw.NavigationPage:
        page = Adw.NavigationPage(title="Add keybind")
        group = Adw.PreferencesGroup(
            title="What should this key do?",
            description="Most keybinds run a command.",
        )

        run = Adw.ActionRow(
            title="Run a command",
            subtitle="Launch a terminal, a launcher, a script",
            activatable=True,
        )
        run.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        run.connect("activated", lambda _row: self._choose(lookup(EXEC_PATH)))
        group.add(run)

        action = Adw.ActionRow(
            title="Hyprland action",
            subtitle="Close a window, switch workspace, and everything else",
            activatable=True,
        )
        action.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        action.connect("activated", lambda _row: self._view.push(self._picker_page()))
        group.add(action)

        page.set_child(_dialog_body(group))
        return page

    # --- door 2: the dispatcher picker ----------------------------------------------------

    def _picker_page(self) -> Adw.NavigationPage:
        page = Adw.NavigationPage(title="Choose an action")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        for name, entries in namespaces().items():
            group = Adw.PreferencesGroup(title=NAMESPACE_LABELS.get(name, name or "General"))
            for entry in entries:
                if entry.path == EXEC_PATH:
                    continue  # its own door
                row = Adw.ActionRow(title=entry.label, subtitle=f"hl.dsp.{entry.path}")
                row.set_activatable(True)
                row.connect("activated", lambda _row, e=entry: self._choose(e))
                group.add(row)
            box.append(group)

        page.set_child(_dialog_body(box))
        return page

    def _choose(self, entry: Dispatcher | None) -> None:
        self._chosen = entry
        self._view.push(self._form_page())

    # --- the form -------------------------------------------------------------------------

    def _form_page(self) -> Adw.NavigationPage:
        entry = self._chosen
        page = Adw.NavigationPage(title=entry.label if entry else "Keybind")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        trigger_group = Adw.PreferencesGroup(title="Trigger", description=TRIGGER_HELP)
        self._trigger = Adw.EntryRow(title="Keys")
        if self._original is not None:
            self._trigger.set_text(self._original.keys)
        capture = Gtk.Button(
            icon_name="input-keyboard-symbolic",
            tooltip_text="Press the keys instead of typing them",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        capture.connect("clicked", lambda _button: self._capture())
        self._trigger.add_suffix(capture)
        trigger_group.add(self._trigger)

        self._description = Adw.EntryRow(title="Description (optional)")
        if self._original is not None:
            self._description.set_text(self._original.options.description)
        trigger_group.add(self._description)
        box.append(trigger_group)

        box.append(self._args_group(entry))
        box.append(self._flags_group())

        self._error = Gtk.Label(css_classes=["error"], visible=False, wrap=True)
        box.append(self._error)

        save = Gtk.Button(label="Save", css_classes=["suggested-action"], halign=Gtk.Align.END)
        save.connect("clicked", lambda _button: self._save())
        box.append(save)

        page.set_child(_dialog_body(box))
        return page

    def _args_group(self, entry: Dispatcher | None) -> Adw.PreferencesGroup:
        existing = self._original.dispatcher if self._original else None
        group = Adw.PreferencesGroup(title="Action")

        if entry is None or entry.free_form:
            group.set_description(
                "This action's arguments are not documented in a form this app can "
                "generate, so they are entered as Lua-style key = value pairs, one per line."
            )
            view = Gtk.TextView(monospace=True, top_margin=6, bottom_margin=6, left_margin=6)
            view.set_size_request(-1, 96)
            if existing is not None:
                view.get_buffer().set_text(
                    "\n".join(f"{key} = {value}" for key, value in existing.args.items())
                )
            frame = Gtk.Frame(child=view)
            group.add(frame)
            self._arg_entries = {"__free__": view}
            return group

        self._arg_entries = {}
        for spec in entry.args:
            row = Adw.EntryRow(title=spec.title())
            if spec.placeholder:
                row.set_tooltip_text(spec.placeholder)
            if existing is not None:
                current = existing.args.get(spec.name)
                if current is None and existing.positional:
                    current = existing.positional[0]
                if current is not None:
                    row.set_text(str(current))
            self._arg_entries[spec.name] = row
            group.add(row)

        if not entry.args:
            group.set_description("This action takes no arguments.")
        return group

    def _flags_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Options")
        options = self._original.options if self._original else BindOptions()
        for name, title, subtitle in FLAGS:
            row = Adw.SwitchRow(title=title, subtitle=subtitle or None)
            row.set_active(bool(getattr(options, name)))
            self._flag_switches[name] = row
            group.add(row)
        return group

    # --- saving ---------------------------------------------------------------------------

    def _collect_args(self) -> tuple[dict[str, object], tuple[object, ...]]:
        entry = self._chosen
        if entry is None or entry.free_form:
            view = self._arg_entries.get("__free__")
            if view is None:
                return {}, ()
            buffer = view.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            args: dict[str, object] = {}
            for line in text.splitlines():
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                args[key.strip()] = _coerce(value.strip())
            return args, ()

        # Typed, not stringly: `ArgSpec.type` exists so an int argument reaches Lua as `9`
        # rather than `"9"`. The catalog carries the type precisely because the compositor
        # checks it -- emitting the wrong one is a config error, not a coercion.
        specs = {spec.name: spec for spec in entry.args}
        values: dict[str, object] = {}
        for name, row in self._arg_entries.items():
            if not isinstance(row, Adw.EntryRow):
                continue
            text = row.get_text().strip()
            if not text:
                continue
            values[name] = _typed(text, specs[name].type if name in specs else "string")

        if entry.positional:
            first = entry.args[0].name if entry.args else ""
            return {}, (values[first],) if first in values else ()
        return values, ()

    def _in_submap(self) -> bool:
        return bool(self._original.submap) if self._original else False

    def _capture(self) -> None:
        """Open Capture, prefilled with whatever is typed, and take back what it records."""
        dialog = CaptureDialog(
            on_done=self._trigger.set_text,
            initial=self._trigger.get_text(),
            in_submap=self._in_submap(),
        )
        dialog.present(self)

    def _validate(self) -> str:
        trigger = self._trigger.get_text().strip()
        if not trigger:
            return "A keybind needs a trigger."
        # Typed triggers get the same hard block Capture applies. A dead keysym reaching
        # the writer is not a cosmetic problem: Lua fails the whole config on it, and the
        # compositor gives no error to find it by (ADR-0007).
        problem = validate_trigger(trigger, in_submap=self._in_submap())
        if problem is not None and problem.blocking:
            return problem.full_text()
        for left, right in INCOMPATIBLE:
            if (
                self._flag_switches[left].get_active()
                and self._flag_switches[right].get_active()
            ):
                return f"{left} and {right} cannot both be set."
        entry = self._chosen
        if entry is not None and not entry.free_form:
            args, positional = self._collect_args()
            for spec in entry.args:
                if spec.required and spec.name not in args and not positional:
                    return f"{spec.title()} is required."
        return ""

    def _save(self) -> None:
        if problem := self._validate():
            self._error.set_text(problem)
            self._error.set_visible(True)
            return

        entry = self._chosen
        args, positional = self._collect_args()
        path = entry.path if entry else EXEC_PATH

        # `replace` rather than a fresh `BindOptions`, so editing a bind keeps the fields
        # this dialog does not show. `device`, `auto_consuming`, `click`, `drag` and
        # `submap_universal` all belong to binds this app can import but not yet edit
        # (#105, #66), and building the options from the switches alone would delete them
        # the first time a user touched an unrelated flag -- exactly the silent overwrite
        # ADR-0007 forbids. `origin` is carried for the same reason: it is where the bind
        # came from, and this edit does not move it.
        base = self._original.options if self._original else BindOptions()
        options = replace(
            base,
            description=self._description.get_text().strip(),
            **{name: row.get_active() for name, row in self._flag_switches.items()},
        )

        self._on_done(
            Bind(
                # Canonicalised on the way out: ADR-0007 requires the emitted string be
                # the canonical `"SUPER + SHIFT + Q"` spelling, so `win + q` typed by hand
                # becomes `SUPER + q` rather than reaching the writer as the user spelled
                # it. Hyprland matches modifier names case-sensitively, so passing an
                # alias through verbatim is a bind that silently does not fire.
                keys=str(parse_trigger(self._trigger.get_text().strip())),
                dispatcher=DispatcherCall(path=path, args=args, positional=positional),
                options=options,
                submap=self._original.submap if self._original else None,
                origin=self._original.origin if self._original else "",
            )
        )
        self.close()


def _typed(text: str, arg_type: str) -> object:
    """One form field's text as the type the catalog says the dispatcher wants.

    A field the user left in a shape the type cannot take comes back as the string they
    typed rather than raising: `_validate` has already run, and silently substituting `0`
    for what someone wrote would emit a bind that works and does the wrong thing.
    """
    if arg_type == "int":
        try:
            return int(text)
        except ValueError:
            return text
    if arg_type == "bool":
        return text.strip().lower() in {"true", "yes", "1"}
    return text


def _coerce(text: str) -> object:
    """A free-form value as the closest Lua-ish type, falling back to the string.

    Quoted stays a string on purpose: a user who wrote `"1"` meant the string, and the
    round-trip through `binds.lua` has to give them back what they typed.
    """
    if text[:1] in {'"', "'"} and text[-1:] == text[:1] and len(text) >= 2:
        return text[1:-1]
    if text in {"true", "false"}:
        return text == "true"
    try:
        return int(text)
    except ValueError:
        return text


def _dialog_body(child: Gtk.Widget) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    header = Adw.HeaderBar()
    box.append(header)
    scroller = Gtk.ScrolledWindow(vexpand=True)
    clamp = Adw.Clamp(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    clamp.set_child(child)
    scroller.set_child(clamp)
    box.append(scroller)
    return box
