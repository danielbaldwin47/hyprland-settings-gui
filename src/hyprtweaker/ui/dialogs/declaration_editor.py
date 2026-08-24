"""One editor dialog for all seven declarative Entity kinds (#70).

Parameterised by a `DeclarationKind` the same way `RuleEditor` is parameterised by rule
kind, and for the same reason: the seven forms differ only in which fields they ask for,
and seven hand-written dialogs would be seven places for "required field left empty" to be
handled differently.

**Widgets come from the field type, not from the field name.** `entities_catalog` says what
each key holds and what bounds Hyprland enforces; this module turns that into a switch, a
spin, a dropdown or an entry. A bounded number therefore *cannot* be given an out-of-range
value here, which is the "unenterable" half of the ticket's type-correctness -- rather than
letting it through and reporting it afterwards.

**The optional tier is a picker, not a wall of rows.** A device has 43 optional fields. The
form shows the ones that are set, each with a remove button, and offers the rest through an
"Add a setting" dropdown -- the shape `RuleEditor` uses for effects, for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.entities_catalog import (  # noqa: E402
    BUILTIN_CURVES,
    IDENTITY_FIELD,
    FieldSpec,
    FieldType,
    field_text,
)
from hyprtweaker.ui.pages.declaration_kinds import (  # noqa: E402
    BY_KIND,
    DeclarationKind,
    choice_label,
    missing_required,
)

_NOT_SET = "—"
"""What an optional dropdown shows for "leave this key out of the table".

