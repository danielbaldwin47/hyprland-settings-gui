"""The Monitors Page: the Arrangement canvas and one row per display rule (ADR-0008).

**IPC positions the canvas; the model owns the rules.** Connected outputs come from
`hyprctl -j monitors` as helper data -- geometry, scale, transform, `availableModes` --
and are never written back. Every edit lands on a `MonitorRule` keyed by its `output`
string, because state cannot answer for `desc:` identities, the catch-all, or a docked
monitor that is not plugged in today (ADR-0008).

**Two apply lanes.** Display-breaking fields (`DISPLAY_BREAKING_FIELDS`) go through
`apply_breaking`, which the window wraps in Confirm-or-revert; benign ones (vrr) apply
instantly per ADR-0003. The catch-all's edits ride the breaking lane too -- a connected
display with no rule of its own is exactly the display the fallback is describing.

**No rule is a state worth showing.** A connected output with no matching rule gets a
"No rule yet" badge -- the hotplug hint (ADR-0008) -- and its first edit or drag creates
the rule, preferring `desc:` identity when the description is unique.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.model.entities import MonitorRule  # noqa: E402
from hyprtweaker.engine.monitors_catalog import (  # noqa: E402
    CATCH_ALL_OUTPUT,
    DISPLAY_BREAKING_FIELDS,
    TRANSFORM_NAMES,
    description_of,
    disconnected_rules,
    format_mode,
    format_position,
    logical_size,
    preferred_identity,
    rule_for,
    snap_position,
)
from hyprtweaker.engine.profiles import MonitorProfile  # noqa: E402
from hyprtweaker.ui.flash import flash  # noqa: E402
from hyprtweaker.ui.pages.entity_text import profile_summary, rule_summary  # noqa: E402
from hyprtweaker.ui.pages.monitor_rows import (  # noqa: E402
    ColourRows,
    ModeRows,
    ScaleRows,
    reserved_row,
)
from hyprtweaker.ui.pages.tasks import entity_page_id  # noqa: E402
from hyprtweaker.ui.release import release  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - a cycle at runtime, a type here
    from hyprtweaker.session import Session

_VRR_CHOICES: tuple[tuple[str, int], ...] = (
    ("Default", -1),
    ("Off", 0),
    ("On", 1),
    ("Fullscreen only", 2),
    ("Fullscreen video", 3),
)

_RowKey = tuple[str, str]
"""Which display row an expander is, across rebuilds: `("connected", connector)`,
`("rule", output)` for a display that is not plugged in, or `("catch-all", "")`."""


@dataclass(frozen=True, slots=True)
class _FocusSpot:
    """Where keyboard focus sat before a rebuild, by what survives one: the display row,
    the setting's title inside it (`None` for the display row itself), and the focused
    widget's place among that setting's descendants (`-1` for the setting itself)."""

    row: _RowKey
    setting: str | None
    index: int
    kind: type


@dataclass(frozen=True, slots=True)
class MonitorActions:
    """The verbs the window wires into the Page, bundled once.

    Every output string is a rule identity (ADR-0008). `apply_breaking` is the
    Confirm-or-revert lane; `apply_benign` writes instantly.
    """

    apply_breaking: Callable[[str, Mapping[str, Any]], None]
    apply_benign: Callable[[str, Mapping[str, Any]], None]
    rename: Callable[[str, str], None]
    """Change a rule's identity string -- the "Match by" toggle (ADR-0008)."""
    remove: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ProfileActions:
    """The Monitor-profile verbs the window wires into the Page (ADR-0015).

    Slugs address profiles the way output strings address rules. `activate` is a
    breaking lane -- the window wraps it in Confirm-or-revert; the rest are benign,
    file-level operations that never touch the compositor.
    """

    save: Callable[[str], None]
    """Capture the current setup under a name from the save dialog."""
    activate: Callable[[str], None]
    """Render the named profile into the config -- wrapped in Confirm-or-revert."""
    update: Callable[[str], None]
    """Recapture over the named profile -- the drift badge's "Update profile"."""
    detach: Callable[[], None]
    """Forget the active pointer; the config stays exactly as it is."""
    delete: Callable[[str], None]
    """Remove the named profile's file; never touches the config."""


