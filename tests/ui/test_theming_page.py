"""UI tier: the Theming page (#164, ADR-0014).

The page fronts matugen and wallust as tabs with exactly one in use, switches only through a
confirm, always shows the Color source read off the Entrypoint, and says so plainly when no
tool is installed. The words each state reads are decided in
`tests/unit/test_ui_theming_state.py`; here the question is what the assembled page shows,
what its buttons do to the files on disk, and that browsing changes nothing.

Every tool is a stub on this test's tool path (`stub_tool`, #233): nothing real runs. A
Session is made live by an applier that runs each Entrypoint transaction at once and counts
them, since the UI tier has no compositor. Toolkit imports sit inside the test functions.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import main_loop
from started_app import started_application

APP_VERSION = "0.0.0-test"


class EntrypointApplier:
    """Stands where `_go_live` puts the real applier: runs each Entrypoint rewrite now."""

    def __init__(self) -> None:
        self.transactions = 0

    def recover_entrypoint(self, write: Callable[[Any], bool], options: Any = ()) -> Any:
        return write

    async def restore_now(self, write: Callable[[Any], bool]) -> Any:
        from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult

        self.transactions += 1
        write(None)
        return ApplyResult(ApplyOutcome.OK)


def run_now(coro: Any) -> None:
    """Drive a coroutine that awaits nothing real to its end, with no event loop: the
    applier above answers at once, and a nested loop would run GTK's idles mid-handler."""
    try:
        coro.send(None)
    except StopIteration:
        return
    raise AssertionError("the coroutine waited on something")


