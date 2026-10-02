"""The Color source and the Bridge entries, driven through a whole `Session` (#163).

ADR-0014: the Color source is which Bridge require the Entrypoint enables, switched by one
Entrypoint transaction. ADR-0006: Bridge modules are the tool's, never rewritten. S4: a tool
that has not run yet waits, commented, and loads once its file appears. Every assertion is
about what is on disk, what the compositor was asked, or what the UI reads.
"""

from __future__ import annotations

from pathlib import Path

from _fake_hyprland import FakeHyprland, option_reply, run_with_fake
from _support import Runner, drain_events, sample_schema, section_conversation, session_for

from hyprtweaker.engine.bridge import (
    DMS,
    MATUGEN,
    SHELL_SWITCH,
    WALLUST,
    ManualColors,
    PresetColors,
    ToolSpec,
    Wallpaper,
)
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.state import Manifest
from hyprtweaker.engine.writer import Writer
from hyprtweaker.session import Session

BORDER_SIZE = "general:border_size"
INACTIVE = "general:col.inactive_border"
MINE = "595959aa"
"""The user's own inactive border colour, set in the app."""

MATUGENS = "ff0000ff"
"""What matugen's module sets it to, once it loads."""


def conversation(**values: object) -> dict[str, str]:
    return section_conversation("general", "group", **values)


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


async def settle(session: Session, runner: Runner) -> None:
    await session.drain()
    await runner.settle()
    await session.drain()
    await runner.settle()


def paths_of(root: Path) -> ConfigPaths:
    return ConfigPaths.rooted_at(root)


def put(root: Path, relpath: str, text: str = "return {}\n") -> Path:
    path = paths_of(root).hypr_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def wire(session: Session, *specs: ToolSpec) -> None:
    """What #166's `wire` leaves behind in the Manifest: an entry per module, ungated."""
    paths = session.paths
    manifest = Manifest.load(paths.manifest, app_version="x", schema_version="y")
    for spec in specs:
        present = {m.file for m in spec.modules if (paths.hypr_dir / m.file).is_file()}
        manifest = manifest.add_bridge(spec, present=present)
    Writer(paths, app_version=session.app_version).record_bridges(
        session.model, manifest.bridges
    )


def bridge_lines(root: Path) -> list[str]:
    text = paths_of(root).entrypoint.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if "dms" in line or "bridge/" in line]


def set_live(fake: FakeHyprland, name: str, value: str) -> None:
    option = sample_schema().get(name)
    assert option is not None
    fake.conversation[f"j/getoption {name}"] = option_reply(option, value)