class SaveProfileDialog(Adw.AlertDialog):
    """ "Save as profile": one name entry, save disabled until it has one."""

    def __init__(self, *, on_save: Callable[[str], None]) -> None:
        super().__init__(
            heading="Save as profile",
            body=(
                "Captures every display rule and each workspace's display, "
                "to bring back in one click."
            ),
        )
        self._on_save = on_save
        self.entry = Gtk.Entry(placeholder_text="Docked", activates_default=True)
        # A placeholder is not a name: a screen reader announced nothing (ruling A10).
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], ["Profile name"])
        self.set_extra_child(self.entry)
        self.add_response("cancel", "Cancel")
        self.add_response("save", "Save")
        self.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        self.set_response_enabled("save", False)
        self.set_default_response("save")
        self.set_close_response("cancel")
        self.entry.connect(
            "changed",
            lambda entry: self.set_response_enabled("save", bool(entry.get_text().strip())),
        )
        self.connect("response", self._on_response)

    def _on_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        name = self.entry.get_text().strip()
        if response == "save" and name:
            self._on_save(name)


@dataclass(frozen=True, slots=True)
class DisplayRect:
    """One connected output on the canvas, in logical layout coordinates."""

    name: str
    x: int
    y: int
    width: int
    height: int
    has_rule: bool


