"""What the Theming page says, decided without a toolkit (#164, ADR-0014).

The Color source line, each backend tab's state, and each other tool's row are pure
functions of what detection found, what the Manifest records and what the Entrypoint loads;
the page only draws them. Every expected value here is the sentence the user reads.
"""

from __future__ import annotations

from hyprtweaker.engine.bridge import (
    ACTIVE,
    DMS,
    MATUGEN,
    NOCTALIA,
    SHELL_SWITCH,
    WAITING,
    WALLUST,
    BridgeEntry,
    BridgeState,
    ManualColors,
    Off,
    PresetColors,
    Several,
    ToolSpec,
    VersionState,
    Wallpaper,
    entries_for,
)
from hyprtweaker.engine.bridge.wire import ChangedFile, ToolDetection
from hyprtweaker.ui.pages.theming_state import (
    TabState,
    ToolState,
    backend_tab,
    changed_since_setup,
    has_backend,
    other_tool,
    resume_target,
    source_detail,
    source_line,
)


def found(tool: str, *, wired: bool = False, outdated: bool = False) -> ToolDetection:
    return ToolDetection(
        tool=tool,
        installed=True,
        configured=False,
        wired=wired,
        version_state=VersionState.NEEDS_UPDATE if outdated else VersionState.SUPPORTED,
    )


def absent(tool: str) -> ToolDetection:
    return ToolDetection(
        tool=tool,
        installed=False,
        configured=False,
        wired=False,
        version_state=VersionState.SUPPORTED,
    )


def entries(spec: ToolSpec, state: BridgeState) -> tuple[BridgeEntry, ...]:
    return tuple(entry.with_state(state) for entry in entries_for(spec, present=()))


def test_the_color_source_line_reads_as_adr_0014_names_it() -> None:
    assert source_line(Wallpaper("matugen")) == "Wallpaper (matugen)"
    assert source_line(Wallpaper("wallust")) == "Wallpaper (wallust)"
    assert source_line(PresetColors()) == "Preset"
    assert source_line(ManualColors()) == "Manual"
    assert (
        source_line(Several(("matugen", "wallust")))
        == "matugen and wallust both set your colors; wallust wins"
    )


def test_manual_does_not_hide_a_shell_that_still_sets_colors() -> None:
    dms_on = entries(DMS, ACTIVE)
    assert source_detail(ManualColors(), dms_on) == (
        "Your own color settings apply, except where DMS sets them."
    )
    assert source_detail(ManualColors(), entries(DMS, Off(ManualColors()))) == (
        "Your own color settings apply. No theming tool overrides them."
    )
    assert source_detail(Wallpaper("matugen"), entries(MATUGEN, ACTIVE)) == (
        "matugen sets your border and group colors from your wallpaper."
    )
    assert source_detail(PresetColors(), ()) == (
        "The colors of the preset you applied. No theming tool overrides them."
    )


def test_the_tab_in_use_is_the_color_source_and_the_other_one_is_not() -> None:
    loaded = entries(MATUGEN, ACTIVE) + entries(WALLUST, Off(Wallpaper("matugen")))
    source = Wallpaper("matugen")

    matugen = backend_tab(found("matugen", wired=True), loaded, source, present=frozenset())
    wallust = backend_tab(found("wallust", wired=True), loaded, source, present=frozenset())

    assert (matugen.state, matugen.label) == (TabState.IN_USE, "matugen · in use")
    assert (wallust.state, wallust.label) == (TabState.NOT_IN_USE, "wallust")


def test_a_backend_in_use_that_has_not_run_waits_and_offers_load_once_its_file_exists() -> None:
    waiting = entries(WALLUST, WAITING)
    source = Wallpaper("wallust")
    detection = found("wallust", wired=True)

    assert backend_tab(detection, waiting, source, present=frozenset()).state is (
        TabState.WAITING
    )
    present = frozenset({"hyprtweaker/bridge/wallust.lua"})
    assert backend_tab(detection, waiting, source, present=present).state is TabState.LOAD_NOW


def test_a_backend_not_found_is_a_tab_that_says_so() -> None:
    tab = backend_tab(absent("wallust"), (), ManualColors(), present=frozenset())
    assert tab.state is TabState.NOT_INSTALLED
    assert tab.label == "wallust"


