"""UI smoke tier: the Presets group of the Theming page (#171, ADR-0014).

The engine is proven headless (`tests/unit/test_session_presets.py`, `test_presets_archive.py`,
`test_session_preset_colours.py`). What this tier answers is what the assembled group shows
and does: an empty list that explains itself, a save dialog with exactly the five Capture
scopes and each one's size, "Replace <name>?" before an overwrite, an Apply that says what it
changes before it does it and leaves the undo toast, a wallpaper part that says when it will
do nothing, an import preview that asks whose colours win only when a wallpaper tool sets
them, and a remembered answer that can be forgotten.

The session is a real `Session` over a tmp App dir, offline unless a test makes it live
(`_offline_reason = None`), with a fake wallpaper daemon through the tool seam. The Apply path
is a stub over the real one, as `test_undo.py` does: a read-only session cannot apply and this
tier has no compositor; the transaction itself is `test_session_presets.py`'s.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import main_loop
import pytest
from started_app import started_application

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

BORDER_SIZE = "general:border_size"
INACTIVE = "general:col.inactive_border"
MINE = Path("/pictures/mine.png")


class FakeDaemon:
    """swww on the tool path and running: the one wallpaper daemon this app can drive."""

    def __init__(self, runtime: Path) -> None:
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "wayland-1-swww-daemon..sock").touch()
        self.environ = {"XDG_RUNTIME_DIR": str(runtime), "WAYLAND_DISPLAY": "wayland-1"}
        self.calls: list[tuple[str, ...]] = []

    def find(self, name: str) -> Path | None:
        return Path("/opt/tools/swww") if name == "swww" else None

    def run(self, argv: Sequence[str], *, timeout: float) -> Any:
        from hyprtweaker.engine.tools import ToolRun

        self.calls.append(tuple(argv[1:]))
        line = f": DP-1: 2560x1440, scale: 1, currently displaying: image: {MINE}\n"
        return ToolRun(tuple(argv), 0, line if argv[1] == "query" else "", "")

    def seam(self) -> Any:
        from hyprtweaker.engine.wallpaper import Wallpapers

        return Wallpapers(find=self.find, run=self.run, environ=self.environ)


def no_daemon() -> Any:
    from hyprtweaker.engine.wallpaper import Wallpapers

    return Wallpapers(find=lambda _name: None, environ={})


def build(
    tmp_path: Path, *, live: bool = False, daemon: bool = False, wallpaper_source: bool = False
) -> tuple[Any, Any]:
    """A window over a session in a tmp App dir. `wallpaper_source` has matugen set colours."""
    from gi.repository import Adw

    from hyprtweaker.engine.apply import Edit, PresetStep, UndoStep
    from hyprtweaker.engine.bridge import ColorSource, Wallpaper
    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.presets import ColorChoice, PresetApplied, PresetColorConflict
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    class Applying(Session):
        """`apply_preset` sets the model and records the step; `undo` puts the values back."""

        applied: list[tuple[str, ColorChoice | None, bool]]
        before: dict[str, Any]

        def color_source(self) -> ColorSource:
            if wallpaper_source:
                return Wallpaper("matugen")
            return super().color_source()

        def apply_preset(
            self, slug: str, *, colors: ColorChoice | None = None, wallpaper: bool = False
        ) -> Any:
            self.applied.append((slug, colors, wallpaper))
            preset = dict(self.presets())[slug]
            if self.preset_color_conflict(preset) is not None and colors is None:
                return PresetColorConflict(Wallpaper("matugen"))
            edits = []
            for name, value in preset.options.items():
                before = self.model.get(name)
                self.before[name] = before
                self.model.set(name, value)
                edits.append(Edit(name, before, value))
            step = PresetStep.of(preset.name, UndoStep.of(edits))
            if step is not None and self.on_recorded is not None:
                self.on_recorded(step)
            return PresetApplied(tuple(preset.options), ())

        @property
        def can_undo(self) -> bool:
            return bool(self.before)

        def undo(self) -> bool:
            for name, value in self.before.items():
                self.model.set(name, value)
            self.before = {}
            return True

    Adw.init()
    fake = FakeDaemon(tmp_path / "run")
    session = Applying(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path / "config"),
        app_version="0.0.0-test",
        connect=no_compositor,
        wallpapers=fake.seam() if daemon else no_daemon(),
    )
    session.applied = []
    session.before = {}
    session.daemon = fake  # type: ignore[attr-defined]
    if live:
        session._offline_reason = None
    window = MainWindow(session, application=started_application())
    session.on_recorded = window.offer_undo
    session.on_preset_note = window.show_preset_note
    return session, window


def save(session: Any, name: str, *scopes: Any, replace: bool = False) -> list[Any]:
    from hyprtweaker.engine.presets import CaptureScope

    results: list[Any] = []
    session.save_preset(
        name, scopes or (CaptureScope.GAPS_LAYOUT,), replace=replace, done=results.append
    )
    return results


def a_preset(session: Any, name: str = "Nord", **options: Any) -> str:
    """A saved Preset: `border_size` 3 unless `options` say otherwise; its slug."""
    from hyprtweaker.engine.presets import CaptureScope, Preset, PresetStore

    store = session._preset_store
    preset = Preset(
        name=name,
        created=datetime(2026, 10, 2, 12, 0, tzinfo=UTC),
        scopes=frozenset({CaptureScope.GAPS_LAYOUT}),
        options=options or {BORDER_SIZE: 3},
        app_version="0.0.0-test",
        hyprland_version="0.56.2",
    )
    slug = PresetStore.slug_for(name)
    store.write(slug, preset)
    return slug


def walk(widget: Any) -> list[Any]:
    found = []
    child = widget.get_first_child()
    while child is not None:
        found.append(child)
        found.extend(walk(child))
        child = child.get_next_sibling()
    return found


def labelled(widget: Any, label: str) -> Any:
    from gi.repository import Gtk

    [found] = [
        w
        for w in walk(widget)
        if isinstance(w, Gtk.Button | Gtk.CheckButton) and w.get_label() == label
    ]
    return found


SHOWN_TOASTS: list[str] = []


@pytest.fixture(autouse=True)
def record_toasts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overlay keeps no list of what it showed: note each title as it is added."""
    from gi.repository import Adw

    SHOWN_TOASTS.clear()
    real = Adw.ToastOverlay.add_toast

    def add_toast(self: Any, toast: Any) -> None:
        SHOWN_TOASTS.append(toast.get_title())
        real(self, toast)

    monkeypatch.setattr(Adw.ToastOverlay, "add_toast", add_toast)