An em dash rather than an empty row: a blank line in a dropdown reads as a rendering fault,
and this option has a meaning worth stating -- the key is not written at all, which is not
the same as writing a default.
"""

_WIDE = 1_000_000.0
"""The spin range for a number the API documents no bounds for."""


class DeclarationEditor(Adw.Dialog):
    """Add or edit one curve, animation, gesture, device, variable, command or permission."""

    def __init__(
        self,
        *,
        kind: str,
        on_done: Callable[[Any], None],
        entity: Any | None = None,
        curve_names: Sequence[str] = (),
        taken: Sequence[str] = (),
        bounds: Mapping[str, tuple[float | None, float | None]] | None = None,
    ) -> None:
        """`curve_names` fills the bezier and spring dropdowns, so an animation can only
        name a curve that exists -- the dangling reference prevented at the point it would
        be created rather than reported after the write. `taken` are identities other rows
        already hold; saving onto one is refused here, because the session refuses it too
        and a silent no-op is the worst of the three possible answers."""
        self._descriptor: DeclarationKind = BY_KIND[kind]
        super().__init__(
            title=(
                f"Edit {self._descriptor.singular}"
                if entity is not None
                else f"Add {self._descriptor.singular}"
            ),
            content_width=560,
            content_height=620,
        )
        self._kind = kind
        self._on_done = on_done
        self._original = entity
        self._taken = tuple(name for name in taken if name)

        self._values: dict[str, Any] = (
            dict(self._descriptor.to_form(entity)) if entity is not None else {}
        )
        self._curve_names = tuple(dict.fromkeys((*BUILTIN_CURVES, *curve_names)))
        # Per-field min/max the *Schema* supplies, for the one kind whose fields shadow
        # Options (`device_field_bounds`). Empty for every other kind, and empty when the
        # caller has no Schema to hand -- in which case the spec's own bounds still apply.
        self._bounds: Mapping[str, tuple[float | None, float | None]] = bounds or {}
        self._rows: dict[str, Gtk.Widget] = {}

        # A label rather than an `Adw.Banner`: ADR-0016 reserves the Banner for the
        # window's one unhealthy-state surface, and "you left a field empty" is neither
        # unhealthy nor the window's business.
        self._error = Gtk.Label(
            wrap=True, xalign=0.0, visible=False, css_classes=["error", "caption"]
        )
        self._group = Adw.PreferencesGroup()
        self._optional_group = Adw.PreferencesGroup(
            title="Settings", description=self._descriptor.description
        )

        self._save = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self._save.connect("clicked", lambda _button: self._commit())

        self.set_child(self._body())
        self._build()

    # --- assembly ---------------------------------------------------------------------

    def _body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar(show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel)
        header.pack_end(self._save)
        box.append(header)
        box.append(self._error)

        page = Adw.PreferencesPage()
        page.add(self._group)
        if self._descriptor.optional:
            page.add(self._optional_group)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(page)
        box.append(scroller)
        return box

    def _build(self) -> None:
        for spec in self._descriptor.fields:
            self._group.add(self._row_for(spec, removable=False))
        if self._descriptor.optional:
            self._rebuild_optional()

    def _rebuild_optional(self) -> None:
        for widget in list(self._rows.values()):
            if widget.get_parent() is self._optional_group:
                self._optional_group.remove(widget)
        self._rows = {
            name: widget
            for name, widget in self._rows.items()
            if widget.get_parent() is not None
        }

        present = [spec for spec in self._descriptor.optional if spec.name in self._values]
        for spec in present:
            self._optional_group.add(self._row_for(spec, removable=True))

        remaining = [
            spec for spec in self._descriptor.optional if spec.name not in self._values
        ]
        if remaining:
            self._optional_group.add(self._add_row(remaining))

    def _add_row(self, remaining: Sequence[FieldSpec]) -> Gtk.Widget:
        model = Gtk.StringList()
        model.append("Add a setting…")
        for spec in remaining:
            model.append(spec.label)
        row = Adw.ComboRow(title="Add a setting", model=model, selected=0)
        row.connect("notify::selected", self._on_add_selected, tuple(remaining))
        return row

    def _on_add_selected(
        self, row: Adw.ComboRow, _param: Any, remaining: tuple[FieldSpec, ...]
    ) -> None:
        index = row.get_selected()
        if index <= 0 or index > len(remaining):
            return
        spec = remaining[index - 1]
        # A newly added field starts at its type's neutral value rather than absent, so the
        # row it grows has something to show and the key is written on save. Removing it is
        # one click away; a row that looked set but wrote nothing would not be.
        self._values[spec.name] = _neutral(spec)
        self._rebuild_optional()

    # --- one row ----------------------------------------------------------------------

    def _row_for(self, spec: FieldSpec, *, removable: bool) -> Gtk.Widget:
        row = self._widget_for(spec)
        if spec.help:
            # `Adw.EntryRow` has no subtitle -- its title is a floating label inside the
            # field, so there is no second line to put help on. A tooltip is the honest
            # fallback rather than dropping the sentence.
            if hasattr(row, "set_subtitle"):
                row.set_subtitle(spec.help)
            else:
                row.set_tooltip_text(spec.help)
        if removable:
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.set_tooltip_text(f"Remove {spec.label.lower()}")
            remove.connect("clicked", self._on_remove, spec)
            row.add_suffix(remove)
        self._rows[spec.name] = row
        return row

    def _on_remove(self, _button: Gtk.Button, spec: FieldSpec) -> None:
        self._values.pop(spec.name, None)
        self._rebuild_optional()

    def _widget_for(self, spec: FieldSpec) -> Any:
        value = self._values.get(spec.name)

        if spec.type is FieldType.BOOL:
            # A required switch with nothing held opens *on*, agreeing with `_neutral`.
            # `enabled` is the only one, and off would answer "no, don't animate" for the
            # user -- plainly wrong for an imported animation that carries a speed and a
            # curve, and it made Add produce a disabled animation.
            opening = bool(value) if value is not None else bool(spec.required)
            row = Adw.SwitchRow(title=spec.label, active=opening)
            row.connect("notify::active", self._on_switch, spec)
            # Seeded, not left to the first toggle. A switch shows a state either way, so
            # an untouched one that wrote nothing produced a table with the key *absent* --
            # and `hl.animation` rejects a missing `enabled` outright, which is what the
            # Add flow produced for anyone who accepted the default.
            if spec.required:
                self._values[spec.name] = row.get_active()
            return row

        if spec.type in (FieldType.ENUM, FieldType.CURVE_REF):
            choices = (
                self._curve_names if spec.type is FieldType.CURVE_REF else tuple(spec.choices)
            )
            # A held value the picker does not offer joins it rather than being dropped.
            # `unset` is the case that made this necessary: deliberately absent from
            # `GESTURE_ACTIONS` because the app never writes one, but an imported config may
            # hold it -- and without this the dialog opened on the first choice and *saved*
            # it, turning a removal into a live binding just by looking at the row. The
            # pass-through rule ADR-0008 sets for an unknown rule effect, applied here.
            if isinstance(value, str) and value and value not in choices:
                choices = (*choices, value)
            optional = not spec.required
            model = Gtk.StringList()
            if optional:
                model.append(_NOT_SET)
            for choice in choices:
                model.append(choice_label(spec, choice))
            offsets = ((None,) if optional else ()) + choices
            selected = offsets.index(value) if value in offsets else 0
            row = Adw.ComboRow(title=spec.label, model=model, selected=selected)
            row.connect("notify::selected", self._on_choice, spec, offsets)
            # Seeded for the same reason a switch is: a required dropdown shows its first
            # choice whether or not anybody picked it, so leaving the value unwritten meant
            # `hl.gesture` got no `direction` and `hl.permission` got no `type` -- both
            # rejected by name, both reachable by opening Add and pressing Save.
            if spec.required and offsets and offsets[selected] is not None:
                self._values[spec.name] = offsets[selected]
            return row

        if spec.type in (FieldType.INT, FieldType.FLOAT):
            digits = 0 if spec.type is FieldType.INT else 3
            step = 1.0 if spec.type is FieldType.INT else 0.05
            low, high = self._bounds.get(spec.name, (None, None))
            low = spec.minimum if spec.minimum is not None else low
            high = spec.maximum if spec.maximum is not None else high
            lower = low if low is not None else -_WIDE
            upper = high if high is not None else _WIDE
            opening = spec.default if spec.default is not None else max(lower, 0.0)
            current = float(value) if isinstance(value, int | float) else opening
            adjustment = Gtk.Adjustment(
                value=min(max(current, lower), upper),
                lower=lower,
                upper=upper,
                step_increment=step,
                page_increment=step * 10,
            )
            row = Adw.SpinRow(title=spec.label, adjustment=adjustment, digits=digits)
            row.connect("notify::value", self._on_number, spec)
            # Seed the value: a spin row that was clamped on construction has to write the
            # clamped number back, or Save would emit the out-of-range one it was given.
            self._values[spec.name] = _as_number(spec, adjustment.get_value())
            return row

        row = Adw.EntryRow(title=spec.label, text=field_text(value))
        row.connect("changed", self._on_text, spec)
        return row

    def _on_switch(self, row: Adw.SwitchRow, _param: Any, spec: FieldSpec) -> None:
        self._values[spec.name] = row.get_active()

    def _on_choice(
        self, row: Adw.ComboRow, _param: Any, spec: FieldSpec, offsets: tuple[Any, ...]
    ) -> None:
        index = row.get_selected()
        choice = offsets[index] if 0 <= index < len(offsets) else None
        if choice is None:
            self._values.pop(spec.name, None)
        else:
            self._values[spec.name] = choice
        if spec.name in ("bezier", "spring"):
            self._clear_sibling_curve(spec.name)
        if spec.name == "type":
            self._clear_wrong_curve_shape()

    def _clear_sibling_curve(self, chosen: str) -> None:
        """An animation names a bezier *or* a spring; setting one drops the other.

        The parser refuses a table that carries both (`animation_findings`), and a stale
        key left behind by changing your mind is the likeliest way to end up with one.
        """
        other = "spring" if chosen == "bezier" else "bezier"
        if self._values.get(chosen):
            self._values.pop(other, None)

    def _clear_wrong_curve_shape(self) -> None:
        """Switching a curve between bezier and spring drops the other shape's numbers."""
        if self._values.get("type") == "spring":
            for key in ("x0", "y0", "x1", "y1"):
                self._values.pop(key, None)
        else:
            for key in ("mass", "stiffness", "dampening"):
                self._values.pop(key, None)

    def _on_number(self, row: Adw.SpinRow, _param: Any, spec: FieldSpec) -> None:
        self._values[spec.name] = _as_number(spec, row.get_value())

    def _on_text(self, row: Adw.EntryRow, spec: FieldSpec) -> None:
        text = row.get_text().strip()
        if text:
            self._values[spec.name] = text
        else:
            self._values.pop(spec.name, None)

    # --- saving -----------------------------------------------------------------------

    def collect(self) -> dict[str, Any]:
        """The form's current values -- the seam the UI tier drives without a main loop."""
        return dict(self._values)

    def validate(self) -> str | None:
        """Why this form cannot be saved yet, or `None` when it can."""
        missing = missing_required(self._kind, self._values)
        if missing:
            return "Still needed: " + ", ".join(missing)

        candidate = self._descriptor.from_form(self._values, self._original)
        identity = _identity_of(self._kind, candidate)
        if identity is not None and identity in self._taken:
            return (
                f"There is already an entry for “{identity}”. "
                f"Hyprland keeps one, so edit that one instead."
            )
        return None

    def build(self) -> Any:
        """The entity this form describes, valid or not -- what `validate` judged."""
        return self._descriptor.from_form(self._values, self._original)

    def _commit(self) -> None:
        problem = self.validate()
        if problem is not None:
            self._error.set_label(problem)
            self._error.set_visible(True)
            return
        self._on_done(self.build())
        self.close()