def test_a_switch_is_one_entrypoint_write_and_one_reload(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        put(tmp_path, "hyprtweaker/bridge/matugen.lua")
        put(tmp_path, "dms/colors.lua")
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        wire(session, MATUGEN, WALLUST, DMS)
        assert session.set_color_source(Wallpaper("matugen"))
        await settle(session, runner)
        reloads, entries = fake.requests.count("reload"), len(session.journal.entries())

        assert session.set_color_source(PresetColors())
        await settle(session, runner)

        assert fake.requests.count("reload") == reloads + 1
        assert len(session.journal.entries()) == entries + 1
        assert bridge_lines(tmp_path) == [
            '-- require("dms.colors")  -- off: Color source is Preset',
            '-- require("hyprtweaker/bridge/matugen")  -- off: Color source is Preset',
            '-- require("hyprtweaker/bridge/wallust")  -- off: Color source is Preset',
        ]
        assert session.color_source() == PresetColors()
        assert session.bridge_owners == {}

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_a_wallpaper_source_swaps_exactly_one_backend_require(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        put(tmp_path, "hyprtweaker/bridge/matugen.lua")
        put(tmp_path, "hyprtweaker/bridge/wallust.lua")
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        wire(session, MATUGEN, WALLUST)
        assert session.set_color_source(Wallpaper("matugen"))
        await settle(session, runner)
        assert bridge_lines(tmp_path) == [
            'require("hyprtweaker/bridge/matugen")',
            '-- require("hyprtweaker/bridge/wallust")  -- off: Color source is matugen',
        ]

        assert session.set_color_source(Wallpaper("wallust"))
        await settle(session, runner)

        assert bridge_lines(tmp_path) == [
            '-- require("hyprtweaker/bridge/matugen")  -- off: Color source is wallust',
            'require("hyprtweaker/bridge/wallust")',
        ]
        assert session.color_source() == Wallpaper("wallust")
        assert session.bridge_owners["general:col.active_border"] == "wallust"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_the_users_own_colour_survives_a_wallpaper_source_and_comes_back(
    tmp_path: Path,
) -> None:
    """The re-read after a switch leaves out what a loading bridge sets: adopting matugen's
    colour would write it into the app's Module as the user's own."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        put(tmp_path, "hyprtweaker/bridge/matugen.lua")
        session = await live_session(fake, tmp_path, runner)
        session.set_option(INACTIVE, MINE)
        await settle(session, runner)
        mine = session.model.get(INACTIVE)
        wire(session, MATUGEN)

        set_live(fake, INACTIVE, MATUGENS)
        assert session.set_color_source(Wallpaper("matugen"))
        await settle(session, runner)
        assert session.model.get(INACTIVE) == mine
        assert session.bridge_owners[INACTIVE] == "matugen"

        set_live(fake, INACTIVE, MINE)
        assert session.set_color_source(ManualColors())
        await settle(session, runner)
        assert session.model.get(INACTIVE) == mine
        assert session.color_source() == ManualColors()
        assert INACTIVE not in session.bridge_owners

    run_with_fake(
        scenario, FakeHyprland(conversation(**{INACTIVE: MINE}), reload_emits_event=True)
    )


def test_bridge_modules_keep_their_bytes_through_switches_and_writes(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        module = put(tmp_path, "hyprtweaker/bridge/matugen.lua", "-- matugen wrote this\n")
        native = put(tmp_path, "dms/colors.lua", "-- DMS wrote this\n")
        before = (module.read_bytes(), native.read_bytes())
        session = await live_session(fake, tmp_path, runner)
        wire(session, MATUGEN, DMS)

        for source in (Wallpaper("matugen"), PresetColors(), Wallpaper("matugen")):
            assert session.set_color_source(source)
            await settle(session, runner)
            session.set_option(BORDER_SIZE, 1 + int(isinstance(source, PresetColors)))
            await settle(session, runner)

        assert (module.read_bytes(), native.read_bytes()) == before
        assert not session.health.unhealthy

    run_with_fake(
        scenario,
        FakeHyprland(conversation(**{BORDER_SIZE: 1}), reload_emits_event=True),
    )


def test_a_hand_edited_entrypoint_refuses_the_switch_and_says_why(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        put(tmp_path, "hyprtweaker/bridge/matugen.lua")
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        wire(session, MATUGEN)
        entrypoint = paths_of(tmp_path).entrypoint
        entrypoint.write_text(entrypoint.read_text() + "-- my note\n", encoding="utf-8")
        edited = entrypoint.read_bytes()
        reloads = fake.requests.count("reload")

        assert not session.set_color_source(Wallpaper("matugen"))
        await settle(session, runner)

        assert session.color_source_blocked == (
            "hyprland.lua was edited outside hyprtweaker. "
            "Regenerate it before changing where colors come from."
        )
        assert entrypoint.read_bytes() == edited
        assert fake.requests.count("reload") == reloads

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_a_backend_that_is_not_set_up_is_refused(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        wire(session, MATUGEN)

        assert not session.set_color_source(Wallpaper("wallust"))

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_read_only_session_cannot_switch(tmp_path: Path) -> None:
    session = Session(
        spawn=lambda coro: coro.close(),
        schema=sample_schema(),
        paths=paths_of(tmp_path),
        app_version="0.0.0-test",
        connect=lambda: (_ for _ in ()).throw(RuntimeError),
        read_live=lambda: None,
    )
    assert not session.set_color_source(PresetColors())
    assert session.color_source() == ManualColors()


def test_a_tool_waits_for_its_first_run_and_loads_after_the_next_foreign_reload(
    tmp_path: Path,
) -> None:
    """S4: a require of a missing file errors on every reload, so the line waits; the file
    a tool creates is watched by nobody, so the app loads it itself."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        wire(session, SHELL_SWITCH)
        assert not session.load_waiting_bridges(), "nothing has been written yet"
        session.set_option(BORDER_SIZE, 4)
        await settle(session, runner)
        text = paths_of(tmp_path).entrypoint.read_text()
        assert (
            '-- require("shell-switcher-binds")  -- waiting for shell-switch\'s first run'
            in (text)
        )

        put(tmp_path, "shell-switcher-startup.lua")
        put(tmp_path, "shell-switcher-binds.lua")
        await fake.emit("configreloaded")
        await drain_events(runner)
        await settle(session, runner)

        loading = [
            line
            for line in paths_of(tmp_path).entrypoint.read_text().splitlines()
            if line.startswith('require("shell')
        ]
        assert loading == [
            'require("shell-switcher-startup")',
            'require("shell-switcher-binds")',
        ]
        assert not session.load_waiting_bridges(), "a second call has nothing to do"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 4}), reload_emits_event=True)
    )


