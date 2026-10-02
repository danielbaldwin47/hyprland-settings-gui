"""UI tier: the Theme override and "Forget remembered choices" in the primary menu (#183).

The override changes this app's own colour scheme through `Adw.StyleManager`, never the
desktop's. "System" is asserted as the scheme the app asks for (`DEFAULT`) and the stored
choice, never as light or dark: what System *looks* like is the platform's answer (until
#212 gives this tier a private D-Bus bus, the answer of whatever settings portal the worker
can reach), so no assertion here depends on it. Light and Dark are forced schemes, so their
`dark` is asserted outright, and the drawn surfaces are checked on those two grounds.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from started_app import started_application

APP_VERSION = "0.0.0-test"


@pytest.fixture(autouse=True)
def system_scheme_after() -> Iterator[None]:
    """The style manager is one per process: leave it as every other test expects it."""
    yield
    from gi.repository import Adw

    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)


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
    app = started_application()
    return MainWindow(session, application=app)


def stored(tmp_path: Path) -> Any:
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.prefs import PrefsStore

    return PrefsStore(ConfigPaths.rooted_at(tmp_path).state_dir).load()


def write_prefs(tmp_path: Path, **fields: Any) -> None:
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.prefs import FORMAT_VERSION, PREFS_FILENAME

    path = ConfigPaths.rooted_at(tmp_path).state_dir / PREFS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format_version": FORMAT_VERSION, **fields}), encoding="utf-8")


def scheme() -> tuple[Any, bool]:
    from gi.repository import Adw

    manager = Adw.StyleManager.get_default()
    return manager.get_color_scheme(), manager.get_dark()


def choose(window: Any, theme: str) -> None:
    """What clicking one of the three radio items does."""
    from gi.repository import GLib

    window.activate_action("win.theme", GLib.Variant.new_string(theme))


MenuItem = tuple[str, str, str | None, str | None]
"""(label, action, target, hidden-when) of one primary-menu item."""


def primary_menu(window: Any) -> list[tuple[str | None, list[MenuItem]]]:
    """The primary menu as the user sees it: (section heading, [(label, action, target)])."""
    from gi.repository import Gio, Gtk

    def find(widget: Any) -> Any:
        if (
            isinstance(widget, Gtk.MenuButton)
            and widget.get_icon_name() == "open-menu-symbolic"
        ):
            return widget
        child = widget.get_first_child()
        while child is not None:
            if (found := find(child)) is not None:
                return found
            child = child.get_next_sibling()
        return None

    def items(model: Any) -> list[MenuItem]:
        entries = []
        for index in range(model.get_n_items()):
            label = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_LABEL, None)
            action = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_ACTION, None)
            target = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_TARGET, None)
            hidden = model.get_item_attribute_value(index, "hidden-when", None)
            if action is not None:  # a section's heading is a label with no action
                entries.append(
                    (
                        label.get_string(),
                        action.get_string(),
                        None if target is None else target.get_string(),
                        None if hidden is None else hidden.get_string(),
                    )
                )
        return entries

    model = find(window).get_popover().get_menu_model()
    sections = [(None, items(model))]
    for index in range(model.get_n_items()):
        section = model.get_item_link(index, Gio.MENU_LINK_SECTION)
        if section is not None:
            heading = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_LABEL, None)
            sections.append((None if heading is None else heading.get_string(), items(section)))
    return sections


# --- the Theme section ------------------------------------------------------------------------


def test_the_primary_menu_offers_system_light_and_dark_then_forgetting(tmp_path: Path) -> None:
    window = build_window(tmp_path)

    sections = primary_menu(window)
    theme = [heading for heading, _ in sections].index("Theme")

    assert sections[theme : theme + 2] == [
        (
            "Theme",
            [
                ("System", "win.theme", "system", None),
                ("Light", "win.theme", "light", None),
                ("Dark", "win.theme", "dark", None),
            ],
        ),
        # Hidden, not greyed, while nothing is remembered: a menu item cannot say why it is
        # grey, and #170 is what first remembers an answer.
        (
            None,
            [("Forget remembered choices", "win.forget-remembered", None, "action-disabled")],
        ),
    ]


def test_each_theme_switches_the_app_live_and_survives_a_relaunch(tmp_path: Path) -> None:
    """#78 "Theme override switches live": no restart between choices."""
    from gi.repository import Adw

    window = build_window(tmp_path)

    choose(window, "dark")
    assert scheme() == (Adw.ColorScheme.FORCE_DARK, True)
    assert window.lookup_action("theme").get_state().get_string() == "dark"
    assert stored(tmp_path).theme == "dark"

    choose(window, "light")
    assert scheme() == (Adw.ColorScheme.FORCE_LIGHT, False)
    assert window.lookup_action("theme").get_state().get_string() == "light"
    assert stored(tmp_path).theme == "light"

    choose(window, "system")
    assert scheme()[0] == Adw.ColorScheme.DEFAULT
    assert window.lookup_action("theme").get_state().get_string() == "system"
    assert stored(tmp_path).theme == "system"


