"""Wiring a theming tool to its Bridge module: detect, plan, consent, wire, unwire (#166).

ADR-0006 mechanism 1, behind S3's consent: nothing that belongs to another tool is written
until the user has seen the plan, and the way back restores exactly what was there. Every
test works on fixture configs written from `docs/research/theming-tools.md` (#167) under the
test's own home (`ConfigPaths.default()` inside #233's fence); no tool is run.
"""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path

import pytest

from hyprtweaker.engine.bridge import (
    MATUGEN,
    REGISTRY,
    SHELL_SWITCH,
    WALLUST,
    ToolSpec,
    VersionState,
)
from hyprtweaker.engine.bridge import wire as wiring
from hyprtweaker.engine.bridge.wire import (
    ConsentRequired,
    IfChanged,
    NeedsChoice,
    NotDone,
    ToolDetection,
    Unwired,
    WireConsent,
    Wired,
    WirePlan,
    detect,
    plan_wire,
    unwire,
    wire,
)
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.state import Manifest

MATUGEN_CONFIG = """\
[config]

[templates.hyprland]
input_path = '~/.config/matugen/templates/hyprland-colors.lua'
output_path = '~/.config/hypr/colors.lua'
post_hook = 'notify-send matugen'  # the user's own hook stays

[templates.kitty]
input_path = '~/.config/matugen/templates/kitty-colors.conf'
output_path = '~/.config/kitty/colors.conf'
"""
"""matugen's README stanza (#167 § matugen, "Config stanza") plus a second template."""


class Registrar:
    """Records Bridge entries the way the Session does, into the Manifest on disk."""

    def __init__(self, paths: ConfigPaths, *, accept: bool = True) -> None:
        self.paths = paths
        self.accept = accept

    def manifest(self) -> Manifest:
        return Manifest.load(self.paths.manifest, app_version="x", schema_version="y")

    def save(self, manifest: Manifest) -> None:
        self.paths.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.paths.manifest.write_text(manifest.render(), encoding="utf-8")

    def add(self, tool: str) -> bool:
        if self.accept:
            self.save(self.manifest().add_bridge(REGISTRY[tool], present=()))
        return self.accept

    def remove(self, tool: str) -> bool:
        if self.accept:
            self.save(self.manifest().remove_bridge(tool))
        return self.accept


@pytest.fixture
def paths() -> ConfigPaths:
    return ConfigPaths.default()


def put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, by relative path, with its bytes."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def pack_text(spec: ToolSpec, index: int) -> str:
    """The Template pack file the registry ships, as `wire` must install it."""
    assert spec.template_pack is not None
    return spec.template_pack.templates[index].text


def planned(paths: ConfigPaths, tool: str) -> WirePlan:
    plan = plan_wire(tool, paths=paths)
    assert isinstance(plan, WirePlan), plan
    return plan


def test_matugen_retargets_the_users_hyprland_entry_and_installs_the_template(
    paths: ConfigPaths,
) -> None:
    home = paths.config_home.parent
    config = put(paths.config_home / "matugen/config.toml", MATUGEN_CONFIG)
    registrar = Registrar(paths)

    plan = planned(paths, "matugen")
    result = wire(plan, WireConsent(plan), register=registrar.add)

    assert isinstance(result, Wired)
    assert config.read_text(encoding="utf-8") == MATUGEN_CONFIG.replace(
        "input_path = '~/.config/matugen/templates/hyprland-colors.lua'",
        f"input_path = '{home}/.config/matugen/templates/hyprtweaker-hyprland.lua'",
    ).replace(
        "output_path = '~/.config/hypr/colors.lua'",
        f"output_path = '{home}/.config/hypr/hyprtweaker/bridge/matugen.lua'",
    )
    template = paths.config_home / "matugen/templates/hyprtweaker-hyprland.lua"
    assert template.read_text(encoding="utf-8") == pack_text(MATUGEN, 0)
    assert [entry.tool for entry in registrar.manifest().bridges] == ["matugen"]
    assert [(edit.shown, edit.change) for edit in plan.files] == [
        ("~/.config/matugen/templates/hyprtweaker-hyprland.lua", "new"),
        ("~/.config/matugen/config.toml", "changed"),
    ]
    assert plan.files[1].excerpt_before == (
        "[templates.hyprland]\n"
        "input_path = '~/.config/matugen/templates/hyprland-colors.lua'\n"
        "output_path = '~/.config/hypr/colors.lua'\n"
    )
    assert plan.files[1].excerpt_after == (
        "[templates.hyprland]\n"
        f"input_path = '{home}/.config/matugen/templates/hyprtweaker-hyprland.lua'\n"
        f"output_path = '{home}/.config/hypr/hyprtweaker/bridge/matugen.lua'\n"
    )
    assert plan.backups_shown == "~/.local/state/hyprtweaker/bridge-backups/"


