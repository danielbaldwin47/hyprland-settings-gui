"""UI smoke tier: the Monitors Page assembles and routes edits to the right lane (#68).

Geometry and identity are settled headless in `tests/unit/test_monitors_catalog.py`;
what is left here is whether GTK builds the page -- canvas, connected rows, Not
connected, the catch-all -- and whether an edit reaches the breaking or the benign
action, probed programmatically per the repo's probe-before-screenshot rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"

MONITORS: tuple[dict[str, Any], ...] = (
    {
        "name": "eDP-1",
        "description": "BOE 0x0791",
        "width": 1920,
        "height": 1080,
        "refreshRate": 60.012,
        "x": 0,
        "y": 0,
        "scale": 1.5,
        "transform": 0,
        "availableModes": ["1920x1080@60.01Hz", "1920x1080@48.01Hz"],
    },
    {
        "name": "DP-3",
        "description": "Dell U2720Q",
        "width": 2560,
        "height": 1440,
        "refreshRate": 144.0,
        "x": 1280,
        "y": 0,
        "scale": 1.0,
        "transform": 0,
        "availableModes": ["2560x1440@144.00Hz", "2560x1440@60.00Hz"],
    },
)


def build_window(tmp_path: Path) -> Any:
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def monitor_rule(output: str, **fields: Any) -> Any:
    from hyprtweaker.engine.model.entities import MonitorRule

    return MonitorRule(output=output, fields=fields)


class FakeSession:
    """Just enough session for a standalone Page: rules, liveness, profiles."""

    def __init__(
        self,
        rules: list[Any],
        *,
        live: bool = True,
        profiles: tuple[tuple[str, Any], ...] = (),
        active: tuple[str, Any] | None = None,
        drifted: bool = False,
    ) -> None:
        self.monitor_rules = rules
        self.live = live
        self._profiles = profiles
        self._active = active
        self._drifted = drifted

    def monitor_profiles(self) -> tuple[tuple[str, Any], ...]:
        return self._profiles

    def active_monitor_profile(self) -> tuple[str, Any] | None:
        return self._active

    def monitor_profile_drift(self) -> bool:
        return self._drifted


class Recorder:
    def __init__(self) -> None:
        self.breaking: list[tuple[str, dict[str, Any]]] = []
        self.benign: list[tuple[str, dict[str, Any]]] = []
        self.renamed: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.saved: list[str] = []
        self.activated: list[str] = []
        self.updated: list[str] = []
        self.detached = 0
        self.deleted: list[str] = []


def build_page(rules: list[Any], session: FakeSession | None = None) -> tuple[Any, Recorder]:
    from gi.repository import Adw

    from hyprtweaker.ui.pages.monitors import MonitorActions, MonitorsPage, ProfileActions

    Adw.init()
    recorder = Recorder()

    def detach() -> None:
        recorder.detached += 1

    page = MonitorsPage(
        session if session is not None else FakeSession(rules),  # type: ignore[arg-type]
        actions=MonitorActions(
            apply_breaking=lambda o, f: recorder.breaking.append((o, dict(f))),
            apply_benign=lambda o, f: recorder.benign.append((o, dict(f))),
            rename=lambda o, t: recorder.renamed.append((o, t)),
            remove=recorder.removed.append,
        ),
        profiles=ProfileActions(
            save=recorder.saved.append,
            activate=recorder.activated.append,
            update=recorder.updated.append,
            detach=detach,
            delete=recorder.deleted.append,
        ),
    )
    return page, recorder


def test_monitors_page_is_in_the_window(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path)

    assert window.monitors_page is not None
    assert window.monitors_page.page.get_title() == "Displays"


def test_catch_all_row_always_exists(tmp_path: Path) -> None:
    page, _recorder = build_page([])

    assert page.catch_all_row is not None
    assert page.catch_all_row.get_title() == "Any other display"


def test_rules_without_their_display_sit_in_not_connected() -> None:
    page, _recorder = build_page(
        [monitor_rule("DP-9", disabled=True), monitor_rule("", mode="preferred")]
    )
    page.set_connected(MONITORS)

    assert [row.get_title() for row in page.disconnected_rows] == ["DP-9"]


def test_canvas_reflects_live_outputs_at_logical_size() -> None:
    page, _recorder = build_page([monitor_rule("desc:BOE 0x0791", scale=1.5)])
    page.set_connected(MONITORS)

    displays = {d.name: d for d in page.canvas.displays}
    # 1920x1080 at scale 1.5 -> 1280x720; DP-3 at scale 1 keeps its pixel size.
    assert (displays["eDP-1"].width, displays["eDP-1"].height) == (1280, 720)
    assert (displays["DP-3"].width, displays["DP-3"].height) == (2560, 1440)
    assert displays["eDP-1"].has_rule
    assert not displays["DP-3"].has_rule  # the hotplug hint's condition


def test_connected_rows_show_one_per_output() -> None:
    page, _recorder = build_page([])
    page.set_connected(MONITORS)

    assert [row.get_title() for row in page.connected_rows] == ["BOE 0x0791", "Dell U2720Q"]


def test_new_rule_prefers_desc_identity_when_unique() -> None:
    page, _recorder = build_page([])
    page.set_connected(MONITORS)

    assert page.identity_for(MONITORS[0]) == "desc:BOE 0x0791"


def test_duplicate_descriptions_fall_back_to_the_connector() -> None:
    twins = (
        dict(MONITORS[0], name="DP-1", description="Dell U2720Q"),
        dict(MONITORS[1], name="DP-2", description="Dell U2720Q"),
    )
    page, _recorder = build_page([])
    page.set_connected(twins)

    assert page.identity_for(twins[0]) == "DP-1"


def test_breaking_fields_route_to_the_confirm_lane() -> None:
    page, recorder = build_page([])
    page.set_connected(MONITORS)

    page._apply("desc:BOE 0x0791", {"mode": "1920x1080@60"})
    page._apply("desc:BOE 0x0791", {"scale": 2})
    page._apply("desc:BOE 0x0791", {"transform": 1})

    assert [fields for _, fields in recorder.breaking] == [
        {"mode": "1920x1080@60"},
        {"scale": 2},
        {"transform": 1},
    ]
    assert recorder.benign == []


def test_benign_fields_apply_instantly() -> None:
    page, recorder = build_page([])
    page.set_connected(MONITORS)

    page._apply("desc:BOE 0x0791", {"vrr": 1})

    assert recorder.benign == [("desc:BOE 0x0791", {"vrr": 1})]
    assert recorder.breaking == []


def test_canvas_drop_writes_an_integer_position_rule() -> None:
    page, recorder = build_page([monitor_rule("desc:BOE 0x0791", scale=1.5)])
    page.set_connected(MONITORS)

    canvas = page.canvas
    display = next(d for d in canvas.displays if d.name == "DP-3")
    scale, _, _ = canvas.transform()
    # Drag DP-3 a little off its 1280x0 berth; the drop must snap back flush against
    # eDP-1's right edge (1280) and commit through the breaking lane.
    canvas._drag_dx = 6 * scale
    canvas._drag_dy = 5 * scale
    x, y = canvas.drop_position(display)
    canvas._drag_dx = canvas._drag_dy = 0.0
    canvas._on_moved(display.name, x, y)

    assert recorder.breaking == [("desc:Dell U2720Q", {"position": "1280x0"})]


def test_first_drag_of_an_unruled_display_creates_its_rule() -> None:
    page, recorder = build_page([])
    page.set_connected(MONITORS)

    page.canvas._on_moved("DP-3", 1280, 0)

    assert recorder.breaking == [("desc:Dell U2720Q", {"position": "1280x0"})]


def test_disconnected_rule_edits_use_the_instant_lane() -> None:
    page, recorder = build_page([monitor_rule("DP-9", mode="1920x1080@60")])
    page.set_connected(MONITORS)

    row = page.disconnected_rows[0]
    assert row is not None
    # Its display is absent, so a field edit cannot break the picture: the row pins the
    # benign lane even for a field the router would otherwise treat as breaking.
    page._apply("DP-9", {"position": "0x0"}, lane=page._actions.apply_benign)
    assert recorder.benign == [("DP-9", {"position": "0x0"})]
    assert recorder.breaking == []


def test_unruled_outputs_is_the_hotplug_hints_condition() -> None:
    page, _recorder = build_page([monitor_rule("desc:BOE 0x0791", scale=1.5)])
    page.set_connected(MONITORS)

    assert page.unruled_outputs == ("DP-3",)


def test_match_by_toggle_renames_the_rule() -> None:
    page, recorder = build_page([monitor_rule("desc:BOE 0x0791", scale=1.5)])
    page.set_connected(MONITORS)

    page._on_match_by_selected(_FakeCombo(1), None, "desc:BOE 0x0791", "eDP-1", "BOE 0x0791")
    assert recorder.renamed == [("desc:BOE 0x0791", "eDP-1")]

    # Re-selecting the form the rule already has is a no-op, not a churn write.
    page._on_match_by_selected(_FakeCombo(0), None, "desc:BOE 0x0791", "eDP-1", "BOE 0x0791")
    assert recorder.renamed == [("desc:BOE 0x0791", "eDP-1")]


class _FakeCombo:
    def __init__(self, selected: int) -> None:
        self._selected = selected

    def get_selected(self) -> int:
        return self._selected


def test_mirror_and_bitdepth_route_to_the_confirm_lane() -> None:
    page, recorder = build_page([])
    page.set_connected(MONITORS)

    page._apply("desc:BOE 0x0791", {"mirror": "DP-3"})
    page._apply("desc:BOE 0x0791", {"bitdepth": 10})

    assert [fields for _, fields in recorder.breaking] == [
        {"mirror": "DP-3"},
        {"bitdepth": 10},
    ]
    assert recorder.benign == []


def test_confirm_revert_dialog_defaults_to_revert() -> None:
    from hyprtweaker.ui.dialogs.confirm_revert import ConfirmRevertDialog

    kept: list[bool] = []
    reverted: list[bool] = []
    dialog = ConfirmRevertDialog(
        on_keep=lambda: kept.append(True),
        on_revert=lambda: reverted.append(True),
        seconds=3,
    )

    # Every way out that is not the Keep button reverts: Esc and the window closing
    # share the close response, the countdown ends in close(), and a blind Enter on a
    # black screen must not lock in the settings that blackened it.
    assert dialog.get_close_response() == "revert"
    assert dialog.get_default_response() == "revert"

    assert dialog.tick() is True
    assert dialog.remaining == 2

    dialog._on_response(dialog, "revert")
    assert reverted == [True]
    assert kept == []
    # A decided dialog is done: later ticks stop the clock and fire nothing further.
    assert dialog.tick() is False
    dialog._on_response(dialog, "keep")
    assert kept == []


def test_confirm_revert_dialog_keep_wins_once() -> None:
    from hyprtweaker.ui.dialogs.confirm_revert import ConfirmRevertDialog

    kept: list[bool] = []
    reverted: list[bool] = []
    dialog = ConfirmRevertDialog(
        on_keep=lambda: kept.append(True),
        on_revert=lambda: reverted.append(True),
    )

    dialog._on_response(dialog, "keep")
    assert kept == [True]
    assert reverted == []


# -- monitor profiles (#69, ADR-0015) --


def _buttons(widget: Any) -> list[Any]:
    """Every Gtk.Button under `widget`, document order -- the probe's way to a suffix."""
    from gi.repository import Gtk

    found: list[Any] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            found.append(child)
        found.extend(_buttons(child))
        child = child.get_next_sibling()
    return found


