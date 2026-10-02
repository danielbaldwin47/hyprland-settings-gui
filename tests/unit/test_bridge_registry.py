"""The Bridge registry and its entries (ADR-0006, ADR-0014; #163).

Facts are #167's (`docs/research/theming-tools.md`); these tests hold the registry to them
where a second source exists -- a template's own text, the bundled Schema -- and drive the
pure functions the Writer and the Session build the Entrypoint and the Color source from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from _support import sample_schema

from hyprtweaker.engine.bridge import (
    ACTIVE,
    DMS,
    MATUGEN,
    NOCTALIA,
    REGISTRY,
    SHELL_SWITCH,
    WAITING,
    WALLUST,
    BridgeEntry,
    ManualColors,
    Off,
    PresetColors,
    Several,
    VersionState,
    Wallpaper,
    bridge_states_for,
    bridges_from_entrypoint,
    color_source_of,
    entries_for,
    is_color_key,
    owners,
    render_line,
    version_state,
    with_presence,
)
from hyprtweaker.engine.schema.types import OptionType

ALL_FILES = frozenset(each.file for spec in REGISTRY.values() for each in spec.modules)


def keys_set_by(template: str) -> tuple[str, ...]:
    """The colon-form Options a template's `hl.config({...})` block assigns, in order.

    Read off the indentation the templates are written in: one `name = {` per nesting level,
    one `name = value,` per leaf. A gradient's own table (`colors`, `angle`) is the value.
    """
    block = template[template.index("hl.config({") :]
    path: list[str] = []
    keys: list[str] = []
    depth_of_value: int | None = None
    for raw in block.splitlines()[1:]:
        line = raw.strip()
        if line.startswith("})"):
            break
        opened = re.match(r"^([\w.]+) = \{$", line)
        if depth_of_value is not None:
            depth_of_value += line.count("{") - line.count("}")
            if depth_of_value <= 0:
                depth_of_value = None
            continue
        if opened:
            name = opened.group(1)
            if "col" in path:  # a gradient: the table is the value
                keys.append(_join([*path, name]))
                depth_of_value = 1
            else:
                path.append(name)
        elif line in ("},", "}"):
            path.pop()
        elif leaf := re.match(r"^([\w.]+) = .+,$", line):
            keys.append(_join([*path, leaf.group(1)]))
    return tuple(keys)


def _join(path: list[str]) -> str:
    """`general`, `col`, `active_border` -> `general:col.active_border`."""
    sections = [part for part in path if part != "col"]
    leaf = path[-1]
    prefix = ":".join(sections[:-1])
    return f"{prefix}:col.{leaf}" if "col" in path else f"{prefix}:{leaf}"


# --- the registry ---------------------------------------------------------------------------


def test_matugens_owned_keys_are_the_keys_its_template_sets() -> None:
    assert keys_set_by(MATUGEN.template_pack.templates[0].text) == (  # type: ignore[union-attr]
        "general:col.active_border",
        "general:col.inactive_border",
        "group:col.border_active",
        "group:col.border_inactive",
        "group:col.border_locked_active",
        "group:col.border_locked_inactive",
    )
    assert MATUGEN.owned_keys == keys_set_by(MATUGEN.template_pack.templates[0].text)  # type: ignore[union-attr]


def test_wallusts_owned_keys_are_the_keys_its_template_sets() -> None:
    text = WALLUST.template_pack.templates[0].text  # type: ignore[union-attr]
    assert keys_set_by(text) == (
        "general:col.active_border",
        "general:col.inactive_border",
        "group:col.border_active",
        "group:col.border_inactive",
    )
    assert WALLUST.owned_keys == keys_set_by(text)


def test_the_colour_test_agrees_with_the_schema_on_every_option() -> None:
    schema = sample_schema()
    disagree = [
        option.name
        for option in schema.options
        if is_color_key(option.name) != (option.type in (OptionType.COLOR, OptionType.GRADIENT))
    ]
    assert disagree == []


def test_every_owned_key_is_an_option_this_hyprland_has() -> None:
    schema = sample_schema()
    unknown = [
        key for spec in REGISTRY.values() for key in spec.owned_keys if schema.get(key) is None
    ]
    assert unknown == []


def test_only_shell_switch_sets_no_colour() -> None:
    assert {spec.tool: spec.sets_colors for spec in REGISTRY.values()} == {
        "noctalia": True,
        "dms": True,
        "shell-switch": False,
        "matugen": True,
        "wallust": True,
    }


def test_no_template_pack_installs_a_reload_hook() -> None:
    """Every v1 tool writes a required file in place, which already reloads (#167 C1)."""
    texts = [
        text
        for spec in REGISTRY.values()
        if spec.template_pack is not None
        for text in (
            spec.template_pack.stanza,
            spec.template_pack.patch,
            *(each.text for each in spec.template_pack.templates),
        )
    ]
    assert [text for text in texts if "reload" in text or "hook" in text] == []


def test_a_stanza_carries_absolute_paths(tmp_path: Path) -> None:
    pack = WALLUST.template_pack
    assert pack is not None
    stanza = pack.stanza_for(
        tmp_path / "config", tmp_path / "config/hypr", WALLUST.modules[0].file
    )
    assert stanza == (
        "[templates]\n"
        "hyprtweaker_hyprland = { template = 'hyprtweaker-hyprland.lua', "
        f"target = '{tmp_path}/config/hypr/hyprtweaker/bridge/wallust.lua' }}\n"
    )
    matugen = MATUGEN.template_pack
    assert matugen is not None
    assert matugen.stanza_for(
        tmp_path, tmp_path / "hypr", "hyprtweaker/bridge/matugen.lua"
    ) == (
        "[templates.hyprtweaker_hyprland]\n"
        f"input_path = '{tmp_path}/matugen/templates/hyprtweaker-hyprland.lua'\n"
        f"output_path = '{tmp_path}/hypr/hyprtweaker/bridge/matugen.lua'\n"
    )


def test_matugen_regenerates_with_its_parameters() -> None:
    binary, image = Path("/bin/matugen"), Path("/w/sea.png")
    assert MATUGEN.rerun_argv(binary, image) == (
        "/bin/matugen",
        "image",
        "/w/sea.png",
        "--source-color-index",
        "0",
        "-m",
        "dark",
        "-t",
        "scheme-tonal-spot",
    )
    assert MATUGEN.rerun_argv(binary, image, {"mode": "light", "contrast": 0.5})[-6:] == (
        "-m",
        "light",
        "-t",
        "scheme-tonal-spot",
        "--contrast",
        "0.5",
    )


def test_wallust_regenerates_from_the_image_and_dms_cannot() -> None:
    assert WALLUST.rerun_argv(Path("/bin/wallust"), Path("/w/a.jpg")) == (
        "/bin/wallust",
        "run",
        "/w/a.jpg",
    )
    with pytest.raises(ValueError, match="DMS has no regenerate command"):
        DMS.rerun_argv(Path("/bin/dms"), Path("/w/a.jpg"))


@pytest.mark.parametrize(
    ("spec", "binaries", "files", "expected"),
    [
        (NOCTALIA, {"noctalia"}, set(), VersionState.SUPPORTED),
        (NOCTALIA, set(), {"hypr/noctalia/noctalia-colors.conf"}, VersionState.NEEDS_UPDATE),
        (DMS, {"dms"}, {"hypr/dms/colors.lua"}, VersionState.SUPPORTED),
        (DMS, {"dms"}, {"hypr/dms/hypr-colors.conf"}, VersionState.NEEDS_UPDATE),
        (
            DMS,
            {"dms"},
            {"hypr/dms/hypr-colors.conf", "hypr/dms/colors.lua"},
            VersionState.SUPPORTED,
        ),
        (MATUGEN, {"matugen"}, set(), VersionState.SUPPORTED),
    ],
)
def test_the_version_comes_from_files_and_binary_names(
    spec: object, binaries: set[str], files: set[str], expected: VersionState
) -> None:
    assert version_state(spec, binaries=binaries, files=files) is expected  # type: ignore[arg-type]


def test_the_needs_update_sentences_are_the_settled_copy() -> None:
    assert NOCTALIA.detection.needs_update == (
        "noctalia 4 found. Update noctalia to 5 to set its colors up here."
    )
    assert DMS.detection.needs_update == (
        "DMS 1.4 writes colors this app cannot load. Update DMS to 1.5 or newer."
    )


# --- entries and the gate -------------------------------------------------------------------


def wired(*specs: object, present: frozenset[str] = ALL_FILES) -> tuple[BridgeEntry, ...]:
    return tuple(
        entry
        for spec in specs
        for entry in entries_for(spec, present=present)  # type: ignore[arg-type]
    )


def states(entries: tuple[BridgeEntry, ...]) -> dict[str, object]:
    return {entry.module: entry.state for entry in entries}


def test_wiring_waits_for_a_file_the_tool_has_not_written() -> None:
    entries = wired(MATUGEN, DMS, present=frozenset({"dms/colors.lua"}))
    assert states(entries) == {"hyprtweaker/bridge/matugen": WAITING, "dms.colors": ACTIVE}


def test_a_preset_gates_off_every_colour_bridge_and_leaves_the_rest() -> None:
    entries = wired(MATUGEN, WALLUST, NOCTALIA, DMS, SHELL_SWITCH)

    gated = bridge_states_for(PresetColors(), entries, present=ALL_FILES)

    off = Off(PresetColors())
    assert states(gated) == {
        "hyprtweaker/bridge/matugen": off,
        "hyprtweaker/bridge/wallust": off,
        "noctalia": off,
        "dms.colors": off,
        "shell-switcher-startup": ACTIVE,
        "shell-switcher-binds": ACTIVE,
    }


def test_a_wallpaper_source_enables_one_backend_and_gates_the_other() -> None:
    entries = bridge_states_for(ManualColors(), wired(MATUGEN, WALLUST, DMS), present=ALL_FILES)

    switched = bridge_states_for(
        Wallpaper("wallust"), entries, present=ALL_FILES - {"hyprtweaker/bridge/wallust.lua"}
    )

    assert states(switched) == {
        "hyprtweaker/bridge/matugen": Off(Wallpaper("wallust")),
        "hyprtweaker/bridge/wallust": WAITING,
        "dms.colors": ACTIVE,
    }


def test_a_backend_that_is_not_set_up_cannot_be_the_source() -> None:
    with pytest.raises(ValueError, match="wallust is not set up"):
        bridge_states_for(Wallpaper("wallust"), wired(MATUGEN), present=ALL_FILES)


def test_presence_loads_a_waiting_module_and_never_reopens_the_gate() -> None:
    entries = bridge_states_for(PresetColors(), wired(MATUGEN, SHELL_SWITCH), present=set())
    assert states(with_presence(entries, present=ALL_FILES)) == {
        "hyprtweaker/bridge/matugen": Off(PresetColors()),
        "shell-switcher-startup": ACTIVE,
        "shell-switcher-binds": ACTIVE,
    }


def test_owners_come_from_loading_unquarantined_entries_only() -> None:
    entries = bridge_states_for(
        Wallpaper("matugen"), wired(MATUGEN, WALLUST, DMS), present=ALL_FILES
    )

    owned = owners(entries, quarantined=("dms.colors",))

    assert owned == {
        "general:col.active_border": "matugen",
        "general:col.inactive_border": "matugen",
        "group:col.border_active": "matugen",
        "group:col.border_inactive": "matugen",
        "group:col.border_locked_active": "matugen",
        "group:col.border_locked_inactive": "matugen",
    }


def test_the_later_bridge_owns_a_key_two_set() -> None:
    """DMS loads before matugen (registry order), so matugen's value is the one that lands."""
    owned = owners(wired(DMS, MATUGEN))
    assert owned["general:col.active_border"] == "matugen"
    assert owned["group:groupbar:col.active"] == "dms"


def test_each_state_renders_one_line() -> None:
    (matugen,) = wired(MATUGEN)
    assert render_line(matugen, present=True) == 'require("hyprtweaker/bridge/matugen")'
    assert render_line(matugen, present=False) == (
        '-- require("hyprtweaker/bridge/matugen")  -- waiting for matugen\'s first run'
    )
    (dms,) = bridge_states_for(PresetColors(), wired(DMS), present=ALL_FILES)
    assert render_line(dms, present=True) == (
        '-- require("dms.colors")  -- off: Color source is Preset'
    )


# --- reading the Entrypoint back ------------------------------------------------------------


def entrypoint_of(entries: tuple[BridgeEntry, ...], present: frozenset[str] = ALL_FILES) -> str:
    lines = [render_line(entry, present=entry.file in present) for entry in entries]
    return '-- header\nrequire("hyprtweaker/options/general")\n' + "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Wallpaper("matugen"), Wallpaper("matugen")),
        (Wallpaper("wallust"), Wallpaper("wallust")),
        (PresetColors(), PresetColors()),
        (ManualColors(), ManualColors()),
    ],
)
def test_the_color_source_reads_back_off_the_entrypoint(
    source: object, expected: object
) -> None:
    entries = bridge_states_for(source, wired(MATUGEN, WALLUST, NOCTALIA), present=ALL_FILES)  # type: ignore[arg-type]
    assert color_source_of(entrypoint_of(entries)) == expected