def make_session(root: Path, *, live: bool = True) -> tuple[Any, EntrypointApplier]:
    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    session = Session(
        spawn=run_now if live else (lambda coro: coro.close()),
        paths=ConfigPaths.rooted_at(root),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    applier = EntrypointApplier()
    if live:
        session._applier = applier
        session._offline_reason = None
    return session, applier


def build_page(session: Any, **actions: Any) -> Any:
    """The page alone, in a window of its own so its dialogs present as the app's do."""
    from gi.repository import Adw

    from hyprtweaker.ui.pages.theming import ThemingActions, ThemingPage

    toasts: list[str] = []
    page = ThemingPage(session, actions=ThemingActions(toast=toasts.append, **actions))
    page.toasts = toasts  # type: ignore[attr-defined]
    Adw.ApplicationWindow(application=started_application(), content=page.page)
    return page


def wired(session: Any, *tools: str, source: str | None = None) -> None:
    """What `wire` leaves in the Manifest, then the Color source set through the Session."""
    from hyprtweaker.engine.bridge import REGISTRY, Wallpaper
    from hyprtweaker.engine.state import Manifest
    from hyprtweaker.engine.writer import Writer

    paths = session.paths
    manifest = Manifest.load(paths.manifest, app_version="x", schema_version="y")
    for tool in tools:
        spec = REGISTRY[tool]
        present = {m.file for m in spec.modules if (paths.hypr_dir / m.file).is_file()}
        manifest = manifest.add_bridge(spec, present=present)
    Writer(paths, app_version=session.app_version).record_bridges(
        session.model, manifest.bridges
    )
    if source is not None:
        assert session.set_color_source(Wallpaper(source))


def put(path: Path, text: str = "return {}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def bridge_lines(session: Any) -> list[str]:
    text = session.paths.entrypoint.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if "bridge/" in line or "dms" in line]


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def answer(dialog: Any, response: str) -> None:
    dialog.emit("response", response)
    dialog.force_close()


def ask(page: Any, label: str) -> Any:
    """Press `label` and wait for the confirm it opens: a switch that will run the tool
    asks the wallpaper daemon first, off the main loop."""
    before = page.dialog
    click(page, label)
    wait_until(lambda: page.dialog is not before, f"the confirm {label!r} opens")
    return page.dialog


def click(page: Any, label: str) -> None:
    button = page.button(label)
    assert button is not None, f"no visible {label!r} button; rows: {page.rows}"
    assert button.get_sensitive(), f"{label!r} is insensitive"
    button.emit("clicked")


MATUGEN_BRIDGE = "hypr/hyprtweaker/bridge/matugen.lua"
WALLUST_BRIDGE = "hypr/hyprtweaker/bridge/wallust.lua"


# --- the page id ---------------------------------------------------------------------------


def test_the_page_sits_under_look_by_its_entity_id_and_no_section_shares_it(
    tmp_path: Path,
) -> None:
    """#120: a page id equal to a Section name is two pages under one name."""

    from hyprtweaker.ui.pages.tasks import entity_page_id
    from hyprtweaker.ui.shell.window import MainWindow

    session, _ = make_session(tmp_path, live=False)
    window = MainWindow(session, application=started_application())

    page_id = entity_page_id("theming")
    assert window.theming_page.section == page_id
    assert "theming" not in session.schema.section_names
    from test_scripting_page import sidebar_by_category

    assert sidebar_by_category(window)[page_id] == "Look"
    window.close()


# --- no tool -------------------------------------------------------------------------------


def test_with_no_tool_the_page_says_so_and_check_again_finds_one(
    tmp_path: Path, stub_tool: Any
) -> None:
    from hyprtweaker.ui.pages.theming import CHECK_AGAIN

    session, _ = make_session(tmp_path)
    page = build_page(session)

    assert page.color_source_text == "Manual"
    assert page.color_source_detail == (
        "Your own color settings apply. No theming tool overrides them."
    )
    assert page.tabs == ()
    assert (
        "Wallpaper colors",
        "No wallpaper color tool found. Install matugen or wallust to take colors from your "
        "wallpaper.",
        "",
    ) in page.rows
    assert not [row for row in page.rows if row[0] == "Other tools"]

    stub_tool("wallust")
    click(page, CHECK_AGAIN)

    assert page.tabs == ("matugen", "wallust")
    assert page.shown_tab == "wallust"
    assert page.rows[1][:2] == ("Wallpaper colors", "Not set up")


# --- one in use, switched by confirm --------------------------------------------------------


def test_viewing_the_other_tab_changes_nothing_and_only_the_confirm_switches(
    tmp_path: Path, stub_tool: Any
) -> None:
    from hyprtweaker.engine.bridge import Wallpaper

    stub_tool("matugen")
    stub_tool("wallust")
    put(tmp_path / MATUGEN_BRIDGE)
    put(tmp_path / WALLUST_BRIDGE)
    session, applier = make_session(tmp_path)
    wired(session, "matugen", "wallust", source="matugen")
    page = build_page(session)
    before = session.paths.entrypoint.read_bytes()
    transactions = applier.transactions

    assert page.tabs == ("matugen · in use", "wallust")
    page.button("wallust").set_active(True)  # the user presses the other tab
    assert page.shown_tab == "wallust"
    assert session.paths.entrypoint.read_bytes() == before
    assert applier.transactions == transactions

    click(page, "Switch to wallust")
    dialog = page.dialog
    assert dialog.get_heading() == "Switch to wallust?"
    assert dialog.get_default_response() == "cancel"
    assert dialog.get_close_response() == "cancel"
    assert dialog.get_response_label("agree") == "Switch to wallust"
    answer(dialog, "cancel")
    assert session.paths.entrypoint.read_bytes() == before
    assert applier.transactions == transactions

    click(page, "Switch to wallust")
    answer(page.dialog, "agree")

    assert applier.transactions == transactions + 1
    assert bridge_lines(session) == [
        '-- require("hyprtweaker/bridge/matugen")  -- off: Color source is wallust',
        'require("hyprtweaker/bridge/wallust")',
    ]
    assert session.color_source() == Wallpaper("wallust")
    assert page.color_source_text == "Wallpaper (wallust)"
    assert page.tabs == ("matugen", "wallust · in use")


def test_switching_to_a_tool_not_set_up_shows_its_files_first_then_sets_it_up_in_one_go(
    tmp_path: Path, stub_tool: Any
) -> None:
    """The confirm names every file outside the App dir; the app writes none under bridge/."""
    stub_tool("matugen")
    stub_tool("wallust")
    put(tmp_path / MATUGEN_BRIDGE)
    session, applier = make_session(tmp_path)
    wired(session, "matugen", source="matugen")
    page = build_page(session)
    page.reveal_backend("wallust")
    transactions = applier.transactions

    ask(page, "Switch to wallust")
    lines = page.dialog.lines
    assert lines[0] == "Switch to wallust?"
    assert lines[1] == (
        "wallust will make your border and group colors from your wallpaper, instead of "
        "matugen. Its colors load after its first run: once switched, press Regenerate on "
        "wallust's tab."
    )
    assert lines[2] == "To set wallust up, these files change:"
    assert f"{tmp_path}/wallust/templates/hyprtweaker-hyprland.lua (new)" in lines
    assert f"{tmp_path}/wallust/wallust.toml (new)" in lines
    assert any(line.startswith("A copy of each changed file is kept in ") for line in lines)
    assert not (tmp_path / "wallust").exists()

    answer(page.dialog, "agree")

    assert applier.transactions == transactions + 1
    assert (tmp_path / "wallust/wallust.toml").is_file()
    assert not (tmp_path / WALLUST_BRIDGE).exists(), "the app never writes a Bridge module"
    assert bridge_lines(session) == [
        '-- require("hyprtweaker/bridge/matugen")  -- off: Color source is wallust',
        '-- require("hyprtweaker/bridge/wallust")  -- waiting for wallust\'s first run',
    ]
    assert page.color_source_text == "Wallpaper (wallust)"
    assert page.rows[1][:2] == ("Wallpaper colors", "Waiting for wallust's first run")


def test_two_backends_loading_offer_a_switch_to_either(tmp_path: Path, stub_tool: Any) -> None:
    """Finding 18 of the #153 review: "Switch to one of them" with no Switch button."""
    from hyprtweaker.engine.bridge import Wallpaper

    stub_tool("matugen")
    stub_tool("wallust")
    put(tmp_path / MATUGEN_BRIDGE)
    put(tmp_path / WALLUST_BRIDGE)
    session, _ = make_session(tmp_path)
    wired(session, "matugen", "wallust")
    from hyprtweaker.engine.writer import Writer

    Writer(session.paths, app_version=session.app_version).set_bridges(
        session.model, session.manifest().bridges
    )  # both load, as a setup from before this fix could leave them
    page = build_page(session)

    assert page.color_source_text == "matugen and wallust both set your colors; wallust wins"
    assert page.button("Switch to matugen") is not None
    page.reveal_backend("wallust")
    assert page.button("Switch to wallust") is not None
    page.reveal_backend("matugen")
    click(page, "Switch to matugen")
    answer(page.dialog, "agree")

    assert session.color_source() == Wallpaper("matugen")


def test_a_refused_switch_leaves_the_old_source_on_screen(
    tmp_path: Path, stub_tool: Any
) -> None:
    stub_tool("matugen")
    stub_tool("wallust")
    put(tmp_path / MATUGEN_BRIDGE)
    put(tmp_path / WALLUST_BRIDGE)
    session, _ = make_session(tmp_path)
    wired(session, "matugen", "wallust", source="matugen")
    page = build_page(session)
    page.reveal_backend("wallust")
    session.paths.entrypoint.write_text(
        session.paths.entrypoint.read_text() + "-- my own line\n", encoding="utf-8"
    )
    page.refresh()

    assert not page.button("Switch to wallust").get_sensitive()
    assert (
        "Colors",
        "Where colors come from cannot change now",
        "hyprland.lua was edited outside this app. Press “Regenerate hyprland.lua…” on the "
        "Theming page, then change where colors come from.",
    ) in page.rows
    page.switch("wallust")
    answer(page.dialog, "agree")
    assert page.dialog.get_heading() == "Could not switch to wallust"
    assert page.color_source_text == "Wallpaper (matugen)"


def test_a_read_only_config_is_refused_with_its_reason_before_any_confirm(
    tmp_path: Path, stub_tool: Any
) -> None:
    """Finding 2 of the #153 review, owner call 6: refuse, saying why, naming the file the
    user knows rather than the store it links to."""
    stub_tool("wallust")
    store = put(tmp_path / "store/wallust.toml", "[templates]\n")
    (tmp_path / "wallust").mkdir()
    (tmp_path / "wallust/wallust.toml").symlink_to(store)
    session, _ = make_session(tmp_path)
    page = build_page(session)
    store.parent.chmod(0o555)
    try:
        ask(page, "Switch to wallust")
    finally:
        store.parent.chmod(0o755)

    assert page.dialog.get_heading() == "wallust cannot be set up"
    assert page.dialog.get_body() == (
        f"{tmp_path}/wallust/wallust.toml is read-only, so wallust was not set up. If a "
        "program such as home-manager manages this file, this app cannot set wallust up "
        "there yet."
    )
    assert session.manifest().bridges == ()


def test_a_hand_edited_entrypoint_offers_its_regenerate_beside_the_reason(
    tmp_path: Path, stub_tool: Any
) -> None:
    """Finding 19 of the #153 review: the row said "Regenerate it" and nothing on the page
    could. The button it names sits on the same row, behind a confirm that says what goes."""
    stub_tool("matugen")
    stub_tool("wallust")
    put(tmp_path / MATUGEN_BRIDGE)
    put(tmp_path / WALLUST_BRIDGE)
    session, applier = make_session(tmp_path)
    wired(session, "matugen", "wallust", source="matugen")
    page = build_page(session)
    page.reveal_backend("wallust")
    entrypoint = session.paths.entrypoint
    entrypoint.write_text(entrypoint.read_text() + "-- my own line\n", encoding="utf-8")
    page.refresh()
    transactions = applier.transactions

    click(page, "Regenerate hyprland.lua…")
    assert page.dialog.get_heading() == "Regenerate hyprland.lua?"
    assert page.dialog.get_body() == (
        "This app writes hyprland.lua again from its own settings. The lines added to it by "
        "hand are removed: put settings of your own in user.lua, which this app never "
        "changes."
    )
    assert page.dialog.get_default_response() == "cancel"
    answer(page.dialog, "cancel")
    assert "-- my own line" in entrypoint.read_text()

    click(page, "Regenerate hyprland.lua…")
    answer(page.dialog, "agree")

    assert applier.transactions == transactions + 1
    assert "-- my own line" not in entrypoint.read_text()
    assert not [row for row in page.rows if row[1].startswith("Where colors come from")]
    assert page.button("Regenerate hyprland.lua…") is None
    assert page.button("Switch to wallust").get_sensitive()


# --- the Color source line -------------------------------------------------------------------


def test_the_line_follows_every_source_and_an_edit_made_outside_the_app(
    tmp_path: Path, stub_tool: Any
) -> None:

    from hyprtweaker.engine.bridge import ManualColors, PresetColors
    from hyprtweaker.ui.shell.window import MainWindow

    stub_tool("matugen")
    put(tmp_path / MATUGEN_BRIDGE)
    session, _ = make_session(tmp_path)
    wired(session, "matugen", source="matugen")
    window = MainWindow(session, application=started_application())
    session.on_state_changed = window.sync
    page = window.theming_page
    assert page.color_source_text == "Wallpaper (matugen)"

    assert session.set_color_source(PresetColors())
    assert session.color_source() == PresetColors()
    assert page.color_source_text == "Preset"
    assert page.button("Resume") is not None

    assert session.set_color_source(ManualColors())
    assert page.color_source_text == "Manual"

    # Hyprland reloads on a foreign edit and the window syncs: the line reads the file.
    text = session.paths.entrypoint.read_text(encoding="utf-8")
    session.paths.entrypoint.write_text(
        text.replace(
            '-- require("hyprtweaker/bridge/matugen")  -- off: Color source is Manual',
            'require("hyprtweaker/bridge/matugen")',
        ),
        encoding="utf-8",
    )
    window.sync()
    assert page.color_source_text == "Wallpaper (matugen)"
    window.close()


def test_resume_wallpaper_colors_switches_back_in_one_transaction(
    tmp_path: Path, stub_tool: Any
) -> None:
    from hyprtweaker.engine.bridge import PresetColors

    stub_tool("matugen")
    put(tmp_path / MATUGEN_BRIDGE)
    session, applier = make_session(tmp_path)
    wired(session, "matugen", source="matugen")
    assert session.set_color_source(PresetColors())
    page = build_page(session)
    assert ("Colors", "Resume wallpaper colors", "Let matugen make your colors again.") in (
        page.rows
    )
    transactions = applier.transactions

    click(page, "Resume")

    assert applier.transactions == transactions + 1
    assert page.color_source_text == "Wallpaper (matugen)"
    assert page.button("Resume") is None


# --- options and Regenerate ------------------------------------------------------------------


def test_options_change_the_command_and_write_nothing(tmp_path: Path, stub_tool: Any) -> None:
    stub_tool("matugen")
    put(tmp_path / MATUGEN_BRIDGE)
    session, _ = make_session(tmp_path)
    wired(session, "matugen", source="matugen")
    page = build_page(session)
    files = tree(tmp_path)

    assert page._options.get_description() == (
        "Used when you press Regenerate here, until you close the app. Your own wallpaper "
        "script keeps its own settings."
    )
    regenerate = [row for row in page.rows if row[1] == "Regenerate colors"]
    assert regenerate == [
        (
            "Wallpaper colors",
            "Regenerate colors",
            "Runs matugen image '<your wallpaper>' --source-color-index 0 -m dark "
            "-t scheme-tonal-spot",
        )
    ]
    from gi.repository import Gtk

    mode = next(row for row in page._rows[page._options] if row.get_title() == "Mode")
    dropdowns = [w for w in descendants(mode) if isinstance(w, Gtk.DropDown)]
    dropdowns[0].set_selected(1)
    contrast = next(row for row in page._rows[page._options] if row.get_title() == "Contrast")
    contrast.set_enable_expansion(True)

    assert [row[2] for row in page.rows if row[1] == "Regenerate colors"] == [
        "Runs matugen image '<your wallpaper>' --source-color-index 0 -m light "
        "-t scheme-tonal-spot --contrast 0"
    ]
    assert tree(tmp_path) == files


def descendants(widget: Any) -> list[Any]:
    found = [widget]
    child = widget.get_first_child()
    while child is not None:
        found.extend(descendants(child))
        child = child.get_next_sibling()
    return found


def wait_until(predicate: Callable[[], bool], waiting_for: str) -> None:
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {waiting_for}")
        main_loop.settle(waiting_for)
        time.sleep(0.01)


def test_regenerate_runs_the_tool_once_on_the_chosen_image_and_loads_its_first_colors(
    tmp_path: Path, stub_tool: Any
) -> None:
    """S4: the file a first run writes is loaded right after the run."""
    log = tmp_path / "ran.log"
    bridge = tmp_path / WALLUST_BRIDGE
    stub_tool(
        "wallust",
        f"echo \"$@\" >> '{log}'\nmkdir -p '{bridge.parent}'\necho 'return {{}}' > '{bridge}'",
    )
    session, applier = make_session(tmp_path)
    wired(session, "wallust", source="wallust")
    asked: list[Any] = []
    page = build_page(
        session, choose_image=lambda parent, done: (asked.append(parent), done(Path("/w.png")))
    )
    assert page.rows[1][1] == "Waiting for wallust's first run"
    transactions = applier.transactions

    click(page, "Regenerate")
    wait_until(lambda: page.running is None, "the regenerate run")

    assert asked == [page.page]
    assert log.read_text() == "run /w.png\n"
    assert applier.transactions == transactions + 1
    assert bridge_lines(session) == ['require("hyprtweaker/bridge/wallust")']
    assert page.rows[1][1] == "In use"
    assert page.toasts == ["wallust made new colors."]


def test_a_failed_run_says_what_the_tool_said(tmp_path: Path, stub_tool: Any) -> None:
    stub_tool("wallust", "echo 'cannot read image' >&2\nexit 3")
    session, _ = make_session(tmp_path)
    wired(session, "wallust", source="wallust")
    page = build_page(session, current_wallpaper=lambda: Path("/w.png"))

    click(page, "Regenerate")
    wait_until(lambda: page.running is None, "the regenerate run")

    assert page.dialog.get_heading() == "wallust did not make new colors"
    assert page.dialog.get_body() == "It stopped with code 3. It said: cannot read image"


def test_a_tool_that_cannot_start_says_so_in_words_with_a_next_step(
    tmp_path: Path, stub_tool: Any
) -> None:
    """Finding 25 of the #153 review: the dialog showed "[Errno 2] ...: '/path'"."""
    stub = stub_tool("wallust")
    stub.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    session, _ = make_session(tmp_path)
    wired(session, "wallust", source="wallust")
    page = build_page(session, current_wallpaper=lambda: Path("/w.png"))

    click(page, "Regenerate")
    wait_until(lambda: page.running is None, "the regenerate run")

    assert page.dialog.get_heading() == "wallust did not make new colors"
    assert page.dialog.get_body() == (
        "wallust could not be started (no such file or directory). Check that it is "
        "installed correctly, then try again."
    )


# --- other tools, Remove -------------------------------------------------------------------


def test_other_tools_offer_set_up_or_say_why_they_cannot(
    tmp_path: Path, stub_tool: Any
) -> None:
    stub_tool("dms")
    put(tmp_path / "hypr/dms/hypr-colors.conf", "")
    stub_tool("noctalia")
    session, _ = make_session(tmp_path)
    page = build_page(session)

    others = [row[1:] for row in page.rows if row[0] == "Other tools"]
    assert others == [
        ("noctalia", "Not set up"),
        ("DMS", "DMS 1.4 writes colors this app cannot load. Update DMS to 1.5 or newer."),
    ]
    assert page.button("Set up…") is not None

    click(page, "Set up…")
    assert page.dialog.get_heading() == "Set up noctalia?"
    assert f"{tmp_path}/noctalia/hyprtweaker.toml (new)" in page.dialog.lines
    answer(page.dialog, "agree")
    assert ("Other tools", "noctalia", "Waiting for noctalia's first run") in page.rows


def test_remove_names_every_file_it_puts_back_or_deletes_and_cancel_keeps_them(
    tmp_path: Path, stub_tool: Any
) -> None:
    """Addendum 38 of the #153 review: Set up listed every file, Remove listed none."""
    stub_tool("matugen")
    put(tmp_path / "matugen/config.toml", "[config]\n")
    session, _ = make_session(tmp_path)
    page = build_page(session)
    ask(page, "Switch to matugen")
    answer(page.dialog, "agree")
    files = tree(tmp_path / "matugen")

    click(page, "Remove…")
    body = page.dialog.get_body()

    assert body.endswith(
        f"Put back: {tmp_path}/matugen/config.toml\n"
        f"Deleted: {tmp_path}/matugen/templates/hyprtweaker-hyprland.lua"
    )
    answer(page.dialog, "cancel")
    assert tree(tmp_path / "matugen") == files


def test_noctalia_4_is_named_with_what_to_do(tmp_path: Path) -> None:
    """Finding 20 of the #153 review: noctalia 4 has no binary, so it was never shown."""
    put(tmp_path / "hypr/noctalia/noctalia-colors.conf", "")
    session, _ = make_session(tmp_path)
    page = build_page(session)

    assert (
        "Other tools",
        "noctalia",
        "noctalia 4 found. Update noctalia to 5 to set its colors up here.",
    ) in page.rows
    assert page.button("Set up…") is None


def test_remove_puts_back_what_setup_changed_and_asks_about_a_file_changed_since(
    tmp_path: Path, stub_tool: Any
) -> None:
    stub_tool("wallust")
    session, _ = make_session(tmp_path)
    page = build_page(session)
    ask(page, "Switch to wallust")
    answer(page.dialog, "agree")
    config = tmp_path / "wallust/wallust.toml"
    config.write_text(config.read_text() + "# mine\n", encoding="utf-8")
    page.refresh()

    click(page, "Remove…")
    assert page.dialog.get_heading() == "Remove wallust?"
    assert "Your own color settings apply again" in page.dialog.get_body()
    answer(page.dialog, "agree")
    assert page.dialog.get_heading() == "Files changed since setup"
    assert page.dialog.get_body() == (
        "This file was changed after wallust was set up:\n\n"
        f"{tmp_path}/wallust/wallust.toml\n"
        "Setup created this file, so restoring deletes it.\n\n"
        "Restore the copy (the file as it is now is kept beside it), or leave it as it is "
        "and only stop loading wallust."
    )
    assert page.dialog.get_default_response() == "cancel"
    answer(page.dialog, "restore")

    assert not config.exists(), "setup created it; restoring takes it away"
    assert page.color_source_text == "Manual"
    assert page.toasts[-1] == "wallust is removed."


def test_a_setup_that_cannot_write_says_why_and_leaves_nothing_set_up(
    tmp_path: Path, stub_tool: Any, monkeypatch: Any
) -> None:
    """Finding 2 of the #153 review: the user saw a traceback and a tool that looked set up
    but never loaded. Now: a sentence, the files as they were, and no entry."""
    from hyprtweaker.engine.bridge import ManualColors
    from hyprtweaker.engine.bridge import wire as wiring

    stub_tool("wallust")
    session, _ = make_session(tmp_path)
    page = build_page(session)
    real_write = wiring._write_atomic

    def denied(path: Path, content: Any) -> None:
        if path.name == "wallust.toml":
            raise PermissionError(13, "Permission denied", str(path))
        real_write(path, content)

    monkeypatch.setattr(wiring, "_write_atomic", denied)
    ask(page, "Switch to wallust")
    answer(page.dialog, "agree")

    assert page.dialog.get_heading() == "wallust was not set up"
    assert page.dialog.get_body() == (
        f"{tmp_path}/wallust/wallust.toml could not be written (permission denied), so "
        "nothing was changed."
    )
    assert not (tmp_path / "wallust").exists()
    assert session.manifest().bridges == ()
    assert session.color_source() == ManualColors()
    assert page.color_source_text == "Manual"


# --- the reveal entry point ----------------------------------------------------------------


def test_reveal_lands_on_the_tab_the_row_or_the_top(tmp_path: Path, stub_tool: Any) -> None:
    from hyprtweaker.ui.flash import FLASH_CLASS

    stub_tool("matugen")
    stub_tool("wallust")
    stub_tool("dms")
    put(tmp_path / "hypr/dms/colors.lua")
    session, _ = make_session(tmp_path)
    page = build_page(session)

    row = page.reveal_backend("wallust")
    assert page.shown_tab == "wallust"
    assert row.get_title() == "Not set up" and row.has_css_class(FLASH_CLASS)

    row = page.reveal_backend("dms")
    assert row.get_title() == "DMS" and row.has_css_class(FLASH_CLASS)

    assert page.reveal_backend("nothing-like-it") is None


def test_the_window_reveal_opens_the_page_on_the_tool(tmp_path: Path, stub_tool: Any) -> None:
    """#165's pill calls this: the click lands on the Theming page, on the tool's tab."""
    from hyprtweaker.ui.pages.tasks import entity_page_id
    from hyprtweaker.ui.shell.window import MainWindow

    stub_tool("matugen")
    stub_tool("wallust")
    session, _ = make_session(tmp_path, live=False)
    window = MainWindow(session, application=started_application())

    window.reveal_backend("wallust")
    main_loop.settle("the reveal")

    assert window._selected_section() == entity_page_id("theming")
    assert window.theming_page.shown_tab == "wallust"
    window.close()


def _option_row(window: Any, name: str) -> Any:
    return next(row for page in window.pages if (row := page.row(name)) is not None)


def test_a_row_matugen_sets_says_so_opens_matugen_and_follows_the_color_source(
    tmp_path: Path, stub_tool: Any
) -> None:
    """#165: "Set by matugen" on the Row, its click lands on matugen's tab, and the pill goes
    when matugen stops setting colours -- with no Option edited, through `window.sync`."""
    from hyprtweaker.engine.bridge import ManualColors, Wallpaper
    from hyprtweaker.ui.pages.tasks import entity_page_id
    from hyprtweaker.ui.shell.window import MainWindow

    stub_tool("matugen")
    stub_tool("wallust")
    put(tmp_path / MATUGEN_BRIDGE)
    session, _ = make_session(tmp_path)
    wired(session, "matugen", source="matugen")
    window = MainWindow(session, application=started_application())
    session.on_state_changed = window.sync
    owned = _option_row(window, "general:col.active_border")
    other = _option_row(window, "general:gaps_in")

    assert owned.chrome.pill_labels == ("Set by matugen",)
    assert other.chrome.pill_labels == ()
    (button,) = owned.chrome.pill_buttons
    window.theming_page.reveal_backend("wallust")  # so landing on matugen's tab is the click's
    assert window.theming_page.shown_tab == "wallust"

    button.emit("clicked")
    main_loop.settle("the pill's reveal")

    assert window._selected_section() == entity_page_id("theming")
    assert window.theming_page.shown_tab == "matugen"

    assert session.set_color_source(ManualColors())
    assert owned.chrome.pill_labels == ()
    assert owned.chrome.pill_buttons == ()

    assert session.set_color_source(Wallpaper("matugen"))
    assert owned.chrome.pill_labels == ("Set by matugen",)
    window.close()


def test_a_switch_whose_tool_has_not_run_names_the_run_and_does_it_once(
    tmp_path: Path, stub_tool: Any
) -> None:
    """Settled S3: one confirm covers setting up, the first run (its exact command) and the
    switch; the colours load right after the run."""
    log = tmp_path / "ran.log"
    bridge = tmp_path / WALLUST_BRIDGE
    wallust = stub_tool(
        "wallust",
        f"echo \"$@\" >> '{log}'\nmkdir -p '{bridge.parent}'\necho 'return {{}}' > '{bridge}'",
    )
    session, applier = make_session(tmp_path)
    page = build_page(session, current_wallpaper=lambda: Path("/pictures/sea.png"))

    dialog = ask(page, "Switch to wallust")
    assert "This runs once, to make the colors:" in dialog.lines
    assert f"{wallust} run /pictures/sea.png" in dialog.lines
    assert "press Regenerate" not in dialog.get_body()
    assert not log.exists()

    answer(dialog, "agree")
    wait_until(lambda: page.running is None, "the first run")

    assert log.read_text() == "run /pictures/sea.png\n"
    assert bridge_lines(session) == ['require("hyprtweaker/bridge/wallust")']
    assert page.rows[1][1] == "In use"
    assert applier.transactions == 2, "set up and switch, then load once the file is there"