def _profile(name: str, **overrides: Any) -> Any:
    from hyprtweaker.engine.profiles import MonitorProfile

    base = dict(
        name=name,
        monitors=(monitor_rule("eDP-1", mode="1920x1080@60"),),
        pins={"1": "eDP-1"},
    )
    base.update(overrides)
    return MonitorProfile(**base)


def test_profiles_group_lists_saved_profiles() -> None:
    session = FakeSession(
        [], profiles=(("docked", _profile("Docked")), ("travel", _profile("Travel")))
    )
    page, _recorder = build_page([], session)

    assert [row.get_title() for row in page.profile_rows] == ["Docked", "Travel"]
    assert "1 display rule" in page.profile_rows[0].get_subtitle()
    assert "1 workspace pin" in page.profile_rows[0].get_subtitle()


def test_activate_button_routes_the_slug() -> None:
    session = FakeSession([], profiles=(("docked", _profile("Docked")),))
    page, recorder = build_page([], session)

    activate, _trash = _buttons(page.profile_rows[0])
    assert activate.get_label() == "Activate"
    activate.emit("clicked")
    assert recorder.activated == ["docked"]


def test_the_active_profile_shows_no_activate_button() -> None:
    docked = _profile("Docked")
    session = FakeSession([], profiles=(("docked", docked),), active=("docked", docked))
    page, _recorder = build_page([], session)

    buttons = _buttons(page.profile_rows[0])
    assert [button.get_label() for button in buttons] == [None]  # just the trash icon


