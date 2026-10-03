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

from started_app import started_application

APP_VERSION = "0.0.0-test"

USER_ERROR = "/home/user/.config/hypr/user.lua:12: unexpected symbol near '}'"
APP_ERROR = "/home/user/.config/hypr/hyprtweaker/options/general.lua:4: unknown config key"
ENTRYPOINT_ERROR = "/home/user/.config/hypr/hyprland.lua:2: unexpected symbol"


def build_window(tmp_path: Path, errors: tuple[str, ...] = (), **health: Any) -> Any:
    """A window over a session whose health is whatever the test needs it to be."""
    from gi.repository import Adw, GLib

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

        can_restore = True

        def restorable(self, module: str) -> bool:
            return self.can_restore

        unverified_import = False

        def unverified_since_import(self, module: str) -> bool:
            return self.unverified_import

        restore_start: Any = None
        """What `restore_last_good` answers; `None` queues it with no copy kept."""
        restore_ends = True
        """Whether a queued restore ends at once, and how (`done(True)`)."""
        edited = True

        def edited_outside(self, module: str) -> bool:
            return self.edited

        def restore_last_good(self, *modules: str, done: Any = None) -> Any:
            from hyprtweaker.session import RestoreStart

            self.calls.append(("restore", modules[0]))
            start = self.restore_start
            if start is None:
                start = RestoreStart(queued=True)
            if start and done is not None and self.restore_ends is not None:
                # After the answer, as the real one's spawned transaction is.
                GLib.idle_add(lambda: done(self.restore_ends) and False)
            return start

        def regenerate_entrypoint(self) -> bool:
            self.calls.append(("regenerate", ""))
            return True

        def quarantine(self, require: str) -> bool:
            self.calls.append(("quarantine", require))
            return True

        def release_quarantine(self, *requires: str) -> bool:
            self.calls.append(("release", requires))
            return True

    Adw.init()
    session = StubbedHealth(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    session.calls = []
    app = started_application()
    window = MainWindow(session, application=app)

    # Toasts are counted at the door: `AdwToastOverlay` exposes no queue to read back, and
    # "did this raise a toast?" is a real question for ADR-0016's toasts-only-for-auto-revert
    # rule.
    window._toast_log = []
    raise_toast = window._toasts.add_toast

    def counted(toast: Any) -> None:
        window._toast_log.append(toast.get_title())
        raise_toast(toast)

    window._toasts.add_toast = counted
    return session, window


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


def test_a_rejected_apply_raises_the_banner_by_itself(tmp_path: Path) -> None:
    """The app's own transaction is how a user most often discovers the config is broken.

    Regression: the Banner used to depend on a full `sync()`, which nothing called after an
    apply -- so a rejected write raised a toast and left the Banner hidden until some
    unrelated state change happened to refresh it.
    """
    from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult

    _session, window = build_window(tmp_path, (APP_ERROR,))
    (banner,) = banners(window)
    # Hidden by hand, so what follows can only be `show_result`'s doing. Under the defect it
    # stayed hidden: nothing between an apply and the Banner ever called `sync`.
    banner.set_revealed(False)

    window.show_result(ApplyResult(ApplyOutcome.CONFIG_ERRORS, errors=(APP_ERROR,)))

    assert banner.get_revealed()
    assert banner.get_button_label() == "Details"


def test_a_config_error_does_not_also_toast(tmp_path: Path) -> None:
    """ADR-0016: "Toasts only for transient auto-revert events."

    An error with a file behind it belongs to the Banner, which can offer to fix it. A toast
    would be a second surface saying the same thing and offering nothing.
    """
    from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult

    _session, window = build_window(tmp_path, (APP_ERROR,))

    window.show_result(ApplyResult(ApplyOutcome.CONFIG_ERRORS, errors=(APP_ERROR,)))

    assert _toast_count(window) == 0


def test_a_value_that_did_not_take_does_not_also_toast(tmp_path: Path) -> None:
    """A read-back mismatch already raises the Banner and badges its Row; a toast would be a
    third surface saying the same sentence."""
    from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult, Mismatch

    _session, window = build_window(tmp_path)

    window.show_result(
        ApplyResult(
            ApplyOutcome.READ_BACK_MISMATCH,
            keys=("decoration:rounding",),
            mismatches=(Mismatch("decoration:rounding", 12, None, live_set=False),),
        )
    )

    assert _toast_count(window) == 0


def test_a_failure_with_no_config_errors_still_toasts(tmp_path: Path) -> None:
    """A refused write never reached the compositor, so no Banner state describes it."""
    from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult

    _session, window = build_window(tmp_path)

    window.show_result(ApplyResult(ApplyOutcome.WRITE_FAILED, detail="disk full"))

    assert _toast_count(window) == 1


def test_a_severe_state_makes_the_banner_red(tmp_path: Path) -> None:
    """ADR-0016 asks for a "Red Banner" on an Entrypoint refusal."""
    _session, window = build_window(tmp_path, (ENTRYPOINT_ERROR,))
    window.sync()

    (banner,) = banners(window)
    assert "error" in banner.get_css_classes()


def test_an_ordinary_error_leaves_the_banner_plain(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, (APP_ERROR,))
    window.sync()

    (banner,) = banners(window)
    assert "error" not in banner.get_css_classes()


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
    confirm = window.get_visible_dialog()
    assert confirm.get_heading() == "Restore general.lua?"
    confirm.emit("response", "restore")
    idle()

    assert session.calls == [("restore", "options/general.lua")]
    assert window._toast_log[-1] == "general.lua is back to the last version Hyprland accepted"


def idle() -> None:
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def restore_through_dialog(window: Any) -> Any:
    _click(window.show_errors(), "Restore last good")
    confirm = window.get_visible_dialog()
    body = confirm.get_body()
    confirm.emit("response", "restore")
    idle()
    return body


def test_a_restore_names_where_the_copy_of_the_edited_file_is(tmp_path: Path) -> None:
    """#266: the answer says where this restore put the hand edit, not just the folder."""
    from hyprtweaker.session import RestoreStart

    session, window = build_window(tmp_path, (APP_ERROR,))
    copy = tmp_path / "state" / "edited-copies" / "20261003-120000" / "options" / "general.lua"
    session.restore_start = RestoreStart(queued=True, copies={"options/general.lua": copy})

    body = restore_through_dialog(window)

    assert body == (
        "general.lua goes back to the last version this app wrote and Hyprland accepted. "
        f"A copy of the file as it is now is kept in {tmp_path / 'state' / 'edited-copies'}."
    )
    assert window._toast_log[-1] == (
        f"general.lua is back to the last version Hyprland accepted. A copy of your edited "
        f"file is at {copy}"
    )


def test_a_restore_that_cannot_keep_a_copy_says_nothing_was_restored(tmp_path: Path) -> None:
    from hyprtweaker.session import RestoreStart

    session, window = build_window(tmp_path, (APP_ERROR,))
    session.restore_start = RestoreStart(queued=False, uncopied=("options/general.lua",))

    restore_through_dialog(window)

    assert window._toast_log == [
        "general.lua was not restored: a copy of it could not be kept, so it was left as it is"
    ]


def test_a_file_the_app_wrote_is_restored_without_promising_a_copy(tmp_path: Path) -> None:
    """Only a hand edit is copied, so only a hand-edited file's dialog promises one."""
    session, window = build_window(tmp_path, (APP_ERROR,))
    session.edited = False
    session.restore_ends = False

    body = restore_through_dialog(window)

    assert (
        body
        == "general.lua goes back to the last version this app wrote and Hyprland accepted."
    )
    assert window._toast_log == [
        "general.lua could not be restored. The Banner says what is wrong"
    ]


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

    assert session.calls == [("release", ("user",))]


def test_two_quarantines_are_lifted_in_one_rewrite(tmp_path: Path) -> None:
    """Two calls would be two Entrypoint rewrites racing each other through the queue."""
    session, window = build_window(tmp_path, quarantined=("bridge/matugen", "user"))
    window.sync()

    (banner,) = banners(window)
    banner.emit("button-clicked")

    assert session.calls == [("release", ("bridge/matugen", "user"))]


def _toast_count(window: Any) -> int:
    """How many toasts the window has raised. The overlay does not expose a queue, so the
    stub counts them at the door."""
    return len(window._toast_log)


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


LONG_USER_ERROR = (
    "/home/alex/.config/hypr/user.lua:2: syntax error near 'is' while loading the user "
    "module required last by hyprland.lua"
)


def test_the_error_and_its_buttons_fit_inside_the_dialog(tmp_path: Path) -> None:
    """Hand-test 14 of #148: the error was one unwrapped line in a sideways-scrolling box,
    cut at "user.lua:2: s", and "Disable until fixed" sat past the dialog's right edge."""
    import main_loop
    from gi.repository import Gtk

    _session, window = build_window(tmp_path, (LONG_USER_ERROR,))
    window.set_default_size(1090, 800)
    window.present()
    dialog = window.show_errors()
    main_loop.settle("the error dialog to lay out")

    viewport = dialog.get_extra_child()  # what the dialog shows of the errors
    width = viewport.get_width()
    assert width > 0
    shown = [*buttons(dialog.get_extra_child())]
    shown += [
        label
        for label in _all(dialog.get_extra_child())
        if isinstance(label, Gtk.Label) and label.get_label() == LONG_USER_ERROR
    ]
    assert len(shown) == 3
    for widget in shown:
        ok, bounds = widget.compute_bounds(viewport)
        assert ok
        assert bounds.origin.x >= 0, widget
        assert bounds.origin.x + bounds.size.width <= width, (widget, bounds.size.width, width)


def _all(widget: Any) -> list[Any]:
    """Every widget under `widget`, in tree order."""
    found = []
    stack = [widget]
    while stack:
        current = stack.pop()
        found.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def test_restore_is_not_offered_without_a_restore_point(tmp_path: Path) -> None:
    """#148 hand-test 17: offered for a Module with no confirmed write, it closed the
    dialog and did nothing. Without one the card says so and offers Open file."""
    session, window = build_window(tmp_path, (APP_ERROR,))
    session.can_restore = False

    dialog = window.show_errors()

    labels = {button.get_label() for button in buttons(dialog.get_extra_child())}
    assert labels == {"Open file"}
    assert (
        "This app has no earlier version of general.lua that Hyprland accepted, so there is "
        "nothing to restore. Open the file to fix the error."
    ) in _labels(dialog.get_extra_child())


def test_an_import_kept_unverified_says_why_there_is_no_restore_point(
    tmp_path: Path,
) -> None:
    """#259 AC4: not "no earlier version" -- there are older ones, and none is offered."""
    session, window = build_window(tmp_path, (APP_ERROR,))
    session.can_restore = False
    session.unverified_import = True

    dialog = window.show_errors()

    labels = {button.get_label() for button in buttons(dialog.get_extra_child())}
    assert labels == {"Open file"}
    assert (
        "There is no verified restore point for general.lua since the import, because its "
        "settings could not all be read back when it was kept. Open the file to fix the "
        "error."
    ) in _labels(dialog.get_extra_child())


def test_cancelling_the_restore_confirm_restores_nothing(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, (APP_ERROR,))
    dialog = window.show_errors()

    _click(dialog, "Restore last good")
    window.get_visible_dialog().emit("response", "cancel")

    assert session.calls == []