WALLUST_CONFIG = """\
backend = "fastresize"
palette = "dark16"

[templates]
# wallust-templates' hyprlang file
hyprland = { template = 'hyprland.conf', target = '~/.config/hypr/wallust-colors.conf' }
kitty = { template = 'kitty.conf', target = '~/.config/kitty/colors.conf' }

[hooks]
reload = 'hyprctl reload'
"""
"""#167 § wallust's stanza, with the user's own reload hook, which stays as they wrote it."""

NOCTALIA_CONFIG = """\
[theme]
source = "wallpaper"

[theme.templates]
builtin_ids = ["gtk", "kitty"]
"""

DMS_COLORS = 'hl.config({ general = { col = { active_border = "rgb(b0c6ff)" } } })\n'

EMPTY = Manifest(app_version="x", schema_version="y")

TOOLS_ON_PATH = {"matugen", "wallust", "noctalia", "dms", "shell-switch"}


def find(name: str) -> Path | None:
    """Every v1 tool's program is installed; none is ever run."""
    return Path("/nonexistent/bin") / name if name in TOOLS_ON_PATH else None


def fixtures(paths: ConfigPaths) -> None:
    """One fixture config per v1 tool, as #167 records each."""
    put(paths.config_home / "matugen/config.toml", MATUGEN_CONFIG)
    put(paths.config_home / "wallust/wallust.toml", WALLUST_CONFIG)
    put(paths.config_home / "noctalia/config.toml", NOCTALIA_CONFIG)
    put(paths.hypr_dir / "dms/colors.lua", DMS_COLORS)
    put(paths.config_home / "shell-switch/shell-switch", "#!/bin/bash\n")
    put(
        paths.config_home / "shell-switch/templates/hyprland/shell-start.conf.template",
        "exec-once = {{LAUNCH_CMD}}\n",
    )


def home_tree(paths: ConfigPaths) -> dict[str, bytes]:
    """The user's files: everything under the home but the app's own state and Manifest."""
    return {
        name: data
        for name, data in tree(paths.config_home.parent).items()
        if not name.startswith(".local/state/hyprtweaker/")
        and not name.startswith(".config/hypr/hyprtweaker/")
    }


def test_wallust_gets_its_template_pack_and_a_templates_entry(paths: ConfigPaths) -> None:
    home = paths.config_home.parent
    fixtures(paths)
    registrar = Registrar(paths)

    plan = planned(paths, "wallust")
    assert isinstance(wire(plan, WireConsent(plan), register=registrar.add), Wired)

    entry = (
        "hyprtweaker_hyprland = { template = 'hyprtweaker-hyprland.lua', "
        f"target = '{home}/.config/hypr/hyprtweaker/bridge/wallust.lua' }}\n"
    )
    assert (paths.config_home / "wallust/wallust.toml").read_text(encoding="utf-8") == (
        WALLUST_CONFIG.replace(
            "kitty = { template = 'kitty.conf', target = '~/.config/kitty/colors.conf' }\n",
            "kitty = { template = 'kitty.conf', target = '~/.config/kitty/colors.conf' }\n"
            + entry,
        )
    )
    template = paths.config_home / "wallust/templates/hyprtweaker-hyprland.lua"
    assert template.read_text(encoding="utf-8") == pack_text(WALLUST, 0)
    assert [entry.line for entry in registrar.manifest().bridges] == [
        'require("hyprtweaker/bridge/wallust")'
    ]