def toasts(_window: Any) -> list[str]:
    return SHOWN_TOASTS


def presets_of(window: Any) -> Any:
    return window.theming_page.presets


def answer(dialog: Any, response: str) -> None:
    dialog.emit("response", response)
    main_loop.settle("the dialog")


# --- the list ---------------------------------------------------------------------------------


def test_an_empty_group_says_what_a_preset_is_and_how_to_save_the_first(tmp_path: Path) -> None:
    _session, window = build(tmp_path)
    group = presets_of(window)

    assert group.group.get_title() == "Presets"
    assert group.rows == {}
    assert group.others == (
        (
            "No presets yet",
            "A preset keeps a look (colors, gaps, animation switches, fonts, wallpaper) so "
            "you can switch back to it later. Press “Save current as preset” to keep this one.",
        ),
    )
    assert group.save_button.get_label() == "Save current as preset…"
    assert group.save_button.get_visible()


def test_a_row_names_what_a_preset_keeps_and_when(tmp_path: Path) -> None:
    session, window = build(tmp_path)
    slug = a_preset(session)
    window.theming_page.refresh()
    group = presets_of(window)

    assert group.others == ()
    row = group.rows[slug]
    assert row.get_title() == "Nord"
    assert row.get_subtitle() == "Gaps & layout · saved 2 Oct 2026"
    for verb in ("apply", "export", "delete"):
        button = group.button(slug, verb)
        assert button.get_sensitive() or verb == "apply"
        assert button.get_focusable(), f"{verb} must be reachable by keyboard"


