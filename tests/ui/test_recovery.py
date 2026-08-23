"""UI smoke tier: the one Banner and the one error dialog, in a real toolkit (#60).

What the policy *is* is settled headless -- the matrix in `tests/unit/test_apply_recovery.py`
and the acting-on-it in `tests/unit/test_session_recovery.py`. What is left for this tier is
the half no headless test can answer: that there really is one Banner widget and not four,
that its button really is wired, and that a dialog button really reaches the session method
the matrix says it should rather than some other path that happens to look similar.

The session below is a real `Session` with its health stubbed, because a session with no
compositor can never reach an unhealthy config state on its own and this tier has no
compositor to give it one.

The toolkit imports sit inside the test functions on purpose: importing ``gi`` at module
scope would raise during collection on a machine without PyGObject, which pytest reports as
an error rather than the skip this tier is supposed to produce.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"

USER_ERROR = "/home/user/.config/hypr/user.lua:12: unexpected symbol near '}'"
APP_ERROR = "/home/user/.config/hypr/hyprtweaker/options/general.lua:4: unknown config key"
ENTRYPOINT_ERROR = "/home/user/.config/hypr/hyprland.lua:2: unexpected symbol"


def build_window(tmp_path: Path, errors: tuple[str, ...] = (), **health: Any) -> Any:
    """A window over a session whose health is whatever the test needs it to be."""
    from gi.repository import Adw

    from hyprtweaker.engine.apply import plan
    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Health, Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    recovery = plan(errors, binds=health.pop("binds", None))

    class StubbedHealth(Session):
        """A fixed unhealthy state, and a record of every recovery the window asked for."""

        calls: list[tuple[str, str]]

        @property
        def recovery(self) -> Any:
            return recovery

        @property
        def health(self) -> Any:
            return Health(recovery=recovery, **health)

        def restore_last_good(self, *modules: str) -> bool:
            self.calls.append(("restore", modules[0]))
            return True

        def regenerate_entrypoint(self) -> bool:
            self.calls.append(("regenerate", ""))
            return True

        def quarantine(self, require: str) -> bool:
            self.calls.append(("quarantine", require))
            return True

        def release_quarantine(self, require: str) -> bool:
            self.calls.append(("release", require))
            return True

    Adw.init()
    session = StubbedHealth(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    session.calls = []
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def banners(widget: Any) -> list[Any]:
    """Every `AdwBanner` in the whole window. ADR-0016 allows exactly one."""
    from gi.repository import Adw

    found = []
    stack = [widget]
    while stack:
        current = stack.pop()
        if isinstance(current, Adw.Banner):
            found.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def buttons(widget: Any) -> list[Any]:
    """Every `GtkButton` under `widget`, in tree order."""
    from gi.repository import Gtk

    found = []
    stack = [widget]
    while stack:
        current = stack.pop()
        if isinstance(current, Gtk.Button):
            found.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


# --- exactly one Banner ---------------------------------------------------------------------


def test_the_window_has_exactly_one_banner(tmp_path: Path) -> None:
    """ADR-0016 §Surfacing: "One persistent Banner ... app-wide".

    Counted in the widget tree rather than trusted from the code, because "one Banner" is a
    claim about what the user sees -- a second one added to a Page later would satisfy every
    other test in this file.
    """
    _session, window = build_window(tmp_path, (APP_ERROR,))

    assert len(banners(window)) == 1


def test_the_banner_shows_the_sessions_line_and_button(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, (APP_ERROR,))
    window.sync()

    (banner,) = banners(window)
    assert banner.get_revealed()
    assert banner.get_title() == session.health.title
    assert banner.get_button_label() == "Details"


def test_a_healthy_session_hides_the_banner(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path)
    window.sync()

    (banner,) = banners(window)
    assert not banner.get_revealed()


def test_a_banner_with_nothing_to_open_has_no_button(tmp_path: Path) -> None:
    """An offline session has a sentence but no errors -- a button would open an empty list."""
    _session, window = build_window(tmp_path, offline_reason="Hyprland is not running")
    window.sync()

    (banner,) = banners(window)
    assert banner.get_revealed()
    assert banner.get_button_label() == ""


# --- the one error dialog ----------------------------------------------------------------


def test_the_dialog_lists_every_error_verbatim(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, (APP_ERROR, USER_ERROR))

    dialog = window.show_errors()

    labels = _labels(dialog.get_extra_child())
    assert APP_ERROR in labels
    assert USER_ERROR in labels


def test_a_hand_edited_module_offers_restore_and_open(tmp_path: Path) -> None:
    """ADR-0016 class 2, as buttons the user can actually reach."""
    _session, window = build_window(tmp_path, (APP_ERROR,))

    dialog = window.show_errors()

    labels = {button.get_label() for button in buttons(dialog.get_extra_child())}
    assert labels == {"Restore last good", "Open file"}


def test_a_foreign_file_offers_open_and_quarantine(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, (USER_ERROR,))

    dialog = window.show_errors()

    labels = {button.get_label() for button in buttons(dialog.get_extra_child())}
    assert labels == {"Open file", "Disable until fixed"}


def test_restore_reaches_the_session(tmp_path: Path) -> None:
    """The wire between the matrix's decision and the session's method."""
    session, window = build_window(tmp_path, (APP_ERROR,))
    dialog = window.show_errors()

    _click(dialog, "Restore last good")

    assert session.calls == [("restore", "options/general.lua")]


def test_regenerate_reaches_the_session(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, (ENTRYPOINT_ERROR,))
    dialog = window.show_errors()

    _click(dialog, "Regenerate")

    assert session.calls == [("regenerate", "")]


def test_quarantine_asks_before_disabling_somebody_elses_file(tmp_path: Path) -> None:
    """The consent gate ADR-0016 requires: the click opens a question, not a write."""
    session, window = build_window(tmp_path, (USER_ERROR,))
    dialog = window.show_errors()

    _click(dialog, "Disable until fixed")

    assert session.calls == [], "nothing is disabled until the user says so"


def test_an_auto_reverted_error_offers_no_actions(tmp_path: Path) -> None:
    """By the time Details can be clicked the app has already put the file back."""
    from hyprtweaker.engine.apply import plan
    from hyprtweaker.ui.dialogs.errors import error_dialog

    _session, window = build_window(tmp_path)

    dialog = error_dialog(window, plan([APP_ERROR]))

    assert buttons(dialog.get_extra_child()) == []


def test_the_banner_button_opens_the_dialog(tmp_path: Path) -> None:
    """The Banner is the only way to reach the error dialog, so the wiring is the feature."""
    _session, window = build_window(tmp_path, (APP_ERROR,))
    window.sync()

    opened: list[Any] = []
    window.show_errors = lambda: opened.append(True)  # type: ignore[method-assign]

    (banner,) = banners(window)
    banner.emit("button-clicked")  # the signal a real click raises

    assert opened == [True]


def test_the_banner_button_lifts_a_quarantine_when_there_is_nothing_to_show(
    tmp_path: Path,
) -> None:
    """One button, two jobs -- and `Health.button` is what decided which one this is."""
    session, window = build_window(tmp_path, quarantined=("user",))
    window.sync()

    (banner,) = banners(window)
    assert banner.get_button_label() == "Re-enable"
    banner.emit("button-clicked")

    assert session.calls == [("release", "user")]


def _click(dialog: Any, label: str) -> None:
    for button in buttons(dialog.get_extra_child()):
        if button.get_label() == label:
            button.emit("clicked")
            return
    raise AssertionError(f"no {label!r} button in the dialog")


def _labels(widget: Any) -> set[str]:
    from gi.repository import Gtk

    found = set()
    stack = [widget]
    while stack:
        current = stack.pop()
        if isinstance(current, Gtk.Label):
            found.add(current.get_label())
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found