def test_the_saved_theme_is_in_force_before_the_window_is_shown(tmp_path: Path) -> None:
    """Applied at construction: the first frame is already the user's choice, no flash."""
    from gi.repository import Adw

    write_prefs(tmp_path, theme="dark")

    window = build_window(tmp_path)

    assert not window.get_visible()
    assert scheme() == (Adw.ColorScheme.FORCE_DARK, True)
    assert window.lookup_action("theme").get_state().get_string() == "dark"


def test_an_unknown_saved_theme_opens_as_system(tmp_path: Path) -> None:
    """A newer app's name, or a hand edit, degrades to System instead of failing to open."""
    from gi.repository import Adw

    Adw.init()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    write_prefs(tmp_path, theme="solarized")

    window = build_window(tmp_path)

    assert scheme()[0] == Adw.ColorScheme.DEFAULT
    assert window.lookup_action("theme").get_state().get_string() == "system"


# --- Forget remembered choices ----------------------------------------------------------------


def test_forgetting_is_unavailable_while_nothing_is_remembered(tmp_path: Path) -> None:
    window = build_window(tmp_path)

    assert window.lookup_action("forget-remembered").get_enabled() is False


def test_forgetting_clears_every_remembered_choice_and_keeps_the_rest(tmp_path: Path) -> None:
    from gi.repository import Adw

    write_prefs(
        tmp_path,
        view="config",
        theme="dark",
        remembered={"import-overwrite": "replace", "leave-unsaved": "discard"},
    )
    window = build_window(tmp_path)
    forget = window.lookup_action("forget-remembered")
    assert forget.get_enabled() is True

    window.activate_action("win.forget-remembered", None)

    after = stored(tmp_path)
    assert dict(after.remembered) == {}
    assert (after.view, after.theme) == ("config", "dark")
    assert forget.get_enabled() is False
    assert scheme()[0] == Adw.ColorScheme.FORCE_DARK


def test_remembering_a_choice_makes_forgetting_available(tmp_path: Path) -> None:
    """The way #170's dialog will store an answer: through the window's one remember path."""
    window = build_window(tmp_path)

    window._remember(window._prefs.with_remembered("import-overwrite", "replace"))

    assert window.lookup_action("forget-remembered").get_enabled() is True
    assert dict(stored(tmp_path).remembered) == {"import-overwrite": "replace"}


# --- swatches legible on both grounds (ADR-0019) ----------------------------------------------

NEAR_BLACK = "#050505"
NEAR_WHITE = "#fafafa"
SEE_THROUGH = "#33ccff55"


def luminance(pixel: tuple[int, int, int]) -> float:
    """WCAG relative luminance of an sRGB pixel."""

    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in pixel)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class Drawn:
    """A widget as rendered on its window, readable pixel by pixel in widget coordinates."""

    def __init__(self, widget: Any, margin: int) -> None:
        from gi.repository import Gdk, Graphene, Gtk

        native = widget.get_native()
        found, bounds = widget.compute_bounds(native)
        assert found
        self.left = round(bounds.get_x()) - margin
        self.top = round(bounds.get_y()) - margin
        self.width = round(bounds.get_width())
        self.height = round(bounds.get_height())
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(native).snapshot(
            snapshot, native.get_width(), native.get_height()
        )
        viewport = Graphene.Rect().init(
            self.left, self.top, self.width + 2 * margin, self.height + 2 * margin
        )
        texture = native.get_renderer().render_texture(snapshot.to_node(), viewport)
        downloader = Gdk.TextureDownloader.new(texture)
        downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, self._stride = downloader.download_bytes()
        self._data = data.get_data()
        self._margin = margin

    def at(self, x: int, y: int) -> tuple[int, int, int]:
        """The pixel at widget coordinates (x, y); negative or past the width is the ground."""
        offset = (y + self._margin) * self._stride + (x + self._margin) * 4
        r, g, b, _a = self._data[offset : offset + 4]
        return (r, g, b)