def test_noctalia_5_gets_a_drop_in_that_keeps_the_users_templates_on(
    paths: ConfigPaths,
) -> None:
    fixtures(paths)
    registrar = Registrar(paths)

    plan = plan_wire("noctalia", paths=paths, find=find)
    assert isinstance(plan, WirePlan)
    assert isinstance(wire(plan, WireConsent(plan), register=registrar.add), Wired)

    assert [edit.shown for edit in plan.files] == ["~/.config/noctalia/hyprtweaker.toml"]
    assert (paths.config_home / "noctalia/hyprtweaker.toml").read_text(encoding="utf-8") == (
        "# Written by hyprtweaker: turns on noctalia's Hyprland colors.\n"
        "# Remove noctalia on hyprtweaker's Theming page to take it out again.\n"
        "[theme.templates]\n"
        'builtin_ids = ["gtk", "kitty", "hyprland"]\n'
    )
    assert plan.lines == ('require("noctalia").apply_theme()',)
    assert [entry.line for entry in registrar.manifest().bridges] == list(plan.lines)


def test_dms_writes_nothing_of_its_own_and_gets_its_entry(paths: ConfigPaths) -> None:
    fixtures(paths)
    registrar = Registrar(paths)
    before = home_tree(paths)

    plan = plan_wire("dms", paths=paths, find=find)
    assert isinstance(plan, WirePlan)
    result = wire(plan, WireConsent(plan), register=registrar.add)

    assert result == Wired("dms", written=(), backup=None)
    assert home_tree(paths) == before
    assert [(e.line, e.file) for e in registrar.manifest().bridges] == [
        ('require("dms.colors")', "dms/colors.lua")
    ]


def test_shell_switch_gets_two_templates_and_a_patch_it_never_applies(
    paths: ConfigPaths,
) -> None:
    fixtures(paths)
    script = paths.config_home / "shell-switch/shell-switch"
    registrar = Registrar(paths)

    plan = plan_wire("shell-switch", paths=paths, find=find)
    assert isinstance(plan, WirePlan)
    assert isinstance(wire(plan, WireConsent(plan), register=registrar.add), Wired)

    templates = paths.config_home / "shell-switch/templates/hyprland"
    assert (templates / "shell-start.lua.template").read_text(encoding="utf-8") == pack_text(
        SHELL_SWITCH, 0
    )
    assert (templates / "shell-binds.lua.template").read_text(encoding="utf-8") == pack_text(
        SHELL_SWITCH, 1
    )
    assert "get_startup_config_path" in plan.patch
    assert script.read_text(encoding="utf-8") == "#!/bin/bash\n"
    assert [e.module for e in registrar.manifest().bridges] == [
        "shell-switcher-startup",
        "shell-switcher-binds",
    ]


def test_matugen_without_a_hyprland_entry_gets_one_of_the_apps_own(paths: ConfigPaths) -> None:
    home = paths.config_home.parent
    kitty_only = (
        "[templates.kitty]\n"
        "input_path = '~/.config/matugen/templates/kitty-colors.conf'\n"
        "output_path = '~/.config/kitty/colors.conf'\n"
    )
    config = put(paths.config_home / "matugen/config.toml", kitty_only)

    plan = planned(paths, "matugen")
    wire(plan, WireConsent(plan), register=Registrar(paths).add)

    assert config.read_text(encoding="utf-8") == (
        kitty_only + "\n[templates.hyprtweaker_hyprland]\n"
        f"input_path = '{home}/.config/matugen/templates/hyprtweaker-hyprland.lua'\n"
        f"output_path = '{home}/.config/hypr/hyprtweaker/bridge/matugen.lua'\n"
    )
    assert plan.files[1].excerpt_before == ""
    assert plan.files[1].excerpt_after.startswith("[templates.hyprtweaker_hyprland]\n")