def test_a_preset_saved_elsewhere_shows_after_the_page_refreshes(tmp_path: Path) -> None:
    session, window = build(tmp_path)
    assert presets_of(window).rows == {}

    session.model.set(BORDER_SIZE, 4)
    [saved] = save(session, "Fjord")
    window.theming_page.refresh()

    assert list(presets_of(window).rows) == [saved.slug]


# --- save by scope ----------------------------------------------------------------------------


def test_the_save_dialog_offers_the_five_scopes_each_with_its_size(tmp_path: Path) -> None:
    from hyprtweaker.engine.presets import CaptureScope

    session, window = build(tmp_path, live=True, daemon=True)
    group = presets_of(window)

    group.save_button.emit("clicked")
    dialog = group.dialog

    assert dialog.get_heading() == "Save current as preset"
    titles = [(row.get_title(), row.get_subtitle()) for row in dialog.rows.values()]
    assert [title for title, _ in titles] == [
        "Colors",
        "Gaps & layout",
        "Animation switches",
        "Fonts & cursor",
        "Wallpaper",
    ]
    assert "Animations" not in [title for title, _ in titles]
    sizes = dict(titles)
    assert sizes["Animation switches"] == "2 settings"
    for scope in CaptureScope:
        if scope is not CaptureScope.WALLPAPER:
            assert sizes[scope.label] == f"{session.scope_size(scope)} settings"
    assert sizes["Wallpaper"] == "Your wallpaper"
    assert dialog.get_response_enabled("save") is False  # no name yet


def test_saving_writes_a_preset_of_the_chosen_scopes_and_lists_it(tmp_path: Path) -> None:
    from hyprtweaker.engine.presets import CaptureScope

    session, window = build(tmp_path)
    session.model.set(BORDER_SIZE, 3)
    group = presets_of(window)
    group.save_button.emit("clicked")
    dialog = group.dialog
    dialog.entry.set_text("Nord")
    for scope, check in dialog.checks.items():
        check.set_active(scope is CaptureScope.GAPS_LAYOUT)
    assert dialog.get_response_enabled("save") is True

    answer(dialog, "save")

    [(slug, preset)] = session.presets()
    assert (slug, preset.name, dict(preset.options)) == ("nord", "Nord", {BORDER_SIZE: 3})
    assert preset.scopes == {CaptureScope.GAPS_LAYOUT}
    assert list(group.rows) == ["nord"]
    assert "Saved Nord." in toasts(window)


def test_save_waits_for_a_scope_as_well_as_a_name(tmp_path: Path) -> None:
    _session, window = build(tmp_path)
    group = presets_of(window)
    group.save_button.emit("clicked")
    dialog = group.dialog
    dialog.entry.set_text("Nord")
    for check in dialog.checks.values():
        check.set_active(False)

    assert dialog.get_response_enabled("save") is False


def test_a_name_already_taken_asks_to_replace_and_cancel_writes_nothing(
    tmp_path: Path,
) -> None:
    session, window = build(tmp_path)
    session.model.set(BORDER_SIZE, 3)
    save(session, "Nord")
    session.model.set(BORDER_SIZE, 9)
    group = presets_of(window)
    group.save_button.emit("clicked")
    group.dialog.entry.set_text("Nord")

    answer(group.dialog, "save")

    asking = group.dialog
    assert asking.get_heading() == "Replace Nord?"
    assert asking.get_default_response() == "cancel"
    assert asking.get_response_label("replace") == "Replace"

    answer(asking, "cancel")

    [(_, preset)] = session.presets()
    assert dict(preset.options) == {BORDER_SIZE: 3}
    back = group.dialog
    assert back.get_heading() == "Save current as preset"
    assert back.entry.get_text() == "Nord"