def test_a_drifted_profile_offers_update_or_detach() -> None:
    docked = _profile("Docked")
    session = FakeSession(
        [], profiles=(("docked", docked),), active=("docked", docked), drifted=True
    )
    page, recorder = build_page([], session)

    update, detach, _trash = _buttons(page.profile_rows[0])
    assert (update.get_label(), detach.get_label()) == ("Update", "Detach")
    update.emit("clicked")
    detach.emit("clicked")
    assert recorder.updated == ["docked"]
    assert recorder.detached == 1


def test_delete_routes_the_slug() -> None:
    session = FakeSession([], profiles=(("docked", _profile("Docked")),))
    page, recorder = build_page([], session)

    *_rest, trash = _buttons(page.profile_rows[0])
    trash.emit("clicked")
    assert recorder.deleted == ["docked"]


def test_activation_needs_a_live_session() -> None:
    session = FakeSession([], live=False, profiles=(("docked", _profile("Docked")),))
    page, _recorder = build_page([], session)

    activate, _trash = _buttons(page.profile_rows[0])
    assert not activate.get_sensitive()


def test_save_dialog_hands_over_the_name() -> None:
    from gi.repository import Adw

    from hyprtweaker.ui.pages.monitors import SaveProfileDialog

    Adw.init()
    saved: list[str] = []
    dialog = SaveProfileDialog(on_save=saved.append)
    assert not dialog.get_response_enabled("save")

    dialog.entry.set_text("  Docked  ")
    assert dialog.get_response_enabled("save")
    dialog.emit("response", "save")
    assert saved == ["Docked"]