@pytest.mark.parametrize("tool", sorted(TOOLS_ON_PATH))
def test_no_consent_no_write(paths: ConfigPaths, tool: str) -> None:
    fixtures(paths)
    registrar = Registrar(paths)
    before = tree(paths.config_home.parent)

    plan = plan_wire(tool, paths=paths, find=find)
    assert isinstance(plan, WirePlan)
    other = replace(plan, lines=("-- something else",))
    with pytest.raises(ConsentRequired):
        wire(plan, None, register=registrar.add)
    with pytest.raises(ConsentRequired):
        wire(plan, WireConsent(other), register=registrar.add)

    assert tree(paths.config_home.parent) == before


@pytest.mark.parametrize("tool", sorted(TOOLS_ON_PATH))
def test_unwire_puts_back_every_byte_and_removes_the_entry(
    paths: ConfigPaths, tool: str
) -> None:
    fixtures(paths)
    registrar = Registrar(paths)
    before = home_tree(paths)
    plan = plan_wire(tool, paths=paths, find=find)
    assert isinstance(plan, WirePlan)
    wire(plan, WireConsent(plan), register=registrar.add)

    result = unwire(
        tool, paths=paths, manifest=registrar.manifest(), unregister=registrar.remove
    )

    assert isinstance(result, Unwired)
    assert home_tree(paths) == before
    assert registrar.manifest().bridges == ()
    again = unwire(
        tool, paths=paths, manifest=registrar.manifest(), unregister=registrar.remove
    )
    assert again == Unwired(
        tool, note=f"{REGISTRY[tool].title} is not set up here, so there was nothing to undo."
    )


def test_a_wired_tool_plans_no_further_change(paths: ConfigPaths) -> None:
    fixtures(paths)
    registrar = Registrar(paths)
    for tool in ("matugen", "wallust", "noctalia", "shell-switch"):
        plan = plan_wire(tool, paths=paths, find=find)
        assert isinstance(plan, WirePlan)
        wire(plan, WireConsent(plan), register=registrar.add)

        again = plan_wire(tool, paths=paths, find=find)

        assert isinstance(again, WirePlan)
        assert again.files == (), tool


@pytest.mark.parametrize("tool", sorted(TOOLS_ON_PATH))
def test_installed_configs_carry_no_reload_hook(paths: ConfigPaths, tool: str) -> None:
    fixtures(paths)
    plan = plan_wire(tool, paths=paths, find=find)
    assert isinstance(plan, WirePlan)

    added = [
        line
        for edit in plan.files
        for line in edit.after.splitlines()
        if line not in (edit.before or "").splitlines()
    ]

    assert [line for line in added if re.search(r"hook|hyprctl|reload", line)] == []


def test_a_version_that_cannot_be_bridged_is_refused_with_s5s_sentence(
    paths: ConfigPaths,
) -> None:
    put(paths.hypr_dir / "noctalia/noctalia-colors.conf", "$primary = rgb(b0c6ff)\n")
    put(paths.hypr_dir / "dms/hypr-colors.conf", "general { }\n")
    v4_only = {"dms"}  # noctalia 4 has no `noctalia` program; DMS 1.4 has `dms`

    def older(name: str) -> Path | None:
        return Path("/nonexistent/bin") / name if name in v4_only else None

    assert plan_wire("noctalia", paths=paths, find=older) == NotDone(
        "noctalia", "noctalia 4 found. Its colors can be set up here from noctalia 5 on."
    )
    assert plan_wire("dms", paths=paths, find=older) == NotDone(
        "dms", "DMS 1.4 writes colors this app cannot load. Update DMS to 1.5 or newer."
    )
    assert detect("dms", paths=paths, manifest=EMPTY, find=older).needs_update == (
        "DMS 1.4 writes colors this app cannot load. Update DMS to 1.5 or newer."
    )