def test_replace_overwrites_that_preset_and_only_that_one(tmp_path: Path) -> None:
    session, window = build(tmp_path)
    session.model.set(BORDER_SIZE, 3)
    save(session, "Nord")
    save(session, "Fjord")
    session.model.set(BORDER_SIZE, 9)
    group = presets_of(window)
    group.save_button.emit("clicked")
    group.dialog.entry.set_text("Nord")
    answer(group.dialog, "save")

    answer(group.dialog, "replace")

    by_name = {preset.name: dict(preset.options) for _, preset in session.presets()}
    assert by_name == {"Nord": {BORDER_SIZE: 9}, "Fjord": {BORDER_SIZE: 3}}


def test_a_refused_save_returns_to_the_dialog_with_the_reason(tmp_path: Path) -> None:
    """Everything at its default: nothing to keep. The dialog comes back, answers intact."""
    _session, window = build(tmp_path)
    group = presets_of(window)
    group.save_button.emit("clicked")
    group.dialog.entry.set_text("Empty")

    answer(group.dialog, "save")

    again = group.dialog
    assert again.get_body() == "Nothing to save: everything you chose is at Hyprland's default."
    assert again.entry.get_text() == "Empty"


def test_the_wallpaper_scope_is_insensitive_with_no_daemon_and_says_why(
    tmp_path: Path,
) -> None:
    _session, window = build(tmp_path, live=True, daemon=False)
    group = presets_of(window)

    group.save_button.emit("clicked")
    row = group.dialog.rows[_scope("WALLPAPER")]

    assert row.get_sensitive() is False
    assert group.dialog.checks[_scope("WALLPAPER")].get_active() is False
    assert row.get_subtitle() == (
        "No wallpaper daemon is running. This app changes the wallpaper through awww or swww."
    )


def test_the_wallpaper_scope_is_offered_when_a_daemon_runs(tmp_path: Path) -> None:
    _session, window = build(tmp_path, live=True, daemon=True)
    group = presets_of(window)

    group.save_button.emit("clicked")

    row = group.dialog.rows[_scope("WALLPAPER")]
    assert row.get_sensitive() is True
    assert row.get_subtitle() == "Your wallpaper"


def test_wallpaper_colors_cannot_be_captured_while_hyprland_is_not_running(
    tmp_path: Path,
) -> None:
    _session, window = build(tmp_path, live=False, wallpaper_source=True)
    group = presets_of(window)

    group.save_button.emit("clicked")

    colors = group.dialog.rows[_scope("COLORS")]
    assert colors.get_sensitive() is False
    assert (
        colors.get_subtitle()
        == "Wallpaper colors can only be captured while Hyprland is running"
    )
    assert group.dialog.rows[_scope("GAPS_LAYOUT")].get_sensitive() is True


def _scope(name: str) -> Any:
    from hyprtweaker.engine.presets import CaptureScope

    return CaptureScope[name]


# --- apply ------------------------------------------------------------------------------------


def test_apply_says_what_it_changes_then_applies_and_offers_the_undo(tmp_path: Path) -> None:
    session, window = build(tmp_path, live=True)
    session.model.set(BORDER_SIZE, 2)
    slug = a_preset(session)
    window.theming_page.refresh()
    group = presets_of(window)

    group.button(slug, "apply").emit("clicked")
    dialog = group.dialog

    assert dialog.get_heading() == "Apply Nord?"
    assert dialog.get_body() == (
        "Nord changes 1 setting, in General. One Ctrl+Z puts everything back."
    )
    assert session.applied == []  # asking applied nothing
    assert session.model.get(BORDER_SIZE) == 2

    answer(dialog, "apply")

    assert session.applied == [("nord", None, False)]
    assert session.model.get(BORDER_SIZE) == 3
    toast = window.undo_toast
    assert toast.get_title() == "Applied Nord. Press Ctrl+Z to undo."
    assert toast.get_button_label() == "Undo"

    toast.emit("button-clicked")

    assert session.model.get(BORDER_SIZE) == 2


