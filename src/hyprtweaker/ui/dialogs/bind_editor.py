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

import re
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
from hyprtweaker.engine.triggers import (  # noqa: E402
    DeadKeys,
    parse_trigger,
    trigger_load_problem,
)
from hyprtweaker.engine.writer.binds import lua_value  # noqa: E402
from hyprtweaker.engine.writer.lua import table_key  # noqa: E402
from hyprtweaker.ui.dialogs.capture import CaptureDialog  # noqa: E402

TRIGGER_HELP = "Modifiers and one key, joined by +. For example: SUPER + SHIFT + Q"

ENABLES_NOTE = "Saving with a working key also enables this bind."
"""Shown above Save while Save would enable an Importer-disabled bind (#149's review)."""

FLAGS: tuple[tuple[str, str, str], ...] = (
    ("locked", "Works on the lock screen", ""),
    ("release", "Fires when the key is released", ""),
    ("click", "Fires on a click", "Mouse button pressed and released without moving"),
    ("drag", "Fires on a drag", "Mouse button held while the pointer moves"),
    ("repeating", "Repeats while held", ""),
    ("non_consuming", "Lets the key through to the app", ""),
    ("auto_consuming", "Consumes the key automatically", "Hyprland's auto-consuming flag"),
    ("transparent", "Does not block other binds", ""),
    ("ignore_mods", "Ignores extra modifiers", ""),
    ("long_press", "Fires on a long press", ""),
    ("dont_inhibit", "Works while shortcuts are inhibited", ""),
    ("allow_input_capture", "Works during input capture", ""),
    ("submap_universal", "Works in every submap", "Fires everywhere, not just where defined"),
)
"""The flags the editor offers, in the order they read best: every `BindOptions` flag.

`click` and `drag` imply `release` and exclude each other (ADR-0007): the editor sets
`release` for the user while either is on (`_sync_release`) and refuses the pairs in
`INCOMPATIBLE`.
"""

INCOMPATIBLE: tuple[tuple[str, str, str], ...] = (
    ("click", "drag", "Click and Drag can't both be on."),
    ("click", "repeating", "Click fires on release, so it can't repeat."),
    ("drag", "repeating", "Drag fires on release, so it can't repeat."),
    ("long_press", "repeating", "Long press can't repeat."),
    ("release", "repeating", "Release can't repeat."),
)
"""Pairs the compositor rejects (`Hyprland --verify-config`, 0.56.2), each with the words
the form shows. Enforced as the editor's own validation (ADR-0007).

Probed and accepted, so left unconstrained: `click` with `long_press`, `auto_consuming`
with `non_consuming`. `release` here is the user's own, not the one `click` or `drag` sets:
those two name themselves in their own pairs, so the message blames what the user turned on.
"""

FREE_FORM_HOW = "Type each setting as key = value, one per line."
"""Follows the dispatcher's own `free_form_reason` above the raw table."""

UNKNOWN_ACTION = "This version of the app does not know this action."
"""The raw table's reason for a saved dispatcher the catalog has never heard of."""

KEPT_TITLE = "Also kept from your config"
KEPT_NOTE = "The form has no field for these, so Save keeps them as they are."

BOOL_WORDS = {"true": True, "false": False, "yes": True, "no": False}
"""What a yes-or-no field reads, lower-cased. Anything else is refused, never guessed."""

_NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