def test_save_dialog_cancel_saves_nothing() -> None:
    from gi.repository import Adw

    from hyprtweaker.ui.pages.monitors import SaveProfileDialog

    Adw.init()
    saved: list[str] = []
    dialog = SaveProfileDialog(on_save=saved.append)
    dialog.entry.set_text("Docked")
    dialog.emit("response", "cancel")
    assert saved == []


def test_profile_toast_offers_a_match_at_open(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    session.save_monitor_profile("Docked", MONITORS)
    # Diverge from the capture, so activating the profile would change something.
    session.monitor_rules.append(monitor_rule("eDP-1", mode="1920x1080@60"))

    window._on_monitors_at_open(MONITORS)
    toast = window.profile_toast
    assert toast is not None
    assert "Docked" in toast.get_title()
    assert toast.get_button_label() == "Activate"


def test_profile_toast_is_app_open_only(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    session.save_monitor_profile("Docked", MONITORS)
    session.monitor_rules.append(monitor_rule("eDP-1", mode="1920x1080@60"))

    window._on_monitors_at_open(MONITORS)
    first = window.profile_toast
    window._profile_toast = None
    window._on_monitors_at_open(MONITORS)
    assert first is not None and window.profile_toast is None


def test_no_toast_when_nothing_would_change(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    # The profile equals the current (empty) setup: activating it is a no-op, so the
    # toast must stay quiet however well the connected set matches.
    session.save_monitor_profile("Empty", MONITORS)
    for rule in list(session.monitor_rules):
        session.monitor_rules.remove(rule)

    window._on_monitors_at_open(MONITORS)
    assert window.profile_toast is None