class ArrangementCanvas(Gtk.DrawingArea):
    """The drag surface of connected displays at logical size (ADR-0008).

    Pure view plus one gesture: rectangles in, a `(name, x, y)` commit out on drop,
    edge-snapped in logical pixels. Everything asserted about it -- layout, hit testing,
    the drop arithmetic -- is reachable without rendering a frame, per the repo's
    probe-before-screenshot rule.
    """

    MARGIN = 24

    def __init__(self, *, on_moved: Callable[[str, int, int], None]) -> None:
        super().__init__(content_height=240, hexpand=True)
        self._on_moved = on_moved
        self._displays: tuple[DisplayRect, ...] = ()
        self._dragging: str | None = None
        self._drag_dx = 0.0
        self._drag_dy = 0.0
        self.set_draw_func(self._draw)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

    # -- data --

    @property
    def displays(self) -> tuple[DisplayRect, ...]:
        return self._displays

    def set_displays(self, displays: Sequence[DisplayRect]) -> None:
        self._displays = tuple(displays)
        self._dragging = None
        self.queue_draw()

    # -- geometry --

    def transform(self) -> tuple[float, float, float]:
        """`(scale, offset_x, offset_y)` mapping logical coordinates onto the canvas.

        Fits the displays' bounding box inside the widget with a margin, capped at 1:4
        so a single laptop panel does not balloon to fill the whole strip.
        """
        if not self._displays:
            return 0.25, self.MARGIN, self.MARGIN
        min_x = min(d.x for d in self._displays)
        min_y = min(d.y for d in self._displays)
        max_x = max(d.x + d.width for d in self._displays)
        max_y = max(d.y + d.height for d in self._displays)
        width = max(self.get_width(), 320)
        height = max(self.get_height(), 240)
        scale = min(
            (width - 2 * self.MARGIN) / max(max_x - min_x, 1),
            (height - 2 * self.MARGIN) / max(max_y - min_y, 1),
            0.25,
        )
        offset_x = self.MARGIN - min_x * scale
        offset_y = self.MARGIN - min_y * scale
        return scale, offset_x, offset_y

    def canvas_rect(self, display: DisplayRect) -> tuple[float, float, float, float]:
        scale, offset_x, offset_y = self.transform()
        x = display.x * scale + offset_x
        y = display.y * scale + offset_y
        if self._dragging == display.name:
            x += self._drag_dx
            y += self._drag_dy
        return x, y, display.width * scale, display.height * scale

    def display_at(self, canvas_x: float, canvas_y: float) -> DisplayRect | None:
        # Last drawn is on top, so hit-test back to front.
        for display in reversed(self._displays):
            x, y, w, h = self.canvas_rect(display)
            if x <= canvas_x <= x + w and y <= canvas_y <= y + h:
                return display
        return None

    def drop_position(self, display: DisplayRect) -> tuple[int, int]:
        """Where the dragged display lands in logical pixels, edge-snapped."""
        scale, _, _ = self.transform()
        raw_x = display.x + round(self._drag_dx / scale) if scale else display.x
        raw_y = display.y + round(self._drag_dy / scale) if scale else display.y
        others = [
            (d.x, d.y, d.width, d.height) for d in self._displays if d.name != display.name
        ]
        return snap_position(raw_x, raw_y, display.width, display.height, others)

    # -- the gesture --

    def _on_drag_begin(self, _gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        hit = self.display_at(x, y)
        self._dragging = hit.name if hit is not None else None
        self._drag_dx = 0.0
        self._drag_dy = 0.0

    def _on_drag_update(self, _gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self._dragging is None:
            return
        self._drag_dx = dx
        self._drag_dy = dy
        self.queue_draw()

    def _on_drag_end(self, _gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        name, self._dragging = self._dragging, None
        if name is None:
            return
        display = next((d for d in self._displays if d.name == name), None)
        if display is None:
            self._drag_dx = self._drag_dy = 0.0
            return
        self._drag_dx, self._drag_dy = dx, dy
        new_x, new_y = self.drop_position(display)
        self._drag_dx = self._drag_dy = 0.0
        if (new_x, new_y) != (display.x, display.y):
            self._on_moved(name, new_x, new_y)
        else:
            self.queue_draw()

    # -- drawing --

    def _draw(self, _area: Gtk.DrawingArea, cr: Any, _width: int, _height: int) -> None:
        color = self.get_color()
        for display in self._displays:
            # On whole pixels, the outline stroked just inside the box: a 1 px line centred
            # on a pixel edge smears into two half-strength pixels, and the dashed outline of
            # a display with no rule fell to 2.5:1 on the light ground (#183).
            x, y, w, h = (round(value) for value in self.canvas_rect(display))
            line = 2 if display.has_rule else 1
            cr.set_source_rgba(color.red, color.green, color.blue, 0.12)
            cr.rectangle(x, y, w, h)
            cr.fill()
            cr.set_source_rgba(color.red, color.green, color.blue, 0.55)
            cr.set_line_width(line)
            if not display.has_rule:
                cr.set_dash([4.0, 4.0])
            cr.rectangle(x + line / 2, y + line / 2, w - line, h - line)
            cr.stroke()
            cr.set_dash([])
            cr.set_source_rgba(color.red, color.green, color.blue, 0.9)
            cr.move_to(x + 8, y + 18)
            cr.show_text(display.name)
            cr.move_to(x + 8, y + 34)
            cr.set_source_rgba(color.red, color.green, color.blue, 0.6)
            cr.show_text(f"{display.width}x{display.height}")


class MonitorsPage:
    """The Displays destination: canvas, connected rows, Not connected, catch-all."""

    section = entity_page_id("monitors")
    title = "Displays"

    def __init__(
        self, session: Session, *, actions: MonitorActions, profiles: ProfileActions
    ) -> None:
        self._session = session
        self._actions = actions
        self._profiles = profiles
        self._connected: tuple[Mapping[str, Any], ...] | None = None
        self._building = False

        self._page = Adw.PreferencesPage(title=self.title)

        self._canvas = ArrangementCanvas(on_moved=self._on_display_moved)
        self._canvas_group = Adw.PreferencesGroup(
            title="Arrangement",
            description=(
                "Displays at their logical size. Drag one to move it; edges snap to "
                "the displays around it."
            ),
        )
        self._canvas_group.add(self._canvas)
        self._page.add(self._canvas_group)

        self._connected_group = Adw.PreferencesGroup(title="Connected")
        self._page.add(self._connected_group)

        self._disconnected_group = Adw.PreferencesGroup(
            title="Not connected",
            description=(
                "Rules for displays that are not plugged in right now, such as a dock "
                "or a TV. They apply the moment their display returns."
            ),
        )
        self._page.add(self._disconnected_group)

        self._catch_all_group = Adw.PreferencesGroup(
            title="Any other display",
            description="The fallback for displays no other rule names.",
        )
        self._page.add(self._catch_all_group)

        self._profiles_group = Adw.PreferencesGroup(
            title="Profiles",
            description=(
                "Named captures of everything above, plus each workspace's display. "
                "Activating one brings the whole setup back."
            ),
        )
        save = Gtk.Button(label="Save current…", valign=Gtk.Align.CENTER)
        save.set_tooltip_text("Capture the current setup as a new profile")
        save.connect("clicked", self._on_save_profile_clicked)
        self._profiles_group.set_header_suffix(save)
        self._page.add(self._profiles_group)

        self._connected_rows: list[Adw.ExpanderRow] = []
        self._disconnected_rows: list[Adw.ExpanderRow] = []
        self._profile_rows: list[Adw.ActionRow] = []
        self._catch_all_row: Adw.ExpanderRow | None = None
        self._listed: dict[Adw.PreferencesGroup, list[Gtk.Widget]] = {}
        self._rule_rows: dict[str, Gtk.Widget] = {}
        """Each rule's row by its identity, the `output` string: what a search hit reveals.
        Keyed rather than parallel to a list, because a rule's row may sit in Connected,
        Not connected or the catch-all group depending on what is plugged in."""
        self._profile_rows_by_slug: dict[str, Adw.ActionRow] = {}
        self._expanders: dict[_RowKey, Adw.ExpanderRow] = {}
        self._colours: dict[str, ColourRows] = {}

        self.refresh()

    # -- what the window talks to --

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def canvas(self) -> ArrangementCanvas:
        return self._canvas

    @property
    def connected_rows(self) -> tuple[Adw.ExpanderRow, ...]:
        return tuple(self._connected_rows)

    @property
    def disconnected_rows(self) -> tuple[Adw.ExpanderRow, ...]:
        return tuple(self._disconnected_rows)

    @property
    def catch_all_row(self) -> Adw.ExpanderRow | None:
        return self._catch_all_row

    @property
    def profile_rows(self) -> tuple[Adw.ActionRow, ...]:
        return tuple(self._profile_rows)

    @property
    def connected(self) -> tuple[Mapping[str, Any], ...]:
        """The last helper answer as capture material: the fingerprint's raw form."""
        return self._connected or ()

    @property
    def rules(self) -> list[MonitorRule]:
        return list(self._session.monitor_rules)

    @property
    def unruled_outputs(self) -> tuple[str, ...]:
        """Connected outputs with no matching rule -- the hotplug hint's subjects.

        What the "No rule yet" badge and the canvas's dashed outline both key on,
        exposed so the smoke tier asserts the *condition* rather than walking suffix
        widgets (probe-before-screenshot).
        """
        rules = self._session.monitor_rules
        return tuple(
            str(monitor.get("name", ""))
            for monitor in self._connected or ()
            if rule_for(
                rules,
                connector=str(monitor.get("name", "")),
                description=str(monitor.get("description", "")),
            )
            is None
        )

    def set_connected(self, monitors: tuple[Mapping[str, Any], ...] | None) -> None:
        """Fresh `hyprctl -j monitors` data, or `None` when nobody answered.

        `None` degrades to the off-canvas lists rather than an empty canvas lying that
        no displays exist -- the same distinction `fetch_monitors` draws.
        """
        self._connected = monitors
        self.refresh()

    def identity_for(self, monitor: Mapping[str, Any]) -> str:
        """The `output` string a new rule for this connected display takes (ADR-0008).

        `desc:` when the description is unique among the *other* connected displays and
        every already-configured identity, else the connector.
        """
        connector = str(monitor.get("name", ""))
        description = str(monitor.get("description", ""))
        taken: set[str] = set()
        for other in self._connected or ():
            if str(other.get("name", "")) != connector:
                taken.add(str(other.get("description", "")).strip())
        for rule in self._session.monitor_rules:
            if rule.output.startswith("desc:"):
                taken.add(rule.output[len("desc:") :].strip())
        return preferred_identity(connector, description, taken_descriptions=taken)

    # -- rebuild --

    def refresh(self) -> None:
        """Rebuild every group from the model plus the last helper answer."""
        self._building = True
        try:
            self._rebuild()
        finally:
            self._building = False

    def _rebuild(self) -> None:
        rules = self._session.monitor_rules
        monitors = self._connected or ()
        editable = bool(self._session.live)

        # Every applied edit rebuilds the page, and the rows that take several edits in a
        # row (the four reserved sides, the luminance fields) sit inside a display's
        # expander: what the user had open, and where their cursor was, comes back.
        expanded = {key for key, row in self._expanders.items() if row.get_expanded()}
        colours = {name for name, colour in self._colours.items() if colour.shown}
        focus = self._focus_spot()
        self._expanders = {}
        self._colours = {}

        for group, widgets in self._listed.items():
            for widget in widgets:
                group.remove(widget)
                release(widget)
        self._listed = {}
        self._connected_rows = []
        self._disconnected_rows = []
        self._profile_rows = []
        self._catch_all_row = None
        self._rule_rows = {}
        self._profile_rows_by_slug = {}

        # The canvas: live outputs at logical size, IPC geometry (ADR-0008).
        displays = []
        for monitor in monitors:
            width, height = _logical(monitor)
            displays.append(
                DisplayRect(
                    name=str(monitor.get("name", "")),
                    x=int(monitor.get("x", 0)),
                    y=int(monitor.get("y", 0)),
                    width=width,
                    height=height,
                    has_rule=rule_for(
                        rules,
                        connector=str(monitor.get("name", "")),
                        description=str(monitor.get("description", "")),
                    )
                    is not None,
                )
            )
        self._canvas.set_displays(displays)
        self._canvas_group.set_visible(bool(monitors))

        for monitor in monitors:
            rule = rule_for(
                rules,
                connector=str(monitor.get("name", "")),
                description=str(monitor.get("description", "")),
            )
            connector = str(monitor.get("name", ""))
            row = self._connected_row(
                monitor, rule, editable=editable, colour_shown=connector in colours
            )
            self._keep(("connected", connector), row, expanded)
            if rule is not None:
                self._rule_rows[rule.output] = row
            self._connected_group.add(row)
            self._connected_rows.append(row)
            self._listed.setdefault(self._connected_group, []).append(row)
        if not monitors:
            empty = Adw.ActionRow(
                title="No connected displays to show",
                subtitle="Hyprland is not answering, so only saved rules are listed.",
            )
            self._connected_group.add(empty)
            self._listed.setdefault(self._connected_group, []).append(empty)

        leftover = disconnected_rules(rules, monitors)
        for rule in leftover:
            row = self._disconnected_row(rule, editable=editable)
            self._rule_rows[rule.output] = row
            self._keep(("rule", rule.output), row, expanded)
            self._disconnected_group.add(row)
            self._disconnected_rows.append(row)
            self._listed.setdefault(self._disconnected_group, []).append(row)
        self._disconnected_group.set_visible(bool(leftover))

        catch_all = next((r for r in rules if r.output == CATCH_ALL_OUTPUT), None)
        self._catch_all_row = self._fields_editor(
            catch_all if catch_all is not None else MonitorRule(output=CATCH_ALL_OUTPUT),
            title="Any other display",
            subtitle="Applied to displays no other rule matches.",
            editable=editable,
            removable=catch_all is not None,
            breaking=True,
        )
        self._rule_rows[CATCH_ALL_OUTPUT] = self._catch_all_row
        self._keep(("catch-all", ""), self._catch_all_row, expanded)
        self._catch_all_group.add(self._catch_all_row)
        self._listed.setdefault(self._catch_all_group, []).append(self._catch_all_row)

        active = self._session.active_monitor_profile()
        active_slug = active[0] if active is not None else None
        drifted = self._session.monitor_profile_drift()
        profiles = self._session.monitor_profiles()
        for slug, profile in profiles:
            row = self._profile_row(
                slug,
                profile,
                active=slug == active_slug,
                drifted=drifted and slug == active_slug,
                editable=editable,
            )
            self._profiles_group.add(row)
            self._profile_rows.append(row)
            self._profile_rows_by_slug[slug] = row
            self._listed.setdefault(self._profiles_group, []).append(row)
        if not profiles:
            hint = Adw.ActionRow(
                title="No profiles yet",
                subtitle="Save the current setup to switch between arrangements later.",
            )
            self._profiles_group.add(hint)
            self._listed.setdefault(self._profiles_group, []).append(hint)

        if focus is not None:
            self._restore_focus(focus)

    def _keep(self, key: _RowKey, row: Adw.ExpanderRow, expanded: set[_RowKey]) -> None:
        """Track `row` under `key`, open if it was open: before it is added, so it opens
        without the reveal animation and the page does not scroll while it grows."""
        self._expanders[key] = row
        row.set_expanded(key in expanded)

    def _focus_spot(self) -> _FocusSpot | None:
        root = self._page.get_root()
        focus = root.get_focus() if root is not None else None
        if focus is None:
            return None
        for key, expander in self._expanders.items():
            if not focus.is_ancestor(expander):
                continue
            setting = focus.get_ancestor(Adw.PreferencesRow)
            if setting is None or setting.get_title() == expander.get_title():
                setting = expander  # the display row's own header
            index = -1 if focus is setting else _descendants(setting).index(focus)
            title = None if setting is expander else setting.get_title()
            return _FocusSpot(key, title, index, type(focus))
        return None

    def _restore_focus(self, spot: _FocusSpot) -> None:
        expander = self._expanders.get(spot.row)
        if expander is None:
            return
        setting: Gtk.Widget | None = expander
        if spot.setting is not None:
            setting = next(
                (
                    widget
                    for widget in _descendants(expander)
                    if isinstance(widget, Adw.PreferencesRow)
                    and widget.get_title() == spot.setting
                ),
                None,
            )
        if setting is None:
            return
        inside = _descendants(setting)
        target = (
            setting
            if spot.index < 0
            else inside[spot.index]
            if spot.index < len(inside)
            else None
        )
        if type(target) is spot.kind and target is not None:
            target.grab_focus()

    # -- search reveals --

    def reveal_rule(self, output: str) -> Gtk.Widget | None:
        """Bring the row for the monitor rule `output` into view and flash it.

        Wherever it is listed -- Connected, Not connected, or the catch-all. Navigate +
        flash as `BindsPage.reveal`; returns the row for an explicit scroll, or `None`.
        """
        return _reveal(self._rule_rows.get(output))

    def reveal_profile(self, slug: str) -> Gtk.Widget | None:
        """Bring Monitor profile `slug`'s row, in the Profiles group, into view and flash it."""
        return _reveal(self._profile_rows_by_slug.get(slug))

    # -- profiles --

    def _profile_row(
        self,
        slug: str,
        profile: MonitorProfile,
        *,
        active: bool,
        drifted: bool,
        editable: bool,
    ) -> Adw.ActionRow:
        row = Adw.ActionRow(use_markup=False)
        row.set_title(profile.name)
        row.set_subtitle(profile_summary(profile))

        if active and drifted:
            # The drift badge (ADR-0015): reality and the capture disagree.
            badge = Gtk.Label(
                label="Changed since capture", css_classes=["dim-label", "caption"]
            )
            badge.set_tooltip_text(
                "The display setup no longer matches this profile. Update the profile "
                "to keep what you have, or detach to let them differ."
            )
            row.add_suffix(badge)
            update = Gtk.Button(label="Update", valign=Gtk.Align.CENTER, css_classes=["flat"])
            update.set_tooltip_text("Recapture the current setup into this profile")
            update.connect("clicked", lambda _b: self._profiles.update(slug))
            row.add_suffix(update)
            detach = Gtk.Button(label="Detach", valign=Gtk.Align.CENTER, css_classes=["flat"])
            detach.set_tooltip_text("Keep the current setup and stop tracking this profile")
            detach.connect("clicked", lambda _b: self._profiles.detach())
            row.add_suffix(detach)
        elif active:
            pill = Gtk.Label(label="Active", css_classes=["accent", "caption"])
            pill.set_tooltip_text("The current setup is this profile")
            row.add_suffix(pill)
        else:
            activate = Gtk.Button(label="Activate", valign=Gtk.Align.CENTER)
            activate.set_tooltip_text(
                "Apply this profile's rules and workspace pins, behind a revert countdown"
            )
            activate.set_sensitive(editable)
            activate.connect("clicked", lambda _b: self._profiles.activate(slug))
            row.add_suffix(activate)

        remove = Gtk.Button(
            icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"]
        )
        remove.set_tooltip_text("Delete this profile")
        remove.connect("clicked", lambda _b: self._profiles.delete(slug))
        row.add_suffix(remove)
        return row

    def _on_save_profile_clicked(self, _button: Gtk.Button) -> None:
        SaveProfileDialog(on_save=self._profiles.save).present(self._page)

    # -- connected rows --

    def _connected_row(
        self,
        monitor: Mapping[str, Any],
        rule: MonitorRule | None,
        *,
        editable: bool,
        colour_shown: bool,
    ) -> Adw.ExpanderRow:
        connector = str(monitor.get("name", ""))
        description = str(monitor.get("description", "")).strip()
        width, height = int(monitor.get("width", 0)), int(monitor.get("height", 0))
        refresh = float(monitor.get("refreshRate", 0.0))
        row = Adw.ExpanderRow(
            use_markup=False,  # the EDID description is the vendor's text, not markup
        )
        row.set_title(description or connector)
        row.set_subtitle(f"{connector} · {format_mode(width, height, refresh)}")
        if rule is None:
            badge = Gtk.Label(label="No rule yet", css_classes=["dim-label", "caption"])
            badge.set_tooltip_text(
                "This display uses defaults. Any change here writes its first rule."
            )
            row.add_suffix(badge)

        output = rule.output if rule is not None else self.identity_for(monitor)
        fields: Mapping[str, Any] = rule.fields if rule is not None else {}

        if rule is not None:
            # The per-rule identity toggle (ADR-0008): the same rule addressed by what
            # the display *is* or by where it is plugged in. Only offered once a rule
            # exists -- before that, the first edit picks desc-when-unique on its own.
            match_by = Adw.ComboRow(
                title="Match by",
                subtitle="A description survives replug; a port survives identical twins.",
                model=Gtk.StringList.new(
                    [
                        f"This exact display ({description or 'no description'})",
                        f"Port {connector}",
                    ]
                ),
            )
            match_by.set_selected(0 if description_of(rule.output) is not None else 1)
            match_by.set_sensitive(editable and bool(description))
            match_by.connect(
                "notify::selected",
                self._on_match_by_selected,
                rule.output,
                connector,
                description,
            )
            row.add_row(match_by)

        enabled = Adw.SwitchRow(title="Enabled", active=not bool(fields.get("disabled")))
        enabled.set_sensitive(editable)
        enabled.connect(
            "notify::active",
            lambda sw, _p: self._apply(output, {"disabled": not sw.get_active()}),
        )
        row.add_row(enabled)

        def apply(changed: Mapping[str, Any]) -> None:
            self._apply(output, changed)

        current_scale = fields.get("scale", monitor.get("scale", 1.0))
        for setting in (
            *ModeRows(monitor, fields, apply, editable=editable).rows,
            *ScaleRows(current_scale, apply, editable=editable).rows,
        ):
            row.add_row(setting)

        rotation = Adw.ComboRow(
            title="Rotation", model=Gtk.StringList.new(list(TRANSFORM_NAMES))
        )
        rotation.set_selected(_int_or(fields.get("transform", monitor.get("transform", 0)), 0))
        rotation.set_sensitive(editable)
        rotation.connect(
            "notify::selected",
            lambda combo, _p: self._apply(output, {"transform": int(combo.get_selected())}),
        )
        row.add_row(rotation)

        others = ["none"] + [
            str(m.get("name", ""))
            for m in self._connected or ()
            if str(m.get("name", "")) != connector
        ]
        mirror_value = str(fields.get("mirror", "none")) or "none"
        if mirror_value not in others:
            others.append(mirror_value)
        mirror = Adw.ComboRow(
            title="Mirror",
            subtitle="Show another display's picture instead of an extended desktop.",
            model=Gtk.StringList.new(others),
        )
        mirror.set_selected(others.index(mirror_value))
        mirror.set_sensitive(editable and len(others) > 1)
        mirror.connect(
            "notify::selected",
            lambda combo, _p: self._apply(output, {"mirror": others[combo.get_selected()]}),
        )
        row.add_row(mirror)

        ten_bit = Adw.SwitchRow(
            title="10-bit colour",
            subtitle="Ask for 10 bits per channel; not every display honours it.",
            active=_int_or(fields.get("bitdepth", 8), 8) == 10,
        )
        ten_bit.set_sensitive(editable)
        ten_bit.connect(
            "notify::active",
            lambda sw, _p: self._apply(output, {"bitdepth": 10 if sw.get_active() else 8}),
        )
        row.add_row(ten_bit)

        vrr_labels = [label for label, _ in _VRR_CHOICES]
        vrr = Adw.ComboRow(title="Variable refresh rate", model=Gtk.StringList.new(vrr_labels))
        vrr_value = _int_or(fields.get("vrr", -1), -1)
        vrr.set_selected(
            next((i for i, (_, value) in enumerate(_VRR_CHOICES) if value == vrr_value), 0)
        )
        vrr.set_sensitive(editable)
        vrr.connect(
            "notify::selected",
            lambda combo, _p: self._apply(
                output, {"vrr": _VRR_CHOICES[combo.get_selected()][1]}
            ),
        )
        row.add_row(vrr)

        row.add_row(reserved_row(fields.get("reserved"), apply, editable=editable))
        colour = ColourRows(fields, apply, editable=editable, shown=colour_shown)
        self._colours[connector] = colour
        for setting in colour.rows:
            row.add_row(setting)
        return row

    def _on_match_by_selected(
        self,
        combo: Adw.ComboRow,
        _param: Any,
        current: str,
        connector: str,
        description: str,
    ) -> None:
        if self._building:
            return
        wanted = f"desc:{description}" if combo.get_selected() == 0 else connector
        if wanted != current:
            self._actions.rename(current, wanted)

    # -- off-canvas rows --

    def _disconnected_row(self, rule: MonitorRule, *, editable: bool) -> Adw.ExpanderRow:
        return self._fields_editor(
            rule,
            title=rule.output,
            subtitle=rule_summary(rule),
            editable=editable,
            removable=True,
            # Its display is absent, so nothing on screen can break: instant per
            # ADR-0003. The rule takes effect on replug, behind that day's hotplug.
            breaking=False,
        )

    def _fields_editor(
        self,
        rule: MonitorRule,
        *,
        title: str,
        subtitle: str,
        editable: bool,
        removable: bool,
        breaking: bool,
    ) -> Adw.ExpanderRow:
        """The raw-fields editor for rules with no live display to build combos from."""
        row = Adw.ExpanderRow(use_markup=False)
        row.set_title(title)
        row.set_subtitle(subtitle)
        output = rule.output
        lane = self._actions.apply_breaking if breaking else self._actions.apply_benign

        def entry(field_title: str, key: str) -> Adw.EntryRow:
            widget = Adw.EntryRow(use_markup=False, show_apply_button=True)
            widget.set_title(field_title)
            value = rule.fields.get(key)
            if value is not None:
                widget.set_text(str(value))
            widget.set_sensitive(editable)
            refusal = Gtk.Label(css_classes=["error", "caption"], visible=False)
            widget.add_suffix(refusal)

            def applied(w: Adw.EntryRow) -> None:
                message = self._apply_text(output, key, w.get_text(), lane=lane)
                refusal.set_label(message or "")
                refusal.set_visible(message is not None)

            widget.connect("apply", applied)
            return widget

        row.add_row(entry("Mode", "mode"))
        row.add_row(entry("Position", "position"))
        row.add_row(entry("Scale", "scale"))

        enabled = Adw.SwitchRow(title="Enabled", active=not bool(rule.fields.get("disabled")))
        enabled.set_sensitive(editable)
        enabled.connect(
            "notify::active",
            lambda sw, _p: self._apply(output, {"disabled": not sw.get_active()}, lane=lane),
        )
        row.add_row(enabled)

        if removable:
            remove = Gtk.Button(
                icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"]
            )
            remove.set_tooltip_text("Remove this rule")
            remove.set_sensitive(editable)
            remove.connect("clicked", lambda _b: self._actions.remove(output))
            row.add_suffix(remove)

        return row

    def _apply_text(
        self,
        output: str,
        key: str,
        text: str,
        *,
        lane: Callable[[str, Mapping[str, Any]], None],
    ) -> str | None:
        """Write one raw field, or answer why not: the refusal the entry shows inline."""
        value: Any = text.strip()
        if not value:
            return None
        if key == "scale" and value != "auto":
            try:
                number = float(value)
            except ValueError:
                return "Scale must be a number or auto"
            if number < 0.25:  # Hyprland's parser rejects it (`--verify-config`)
                return "Scale must be at least 0.25"
            value = int(number) if number.is_integer() else number
        self._apply(output, {key: value}, lane=lane)
        return None

    # -- the apply seam --

    def _apply(
        self,
        output: str,
        fields: Mapping[str, Any],
        *,
        lane: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        """Route one edit to its lane: breaking fields to Confirm-or-revert (ADR-0008).

        `lane` overrides the routing for the rows that know better than the field name --
        a disconnected display's edits are all benign, because there is no picture its
        absent display could break.
        """
        if self._building:
            return
        if lane is None:
            lane = (
                self._actions.apply_breaking
                if any(key in DISPLAY_BREAKING_FIELDS for key in fields)
                else self._actions.apply_benign
            )
        lane(output, fields)

    def _on_display_moved(self, name: str, x: int, y: int) -> None:
        """A canvas drop: write the integer position rule for that display (ADR-0008)."""
        monitor = next(
            (m for m in self._connected or () if str(m.get("name", "")) == name), None
        )
        if monitor is None:
            return
        rule = rule_for(
            self._session.monitor_rules,
            connector=name,
            description=str(monitor.get("description", "")),
        )
        output = rule.output if rule is not None else self.identity_for(monitor)
        self._apply(output, {"position": format_position(x, y)})


def _reveal(row: Gtk.Widget | None) -> Gtk.Widget | None:
    if row is not None:
        row.grab_focus()
        flash(row)
    return row


def _descendants(widget: Gtk.Widget) -> list[Gtk.Widget]:
    """Every widget under `widget`, depth first: the same order for the same shape."""
    found: list[Gtk.Widget] = []
    child = widget.get_first_child()
    while child is not None:
        found.append(child)
        found.extend(_descendants(child))
        child = child.get_next_sibling()
    return found


def _logical(monitor: Mapping[str, Any]) -> tuple[int, int]:
    return logical_size(
        int(monitor.get("width", 0)),
        int(monitor.get("height", 0)),
        scale=monitor.get("scale", 1.0),
        transform=monitor.get("transform", 0),
    )


def _int_or(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
