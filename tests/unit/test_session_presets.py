"""Presets through a whole `Session`: capture by scope, apply as one gesture, undo (#168).

Capture and apply are driven against a scripted compositor (`FakeHyprland`) with the real
Apply queue, so "one transaction" is counted in reloads and "undoes cleanly" is read off the
model and the Module on disk -- never off a stub that only records calls.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _fake_hyprland import FakeHyprland, run_with_fake
from _support import (
    SAMPLE_APP_VERSION,
    Runner,
    sample_schema,
    section_conversation,
    session_for,
)

from hyprtweaker.engine.apply import PresetStep
from hyprtweaker.engine.ipc import Instance, NoInstance
from hyprtweaker.engine.model import UNSET, Bind, CssGaps, DispatcherCall
from hyprtweaker.engine.model.values import display_text, parse_value
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.presets import (
    CaptureScope,
    Preset,
    PresetApplied,
    PresetImported,
    PresetNameTaken,
    PresetNotApplied,
    PresetNotImported,
    PresetNotSaved,
    PresetPreview,
    PresetSaved,
    PresetSaveResult,
    scoped_options,
)
from hyprtweaker.engine.presets_archive import ArchiveImage, ThemeArchive
from hyprtweaker.session import Session

BORDER_SIZE = "general:border_size"
GAPS_IN = "general:gaps_in"
ROUNDING = "decoration:rounding"
ACTIVE_BORDER = "general:col.active_border"
GROUPBAR_FONT = "group:groupbar:font_family"


def offline_session(root: Path) -> Session:
    def no_compositor() -> Instance:
        raise NoInstance("headless")

    return Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(root),
        app_version=SAMPLE_APP_VERSION,
        connect=no_compositor,
    )


def save(
    session: Session, name: str, *scopes: CaptureScope, replace: bool = False
) -> list[Any]:
    results: list[PresetSaveResult] = []
    session.save_preset(name, scopes, replace=replace, done=results.append)
    return results


def preset_file(root: Path, slug: str) -> dict[str, Any]:
    path = root / "hypr" / "hyprtweaker" / "presets" / f"{slug}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def colour_sections() -> tuple[str, ...]:
    return tuple(
        sorted({o.section for o in scoped_options(sample_schema(), CaptureScope.COLORS)})
    )


def conversation(**set_values: object) -> dict[str, str]:
    return section_conversation("general", "decoration", *colour_sections(), **set_values)


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


def module_text(root: Path, module: str) -> str:
    """The Module's text; empty when the Writer removed it for having nothing to set."""
    path = root / "hypr" / "hyprtweaker" / module
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- saving ---------------------------------------------------------------------------------


def test_a_read_only_session_saves_the_models_set_values_in_scope(tmp_path: Path) -> None:
    """Not live: the model's set values only. An Unset Option is never saved, and an Option
    outside the chosen scopes is never saved whatever it holds."""
    session = offline_session(tmp_path)
    session.model.set(BORDER_SIZE, 3)
    session.model.set(GAPS_IN, "5 10 5 10")
    session.model.set(ACTIVE_BORDER, "rgba(33ccffee) 45deg")

    [result] = save(session, "Nord evening", CaptureScope.GAPS_LAYOUT)

    assert isinstance(result, PresetSaved)
    assert result.slug == "nord-evening"
    data = preset_file(tmp_path, "nord-evening")
    assert data["options"] == {BORDER_SIZE: 3, GAPS_IN: "5 10 5 10"}
    assert data["scopes"] == ["gaps-layout"]
    assert data["name"] == "Nord evening"
    assert data["app_version"] == SAMPLE_APP_VERSION
    assert data["hyprland_version"] == "0.56.2"
    assert session.presets() == ((result.slug, result.preset),)


def test_a_preset_file_never_holds_a_bind_rule_or_monitor(tmp_path: Path) -> None:
    session = offline_session(tmp_path)
    session.model.entities.binds.append(
        Bind(keys="SUPER + Q", dispatcher=DispatcherCall(path="exec_cmd", positional=("foot",)))
    )
    session.model.set(BORDER_SIZE, 3)
    session.model.set(GROUPBAR_FONT, "Inter")
    session.model.set(ACTIVE_BORDER, "rgba(33ccffee) 45deg")
    session.model.set("animations:enabled", False)
    session.model.set("input:kb_layout", "de")

    # Every scope but the wallpaper, which only a running daemon can say (#170).
    scopes = [scope for scope in CaptureScope if scope is not CaptureScope.WALLPAPER]
    [result] = save(session, "Everything", *scopes)

    assert isinstance(result, PresetSaved)
    data = preset_file(tmp_path, "everything")
    assert set(data) == {
        "format",
        "name",
        "created",
        "scopes",
        "options",
        "app_version",
        "hyprland_version",
        "wallpaper",
    }
    assert data["options"] == {
        BORDER_SIZE: 3,
        GROUPBAR_FONT: "Inter",
        ACTIVE_BORDER: "ee33ccff 45deg",
        "animations:enabled": False,
    }