def test_a_backend_still_waiting_is_the_source_it_was_chosen_as() -> None:
    entries = bridge_states_for(
        Wallpaper("matugen"), wired(MATUGEN, WALLUST), present=frozenset()
    )
    assert color_source_of(entrypoint_of(entries, frozenset())) == Wallpaper("matugen")


def test_nothing_wired_is_manual_and_a_hand_edit_can_load_both() -> None:
    assert color_source_of("-- nothing\n") == ManualColors()
    text = 'require("hyprtweaker/bridge/matugen")\nrequire("hyprtweaker.bridge.wallust")\n'
    assert color_source_of(text) == Several(("matugen", "wallust"))


def test_a_lost_manifest_is_rebuilt_from_the_entrypoints_lines() -> None:
    entries = bridge_states_for(
        PresetColors(), wired(NOCTALIA, DMS, SHELL_SWITCH, MATUGEN), present=ALL_FILES
    )

    rebuilt = bridges_from_entrypoint(entrypoint_of(entries))

    assert rebuilt == entries


def test_either_spelling_rebuilds_the_one_the_app_writes() -> None:
    rebuilt = bridges_from_entrypoint('require("dms/colors")\n-- require("user")\n')
    assert [(entry.module, entry.line, entry.state) for entry in rebuilt] == [
        ("dms.colors", 'require("dms.colors")', ACTIVE)
    ]


def test_an_entry_survives_json_and_a_malformed_one_is_dropped() -> None:
    (entry,) = bridge_states_for(
        Wallpaper("matugen"), wired(WALLUST, MATUGEN), present=ALL_FILES
    )[:1]
    assert entry.state == Off(Wallpaper("matugen"))
    assert entry.as_json() == {
        "tool": "wallust",
        "module": "hyprtweaker/bridge/wallust",
        "line": 'require("hyprtweaker/bridge/wallust")',
        "file": "hyprtweaker/bridge/wallust.lua",
        "mechanism": "template-pack",
        "state": "off",
        "off_for": "wallpaper:matugen",
    }
    assert BridgeEntry.from_json(entry.as_json()) == entry
    assert BridgeEntry.from_json({**entry.as_json(), "state": "sideways"}) is None
    assert BridgeEntry.from_json({**entry.as_json(), "line": 3}) is None