def _identity_of(kind: str, entity: Any) -> str | None:
    """One entity's identity string, or `None` for a kind that takes duplicates.

    Reads `IDENTITY_FIELD` rather than its own copy of the map: the editor refuses a
    duplicate up front and the session refuses it again at the write gate, and two copies
    of "what counts as the same row" is two chances for the dialog to accept what the
    session will silently drop.
    """
    attribute = IDENTITY_FIELD.get(kind)
    return None if attribute is None else str(getattr(entity, attribute))


def _neutral(spec: FieldSpec) -> Any:
    """The value a newly added optional field opens with."""
    if spec.type is FieldType.BOOL:
        return True
    if spec.type in (FieldType.INT, FieldType.FLOAT):
        if spec.default is not None:
            return _as_number(spec, spec.default)
        lower = spec.minimum if spec.minimum is not None else 0.0
        return _as_number(spec, max(lower, 0.0))
    if spec.type is FieldType.ENUM and spec.choices:
        return spec.choices[0]
    return ""


def _as_number(spec: FieldSpec, value: float) -> Any:
    return round(value) if spec.type is FieldType.INT else float(value)


def taken_identities(
    kind: str, entities: Sequence[Any], *, skip: int | None = None
) -> tuple[str, ...]:
    """Identities already held by other rows -- what the editor refuses to duplicate."""
    return tuple(
        identity
        for index, entity in enumerate(entities)
        if index != skip and (identity := _identity_of(kind, entity)) is not None
    )


__all__ = ["DeclarationEditor", "taken_identities"]