def test_a_live_session_saves_the_colours_hyprland_is_showing(tmp_path: Path) -> None:
    """ADR-0014: colours are frozen to the values live at capture, whatever generated them. A
    Bridge module sets them where the model never sees, so the model alone would save
    nothing; an Option the running config does not set is still never saved."""
    bridge_colour = parse_value(sample_schema()[ACTIVE_BORDER].type, "rgba(ff8800ff) 90deg")

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        fake.conversation.update(conversation(**{ACTIVE_BORDER: bridge_colour}))
        assert session.model.get(ACTIVE_BORDER) is UNSET

        results = save(session, "Sunset", CaptureScope.COLORS)
        await runner.settle()

        assert [type(r) for r in results] == [PresetSaved]
        assert preset_file(tmp_path, "sunset")["options"] == {ACTIVE_BORDER: "ffff8800 90deg"}

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_saving_over_an_existing_name_asks_first(tmp_path: Path) -> None:
    session = offline_session(tmp_path)
    session.model.set(BORDER_SIZE, 3)
    save(session, "Nord", CaptureScope.GAPS_LAYOUT)
    session.model.set(BORDER_SIZE, 5)

    [taken] = save(session, "NORD", CaptureScope.GAPS_LAYOUT)

    assert taken == PresetNameTaken(slug="nord", name="Nord")
    assert preset_file(tmp_path, "nord")["options"] == {BORDER_SIZE: 3}

    [replaced] = save(session, "NORD", CaptureScope.GAPS_LAYOUT, replace=True)

    assert isinstance(replaced, PresetSaved) and replaced.slug == "nord"
    assert preset_file(tmp_path, "nord")["options"] == {BORDER_SIZE: 5}
    assert preset_file(tmp_path, "nord")["name"] == "NORD"


def test_a_save_with_nothing_to_hold_says_why(tmp_path: Path) -> None:
    session = offline_session(tmp_path)

    assert save(session, "  ", CaptureScope.GAPS_LAYOUT) == [
        PresetNotSaved("Give the preset a name.")
    ]
    assert save(session, "Empty") == [PresetNotSaved("Choose at least one thing to save.")]
    # Never read (no compositor, no files): not "at Hyprland's default" (#148 hand-test 12).
    assert save(session, "Defaults", CaptureScope.GAPS_LAYOUT) == [
        PresetNotSaved(
            "Your settings have not been read, so there is nothing to save. "
            "This app is not connected to Hyprland."
        )
    ]
    assert session.presets() == ()


def test_deleting_a_preset_removes_its_file_and_moves_the_revision(tmp_path: Path) -> None:
    session = offline_session(tmp_path)
    session.model.set(BORDER_SIZE, 3)
    [saved] = save(session, "Nord", CaptureScope.GAPS_LAYOUT)
    before = session.presets_revision

    session.delete_preset(saved.slug)

    assert session.presets() == ()
    assert session.presets_revision > before
    assert not (tmp_path / "hypr" / "hyprtweaker" / "presets" / "nord.json").exists()


def test_scope_size_counts_the_loaded_schemas_options(tmp_path: Path) -> None:
    session = offline_session(tmp_path)

    assert session.scope_size(CaptureScope.ANIMATION_SWITCHES) == 2
    assert session.scope_size(CaptureScope.COLORS) == 22
    assert session.scope_size(CaptureScope.WALLPAPER) == 0


# --- applying -------------------------------------------------------------------------------

GENERAL_MODULE = "options/general.lua"
DECORATION_MODULE = "options/decoration.lua"

REJECTION = (
    '[\n\t"/home/user/.config/hypr/hyprtweaker/options/general.lua:4: '
    "unknown config key 'general.nope'\"\n]\n"
)


def write_preset(root: Path, slug: str, name: str, options: dict[str, Any]) -> None:
    directory = root / "hypr" / "hyprtweaker" / "presets"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{slug}.json").write_text(
        json.dumps(
            {
                "format": 1,
                "name": name,
                "created": "2026-10-02T09:30:00+00:00",
                "scopes": ["gaps-layout"],
                "options": options,
            }
        ),
        encoding="utf-8",
    )


def reject_the_next_reload(fake: FakeHyprland) -> None:
    """Hyprland refuses the next reload's `general.lua`, then is healthy again."""
    clean = fake.conversation["j/configerrors"]
    armed = [True]

    def hook(request: str, _seen: int) -> None:
        if request == "j/configerrors":
            fake.conversation["j/configerrors"] = REJECTION if armed[0] else clean
            armed[0] = False

    fake.on_request = hook