def test_a_config_that_is_not_toml_is_refused_and_left_alone(paths: ConfigPaths) -> None:
    put(paths.config_home / "matugen/config.toml", "[templates.hyprland\ninput_path = 1\n")
    before = tree(paths.config_home.parent)

    assert plan_wire("matugen", paths=paths) == NotDone(
        "matugen",
        "matugen's config (~/.config/matugen/config.toml) could not be read as TOML, so it "
        "was left alone. Fix it in an editor, then try again.",
    )
    assert tree(paths.config_home.parent) == before


def test_noctalias_own_settings_choosing_templates_is_refused_with_what_to_do(
    paths: ConfigPaths,
) -> None:
    put(paths.state_home / "noctalia/settings.toml", NOCTALIA_CONFIG)

    result = plan_wire("noctalia", paths=paths, find=find)

    assert result == NotDone(
        "noctalia",
        "noctalia's own settings (~/.local/state/noctalia/settings.toml) choose its "
        "templates, so a file from hyprtweaker would be ignored. Turn on noctalia's "
        "Hyprland template there, then set noctalia up here.",
    )


def test_a_file_changed_after_the_plan_was_shown_is_not_written(paths: ConfigPaths) -> None:
    config = put(paths.config_home / "matugen/config.toml", MATUGEN_CONFIG)
    registrar = Registrar(paths)
    plan = planned(paths, "matugen")
    config.write_text(MATUGEN_CONFIG + "# edited meanwhile\n", encoding="utf-8")
    before = tree(paths.config_home.parent)

    result = wire(plan, WireConsent(plan), register=registrar.add)

    assert result == NotDone(
        "matugen",
        "~/.config/matugen/config.toml changed after the changes were shown, so nothing was "
        "changed. Review them again.",
    )
    assert tree(paths.config_home.parent) == before


def test_an_entrypoint_that_cannot_be_updated_leaves_every_file(paths: ConfigPaths) -> None:
    fixtures(paths)
    before = tree(paths.config_home.parent)
    plan = planned(paths, "matugen")

    result = wire(plan, WireConsent(plan), register=Registrar(paths, accept=False).add)

    assert result == NotDone(
        "matugen", "hyprland.lua could not be updated right now, so nothing was changed."
    )
    assert tree(paths.config_home.parent) == before


def edited_after_setup(paths: ConfigPaths) -> tuple[Registrar, Path, str]:
    config = put(paths.config_home / "matugen/config.toml", MATUGEN_CONFIG)
    registrar = Registrar(paths)
    plan = planned(paths, "matugen")
    wire(plan, WireConsent(plan), register=registrar.add)
    edited = config.read_text(encoding="utf-8") + "# mine\n"
    config.write_text(edited, encoding="utf-8")
    return registrar, config, edited


def test_a_file_changed_since_setup_is_never_silently_overwritten(paths: ConfigPaths) -> None:
    registrar, _, _ = edited_after_setup(paths)
    before = tree(paths.config_home.parent)

    result = unwire(
        "matugen", paths=paths, manifest=registrar.manifest(), unregister=registrar.remove
    )

    assert isinstance(result, NeedsChoice)
    assert [each.shown for each in result.changed] == ["~/.config/matugen/config.toml"]
    assert tree(paths.config_home.parent) == before


def test_restore_the_copy_keeps_the_users_edit_beside_the_backups(paths: ConfigPaths) -> None:
    registrar, config, edited = edited_after_setup(paths)

    result = unwire(
        "matugen",
        paths=paths,
        manifest=registrar.manifest(),
        unregister=registrar.remove,
        if_changed=IfChanged.RESTORE,
    )

    assert isinstance(result, Unwired)
    assert config.read_text(encoding="utf-8") == MATUGEN_CONFIG
    assert not (paths.config_home / "matugen/templates/hyprtweaker-hyprland.lua").exists()
    kept = [
        path.read_text(encoding="utf-8")
        for path in paths.bridge_backups_dir.rglob("replaced/*/config.toml")
    ]
    assert kept == [edited]
    assert registrar.manifest().bridges == ()


