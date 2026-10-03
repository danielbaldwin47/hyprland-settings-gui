"""Harness tier: an imported rice against the upstream Lua port of the same rice.

The unit tier proves the mapping is what the sources say it should be. It cannot prove the
mapping is *right*, because both the test and the code read the same table. This tier asks
the only authority that settles it: boot two nested compositors, one on a config the
Importer produced from a rice's `hyprland.conf`, one on the hand-written `hyprland.lua` the
rice's own author shipped, and compare what Hyprland ended up with.

Two limits are deliberate and stated rather than worked around:

- **What each comparison can see.** The imported config is written the way the wizard
  writes it, Entity Modules included (#101), so every boot below loads its binds, rules
  and monitor rules. The state comparison reads Options and every Entity surface `hyprctl`
  lists (binds, animations and curves, monitors, workspace rules, layers); window and
  layer rules have no such surface, so the pixel comparison is the only place they are
  judged, and only for the probe windows it opens.
- **The port is a port, not a transcript.** Upstream hand-wrote their Lua; where it
  deliberately differs from their `.conf`, agreement is the wrong expectation. So the
  comparison is over the options *both* configs set, and disagreements are reported with
  both values rather than silently tolerated.

Staged rather than read in place: staging puts the rice at a real `$HOME`, which is what
makes its `source=` lines resolve -- the unit tier's synthetic environment cannot, so this
is also the only place the *whole* tree gets mapped.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from harness.corpus import rice, rices_with_ground_truth, stage
from harness.nested import NestedHyprland
from harness.state import capture, diff
from harness.visual import Canvas, compare

from hyprtweaker.engine.importer import import_config
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

RICE = "end-4"
APP_VERSION = "0.0.0-test"

KNOWN_PORT_DIVERGENCES: dict[str, str] = {
    "general:col.active_border": "port takes its colors from a theme module, not the .conf",
    "general:col.inactive_border": "port takes its colors from a theme module, not the .conf",
    "misc:background_color": "port takes its colors from a theme module, not the .conf",
    "gestures:workspace_swipe_cancel_ratio": "commented out in the port; falls back to default",
    "gestures:workspace_swipe_distance": "commented out in the port; falls back to default",
    "gestures:workspace_swipe_min_speed_to_force": (
        "commented out in the port; falls back to default"
    ),
}
"""Options where upstream's hand port deliberately differs from upstream's own `.conf`.

Each was checked against both trees: `hyprland/general.conf:10` sets
`workspace_swipe_distance = 700` while `hyprland/general.lua:16` has the same line
**commented out**, so the compositor falls back to 300. The importer is right and the port
is the one that diverges -- which is exactly why this is an allowlist of named, explained
options rather than a tolerance: a *new* disagreement is a mapping bug and must fail.
"""

pytestmark = pytest.mark.hyprland

BindSignature = tuple[int, str, str, tuple[str, ...], str]
"""(modmask, key, submap, flags, description): what a bind is, as `_bind_signature` reads it."""

KNOWN_PORT_BIND_DIVERGENCES: tuple[tuple[str, BindSignature, str], ...] = (
    (
        "import",
        (0, "", "global", ("catch_all", "non_consuming"), ""),
        "`hyprland/keybinds.conf:16` binds `Super, catchall`; `hyprland/keybinds.lua:8-11` has "
        "it commented out",
    ),
    (
        "import",
        (68, "slash", "global", (), ""),
        "`custom/keybinds.conf:5` binds Ctrl+Super Slash; `custom/keybinds.lua` ports no bind",
    ),
    (
        "import",
        (76, "slash", "global", (), ""),
        "`custom/keybinds.conf:6` binds Ctrl+Super+Alt Slash; `custom/keybinds.lua` ports "
        "no bind",
    ),
    (
        "import",
        (64, "", "global", ("repeat",), ""),
        "`hyprland/keybinds.conf:240-243` binds Super code:82 twice; "
        "`hyprland/keybinds.lua:263-269`"
        " keeps one and comments out the rest",
    ),
    (
        "import",
        (64, "", "global", ("repeat",), ""),
        "the same for Super code:86",
    ),
    (
        "import",
        (72, "f1", "global", (), ""),
        "`hyprland/keybinds.conf:218-221` enters the virtual-machine submap with one bind and "
        "leaves it with another; `hyprland/keybinds.lua:217-228` has one universal bind that "
        "toggles",
    ),
    (
        "import",
        (72, "f1", "virtual-machine", (), ""),
        "the leaving half of that pair",
    ),
    (
        "port",
        (72, "f1", "virtual-machine", ("submap_universal",), ""),
        "the port's one toggling bind",
    ),
)
"""Binds upstream's hand port changed, each traced to both trees (#253, 2026-10-02).