def test_cancelling_the_apply_confirm_changes_nothing(tmp_path: Path) -> None:
    session, window = build(tmp_path, live=True)
    session.model.set(BORDER_SIZE, 2)
    slug = a_preset(session)
    window.theming_page.refresh()
    group = presets_of(window)
    group.button(slug, "apply").emit("clicked")

    answer(group.dialog, "cancel")

    assert session.applied == []
    assert session.model.get(BORDER_SIZE) == 2
    assert window.undo_toast is None


def test_a_preset_that_already_matches_says_so_instead_of_asking(tmp_path: Path) -> None:
    session, window = build(tmp_path, live=True)
    session.model.set(BORDER_SIZE, 3)
    slug = a_preset(session)
    window.theming_page.refresh()
    group = presets_of(window)

    group.button(slug, "apply").emit("clicked")

    assert group.dialog is None
    assert "Nord already matches your settings. Nothing changed." in toasts(window)


def test_a_read_only_session_cannot_apply_and_the_button_says_why(tmp_path: Path) -> None:
    session, window = build(tmp_path, live=False)
    slug = a_preset(session)
    window.theming_page.refresh()
    group = presets_of(window)

    assert group.button(slug, "apply").get_sensitive() is False
    assert group.button(slug, "apply").get_tooltip_text() == session.offline_reason
    assert group.button(slug, "export").get_sensitive() is True
    assert group.button(slug, "delete").get_sensitive() is True
    assert group.save_button.get_sensitive() is True
    assert "applying is off" in group.group.get_description()


def test_going_live_makes_apply_available_on_the_next_refresh(tmp_path: Path) -> None:
    session, window = build(tmp_path, live=False)
    slug = a_preset(session)
    window.theming_page.refresh()
    group = presets_of(window)
    assert group.button(slug, "apply").get_sensitive() is False

    session._offline_reason = None
    window.theming_page.refresh()

    assert group.button(slug, "apply").get_sensitive() is True
    assert "applying is off" not in group.group.get_description()


# --- the wallpaper part -----------------------------------------------------------------------


def _with_wallpaper(session: Any, window: Any, tmp_path: Path) -> str:
    image = tmp_path / "fjord.png"
    image.write_bytes(b"png")
    slug = a_preset(session)
    preset = dict(session.presets())[slug]
    session._preset_store.write(slug, _replace(preset, wallpaper=str(image)))
    window.theming_page.refresh()
    return slug


def _replace(preset: Any, **changes: Any) -> Any:
    from dataclasses import replace

    return replace(preset, **changes)


def test_a_wallpaper_with_no_daemon_is_said_on_the_row_and_in_the_confirm(
    tmp_path: Path,
) -> None:
    session, window = build(tmp_path, live=True, daemon=False)
    session.model.set(BORDER_SIZE, 2)
    slug = _with_wallpaper(session, window, tmp_path)
    group = presets_of(window)
    reason = (
        "No wallpaper daemon is running. This app changes the wallpaper through awww or swww."
    )

    assert group.rows[slug].get_subtitle() == (
        f"Gaps & layout · saved 2 Oct 2026\nIts wallpaper will not change. {reason}"
    )
    group.button(slug, "apply").emit("clicked")

    assert group.dialog.wallpaper is None
    assert group.dialog.get_body().endswith(f"Its wallpaper will not change. {reason}")