NORD = {BORDER_SIZE: 3, GAPS_IN: "5 10 5 10", ROUNDING: 12}
NORD_LIVE = {BORDER_SIZE: 3, GAPS_IN: CssGaps(5, 10, 5, 10), ROUNDING: 12}


def test_applying_a_preset_is_one_transaction_that_one_ctrl_z_takes_back(
    tmp_path: Path,
) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        fake.conversation.update(conversation(**{ROUNDING: 4}))
        session.set_option(ROUNDING, 4)
        await settle(session, runner)
        rounding_step = session.last_gesture
        write_preset(tmp_path, "nord", "Nord", NORD)
        recorded: list[object] = []
        session.on_recorded = recorded.append
        reloads = fake.requests.count("reload")

        fake.conversation.update(conversation(**NORD_LIVE))
        result = session.apply_preset("nord")
        await settle(session, runner)

        assert result == PresetApplied(applied=(BORDER_SIZE, GAPS_IN, ROUNDING), skipped=())
        assert fake.requests.count("reload") == reloads + 1
        step = session.last_gesture
        assert isinstance(step, PresetStep) and step.name == "Nord"
        assert recorded == [step]
        assert session.model.get(GAPS_IN) == CssGaps(5, 10, 5, 10)
        assert "rounding = 12" in module_text(tmp_path, DECORATION_MODULE)

        fake.conversation.update(conversation(**{ROUNDING: 4}))
        assert session.undo()
        await settle(session, runner)

        assert fake.requests.count("reload") == reloads + 2
        assert session.model.get(BORDER_SIZE) is UNSET
        assert session.model.get(GAPS_IN) is UNSET
        assert session.model.get(ROUNDING) == 4
        assert "rounding = 4" in module_text(tmp_path, DECORATION_MODULE)
        assert "border_size" not in module_text(tmp_path, GENERAL_MODULE)
        assert session.last_gesture is rounding_step
        # Two writes later, the Writer's prune has left the Preset where it was.
        assert [slug for slug, _ in session.presets()] == ["nord"]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_preset_naming_what_this_hyprland_lacks_applies_the_rest(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        write_preset(
            tmp_path,
            "future",
            "From the future",
            {BORDER_SIZE: 3, "general:sparkle": 1, ROUNDING: "twelve"},
        )
        fake.conversation.update(conversation(**{BORDER_SIZE: 3}))

        result = session.apply_preset("future")
        await settle(session, runner)

        assert result == PresetApplied(
            applied=(BORDER_SIZE,), skipped=("general:sparkle", ROUNDING)
        )
        assert session.model.get(BORDER_SIZE) == 3
        assert session.model.get(ROUNDING) is UNSET
        step = session.last_gesture
        assert isinstance(step, PresetStep)
        assert step.options.names == (BORDER_SIZE,)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_rejected_preset_puts_every_option_back_and_records_nothing(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        fake.conversation.update(conversation(**{ROUNDING: 4}))
        session.set_option(ROUNDING, 4)
        await settle(session, runner)
        rounding_step = session.last_gesture
        write_preset(tmp_path, "nord", "Nord", NORD)

        reject_the_next_reload(fake)
        session.apply_preset("nord")
        fake.conversation.update(conversation(**{ROUNDING: 4}))
        await settle(session, runner)

        assert session.model.get(BORDER_SIZE) is UNSET
        assert session.model.get(GAPS_IN) is UNSET
        assert session.model.get(ROUNDING) == 4
        assert "rounding = 4" in module_text(tmp_path, DECORATION_MODULE)
        assert session.last_gesture is rounding_step

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_preset_matching_the_current_look_records_no_step(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        fake.conversation.update(conversation(**NORD_LIVE))
        write_preset(tmp_path, "nord", "Nord", NORD)
        session.apply_preset("nord")
        await settle(session, runner)
        first = session.last_gesture
        recorded: list[object] = []
        session.on_recorded = recorded.append

        session.apply_preset("nord")
        await settle(session, runner)

        assert isinstance(first, PresetStep)
        assert session.last_gesture is first
        assert recorded == []

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_read_only_session_refuses_to_apply_with_the_banners_reason(tmp_path: Path) -> None:
    session = offline_session(tmp_path)
    write_preset(tmp_path, "nord", "Nord", NORD)

    result = session.apply_preset("nord")

    assert isinstance(result, PresetNotApplied)
    assert result.reason == "Applying is off. This app is not connected to Hyprland."
    assert session.model.get(BORDER_SIZE) is UNSET


# --- importing a Theme archive (#169) ---------------------------------------------------------

ARCHIVE_OPTIONS = {
    BORDER_SIZE: 3,
    GAPS_IN: "5 10 5 10",
    ROUNDING: 0,
    ACTIVE_BORDER: "ee33ccff 45deg",
    GROUPBAR_FONT: "Inter",
    "general:sparkle": 1,
    "decoration:active_opacity": "lots",
    "decoration:dim_inactive": None,
}


def archived(name: str = "Nord", **options: Any) -> ThemeArchive:
    return ThemeArchive(
        preset=Preset(
            name=name,
            created=datetime(2026, 10, 2, 9, 30, tzinfo=UTC),
            scopes=frozenset({CaptureScope.GAPS_LAYOUT, CaptureScope.COLORS}),
            options=options or ARCHIVE_OPTIONS,
            app_version="0.1.0",
            hyprland_version="0.56.2",
        ),
        wallpaper=ArchiveImage("png", b"\x89PNG\r\n\x1a\n not decoded here"),
        newer_format=False,
        dropped=(),
    )


def shown(preview: PresetPreview) -> list[tuple[str, str, str, str]]:
    """The preview as the dialog lists it: Section title, Option, before, after."""
    return [
        (
            section.title,
            change.option.name,
            display_text(change.before),
            display_text(change.after),
        )
        for section in preview.sections
        for change in section.changes
    ]


def test_the_preview_lists_each_change_by_section_and_what_is_left_out(tmp_path: Path) -> None:
    session = offline_session(tmp_path)
    session.model.set(BORDER_SIZE, 2)
    before = tree(tmp_path)

    preview = session.preview_preset(archived().preset)

    assert shown(preview) == [
        ("General", BORDER_SIZE, "2", "3"),
        ("General", GAPS_IN, "5 5 5 5", "5 10 5 10"),
        ("General", ACTIVE_BORDER, "ffffffff 0deg", "ee33ccff 45deg"),
        ("Groups", GROUPBAR_FONT, "None", "Inter"),  # None: the Row shows its null label
    ]
    assert preview.unchanged == (ROUNDING,)
    assert preview.unknown == ("general:sparkle",)
    assert preview.invalid == ("decoration:active_opacity", "decoration:dim_inactive")
    assert tree(tmp_path) == before  # a preview writes nothing


def tree(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def test_an_imported_theme_applies_as_one_transaction_one_ctrl_z_takes_back(
    tmp_path: Path,
) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        reloads = fake.requests.count("reload")

        imported = session.import_preset(archived(**NORD))
        assert isinstance(imported, PresetImported)
        fake.conversation.update(conversation(**NORD_LIVE))
        result = session.apply_preset(imported.slug)
        await settle(session, runner)

        assert result == PresetApplied(applied=(BORDER_SIZE, GAPS_IN, ROUNDING), skipped=())
        assert fake.requests.count("reload") == reloads + 1
        step = session.last_gesture
        assert isinstance(step, PresetStep) and step.name == "Nord"
        image = tmp_path / "hypr" / "hyprtweaker" / "presets" / "wallpapers" / "nord.png"
        assert image.read_bytes() == b"\x89PNG\r\n\x1a\n not decoded here"
        assert preset_file(tmp_path, "nord")["wallpaper"] == str(image)

        fake.conversation.update(conversation())
        assert session.undo()
        await settle(session, runner)
        assert session.model.get(BORDER_SIZE) is UNSET
        assert session.model.get(ROUNDING) is UNSET

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_importing_a_name_already_taken_keeps_both(tmp_path: Path) -> None:
    session = offline_session(tmp_path)
    write_preset(tmp_path, "nord", "Nord", NORD)
    revision = session.presets_revision

    theme = archived(**{BORDER_SIZE: 5})

    imported = session.import_preset(theme)

    image = tmp_path / "hypr" / "hyprtweaker" / "presets" / "wallpapers" / "nord-2.png"
    assert imported == PresetImported(
        "nord-2", replace(theme.preset, name="Nord 2", wallpaper=str(image))
    )
    assert [(slug, preset.name) for slug, preset in session.presets()] == [
        ("nord", "Nord"),
        ("nord-2", "Nord 2"),
    ]
    assert preset_file(tmp_path, "nord")["options"] == NORD
    assert session.presets_revision != revision


def test_an_import_that_cannot_be_written_says_why_and_leaves_nothing(tmp_path: Path) -> None:
    session = offline_session(tmp_path)
    presets = tmp_path / "hypr" / "hyprtweaker" / "presets"
    presets.parent.mkdir(parents=True)
    presets.write_text("a file where the presets folder goes")
    before = tree(tmp_path)

    imported = session.import_preset(archived())

    assert isinstance(imported, PresetNotImported)
    assert imported.reason.startswith("The theme could not be added to your presets: ")
    assert tree(tmp_path) == before