Measured at a nested 0.56.2: the import registers 197 binds and the port 191, and these
eight are the whole difference. Read with `_bind_signature`, which compares key names
case-blind (`SUPER_L` in the port, `Super_L` in the `.conf`) and reads the `.conf`'s submap
named `global` (`hyprland/keybinds.conf:5-7`, which the port drops) as the default submap.
A bind outside this table is a mapping bug and fails the test.
"""

PORT_THEME: dict[str, str] = {
    "general:col.active_border": "rgba(F7DCDE39)",
    "general:col.inactive_border": "rgba(A58A8D30)",
    "misc:background_color": "rgba(1D1011FF)",
}
"""The `.conf`'s own theme colours (`hyprland/colors.conf:4-9`), for the pixel comparison.

The port reads its colours from `hyprland/colors.lua`, a theme generated for another
wallpaper (`KNOWN_PORT_DIVERGENCES`). Rendered as shipped, that alone is the whole pixel
difference (#253, 2026-10-02, nested 0.56.2): 26.9% of pixels at a max delta of 35/255, all
of it the background colour in the gaps and behind the translucent probe, and the border
colours at the window edges; with these three values set on the port the two screenshots
are byte-identical. Copied from the `.conf` rather than from the import, so the screenshot
still checks the Importer's colour mapping. The pinned-window border rule
(`colors.conf:34`) differs the same way and is invisible here: no probe window is pinned.
"""


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema("0.56.2")


def _import_staged(staged, schema):  # type: ignore[no-untyped-def]
    """Map a staged rice's `.conf` tree, with the staged home as the environment."""
    return import_config(
        staged.entrypoint,
        schema,
        env={"HOME": str(staged.home), "XDG_CONFIG_HOME": str(staged.home / ".config")},
    )


def _write_model(result, hypr_dir: Path, state_dir: Path) -> Path:
    """Render the imported model, Entities too, into a hypr dir and return its Entrypoint."""
    paths = ConfigPaths(hypr_dir=hypr_dir, state_dir=state_dir)
    result.model.adopt_entities(result.entities)  # what the wizard's write step does
    Writer(paths, app_version=APP_VERSION).write(result.model)
    return paths.entrypoint


def test_a_staged_rice_maps_far_more_than_the_unit_tier_can(tmp_path: Path, schema) -> None:  # type: ignore[no-untyped-def]
    """Staging is what makes `source=` resolve, so this is the fullest mapping there is.

    Not marked `hyprland` in spirit -- it needs no compositor -- but it lives here because
    only the harness can stage, and it is the precondition every test below relies on.
    """
    staged = stage(rice(RICE), tmp_path / "home")
    result = _import_staged(staged, schema)
    assert len(result.files) > 10, "the staged tree did not follow its source= lines"
    assert len(result.model) > 50
    assert result.entities.counts().get("binds", 0) > 100


def test_an_imported_rice_boots_without_config_errors(
    tmp_path: Path, artifacts: Path, schema
) -> None:  # type: ignore[no-untyped-def]
    """The end-to-end promise: point the Importer at a real rice and Hyprland accepts what
    comes out. A config error here means the mapping produced something the compositor
    rejects, which no amount of unit testing would have caught."""
    staged = stage(rice(RICE), tmp_path / "home")
    result = _import_staged(staged, schema)
    entrypoint = _write_model(result, staged.hypr_dir, tmp_path / "state")

    with NestedHyprland(entrypoint, home=staged.home, log=artifacts / "imported.log") as nested:
        state = capture(nested)
        state.write(artifacts / "imported-state.json")

    assert state.config_errors == (), f"the imported config was rejected: {state.config_errors}"


def test_imported_state_agrees_with_the_upstream_port(
    tmp_path: Path, artifacts: Path, schema
) -> None:  # type: ignore[no-untyped-def]
    """Every option both configs set lands on the same live value, and every Entity
    `hyprctl` lists agrees but for `KNOWN_PORT_BIND_DIVERGENCES`."""
    staged = stage(rice(RICE), tmp_path / "home")
    result = _import_staged(staged, schema)
    assert staged.ground_truth_lua is not None

    names = tuple(option.name for option, _ in result.model.set_options())
    assert names, "the import set no options at all"

    entrypoint = _write_model(result, staged.hypr_dir, tmp_path / "state")
    with NestedHyprland(entrypoint, home=staged.home, log=artifacts / "imported.log") as nested:
        imported = capture(nested, options=names)

    # Re-stage: the write above replaced the tree's Entrypoint, and the port must boot from
    # the rice as its author shipped it.
    port_home = tmp_path / "port"
    port = stage(rice(RICE), port_home)
    assert port.ground_truth_lua is not None
    with NestedHyprland(
        port.ground_truth_lua, home=port.home, log=artifacts / "port.log"
    ) as nested:
        upstream = capture(nested, options=names)

    imported.write(artifacts / "imported-options.json")
    upstream.write(artifacts / "port-options.json")

    disagreements = {
        name: (imported.option(name), upstream.option(name))
        for name in names
        if imported.option(name) != upstream.option(name)
    }
    unexpected = set(disagreements) - set(KNOWN_PORT_DIVERGENCES)
    assert not unexpected, (
        "options disagreed with the upstream port that are not known divergences: "
        + ", ".join(f"{name}={disagreements[name]}" for name in sorted(unexpected))
    )
    # The allowlist must not rot into a blanket excuse: every entry has to still be an
    # option this import actually sets, or it is hiding nothing and should be deleted.
    stale = set(KNOWN_PORT_DIVERGENCES) - set(names)
    assert not stale, f"KNOWN_PORT_DIVERGENCES lists options the import no longer sets: {stale}"

    # Entities: every surface `hyprctl` lists, from the same two boots.
    entities = diff(imported, upstream)
    unchanged = (entities.animations, entities.beziers, *entities.lists)
    assert all(delta.empty for delta in unchanged), (
        "Entities disagreed with the upstream port: "
        + "; ".join(str(delta) for delta in unchanged if not delta.empty)
    )
    imported_binds = Counter(_bind_signature(b) for b in imported.surfaces["binds"])
    port_binds = Counter(_bind_signature(b) for b in upstream.surfaces["binds"])
    expected = {
        side: Counter(
            signature for owner, signature, _ in KNOWN_PORT_BIND_DIVERGENCES if owner == side
        )
        for side in ("import", "port")
    }
    assert (imported_binds - port_binds, port_binds - imported_binds) == (
        expected["import"],
        expected["port"],
    ), "binds disagreed with the upstream port beyond KNOWN_PORT_BIND_DIVERGENCES"


#: The boolean `hyprctl binds` fields that change what a bind does.
BIND_FLAGS = (
    "locked",
    "mouse",
    "release",
    "repeat",
    "longPress",
    "non_consuming",
    "auto_consuming",
    "catch_all",
    "allow_input_capture",
)


def _bind_signature(bind: dict[str, Any]) -> BindSignature:
    """A `hyprctl binds` record reduced to what the bind is, not how Lua registered it.

    `dispatcher` and `arg` are left out: both configs are Lua, so every bind reads back as
    `__lua` with a callback number, and the numbers differ whenever the bind order does.
    """
    flags = [name for name in BIND_FLAGS if bind.get(name) is True]
    if bind.get("submap_universal") == "true":
        flags.append("submap_universal")
    return (
        int(bind["modmask"]),
        str(bind["key"]).casefold(),
        str(bind.get("submap") or "global"),
        tuple(sorted(flags)),
        str(bind.get("description", "")),
    )


def _with_conf_theme(port: Path) -> Path:
    """The staged port Entrypoint with `PORT_THEME` set last, so it wins over `colors.lua`.

    Edits the staged copy under the test's tmp dir; the vendored corpus stays as shipped.
    """
    assert set(PORT_THEME) <= set(KNOWN_PORT_DIVERGENCES), "PORT_THEME sets a non-divergence"
    conf_colours = (port.parent / "hyprland" / "colors.conf").read_text()
    for literal in PORT_THEME.values():
        colour = literal.removeprefix("rgba(").removesuffix(")")
        assert colour in conf_colours, f"PORT_THEME {literal} is no longer in colors.conf"
    lines = ["", "-- Harness (#253): the .conf's theme colours, set over colors.lua."]
    for name, literal in PORT_THEME.items():
        section, key = name.split(":", 1)
        path = key.split(".")
        table = f'{path[-1]} = "{literal}"'
        for part in reversed(path[:-1]):
            table = f"{part} = {{ {table} }}"
        lines.append(f"hl.config({{ {section} = {{ {table} }} }})")
    port.write_text(port.read_text() + "\n".join(lines) + "\n")
    return port


def _render(entrypoint: Path, home: Path, png: Path, log: Path) -> Path:
    with (
        NestedHyprland(entrypoint, home=home, log=log) as nested,
        Canvas(nested) as canvas,
    ):
        canvas.spawn_probes()
        canvas.screenshot(png)
    return png


def test_the_imported_config_renders_the_same_screen_every_time(
    tmp_path: Path, artifacts: Path, schema
) -> None:  # type: ignore[no-untyped-def]
    """Pixels, because some of what a config decides is only visible on screen.

    Two boots of the *same* imported config must paint the same screen. That is the visual
    half of the fixpoint: a mapping that produced a set rather than a sequence somewhere --
    an unordered dict of rules, a colour built from an unstable iteration -- would render
    differently on the second boot while every value still read back correctly.
    """
    staged = stage(rice(RICE), tmp_path / "home")
    result = _import_staged(staged, schema)
    entrypoint = _write_model(result, staged.hypr_dir, tmp_path / "state")

    first = _render(entrypoint, staged.home, artifacts / "boot-1.png", artifacts / "boot-1.log")
    second = _render(
        entrypoint, staged.home, artifacts / "boot-2.png", artifacts / "boot-2.log"
    )

    comparison = compare(first, second, heatmap=artifacts / "boot-diff.png")
    assert comparison.visually_identical, (
        f"the same imported config rendered differently on a second boot: {comparison}"
    )


def test_the_imported_config_renders_the_same_screen_as_the_port(
    tmp_path: Path, artifacts: Path, schema
) -> None:  # type: ignore[no-untyped-def]
    """The pixel comparison: the imported rice paints what upstream's port paints.

    The port is rendered with the `.conf`'s theme colours (`PORT_THEME`); as shipped it
    differs by exactly those three colours, which the Option comparison above already names.
    The tolerance is `visually_identical`'s, unchanged.
    """
    staged = stage(rice(RICE), tmp_path / "home")
    result = _import_staged(staged, schema)
    entrypoint = _write_model(result, staged.hypr_dir, tmp_path / "state")
    imported_png = _render(
        entrypoint, staged.home, artifacts / "imported.png", artifacts / "imported-visual.log"
    )

    port = stage(rice(RICE), tmp_path / "port")
    assert port.ground_truth_lua is not None
    port_png = _render(
        _with_conf_theme(port.ground_truth_lua),
        port.home,
        artifacts / "port.png",
        artifacts / "port-visual.log",
    )

    comparison = compare(imported_png, port_png, heatmap=artifacts / "diff.png")
    assert comparison.visually_identical, (
        f"the imported config renders differently from upstream's port: "
        f"{comparison}; see {artifacts / 'diff.png'}"
    )


def test_every_rice_with_a_port_can_be_imported_and_booted(
    tmp_path: Path, artifacts: Path, schema
) -> None:  # type: ignore[no-untyped-def]
    """Whatever ports the corpus carries, all of them import to a config that loads."""
    candidates = rices_with_ground_truth()
    assert candidates, "no corpus rice ships a ground-truth Lua port"
    for candidate in candidates:
        staged = stage(candidate, tmp_path / f"home-{candidate.name}")
        result = _import_staged(staged, schema)
        entrypoint = _write_model(result, staged.hypr_dir, tmp_path / f"state-{candidate.name}")
        with NestedHyprland(
            entrypoint, home=staged.home, log=artifacts / f"{candidate.name}.log"
        ) as nested:
            state = capture(nested, options=("general:border_size",))
        assert state.config_errors == (), (
            f"{candidate.name} imported to a config Hyprland rejected: {state.config_errors}"
        )