class BindEditor(Adw.Dialog):
    """Add or edit one Bind. Calls `on_done` with the finished Bind, or never."""

    def __init__(
        self,
        *,
        on_done: Callable[[Bind], None],
        bind: Bind | None = None,
        submap: str | None = None,
    ) -> None:
        """`submap` is where a *new* bind will live (#66's per-submap add); an edited
        bind keeps the submap it already has, and the parameter is ignored."""
        super().__init__(
            title="Edit keybind" if bind else "Add keybind",
            content_width=560,
            content_height=520,
        )
        self._on_done = on_done
        self._original = bind
        self._submap = bind.submap if bind is not None else submap
        self._chosen: Dispatcher | None = None
        self._arg_entries: dict[str, Gtk.Widget] = {}
        self._kept: dict[str, object] = {}
        self._flag_switches: dict[str, Adw.SwitchRow] = {}
        self._own_release = False

        self._view = Adw.NavigationView()
        self.set_child(self._view)

        if bind is None:
            self._view.push(self._door_page())
        else:
            path = bind.dispatcher.path if bind.dispatcher else EXEC_PATH
            self._chosen = lookup(path) or Dispatcher(
                path=path, label=f"hl.dsp.{path}", free_form_reason=UNKNOWN_ACTION
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

        self._kept = self._kept_args(entry)
        box.append(self._args_group(entry))
        if self._kept:
            box.append(self._kept_group())
        box.append(self._flags_group())

        self._error = Gtk.Label(css_classes=["error"], visible=False, wrap=True)
        box.append(self._error)

        # An enable is never quiet: when Save would turn the bind on, this line says so.
        self._enables_note = Gtk.Label(label=ENABLES_NOTE, visible=False, wrap=True)
        box.append(self._enables_note)
        self._trigger.connect(
            "changed",
            lambda _row: self._enables_note.set_visible(self._enables_on_save()),
        )

        save = Gtk.Button(label="Save", css_classes=["suggested-action"], halign=Gtk.Align.END)
        save.connect("clicked", lambda _button: self._save())
        box.append(save)

        page.set_child(_dialog_body(box))
        return page

    def _args_group(self, entry: Dispatcher | None) -> Adw.PreferencesGroup:
        existing = self._original.dispatcher if self._original else None
        group = Adw.PreferencesGroup(title="Action")

        if entry is None or entry.free_form_reason is not None:
            reason = entry.free_form_reason if entry is not None else UNKNOWN_ACTION
            group.set_description(f"{reason} {FREE_FORM_HOW}")
            view = Gtk.TextView(monospace=True, top_margin=6, bottom_margin=6, left_margin=6)
            view.set_size_request(-1, 96)
            if existing is not None:
                view.get_buffer().set_text(
                    "\n".join(
                        f"{key} = {_free_text(value)}"
                        for key, value in existing.args.items()
                        if key not in self._kept
                    )
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
                if _is_scalar(current):
                    row.set_text(_field_text(current))
            self._arg_entries[spec.name] = row
            group.add(row)

        if not entry.args:
            group.set_description("This action takes no arguments.")
        return group

    def _kept_args(self, entry: Dispatcher | None) -> dict[str, object]:
        """The saved keys of this action the form has no field for, to carry through Save.

        A curated form rebuilds the call from its `ArgSpec` names, and the raw table can only
        spell scalars, so without this a hand-written `layout_aware = true` on
        `fullscreen_state`, or a nested table, would be lost on any Save, even an untouched
        one (#126 owner call 4, decided 2026-10-02). They belong to the saved action: once
        another one is picked they are not carried.
        """
        existing = self._original.dispatcher if self._original else None
        if existing is None or entry is None or entry.path != existing.path:
            return {}
        if entry.positional:
            return {}
        fields = (
            None if entry.free_form_reason is not None else {spec.name for spec in entry.args}
        )
        return {
            key: value
            for key, value in existing.args.items()
            if not _is_scalar(value) or (fields is not None and key not in fields)
        }

    def _kept_group(self) -> Adw.PreferencesGroup:
        """The kept keys, read-only, one `key = value` line each, as the file spells them."""
        group = Adw.PreferencesGroup(title=KEPT_TITLE, description=KEPT_NOTE)
        for key, value in self._kept.items():
            row = Adw.ActionRow(
                title=f"{table_key(key)}{lua_value(value)}",
                use_markup=False,
                css_classes=["monospace"],
            )
            group.add(row)
        return group

    def _flags_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Options")
        options = self._original.options if self._original else BindOptions()
        for name, title, subtitle in FLAGS:
            row = Adw.SwitchRow(title=title, subtitle=subtitle or None)
            row.set_active(bool(getattr(options, name)))
            self._flag_switches[name] = row
            group.add(row)
        # The Importer sets `release` on every click and drag bind, so on those it is the
        # flag's doing, not the user's: their own value starts off.
        self._own_release = options.release and not (options.click or options.drag)
        release = self._flag_switches["release"]
        release.connect("notify::active", self._release_toggled)
        for name in ("click", "drag"):
            self._flag_switches[name].connect("notify::active", lambda *_: self._sync_release())
        self._sync_release()
        return group

    def _release_toggled(self, row: Adw.SwitchRow, _pspec: object) -> None:
        """Remember what the user chose. The row is insensitive while click or drag locks
        it on, so a locked toggle is `_sync_release`'s and not remembered."""
        if row.get_sensitive():
            self._own_release = row.get_active()

    def _sync_release(self) -> None:
        """Show `release` as click or drag leave it: on and locked while either is on,
        the user's own value, visibly, once both are off."""
        release = self._flag_switches["release"]
        implied = next(
            (
                title
                for name, title in (("click", "Click"), ("drag", "Drag"))
                if self._flag_switches[name].get_active()
            ),
            None,
        )
        if implied:
            release.set_sensitive(False)
            release.set_active(True)
            release.set_subtitle(f"Set by {implied}")
        else:
            release.set_sensitive(True)
            release.set_active(self._own_release)
            release.set_subtitle("")

    def _flags_the_user_chose(self) -> set[str]:
        """The flags on, with `release` as the user's own: click and drag name themselves in
        their refusals rather than as a release the user never touched."""
        on = {name for name, row in self._flag_switches.items() if row.get_active()}
        on.discard("release")
        return on | ({"release"} if self._own_release else set())

    # --- saving ---------------------------------------------------------------------------

    def _collect_args(self) -> tuple[dict[str, object], tuple[object, ...]]:
        entry = self._chosen
        if entry is None or entry.free_form_reason is not None:
            view = self._arg_entries.get("__free__")
            if view is None:
                return self._with_kept({}), ()
            buffer = view.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            args: dict[str, object] = {}
            for line in text.splitlines():
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                args[key.strip()] = _coerce(value.strip())
            return self._with_kept(args), ()

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
        return self._with_kept(values), ()

    def _with_kept(self, values: dict[str, object]) -> dict[str, object]:
        """`values` plus the kept keys, in the saved call's key order; a typed key wins."""
        if not self._kept or self._original is None or self._original.dispatcher is None:
            return values
        merged = {**self._kept, **values}
        rank = {key: index for index, key in enumerate(self._original.dispatcher.args)}
        return dict(sorted(merged.items(), key=lambda item: rank.get(item[0], len(rank))))

    def _in_submap(self) -> bool:
        """Whether `catchall` is a legal Trigger for this bind.

        `submap_universal` counts: a universal bind fires inside submaps, which is where
        `catchall` lives -- and the Capture launched from the conflict popover's rebind
        already treats it that way, so this form must agree with it.
        """
        universal = self._flag_switches.get("submap_universal")
        return bool(self._submap) or bool(universal is not None and universal.get_active())

    def _capture(self) -> None:
        """Open Capture, prefilled with whatever is typed, and take back what it records."""
        dialog = CaptureDialog(
            on_done=self._trigger.set_text,
            initial=self._trigger.get_text(),
            in_submap=self._in_submap(),
        )
        dialog.present(self)

    def _enables_on_save(self) -> bool:
        """Whether Save turns the bind on: the Importer disabled it for a dead key, and the
        trigger now names working keys.

        The Importer's disable is not the user's choice, so a working key undoes it, as
        "Fix trigger…" on the row does. A bind disabled with a working trigger (the conflict
        surface's disable) is the user's choice and stays off. Without an xkb validator no
        key reads as dead (`trigger_load_problem`), so nothing here enables: it fails safe.
        """
        original = self._original
        if original is None or original.enabled:
            return False
        if not isinstance(trigger_load_problem(original.keys), DeadKeys):
            return False
        return trigger_load_problem(str(parse_trigger(self._trigger.get_text()))) is None

    def _validate(self) -> str:
        trigger = self._trigger.get_text().strip()
        if not trigger:
            return "A keybind needs a trigger."
        # Typed triggers get the same hard block Capture applies, through the one rule the
        # Session enforces (`trigger_load_problem`). A dead keysym reaching the writer is
        # not a cosmetic problem: Lua fails the whole config on it, and the compositor
        # gives no error to find it by (ADR-0007). The one exception is a disabled bind
        # whose trigger this edit left alone, such as a dead keysym the Importer disabled
        # (#108): the Writer keeps a disabled bind commented out, so nothing dead reaches
        # the compositor, and blocking would mean the user cannot fix the description until
        # they have fixed the key.
        problem = trigger_load_problem(str(parse_trigger(trigger)))
        original = self._original
        untouched_and_disabled = (
            original is not None
            and not original.enabled
            and parse_trigger(trigger) == parse_trigger(original.keys)
        )
        if problem is not None and not untouched_and_disabled:
            return problem.message
        chosen = self._flags_the_user_chose()
        for left, right, message in INCOMPATIBLE:
            if left in chosen and right in chosen:
                return message
        entry = self._chosen
        if entry is not None and entry.free_form_reason is None:
            for spec in entry.args:
                row = self._arg_entries.get(spec.name)
                text = row.get_text().strip() if isinstance(row, Adw.EntryRow) else ""
                if text and (refusal := _type_refusal(spec.title(), spec.type, text)):
                    return refusal
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
        # this dialog does not show. `device` is one: building the options from the switches
        # alone would delete it the first time a user touched an unrelated flag -- exactly
        # the silent overwrite ADR-0007 forbids. `release` is saved as the switch shows it:
        # on for the user's own choice and for a click or drag, which imply it.
        # `origin` is carried for the same reason: it is where the bind came from, and
        # this edit does not move it.
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
                submap=self._submap,
                # Editing must not quietly re-enable a bind the conflict surface
                # disabled -- `enabled` is list state, not something this form shows. The
                # one enable is a dead key fixed, and `_enables_note` says so above Save.
                enabled=(self._original.enabled if self._original else True)
                or self._enables_on_save(),
                origin=self._original.origin if self._original else "",
            )
        )
        self.close()