def drawn_strip(colors: tuple[str, ...], scheme: Any) -> Drawn:
    """A swatch strip as the Row summary shows it, on a card, under `scheme`."""
    from gi.repository import Adw, Gtk
    from main_loop import settle

    from hyprtweaker.ui.rows.chrome import SwatchStrip

    Adw.init()
    Adw.StyleManager.get_default().set_color_scheme(scheme)
    strip = SwatchStrip()
    strip.set_colors(colors)
    card = Gtk.Box(css_classes=["card"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    card.append(strip.widget)
    for side in ("start", "end", "top", "bottom"):
        card.set_property(f"margin-{side}", 0)
        strip.widget.set_property(f"margin-{side}", 12)
    window = Gtk.Window(child=card, default_width=200, default_height=80)
    window.present()
    deadline = 200
    while not (strip.widget.get_mapped() and strip.widget.get_width() > 0) and deadline:
        settle("the swatch strip to map")
        deadline -= 1
    settle("the swatch strip to draw")
    return Drawn(strip.widget, margin=4)


@pytest.mark.parametrize("scheme_name", ["FORCE_DARK", "FORCE_LIGHT"])
def test_a_swatch_the_colour_of_the_ground_still_has_a_visible_edge(scheme_name: str) -> None:
    """A near-black stop on the dark ground, a near-white one on the light: without an edge
    either is a hole in the strip. WCAG 1.4.11 asks 3:1 for a graphical object's boundary."""
    from gi.repository import Adw

    scheme = getattr(Adw.ColorScheme, scheme_name)
    blend = NEAR_BLACK if scheme_name == "FORCE_DARK" else NEAR_WHITE
    drawn = drawn_strip((blend, blend), scheme)
    middle = drawn.height // 2

    ground, edge, inside = drawn.at(-2, middle), drawn.at(0, middle), drawn.at(4, middle)

    assert contrast(edge, ground) >= 3.0, (ground, edge)
    assert contrast(edge, inside) >= 3.0, (edge, inside)


def test_a_see_through_stop_sits_on_a_checkerboard() -> None:
    """ADR-0019: alpha shows as alpha, not as a paler opaque colour."""
    from gi.repository import Adw

    drawn = drawn_strip((SEE_THROUGH,), Adw.ColorScheme.FORCE_DARK)

    row = [drawn.at(x, 4) for x in range(2, drawn.width - 2)]

    assert len(set(row)) >= 2, row
    assert contrast(min(row, key=luminance), max(row, key=luminance)) > 1.2, row


def test_an_opaque_stop_is_one_flat_colour() -> None:
    from gi.repository import Adw

    drawn = drawn_strip(("#33ccff",), Adw.ColorScheme.FORCE_DARK)

    row = {drawn.at(x, 4) for x in range(2, drawn.width - 2)}

    assert row == {(0x33, 0xCC, 0xFF)}


# --- the Displays canvas legible on both grounds (ADR-0019) -----------------------------------


def drawn_canvas(scheme: Any) -> tuple[Any, Drawn]:
    """Two displays, one with a rule (solid outline), one without (dashed), under `scheme`."""
    from gi.repository import Adw, Gtk
    from main_loop import settle

    from hyprtweaker.ui.pages.monitors import ArrangementCanvas, DisplayRect

    Adw.init()
    Adw.StyleManager.get_default().set_color_scheme(scheme)
    canvas = ArrangementCanvas(on_moved=lambda *_: None)
    canvas.set_displays(
        [
            DisplayRect("eDP-1", 0, 0, 1280, 720, True),
            DisplayRect("DP-3", 1280, 0, 2560, 1440, False),
        ]
    )
    window = Gtk.Window(child=canvas, default_width=600, default_height=260)
    window.present()
    deadline = 200
    while not (canvas.get_mapped() and canvas.get_width() > 0) and deadline:
        settle("the canvas to map")
        deadline -= 1
    settle("the canvas to draw")
    return canvas, Drawn(canvas, margin=0)


@pytest.mark.parametrize("scheme_name", ["FORCE_DARK", "FORCE_LIGHT"])
def test_every_display_outline_on_the_canvas_stands_off_the_ground(scheme_name: str) -> None:
    """The dashed outline of a display with no rule is its only boundary: 3:1 (WCAG 1.4.11)."""
    from gi.repository import Adw

    canvas, drawn = drawn_canvas(getattr(Adw.ColorScheme, scheme_name))
    ground = drawn.at(2, 2)

    for display in canvas.displays:
        x, y, w, h = canvas.canvas_rect(display)
        bottom = [
            drawn.at(column, round(y + h) - 1) for column in range(round(x), round(x + w))
        ]
        strongest = max(contrast(pixel, ground) for pixel in bottom)

        assert strongest >= 3.0, (display.name, ground, bottom[:12])
