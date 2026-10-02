"""A Preset's Colors against a wallpaper Color source, and its wallpaper, through a `Session`.

ADR-0014 §Color source: a Preset carrying Colors while a wallpaper sets them asks first. "Use
preset's colors" makes the source Preset in the same Apply transaction; "Keep wallpaper
colors" applies the rest. §Wallpaper: the image is set through the running daemon once the
transaction stands. S7: one Ctrl+Z puts back all of it, or says what it left.

The compositor is `FakeHyprland` and the daemon is a fake behind `engine/tools.py`'s seam;
nothing here runs a real tool. Assertions read the Entrypoint on disk, the reloads the
compositor saw, the model, and the daemon calls.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, sample_schema, section_conversation, session_for
from test_session_presets import reject_the_next_reload

from hyprtweaker.engine.apply import PresetStep, WallpaperChange
from hyprtweaker.engine.bridge import MATUGEN, ManualColors, PresetColors, Wallpaper
from hyprtweaker.engine.model import UNSET
from hyprtweaker.engine.model.values import parse_value
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.presets import (
    CaptureScope,
    ColorChoice,
    PresetApplied,
    PresetColorConflict,
    PresetNotSaved,
    PresetSaved,
    PresetSaveResult,
    scoped_options,
)
from hyprtweaker.engine.state import Manifest
from hyprtweaker.engine.tools import ToolRun
from hyprtweaker.engine.wallpaper import Shown, Wallpapers
from hyprtweaker.engine.writer import Writer
from hyprtweaker.session import Session

BORDER_SIZE = "general:border_size"
INACTIVE = "general:col.inactive_border"
PRESET_COLOUR = "ee33ccff"
NORD = {BORDER_SIZE: 3, INACTIVE: PRESET_COLOUR}

MINE = Path("/pictures/mine.png")


def conversation(**values: object) -> dict[str, str]:
    sections = {o.section for o in scoped_options(sample_schema(), CaptureScope.COLORS)}
    return section_conversation("general", "decoration", *sorted(sections), **values)


class FakeDaemon:
    """swww on the tool path and running, answering `query` with what it shows."""

    def __init__(self, runtime: Path) -> None:
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "wayland-1-swww-daemon..sock").touch()
        self.environ = {"XDG_RUNTIME_DIR": str(runtime), "WAYLAND_DISPLAY": "wayland-1"}
        self.showing = MINE
        self.calls: list[tuple[str, ...]] = []

    def find(self, name: str) -> Path | None:
        return Path("/opt/tools/swww") if name == "swww" else None

    def run(self, argv: Sequence[str], *, timeout: float) -> ToolRun:
        self.calls.append(tuple(argv[1:]))
        if argv[1] == "img":
            self.showing = Path(argv[-1])
        line = f": DP-1: 2560x1440, scale: 1, currently displaying: image: {self.showing}\n"
        return ToolRun(tuple(argv), 0, line if argv[1] == "query" else "", "")

    def seam(self) -> Wallpapers:
        return Wallpapers(find=self.find, run=self.run, environ=self.environ)


def no_daemon() -> Wallpapers:
    return Wallpapers(find=lambda _name: None, environ={})


async def live_session(
    fake: FakeHyprland, root: Path, runner: Runner, wallpapers: Wallpapers | None = None
) -> tuple[Session, list[str]]:
    session = session_for(fake, root, runner, wallpapers=wallpapers or no_daemon())
    notes: list[str] = []
    session.on_preset_note = notes.append
    session.start()
    await runner.settle()
    assert session.live
    return session, notes


async def settle(session: Session, runner: Runner) -> None:
    for _ in range(2):
        await session.drain()
        await runner.settle()


async def under_matugen(session: Session, root: Path, runner: Runner) -> None:
    """matugen set up, its file written, and chosen as the Color source."""
    paths = ConfigPaths.rooted_at(root)
    bridge = paths.hypr_dir / "hyprtweaker/bridge/matugen.lua"
    bridge.parent.mkdir(parents=True, exist_ok=True)
    bridge.write_text("return {}\n", encoding="utf-8")
    manifest = Manifest.load(paths.manifest, app_version="x", schema_version="y")
    manifest = manifest.add_bridge(MATUGEN, present={"hyprtweaker/bridge/matugen.lua"})
    Writer(paths, app_version=session.app_version).record_bridges(
        session.model, manifest.bridges
    )
    assert session.set_color_source(Wallpaper("matugen"))
    await settle(session, runner)


def matugen_line(root: Path) -> str:
    text = ConfigPaths.rooted_at(root).entrypoint.read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if "bridge/matugen" in line)


def write_preset(
    root: Path, options: dict[str, Any], *, wallpaper: str | None = None, slug: str = "nord"
) -> None:
    directory = ConfigPaths.rooted_at(root).presets_dir
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "format": 1,
        "name": "Nord",
        "created": "2026-10-02T09:30:00+00:00",
        "scopes": ["colors", "gaps-layout", "wallpaper"],
        "options": options,
        "wallpaper": wallpaper,
    }
    (directory / f"{slug}.json").write_text(json.dumps(data), encoding="utf-8")


def colour(text: str) -> object:
    option = sample_schema().get(INACTIVE)
    assert option is not None
    return parse_value(option.type, text)


# --- the conflict ---------------------------------------------------------------------------


def test_a_preset_with_colors_asks_first_while_a_wallpaper_sets_them(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, _ = await live_session(fake, tmp_path, runner)
        await under_matugen(session, tmp_path, runner)
        write_preset(tmp_path, NORD)
        reloads = fake.requests.count("reload")

        result = session.apply_preset("nord")
        await settle(session, runner)

        assert result == PresetColorConflict(Wallpaper("matugen"))
        assert fake.requests.count("reload") == reloads
        assert session.model.get(BORDER_SIZE) is UNSET
        assert session.model.get(INACTIVE) is UNSET

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_no_wallpaper_source_means_no_question(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, _ = await live_session(fake, tmp_path, runner)
        write_preset(tmp_path, NORD)
        fake.conversation.update(conversation(**{BORDER_SIZE: 3, INACTIVE: PRESET_COLOUR}))

        result = session.apply_preset("nord")
        await settle(session, runner)

        assert result == PresetApplied(applied=(BORDER_SIZE, INACTIVE), skipped=())
        assert session.model.get(INACTIVE) == colour(PRESET_COLOUR)
        assert session.color_source() == ManualColors()

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_preset_without_colors_never_asks(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, _ = await live_session(fake, tmp_path, runner)
        await under_matugen(session, tmp_path, runner)
        write_preset(tmp_path, {BORDER_SIZE: 3})
        fake.conversation.update(conversation(**{BORDER_SIZE: 3}))

        result = session.apply_preset("nord")
        await settle(session, runner)

        assert result == PresetApplied(applied=(BORDER_SIZE,), skipped=())
        assert session.color_source() == Wallpaper("matugen")

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_use_presets_colors_is_one_transaction_and_one_ctrl_z_puts_the_source_back(
    tmp_path: Path,
) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, notes = await live_session(fake, tmp_path, runner)
        await under_matugen(session, tmp_path, runner)
        write_preset(tmp_path, NORD)
        reloads, entries = fake.requests.count("reload"), len(session.journal.entries())
        fake.conversation.update(conversation(**{BORDER_SIZE: 3, INACTIVE: PRESET_COLOUR}))

        result = session.apply_preset("nord", colors=ColorChoice.USE_PRESET)
        await settle(session, runner)

        assert result == PresetApplied(applied=(BORDER_SIZE, INACTIVE), skipped=())
        assert fake.requests.count("reload") == reloads + 1
        assert len(session.journal.entries()) == entries + 1
        assert session.color_source() == PresetColors()
        assert matugen_line(tmp_path) == (
            '-- require("hyprtweaker/bridge/matugen")  -- off: Color source is Preset'
        )
        assert session.model.get(INACTIVE) == colour(PRESET_COLOUR)
        step = session.last_gesture
        assert isinstance(step, PresetStep) and step.color_source is not None

        fake.conversation.update(conversation())
        assert session.undo()
        await settle(session, runner)

        assert fake.requests.count("reload") == reloads + 2
        assert session.color_source() == Wallpaper("matugen")
        assert matugen_line(tmp_path) == 'require("hyprtweaker/bridge/matugen")'
        assert session.model.get(INACTIVE) is UNSET
        assert session.model.get(BORDER_SIZE) is UNSET
        assert notes == []

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_keep_wallpaper_colors_applies_the_rest_and_leaves_the_source(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, _ = await live_session(fake, tmp_path, runner)
        await under_matugen(session, tmp_path, runner)
        manifest_before = session.paths.manifest.read_bytes()
        line_before = matugen_line(tmp_path)
        write_preset(tmp_path, NORD)
        fake.conversation.update(conversation(**{BORDER_SIZE: 3}))

        result = session.apply_preset("nord", colors=ColorChoice.KEEP_WALLPAPER)
        await settle(session, runner)

        assert result == PresetApplied(applied=(BORDER_SIZE,), skipped=())
        assert session.model.get(BORDER_SIZE) == 3
        assert session.model.get(INACTIVE) is UNSET
        assert session.color_source() == Wallpaper("matugen")
        assert matugen_line(tmp_path) == line_before
        bridges = json.loads(session.paths.manifest.read_bytes())["bridges"]
        assert bridges == json.loads(manifest_before)["bridges"]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_rejected_preset_puts_the_wallpaper_colors_back(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, _ = await live_session(fake, tmp_path, runner)
        await under_matugen(session, tmp_path, runner)
        step_before = session.last_gesture
        write_preset(tmp_path, NORD)

        reject_the_next_reload(fake)
        session.apply_preset("nord", colors=ColorChoice.USE_PRESET)
        await settle(session, runner)

        assert session.color_source() == Wallpaper("matugen")
        assert matugen_line(tmp_path) == 'require("hyprtweaker/bridge/matugen")'
        assert session.model.get(INACTIVE) is UNSET
        assert session.last_gesture is step_before

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_undo_leaves_a_color_source_changed_since_and_says_so(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, notes = await live_session(fake, tmp_path, runner)
        await under_matugen(session, tmp_path, runner)
        write_preset(tmp_path, NORD)
        fake.conversation.update(conversation(**{BORDER_SIZE: 3, INACTIVE: PRESET_COLOUR}))
        session.apply_preset("nord", colors=ColorChoice.USE_PRESET)
        await settle(session, runner)
        assert session.set_color_source(ManualColors())
        await settle(session, runner)

        fake.conversation.update(conversation())
        assert session.undo()
        await settle(session, runner)

        assert session.color_source() == ManualColors()
        assert session.model.get(BORDER_SIZE) is UNSET
        assert notes == [
            "Settings restored. Wallpaper colors were changed since, so they stay as they are."
        ]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- the wallpaper --------------------------------------------------------------------------


def test_the_wallpaper_is_set_once_the_transaction_stands_and_ctrl_z_puts_it_back(
    tmp_path: Path,
) -> None:
    daemon = FakeDaemon(tmp_path / "run")
    image = tmp_path / "nord.png"
    image.write_bytes(b"\x89PNG")

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, notes = await live_session(fake, tmp_path, runner, daemon.seam())
        write_preset(tmp_path, {BORDER_SIZE: 3}, wallpaper=str(image))
        fake.conversation.update(conversation(**{BORDER_SIZE: 3}))

        session.apply_preset("nord", wallpaper=True)
        await settle(session, runner)

        assert daemon.calls == [("query",), ("img", str(image))]
        step = session.last_gesture
        assert isinstance(step, PresetStep)
        assert step.wallpaper == WallpaperChange((Shown("DP-1", MINE),), image)

        fake.conversation.update(conversation())
        assert session.undo()
        await settle(session, runner)

        assert daemon.calls[2:] == [("query",), ("img", "--outputs", "DP-1", str(MINE))]
        assert session.model.get(BORDER_SIZE) is UNSET
        assert notes == []

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_keep_mine_never_calls_the_daemon(tmp_path: Path) -> None:
    daemon = FakeDaemon(tmp_path / "run")
    image = tmp_path / "nord.png"
    image.write_bytes(b"\x89PNG")

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, notes = await live_session(fake, tmp_path, runner, daemon.seam())
        write_preset(tmp_path, {BORDER_SIZE: 3}, wallpaper=str(image))
        fake.conversation.update(conversation(**{BORDER_SIZE: 3}))

        session.apply_preset("nord", wallpaper=False)
        await settle(session, runner)

        assert daemon.calls == []
        assert session.model.get(BORDER_SIZE) == 3
        assert notes == []

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_rejected_preset_never_touches_the_wallpaper(tmp_path: Path) -> None:
    daemon = FakeDaemon(tmp_path / "run")
    image = tmp_path / "nord.png"
    image.write_bytes(b"\x89PNG")

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, _ = await live_session(fake, tmp_path, runner, daemon.seam())
        write_preset(tmp_path, {BORDER_SIZE: 3}, wallpaper=str(image))

        reject_the_next_reload(fake)
        session.apply_preset("nord", wallpaper=True)
        await settle(session, runner)

        assert daemon.calls == []
        assert session.model.get(BORDER_SIZE) is UNSET

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_with_no_daemon_the_user_is_told_why_the_wallpaper_did_not_change(
    tmp_path: Path,
) -> None:
    image = tmp_path / "nord.png"
    image.write_bytes(b"\x89PNG")

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, notes = await live_session(fake, tmp_path, runner)
        write_preset(tmp_path, {BORDER_SIZE: 3}, wallpaper=str(image))
        fake.conversation.update(conversation(**{BORDER_SIZE: 3}))

        result = session.apply_preset("nord", wallpaper=True)
        await settle(session, runner)

        assert result == PresetApplied(applied=(BORDER_SIZE,), skipped=())
        assert session.model.get(BORDER_SIZE) == 3
        assert notes == [
            "The wallpaper was not changed. No wallpaper daemon is running. This app "
            "changes the wallpaper through awww or swww."
        ]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_undo_leaves_a_wallpaper_changed_since_and_says_so(tmp_path: Path) -> None:
    daemon = FakeDaemon(tmp_path / "run")
    image = tmp_path / "nord.png"
    image.write_bytes(b"\x89PNG")

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, notes = await live_session(fake, tmp_path, runner, daemon.seam())
        write_preset(tmp_path, {BORDER_SIZE: 3}, wallpaper=str(image))
        fake.conversation.update(conversation(**{BORDER_SIZE: 3}))
        session.apply_preset("nord", wallpaper=True)
        await settle(session, runner)
        daemon.showing = Path("/pictures/since.png")

        fake.conversation.update(conversation())
        assert session.undo()
        await settle(session, runner)

        assert daemon.calls[-1] == ("query",)
        assert session.model.get(BORDER_SIZE) is UNSET
        assert notes == [
            "Settings restored. The wallpaper was changed since, so it stays as it is."
        ]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- saving ---------------------------------------------------------------------------------


def save(session: Session, *scopes: CaptureScope) -> list[PresetSaveResult]:
    results: list[PresetSaveResult] = []
    session.save_preset("Nord", scopes, done=results.append)
    return results


def test_a_saved_wallpaper_is_the_image_the_daemon_shows(tmp_path: Path) -> None:
    daemon = FakeDaemon(tmp_path / "run")

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, _ = await live_session(fake, tmp_path, runner, daemon.seam())

        results = save(session, CaptureScope.WALLPAPER)
        await runner.settle()

        assert len(results) == 1 and isinstance(results[0], PresetSaved)
        assert results[0].preset.wallpaper == str(MINE)
        assert daemon.calls == [("query",)]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_wallpaper_colors_are_not_saved_while_hyprland_is_not_running(
    tmp_path: Path,
) -> None:
    """S7: offline, the model holds the user's colours, not the ones the wallpaper made."""
    paths = ConfigPaths.rooted_at(tmp_path)
    bridge = paths.hypr_dir / "hyprtweaker/bridge/matugen.lua"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("return {}\n", encoding="utf-8")
    paths.entrypoint.write_text('require("hyprtweaker/bridge/matugen")\n', encoding="utf-8")
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=paths,
        app_version="0.1.0",
        connect=_no_compositor,
        wallpapers=no_daemon(),
    )

    assert save(session, CaptureScope.COLORS) == [
        PresetNotSaved(
            "Wallpaper colors can only be captured while applying is on. This app is not "
            "connected to Hyprland."
        )
    ]


def _no_compositor() -> Any:
    from hyprtweaker.engine.ipc import NoInstance

    raise NoInstance("headless")


def test_the_current_wallpaper_is_what_the_daemon_shows_or_none(tmp_path: Path) -> None:
    """What the Theming page's Regenerate runs a tool on (#164)."""
    from hyprtweaker.engine.ipc import Instance, NoInstance

    def no_compositor() -> Instance:
        raise NoInstance("no compositor here")

    def session(wallpapers: Wallpapers) -> Session:
        return Session(
            spawn=lambda coro: coro.close(),
            paths=ConfigPaths.rooted_at(tmp_path),
            app_version="0",
            connect=no_compositor,
            wallpapers=wallpapers,
        )

    daemon = FakeDaemon(tmp_path / "runtime")
    assert session(daemon.seam()).current_wallpaper() == MINE
    assert daemon.calls == [("query",)]
    assert session(no_daemon()).current_wallpaper() is None