def test_leave_it_as_it_is_removes_only_the_entry(paths: ConfigPaths) -> None:
    registrar, config, edited = edited_after_setup(paths)
    template = paths.config_home / "matugen/templates/hyprtweaker-hyprland.lua"

    result = unwire(
        "matugen",
        paths=paths,
        manifest=registrar.manifest(),
        unregister=registrar.remove,
        if_changed=IfChanged.LEAVE,
    )

    assert isinstance(result, Unwired)
    assert config.read_text(encoding="utf-8") == edited
    assert template.read_text(encoding="utf-8") == pack_text(MATUGEN, 0)
    assert registrar.manifest().bridges == ()
    assert unwire(
        "matugen", paths=paths, manifest=registrar.manifest(), unregister=registrar.remove
    ) == Unwired("matugen", note="matugen is not set up here, so there was nothing to undo.")


HAND_EDIT = """\
[config]

[templates.mine]
input_path = '~/.config/matugen/templates/mine.css'
output_path = '~/.config/mine/colors.css'
"""
"""The user's own matugen config, written while the app's Bridge entry was gone."""


def entry_dropped_then_hand_edited(paths: ConfigPaths) -> tuple[Registrar, Path]:
    """Set up, then the Bridge entry vanishes without an unwire (a dotfiles checkout drops
    `manifest.json`), then the user rewrites the tool's config by hand."""
    config = put(paths.config_home / "matugen/config.toml", MATUGEN_CONFIG)
    registrar = Registrar(paths)
    plan = planned(paths, "matugen")
    wire(plan, WireConsent(plan), register=registrar.add)
    registrar.save(registrar.manifest().remove_bridge("matugen"))
    config.write_text(HAND_EDIT, encoding="utf-8")
    return registrar, config


def test_a_hand_edit_made_while_the_entry_was_gone_survives_setting_up_and_removing_again(
    paths: ConfigPaths,
) -> None:
    """Finding 1 of the #153 review: a second `wire` reused the first record, so Remove put
    back the config from before the first setup and the hand edit was gone, with no copy."""
    registrar, config = entry_dropped_then_hand_edited(paths)
    plan = planned(paths, "matugen")
    assert isinstance(wire(plan, WireConsent(plan), register=registrar.add), Wired)
    assert config.read_text(encoding="utf-8") != HAND_EDIT

    result = unwire(
        "matugen", paths=paths, manifest=registrar.manifest(), unregister=registrar.remove
    )

    assert isinstance(result, Unwired)
    assert config.read_bytes() == HAND_EDIT.encode("utf-8")
    assert registrar.manifest().bridges == ()


def test_a_hand_edit_made_while_the_entry_was_gone_stops_remove_and_changes_nothing(
    paths: ConfigPaths,
) -> None:
    registrar, config = entry_dropped_then_hand_edited(paths)
    before = tree(paths.config_home.parent)

    result = unwire(
        "matugen", paths=paths, manifest=registrar.manifest(), unregister=registrar.remove
    )

    assert isinstance(result, NeedsChoice)
    assert tree(paths.config_home.parent) == before


def test_a_symlinked_config_stays_a_symlink(paths: ConfigPaths) -> None:
    dotfiles = put(paths.config_home.parent / "dotfiles/matugen.toml", MATUGEN_CONFIG)
    config = paths.config_home / "matugen/config.toml"
    config.parent.mkdir(parents=True)
    config.symlink_to(dotfiles)
    registrar = Registrar(paths)

    plan = planned(paths, "matugen")
    wire(plan, WireConsent(plan), register=registrar.add)

    assert config.is_symlink()
    assert "hyprtweaker/bridge/matugen.lua" in dotfiles.read_text(encoding="utf-8")
    unwire("matugen", paths=paths, manifest=registrar.manifest(), unregister=registrar.remove)
    assert config.is_symlink()
    assert dotfiles.read_text(encoding="utf-8") == MATUGEN_CONFIG


class Crash(Exception):
    pass


