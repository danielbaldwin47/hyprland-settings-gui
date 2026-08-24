"""The Rule editor: Match pickers, Effect pickers, and the two live pickers (ADR-0008).

One dialog for both rule kinds -- "same list model and editor shell" -- parameterised the
way the Page is. Calls `on_done` with the finished Rule, or never; the dialog itself
writes nothing.

**Match group.** One typed row per added prop -- regex and selector entries, bool
switches, int spins -- each string-valued row with a "Not" toggle that spells the
`negative:` prefix without making the user type it. At least one prop is required, and
the add-prop picker only offers props not already present (one row per prop; a rule
that names one twice is a rule fighting itself).

**Effects group.** Rows generated from the entity catalog (`rules_catalog`), the picker
shelved by category. An effect the catalog does not know -- a plugin's, or a newer
Hyprland's -- shows as a raw custom row and passes through *unedited by identity*: its
value object is only replaced when the user actually changes the text, so a table-valued
effect survives an unrelated edit byte-for-byte (ADR-0008: "never dropped").

**Pick a window / Pick a layer.** Prefills a Match from `hyprctl -j clients` (or the
layer namespaces) -- helper data only, thrown away after the prefill, and the button
simply is not offered when no compositor is answering (manual entry is the fallback).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.model.entities import LayerRule, WindowRule  # noqa: E402
from hyprtweaker.engine.rules_catalog import (  # noqa: E402
    CATEGORIES,
    NEGATABLE_KINDS,
    NEGATIVE_PREFIX,
    Effect,
    EffectType,
    MatchKind,
    MatchProp,
    effects,
    find_effect,
    find_match_prop,
    is_negated,
    match_props,
    prop_title,
    strip_negation,
)

Rule = WindowRule | LayerRule

Fetch = Callable[[Callable[[tuple[Mapping[str, Any], ...] | None], None]], None]
"""How the dialog asks for live windows or layers: a callable that calls back."""

_SPIN_BOUNDS: dict[str, tuple[float, float]] = {
    "rounding": (0, 20),
    "rounding_power": (1, 10),
    "scroll_mouse": (0.01, 10),
    "scroll_touchpad": (0.01, 10),
    "border_size": (0, 99),
    "no_close_for": (0, 100000),
    "scrolling_width": (0, 100000),
    "ignore_alpha": (0, 1),
    "order": (-100, 100),
    "above_lock": (0, 2),
    "fullscreen_state_internal": (0, 3),
    "fullscreen_state_client": (0, 3),
}
"""Bounds the API documents (`lua-api-surface.md` §5); everything else gets a wide spin."""

_DEFAULT_INT_BOUNDS = (0.0, 100000.0)


@dataclass(frozen=True, slots=True)
class _EffectRow:
    """One effect row's bookkeeping: the key, its typed spec (or `None` for raw), the
    editing widget, and the value it opened with -- the identity half of the unknown-
    effect pass-through."""

    name: str
    spec: Effect | None
    widget: Gtk.Widget
    original: Any


class RuleEditor(Adw.Dialog):
    """Add or edit one window or layer rule."""

    def __init__(
        self,
        *,
        kind: str,
        on_done: Callable[[Rule], None],
        rule: Rule | None = None,
        taken_names: Sequence[str] = (),
        fetch_targets: Fetch | None = None,
    ) -> None:
        """`taken_names` are other rules' Labels -- a duplicate Label would silently merge
        on the next read-back (`_merge_named`), so the editor refuses it up front.
        `fetch_targets` answers with live windows (window kind) or layer surfaces (layer
        kind); `None` means nobody to ask, and the pick button is not offered."""
        noun = "window rule" if kind == "window" else "layer rule"
        super().__init__(
            title=f"Edit {noun}" if rule is not None else f"Add {noun}",
            content_width=600,
            content_height=640,
        )
        self._kind = kind
        self._on_done = on_done
        self._original = rule
        self._taken = tuple(name for name in taken_names if name)
        self._fetch = fetch_targets

        # One ordered list per group, so the collected dict keeps the rule's own order.
        self._match_rows: dict[str, tuple[MatchProp, Gtk.Widget, Gtk.ToggleButton | None]] = {}
        self._effect_entries: list[_EffectRow] = []

        # The pick-page opt-ins, built up front so `prefill_from_window` never has to ask
        # whether the picker page has been opened yet (window kind only; layer picking
        # sets nothing but the namespace).
        self._pick_title = Adw.SwitchRow(title="Title")
        self._pick_initial_class = Adw.SwitchRow(title="Initial class")
        self._pick_initial_title = Adw.SwitchRow(title="Initial title")
        self._pick_xwayland = Adw.SwitchRow(title="XWayland")

        self._view = Adw.NavigationView()
        self.set_child(self._view)
        self._view.push(self._form_page())

        if rule is not None:
            for name, value in rule.match.items():
                prop = find_match_prop(kind, name) or MatchProp(name, MatchKind.REGEX)
                self._add_match_row(prop, value)
            for name, value in rule.effects.items():
                self._add_effect_row(name, value)

    # --- the form -------------------------------------------------------------------------

    def _form_page(self) -> Adw.NavigationPage:
        noun = "window rule" if self._kind == "window" else "layer rule"
        page = Adw.NavigationPage(title=f"Edit {noun}", tag="form")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        label_group = Adw.PreferencesGroup(
            title="Label",
            description=(
                "Optional. A labeled rule reads better in the file and can be toggled "
                "at runtime."
            ),
        )
        self._label_entry = Adw.EntryRow(title="Label")
        if self._original is not None and self._original.name:
            self._label_entry.set_text(self._original.name)
        label_group.add(self._label_entry)
        box.append(label_group)

        self._match_group = Adw.PreferencesGroup(
            title="Match",
            description="Which windows this rule applies to. At least one is required."
            if self._kind == "window"
            else "Which layer surfaces this rule applies to.",
        )
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        if self._fetch is not None:
            pick = Gtk.Button(icon_name="find-location-symbolic", valign=Gtk.Align.CENTER)
            pick.add_css_class("flat")
            pick.set_tooltip_text("Pick a window" if self._kind == "window" else "Pick a layer")
            pick.connect("clicked", lambda _button: self._open_picker())
            header.append(pick)
        add = Gtk.MenuButton(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text("Add a match")
        add.set_popover(self._match_popover())
        header.append(add)
        self._match_group.set_header_suffix(header)
        box.append(self._match_group)

        self._effects_group = Adw.PreferencesGroup(
            title="Effects",
            description="What the rule applies. Later rules win when they set the same effect.",
        )
        add_effect = Gtk.MenuButton(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add_effect.add_css_class("flat")
        add_effect.set_tooltip_text("Add an effect")
        add_effect.set_popover(self._effect_popover())
        self._effects_group.set_header_suffix(add_effect)
        box.append(self._effects_group)

        self._error = Gtk.Label(css_classes=["error"], wrap=True, visible=False)
        box.append(self._error)

        save = Gtk.Button(label="Save", halign=Gtk.Align.END, css_classes=["suggested-action"])
        save.connect("clicked", lambda _button: self._save())
        box.append(save)

        page.set_child(_dialog_body(box))
        return page

    # --- pickers (the popover kind) -------------------------------------------------------

    def _match_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        listbox = Gtk.ListBox(css_classes=["boxed-list"])
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        for prop in match_props(self._kind):
            row = Adw.ActionRow(title=prop_title(prop.name), activatable=True)
            row.connect("activated", self._on_add_match, prop, popover)
            listbox.append(row)
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=360)
        scroller.set_child(listbox)
        popover.set_child(scroller)
        return popover

    def _on_add_match(self, _row: Adw.ActionRow, prop: MatchProp, popover: Gtk.Popover) -> None:
        popover.popdown()
        if prop.name not in self._match_rows:
            self._add_match_row(prop, None)

    def _effect_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        catalog = effects(self._kind)
        for category in CATEGORIES:
            shelf = [effect for effect in catalog if effect.category == category]
            if not shelf:
                continue
            box.append(
                Gtk.Label(label=category, halign=Gtk.Align.START, css_classes=["heading"])
            )
            listbox = Gtk.ListBox(css_classes=["boxed-list"])
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            for effect in shelf:
                row = Adw.ActionRow(title=prop_title(effect.name), activatable=True)
                row.connect("activated", self._on_add_effect, effect, popover)
                listbox.append(row)
            box.append(listbox)

        box.append(
            Gtk.Label(label="Custom effect", halign=Gtk.Align.START, css_classes=["heading"])
        )
        custom = Gtk.Entry(placeholder_text="plugin:effect name")
        custom.connect("activate", self._on_add_custom_effect, popover)
        box.append(custom)

        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=420)
        scroller.set_child(box)
        popover.set_child(scroller)
        return popover

    def _on_add_effect(self, _row: Adw.ActionRow, effect: Effect, popover: Gtk.Popover) -> None:
        popover.popdown()
        if not any(row.name == effect.name for row in self._effect_entries):
            default: Any = True if effect.type is EffectType.BOOL else None
            self._add_effect_row(effect.name, default)

    def _on_add_custom_effect(self, entry: Gtk.Entry, popover: Gtk.Popover) -> None:
        name = entry.get_text().strip()
        entry.set_text("")
        popover.popdown()
        if name and not any(row.name == name for row in self._effect_entries):
            self._add_effect_row(name, None)

    # --- rows -----------------------------------------------------------------------------

    def _add_match_row(self, prop: MatchProp, value: Any) -> None:
        negate: Gtk.ToggleButton | None = None
        widget: Gtk.Widget

        if prop.kind is MatchKind.BOOL:
            switch = Adw.SwitchRow(title=prop_title(prop.name))
            switch.set_active(bool(value) if value is not None else True)
            widget = switch
        elif prop.kind is MatchKind.INT:
            low, high = _SPIN_BOUNDS.get(prop.name, _DEFAULT_INT_BOUNDS)
            spin = Adw.SpinRow.new_with_range(low, high, 1)
            spin.set_title(prop_title(prop.name))
            if value is not None:
                spin.set_value(float(value))
            widget = spin
        else:
            entry = Adw.EntryRow(title=prop_title(prop.name))
            if value is not None:
                entry.set_text(strip_negation(str(value)))
            if prop.kind in NEGATABLE_KINDS:
                negate = Gtk.ToggleButton(label="Not", valign=Gtk.Align.CENTER)
                negate.add_css_class("flat")
                negate.set_tooltip_text(
                    f"Match windows that do not match this (the {NEGATIVE_PREFIX} prefix)"
                )
                negate.set_active(is_negated(value))
                entry.add_suffix(negate)
            widget = entry

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove this match")
        remove.connect("clicked", self._on_remove_match, prop.name, widget)
        _row_add_suffix(widget, remove)

        self._match_rows[prop.name] = (prop, widget, negate)
        self._match_group.add(widget)

    def _on_remove_match(self, _button: Gtk.Button, name: str, widget: Gtk.Widget) -> None:
        self._match_rows.pop(name, None)
        self._match_group.remove(widget)

    def _add_effect_row(self, name: str, value: Any) -> None:
        spec = find_effect(self._kind, name)
        widget: Gtk.Widget

        if spec is not None and spec.type is EffectType.BOOL:
            switch = Adw.SwitchRow(title=prop_title(name))
            switch.set_active(bool(value) if value is not None else True)
            widget = switch
        elif spec is not None and spec.type in (EffectType.INT, EffectType.FLOAT):
            low, high = _SPIN_BOUNDS.get(name, _DEFAULT_INT_BOUNDS)
            step = 1 if spec.type is EffectType.INT else 0.05
            spin = Adw.SpinRow.new_with_range(low, high, step)
            spin.set_title(prop_title(name))
            if spec.type is EffectType.FLOAT:
                spin.set_digits(2)
            if value is not None:
                with suppress(TypeError, ValueError):
                    spin.set_value(float(value))
            widget = spin
        else:
            # A typed string effect, or an unknown/plugin effect shown raw. Either way the
            # text is the value; for the unknown kind the original object is kept beside it
            # so an untouched row round-trips by identity, not through a string.
            entry = Adw.EntryRow(title=name if spec is None else prop_title(name))
            if value is not None:
                entry.set_text(_effect_text(value))
            widget = entry

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove this effect")
        remove.connect("clicked", self._on_remove_effect, name, widget)
        _row_add_suffix(widget, remove)

        self._effect_entries.append(
            _EffectRow(name=name, spec=spec, widget=widget, original=value)
        )
        self._effects_group.add(widget)

    def _on_remove_effect(self, _button: Gtk.Button, name: str, widget: Gtk.Widget) -> None:
        self._effect_entries = [row for row in self._effect_entries if row.widget is not widget]
        self._effects_group.remove(widget)

    # --- pick a window / pick a layer -----------------------------------------------------

    def _open_picker(self) -> None:
        if self._fetch is None:
            return
        title = "Pick a window" if self._kind == "window" else "Pick a layer"
        page = Adw.NavigationPage(title=title, tag="picker")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        if self._kind == "window":
            options = Adw.PreferencesGroup(
                title="Also match",
                description="The class is always used. Add more to narrow the match.",
            )
            for row in (
                self._pick_title,
                self._pick_initial_class,
                self._pick_initial_title,
                self._pick_xwayland,
            ):
                options.add(row)
            box.append(options)

        self._picker_group = Adw.PreferencesGroup(
            title="Open windows" if self._kind == "window" else "Layer surfaces"
        )
        self._picker_rows: list[Gtk.Widget] = []
        waiting = Adw.ActionRow(title="Asking Hyprland…")
        self._picker_group.add(waiting)
        self._picker_rows.append(waiting)
        box.append(self._picker_group)

        page.set_child(_dialog_body(box))
        self._view.push(page)
        self._fetch(self._on_targets)

    def _on_targets(self, payload: tuple[Mapping[str, Any], ...] | None) -> None:
        for row in self._picker_rows:
            self._picker_group.remove(row)
        self._picker_rows = []

        def add_row(row: Gtk.Widget) -> None:
            self._picker_group.add(row)
            self._picker_rows.append(row)

        if payload is None:
            add_row(
                Adw.ActionRow(
                    title="Hyprland is not answering",
                    subtitle="Enter the match by hand instead.",
                )
            )
            return

        if self._kind == "window":
            for info in payload:
                cls = str(info.get("class", ""))
                if not cls:
                    continue
                row = Adw.ActionRow(
                    title=cls, subtitle=str(info.get("title", "")), activatable=True
                )
                row.connect("activated", self._on_pick_window, dict(info))
                add_row(row)
            if not self._picker_rows:
                add_row(Adw.ActionRow(title="No windows are open"))
        else:
            seen: set[str] = set()
            for info in payload:
                namespace = str(info.get("namespace", ""))
                if not namespace or namespace in seen:
                    continue
                seen.add(namespace)
                row = Adw.ActionRow(title=namespace, activatable=True)
                row.connect("activated", self._on_pick_layer, namespace)
                add_row(row)
            if not self._picker_rows:
                add_row(Adw.ActionRow(title="No layer surfaces found"))

    def prefill_from_window(self, info: Mapping[str, Any]) -> None:
        """Prefill the Match from one `clients` entry -- exact, escaped (ADR-0008).

        Public so the smoke tier can drive it without a popover: the same method the
        picker row activates.
        """
        if self._kind != "window":
            return
        self._set_match_text("class", _exact(str(info.get("class", ""))))
        if self._pick_title.get_active():
            self._set_match_text("title", _exact(str(info.get("title", ""))))
        if self._pick_initial_class.get_active():
            self._set_match_text("initial_class", _exact(str(info.get("initialClass", ""))))
        if self._pick_initial_title.get_active():
            self._set_match_text("initial_title", _exact(str(info.get("initialTitle", ""))))
        if self._pick_xwayland.get_active():
            self._set_match_bool("xwayland", bool(info.get("xwayland", False)))

    def prefill_from_layer(self, namespace: str) -> None:
        self._set_match_text("namespace", _exact(namespace))

    def _on_pick_window(self, _row: Adw.ActionRow, info: dict[str, Any]) -> None:
        self.prefill_from_window(info)
        self._view.pop()

    def _on_pick_layer(self, _row: Adw.ActionRow, namespace: str) -> None:
        self.prefill_from_layer(namespace)
        self._view.pop()

    def _set_match_text(self, name: str, text: str) -> None:
        if name not in self._match_rows:
            prop = find_match_prop(self._kind, name) or MatchProp(name, MatchKind.REGEX)
            self._add_match_row(prop, text)
            return
        _, widget, negate = self._match_rows[name]
        if isinstance(widget, Adw.EntryRow):
            widget.set_text(text)
            if negate is not None:
                negate.set_active(False)

    def _set_match_bool(self, name: str, value: bool) -> None:
        if name not in self._match_rows:
            prop = find_match_prop(self._kind, name) or MatchProp(name, MatchKind.BOOL)
            self._add_match_row(prop, value)
            return
        _, widget, _ = self._match_rows[name]
        if isinstance(widget, Adw.SwitchRow):
            widget.set_active(value)

    # --- save -----------------------------------------------------------------------------

    def _collect_match(self) -> dict[str, Any]:
        match: dict[str, Any] = {}
        for name, (_prop, widget, negate) in self._match_rows.items():
            if isinstance(widget, Adw.SwitchRow):
                match[name] = widget.get_active()
            elif isinstance(widget, Adw.SpinRow):
                match[name] = int(widget.get_value())
            elif isinstance(widget, Adw.EntryRow):
                text = widget.get_text().strip()
                if not text:
                    continue
                if negate is not None and negate.get_active():
                    text = f"{NEGATIVE_PREFIX}{text}"
                match[name] = text
        return match

    def _collect_effects(self) -> dict[str, Any]:
        collected: dict[str, Any] = {}
        for row in self._effect_entries:
            if isinstance(row.widget, Adw.SwitchRow):
                collected[row.name] = row.widget.get_active()
            elif isinstance(row.widget, Adw.SpinRow):
                value = row.widget.get_value()
                collected[row.name] = (
                    int(value)
                    if row.spec is not None and row.spec.type is EffectType.INT
                    else value
                )
            elif isinstance(row.widget, Adw.EntryRow):
                text = row.widget.get_text().strip()
                if row.original is not None and text == _effect_text(row.original):
                    # Untouched: keep the original object, so a table-valued effect
                    # round-trips by identity rather than through its string.
                    collected[row.name] = row.original
                elif text:
                    collected[row.name] = text
        return collected

    def _validate(self, match: dict[str, Any], name: str) -> str:
        if not match:
            return "A rule needs at least one match."
        original_name = self._original.name if self._original is not None else ""
        if name and name != original_name and name in self._taken:
            return f"Another rule is already labeled “{name}”."
        for prop_name, value in match.items():
            prop = find_match_prop(self._kind, prop_name)
            if prop is not None and prop.kind is MatchKind.REGEX and isinstance(value, str):
                try:
                    re.compile(strip_negation(value))
                except re.error as error:
                    return f"The {prop_title(prop_name)} pattern is not a valid regex: {error}."
        # A blank text effect must be said no to, not silently dropped: the row is on
        # screen, so "Save" quietly meaning "and also delete that one" would be a second
        # meaning nobody asked for. Deleting is what the row's remove button is for.
        for row in self._effect_entries:
            if isinstance(row.widget, Adw.EntryRow) and not row.widget.get_text().strip():
                return (
                    f"The {prop_title(row.name)} effect needs a value — "
                    "remove the row to drop the effect."
                )
        return ""

    def _save(self) -> None:
        match = self._collect_match()
        name = self._label_entry.get_text().strip()
        problem = self._validate(match, name)
        if problem:
            self._error.set_label(problem)
            self._error.set_visible(True)
            return

        enabled = self._original.enabled if self._original is not None else True
        origin = self._original.origin if self._original is not None else ""
        cls = WindowRule if self._kind == "window" else LayerRule
        rule = cls(
            match=match,
            effects=self._collect_effects(),
            name=name,
            enabled=enabled,
            origin=origin,
        )
        self._on_done(rule)
        self.close()


def _exact(text: str) -> str:
    """A picked value as an exact, escaped regex -- the ADR-0008 prefill shape."""
    return f"^({re.escape(text)})$"


def _effect_text(value: Any) -> str:
    """A raw effect value as entry text, and the identity check for "untouched".

    A list renders as its space-joined items because that *is* the string grammar the
    vec2 effects accept (`move`/`size` take `"x y"` or `{x, y}`) -- so a user who edits
    the shown text saves a value the compositor still understands, instead of a Python
    repr. Untouched rows never reach this conversion; they keep the original object.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def _row_add_suffix(widget: Gtk.Widget, suffix: Gtk.Widget) -> None:
    """Every Adwaita row kind spells `add_suffix` the same way; typed narrowly anyway."""
    if isinstance(widget, (Adw.EntryRow, Adw.ActionRow, Adw.SwitchRow, Adw.SpinRow)):
        widget.add_suffix(suffix)


def _dialog_body(child: Gtk.Widget) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(Adw.HeaderBar())
    scroller = Gtk.ScrolledWindow(vexpand=True)
    clamp = Adw.Clamp(margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    clamp.set_child(child)
    scroller.set_child(clamp)
    box.append(scroller)
    return box