def test_a_file_written_while_the_app_was_closed_loads_at_launch(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        first = await live_session(fake, tmp_path, runner)
        wire(first, DMS)
        first.set_option(BORDER_SIZE, 3)
        await settle(first, runner)
        assert bridge_lines(tmp_path) == [
            '-- require("dms.colors")  -- waiting for DMS\'s first run'
        ]
        await first.aclose()

        put(tmp_path, "dms/colors.lua")
        second = await live_session(fake, tmp_path, runner)
        await settle(second, runner)

        assert bridge_lines(tmp_path) == ['require("dms.colors")']
        assert second.bridge_owners["group:groupbar:col.active"] == "dms"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_a_quarantined_native_bridge_is_named_by_its_file() -> None:
    from hyprtweaker.session import Health

    assert (
        Health(quarantined=("dms.colors",)).title
        == "dms/colors.lua is disabled until you fix it."
    )


def test_setting_a_tool_up_adds_its_line_in_one_reload_and_removing_it_takes_it_out(
    tmp_path: Path,
) -> None:
    """What #166's `wire` and `unwire` call to add and remove a tool's Bridge entry."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        put(tmp_path, "dms/colors.lua")
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        reloads = fake.requests.count("reload")

        assert session.add_bridge("dms")
        await settle(session, runner)
        assert bridge_lines(tmp_path) == ['require("dms.colors")']
        assert fake.requests.count("reload") == reloads + 1

        assert session.remove_bridge("dms")
        await settle(session, runner)
        assert bridge_lines(tmp_path) == []
        assert fake.requests.count("reload") == reloads + 2

        assert session.remove_bridge("dms"), "nothing set up is nothing to do"
        await settle(session, runner)
        assert fake.requests.count("reload") == reloads + 2

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_switching_to_a_backend_not_set_up_is_one_reload_that_sets_it_up_and_switches(
    tmp_path: Path,
) -> None:
    """The Theming page's "Switch to wallust" on a tool not yet wired (#164): `wire`'s
    register adds the entry and gates the old backend in the same transaction, so there is
    no reload in between where both backends load."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        put(tmp_path, "hyprtweaker/bridge/matugen.lua")
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        wire(session, MATUGEN)
        assert session.set_color_source(Wallpaper("matugen"))
        await settle(session, runner)
        reloads = fake.requests.count("reload")

        assert session.add_bridge("wallust", source=Wallpaper("wallust"))
        await settle(session, runner)

        assert fake.requests.count("reload") == reloads + 1
        assert bridge_lines(tmp_path) == [
            '-- require("hyprtweaker/bridge/matugen")  -- off: Color source is wallust',
            '-- require("hyprtweaker/bridge/wallust")  -- waiting for wallust\'s first run',
        ]
        assert session.color_source() == Wallpaper("wallust")
        assert [entry.tool for entry in session.manifest().bridges] == ["matugen", "wallust"]

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_a_tool_is_not_set_up_while_the_session_is_read_only(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        session = session_for(fake, tmp_path, Runner())

        assert not session.add_bridge("dms")
        assert not paths_of(tmp_path).entrypoint.exists()

    run_with_fake(scenario, FakeHyprland(conversation()))