def test_both_backends_loading_puts_both_tabs_in_use() -> None:
    both = entries(MATUGEN, ACTIVE) + entries(WALLUST, ACTIVE)
    source = Several(("matugen", "wallust"))
    states = [
        backend_tab(found(tool, wired=True), both, source, present=frozenset()).state
        for tool in ("matugen", "wallust")
    ]
    assert states == [TabState.IN_USE, TabState.IN_USE]


def test_the_empty_state_shows_only_when_neither_backend_is_found() -> None:
    assert not has_backend([absent("matugen"), absent("wallust")])
    assert has_backend([absent("matugen"), found("wallust")])
    configured_only = ToolDetection("matugen", False, True, False, VersionState.SUPPORTED)
    assert has_backend([configured_only, absent("wallust")])


def test_other_tools_read_from_detection_and_their_entries() -> None:
    assert other_tool(found("dms"), (), present=frozenset()) == ToolState(
        "dms", "DMS", "Not set up", setup=True
    )
    assert other_tool(found("dms", outdated=True), (), present=frozenset()) == ToolState(
        "dms",
        "DMS",
        "DMS 1.4 writes colors this app cannot load. Update DMS to 1.5 or newer.",
    )
    assert other_tool(
        found("noctalia", wired=True), entries(NOCTALIA, ACTIVE), present=frozenset()
    ) == ToolState("noctalia", "noctalia", "On: sets your border and group colors", remove=True)
    assert other_tool(
        found("dms", wired=True), entries(DMS, Off(PresetColors())), present=frozenset()
    ) == ToolState("dms", "DMS", "Off while a preset sets your colors", remove=True)
    assert other_tool(
        found("shell-switch", wired=True), entries(SHELL_SWITCH, WAITING), present=frozenset()
    ) == ToolState(
        "shell-switch",
        "shell-switch",
        "Waiting for shell-switch's first run. It loads the next time shell-switch runs.",
        remove=True,
        patch=True,
    )
    present = frozenset({"shell-switcher-startup.lua", "shell-switcher-binds.lua"})
    assert other_tool(
        found("shell-switch", wired=True), entries(SHELL_SWITCH, WAITING), present=present
    ) == ToolState(
        "shell-switch", "shell-switch", "Its files are ready to load", remove=True, load=True
    )
    assert other_tool(
        found("shell-switch", wired=True), entries(SHELL_SWITCH, ACTIVE), present=frozenset()
    ) == ToolState("shell-switch", "shell-switch", "On", remove=True)


def test_resume_picks_the_one_backend_set_up_or_the_tab_on_screen() -> None:
    off = Off(ManualColors())
    only_wallust = entries(WALLUST, off)
    both = entries(MATUGEN, off) + entries(WALLUST, off)

    assert resume_target(ManualColors(), only_wallust, shown="matugen") == "wallust"
    assert resume_target(PresetColors(), both, shown="wallust") == "wallust"
    assert resume_target(ManualColors(), both, shown="matugen") == "matugen"
    assert resume_target(ManualColors(), (), shown="matugen") is None
    assert resume_target(Wallpaper("matugen"), both, shown="wallust") is None


def test_the_changed_files_question_shows_each_file_beside_its_copy() -> None:
    from pathlib import Path

    files = (
        ChangedFile(
            Path("/h/.config/matugen/config.toml"),
            "~/.config/matugen/config.toml",
            "~/.local/state/hyprtweaker/bridge-backups/matugen-1/copies/0/config.toml",
        ),
        ChangedFile(Path("/h/t.lua"), "~/t.lua", None),
    )

    assert changed_since_setup("matugen", files) == (
        "These files were changed after matugen was set up:\n\n"
        "~/.config/matugen/config.toml\n"
        "Copy from before setup: "
        "~/.local/state/hyprtweaker/bridge-backups/matugen-1/copies/0/config.toml\n\n"
        "~/t.lua\n"
        "Setup created this file, so restoring deletes it.\n\n"
        "Restore the copy (the file as it is now is kept beside it), or leave it as it is "
        "and only stop loading matugen."
    )