def _type_refusal(title: str, arg_type: str, text: str) -> str:
    """Why `text` cannot be the field's type, or "" when it can.

    Refused rather than guessed: `forward` typed into "Forwards" once saved as
    `next = false`, the opposite of what was meant (#150 review, finding 11).
    """
    if arg_type == "bool" and text.lower() not in BOOL_WORDS:
        return f"{title} must be true or false."
    if arg_type == "int" and not re.fullmatch(r"-?\d+", text):
        return f"{title} must be a whole number."
    return ""


def _typed(text: str, arg_type: str) -> object:
    """One form field's text as the type the catalog says the dispatcher wants.

    `_validate` has refused any text the type cannot take, so the fallback to the string
    as typed is only a guard: silently substituting `0` or `false` for what someone wrote
    would emit a bind that works and does the wrong thing.
    """
    if arg_type == "int":
        try:
            return int(text)
        except ValueError:
            return text
    if arg_type == "bool":
        return BOOL_WORDS.get(text.lower(), text)
    return text


def _is_scalar(value: object) -> bool:
    """A value one text field can show and read back: not a table, not missing."""
    return isinstance(value, bool | int | float | str)


def _field_text(value: object) -> str:
    """A saved scalar as a typed form field shows it: Lua's `true`, never Python's `True`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _free_text(value: object) -> str:
    """A saved argument as the raw table shows it, such that `_coerce` reads back the same.

    `str(False)` is `False`, which comes back as the string "False", and an unquoted `"3"`
    comes back as the integer 3; either changes what Hyprland is told to do on Save.
    """
    if isinstance(value, str) and _coerce(value) != value:
        return f'"{value}"'
    return _field_text(value)


def _coerce(text: str) -> object:
    """A free-form value as the closest Lua-ish type, falling back to the string.

    Quoted stays a string on purpose: a user who wrote `"1"` meant the string, and the
    round-trip through `binds.lua` has to give them back what they typed.
    """
    if text[:1] in {'"', "'"} and text[-1:] == text[:1] and len(text) >= 2:
        return text[1:-1]
    if text in {"true", "false"}:
        return text == "true"
    if _NUMBER.fullmatch(text):
        return int(text) if text.lstrip("-").isdigit() else float(text)
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