@pytest.mark.parametrize("step", ["register", "record", "template", "config"])
def test_a_crash_at_any_step_converges_on_a_second_wire_or_an_unwire(
    paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    fixtures(paths)
    original = home_tree(paths)
    registrar = Registrar(paths)
    writes = {"record": 0, "template": 1, "config": 2}
    real_write = wiring._write_atomic
    calls: list[Path] = []

    def failing_write(path: Path, content: str | bytes) -> None:
        if len(calls) == writes.get(step, -1):
            raise Crash(path)
        calls.append(path)
        real_write(path, content)

    def failing_register(tool: str) -> bool:
        registrar.add(tool)
        raise Crash(tool)

    monkeypatch.setattr(wiring, "_write_atomic", failing_write)
    plan = planned(paths, "matugen")
    with pytest.raises(Crash):
        wire(
            plan,
            WireConsent(plan),
            register=failing_register if step == "register" else registrar.add,
        )
    monkeypatch.setattr(wiring, "_write_atomic", real_write)

    # A second wire converges on what one clean wire leaves.
    plan = planned(paths, "matugen")
    assert isinstance(wire(plan, WireConsent(plan), register=registrar.add), Wired)
    assert planned(paths, "matugen").files == ()
    assert [entry.tool for entry in registrar.manifest().bridges] == ["matugen"]
    # And the way back still leads to the bytes before the first attempt.
    result = unwire(
        "matugen", paths=paths, manifest=registrar.manifest(), unregister=registrar.remove
    )
    assert isinstance(result, Unwired)
    assert home_tree(paths) == original


@pytest.mark.parametrize("step", ["register", "record", "template", "config"])
def test_an_unwire_after_a_crash_restores_the_original(
    paths: ConfigPaths, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    fixtures(paths)
    original = home_tree(paths)
    registrar = Registrar(paths)
    writes = {"record": 0, "template": 1, "config": 2}
    real_write = wiring._write_atomic
    calls: list[Path] = []

    def failing_write(path: Path, content: str | bytes) -> None:
        if len(calls) == writes.get(step, -1):
            raise Crash(path)
        calls.append(path)
        real_write(path, content)

    def failing_register(tool: str) -> bool:
        registrar.add(tool)
        raise Crash(tool)

    monkeypatch.setattr(wiring, "_write_atomic", failing_write)
    plan = planned(paths, "matugen")
    with pytest.raises(Crash):
        wire(
            plan,
            WireConsent(plan),
            register=failing_register if step == "register" else registrar.add,
        )
    monkeypatch.setattr(wiring, "_write_atomic", real_write)

    result = unwire(
        "matugen", paths=paths, manifest=registrar.manifest(), unregister=registrar.remove
    )

    assert isinstance(result, Unwired)
    assert home_tree(paths) == original
    assert registrar.manifest().bridges == ()


def test_detect_reads_the_tool_path_and_files_only(paths: ConfigPaths) -> None:
    fixtures(paths)
    registrar = Registrar(paths)

    before = detect("matugen", paths=paths, manifest=registrar.manifest(), find=find)
    plan = planned(paths, "matugen")
    wire(plan, WireConsent(plan), register=registrar.add)
    after = detect("matugen", paths=paths, manifest=registrar.manifest(), find=find)

    assert before == ToolDetection(
        "matugen",
        installed=True,
        configured=True,
        wired=False,
        version_state=VersionState.SUPPORTED,
    )
    assert after.wired and after.configured
    assert detect("wallust", paths=paths, manifest=EMPTY).found is True
    assert (
        detect(
            "wallust",
            paths=ConfigPaths.rooted_at(paths.config_home.parent / "x"),
            manifest=EMPTY,
        ).found
        is False
    )


def test_wiring_starts_no_process_and_no_watcher() -> None:
    source = Path(wiring.__file__).read_text(encoding="utf-8")
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }

    assert (
        imported
        & {
            "subprocess",
            "threading",
            "multiprocessing",
            "asyncio",
            "gi",
            "watchdog",
            "inotify",
            "pyinotify",
            "signal",
        }
        == set()
    )
    assert "run_tool" not in source