def test_a_wallpaper_with_a_daemon_is_offered_as_change_or_keep_mine(tmp_path: Path) -> None:
    session, window = build(tmp_path, live=True, daemon=True)
    session.model.set(BORDER_SIZE, 2)
    slug = _with_wallpaper(session, window, tmp_path)
    group = presets_of(window)
    assert group.rows[slug].get_subtitle().endswith("Includes your wallpaper")

    group.button(slug, "apply").emit("clicked")
    check = group.dialog.wallpaper
    assert check.get_label() == "Change my wallpaper too"
    check.set_active(False)
    answer(group.dialog, "apply")

    assert session.applied == [("nord", None, False)]

    session.model.set(BORDER_SIZE, 2)  # something to change again
    group.button(slug, "apply").emit("clicked")
    answer(group.dialog, "apply")

    assert session.applied[-1] == ("nord", None, True)


# --- the colour question ----------------------------------------------------------------------


def test_the_colour_question_is_in_the_apply_confirm_and_remember_is_visible_and_forgettable(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.presets import ColorChoice

    session, window = build(tmp_path, live=True, wallpaper_source=True)
    slug = a_preset(session, "Nord", **{INACTIVE: "ee33ccff"})
    window.theming_page.refresh()
    group = presets_of(window)

    group.button(slug, "apply").emit("clicked")
    question = group.dialog.conflict
    assert question is not None
    assert question.use.get_active()
    question.keep.set_active(True)
    question.remember.set_active(True)
    answer(group.dialog, "apply")

    assert session.applied == [("nord", ColorChoice.KEEP_WALLPAPER, False)]
    assert dict(window._prefs.remembered) == {"preset-colors": "keep-wallpaper"}
    assert group.others == (
        (
            "Remembered: your wallpaper's colors win",
            "Applying a preset with colors keeps the ones your wallpaper makes and leaves "
            "the preset's out. Forget this to be asked again.",
        ),
    )

    session.model.set(INACTIVE, "aabbccff")  # something for the preset to change again
    group.button(slug, "apply").emit("clicked")  # remembered: no question, one sentence
    assert group.dialog.conflict is None
    assert "You asked to remember keeping your wallpaper's colors" in group.dialog.get_body()
    answer(group.dialog, "cancel")

    labelled(group.group, "Forget").emit("clicked")

    assert dict(window._prefs.remembered) == {}
    assert group.others == ()
    assert window.lookup_action("forget-remembered").get_enabled() is False


def test_a_remembered_use_says_it_pauses_the_wallpaper_tool(tmp_path: Path) -> None:
    _session, window = build(tmp_path, live=True, wallpaper_source=True)
    window._remember(window._prefs.with_remembered("preset-colors", "use-preset"))

    title, subtitle = presets_of(window).others[0]

    assert title == "Remembered: a preset's colors win over your wallpaper's"
    assert "pauses the tool that makes yours from your wallpaper" in subtitle
    assert "Resume it under Colors above" in subtitle


# --- export, import, delete -------------------------------------------------------------------


def test_export_writes_a_theme_archive_to_the_chosen_file(tmp_path: Path) -> None:
    from hyprtweaker.engine.presets_archive import read_archive

    session, window = build(tmp_path)
    slug = a_preset(session)
    window.theming_page.refresh()
    group = presets_of(window)
    destination = tmp_path / "out" / "nord.hyprtweaker-theme"
    destination.parent.mkdir()
    asked: list[str] = []
    group._actions = _with(
        group._actions,
        choose_export=lambda _parent, name, done: (asked.append(name), done(destination))[0],
    )

    group.button(slug, "export").emit("clicked")

    assert asked == ["nord.hyprtweaker-theme"]
    archive = read_archive(destination)
    assert archive.preset.name == "Nord"
    assert dict(archive.preset.options) == {BORDER_SIZE: 3}
    assert "Exported Nord to nord.hyprtweaker-theme." in toasts(window)


def _with(actions: Any, **changes: Any) -> Any:
    from dataclasses import replace

    return replace(actions, **changes)


def _import_with(window: Any, archive: Path) -> Any:
    group = presets_of(window)
    group._actions = _with(group._actions, choose_import=lambda _parent, done: done(archive))
    group.import_button.emit("clicked")
    main_loop.settle("the import dialog")
    return group.dialog


def test_import_previews_then_adds_the_preset_to_the_list(tmp_path: Path) -> None:
    session, window = build(tmp_path)
    slug = a_preset(session)
    archive = tmp_path / "nord.hyprtweaker-theme"
    _export(session, slug, archive)
    session._preset_store.delete(slug)
    window.theming_page.refresh()
    assert presets_of(window).rows == {}

    dialog = _import_with(window, archive)
    assert dialog.get_title() == "Import theme"
    assert dialog.choice is None  # no wallpaper tool sets colours: nothing to ask
    labelled(dialog, "Add to Presets").emit("clicked")
    main_loop.settle("the import")

    assert list(presets_of(window).rows) == ["nord"]


def _export(session: Any, slug: str, destination: Path) -> None:
    from hyprtweaker.engine.presets_archive import export_archive

    export_archive(dict(session.presets())[slug], destination)


def test_a_refused_archive_is_one_sentence_and_adds_nothing(tmp_path: Path) -> None:
    from gi.repository import Adw

    session, window = build(tmp_path)
    bogus = tmp_path / "notes.hyprtweaker-theme"
    bogus.write_bytes(b"this is not an archive")

    dialog = _import_with(window, bogus)

    [group] = [
        w
        for w in walk(dialog)
        if isinstance(w, Adw.PreferencesGroup) and w.get_title() == "Can't import this theme"
    ]
    assert group.get_description()
    assert "Traceback" not in group.get_description()
    assert session.presets() == ()
    assert presets_of(window).rows == {}


def test_the_import_preview_asks_whose_colours_win_only_under_a_wallpaper_source(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.presets import ColorChoice

    session, window = build(tmp_path, live=True, wallpaper_source=True)
    slug = a_preset(session, "Nord", **{INACTIVE: "ee33ccff"})
    archive = tmp_path / "nord.hyprtweaker-theme"
    _export(session, slug, archive)
    session._preset_store.delete(slug)
    window.theming_page.refresh()

    dialog = _import_with(window, archive)

    assert dialog.choice is not None
    assert dialog.choice.use.get_active()
    dialog.choice.keep.set_active(True)
    dialog.choice.remember.set_active(True)
    labelled(dialog, "Import and Apply").emit("clicked")
    main_loop.settle("the import")

    assert session.applied == [("nord", ColorChoice.KEEP_WALLPAPER, False)]
    assert dict(window._prefs.remembered) == {"preset-colors": "keep-wallpaper"}
    assert list(presets_of(window).rows) == ["nord"]


def test_deleting_asks_first_and_cancel_keeps_the_preset(tmp_path: Path) -> None:
    session, window = build(tmp_path)
    slug = a_preset(session)
    window.theming_page.refresh()
    group = presets_of(window)

    group.button(slug, "delete").emit("clicked")
    assert group.dialog.get_heading() == "Delete Nord?"
    assert group.dialog.get_default_response() == "cancel"
    answer(group.dialog, "cancel")
    assert [s for s, _ in session.presets()] == [slug]

    group.button(slug, "delete").emit("clicked")
    answer(group.dialog, "delete")

    assert session.presets() == ()
    assert group.rows == {}
    assert group.others[0][0] == "No presets yet"


# --- reveal -----------------------------------------------------------------------------------


def test_reveal_preset_returns_the_row_flashed_and_none_for_a_stranger(tmp_path: Path) -> None:
    from hyprtweaker.ui.flash import FLASH_CLASS

    session, window = build(tmp_path)
    slug = a_preset(session)  # saved behind the group's back: reveal refreshes first

    row = window.theming_page.reveal_preset(slug)

    assert row is not None and row.get_title() == "Nord"
    assert FLASH_CLASS in row.get_css_classes()
    assert window.theming_page.reveal_preset("nope") is None
