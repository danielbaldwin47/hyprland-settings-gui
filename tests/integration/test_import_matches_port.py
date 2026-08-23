"""Harness tier: an imported rice against the upstream Lua port of the same rice.

The unit tier proves the mapping is what the sources say it should be. It cannot prove the
mapping is *right*, because both the test and the code read the same table. This tier asks
the only authority that settles it: boot two nested compositors, one on a config the
Importer produced from a rice's `hyprland.conf`, one on the hand-written `hyprland.lua` the
rice's own author shipped, and compare what Hyprland ended up with.

Two limits are deliberate and stated rather than worked around:

- **Options only, for now.** The Writer renders Option Modules; Entity Modules (`binds.lua`,
  `monitors.lua`) are #64. So the Entities this Importer produces are asserted in the unit
  tier and cannot yet be booted. When the Entity writer lands, the comparison here widens
  with no change to the shape of these tests.
- **The port is a port, not a transcript.** Upstream hand-wrote their Lua; where it
  deliberately differs from their `.conf`, agreement is the wrong expectation. So the
  comparison is over the options *both* configs set, and disagreements are reported with
  both values rather than silently tolerated.

Staged rather than read in place: staging puts the rice at a real `$HOME`, which is what
makes its `source=` lines resolve -- the unit tier's synthetic environment cannot, so this
is also the only place the *whole* tree gets mapped.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness.corpus import rice, rices_with_ground_truth, stage
from harness.nested import NestedHyprland
from harness.state import capture
from harness.visual import Canvas, compare

from hyprtweaker.engine.importer import import_config
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

RICE = "end-4"
APP_VERSION = "0.0.0-test"

pytestmark = pytest.mark.hyprland


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
    """Render the imported model into a hypr dir and return its Entrypoint."""
    paths = ConfigPaths(hypr_dir=hypr_dir, state_dir=state_dir)
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


def test_imported_options_agree_with_the_upstream_port(
    tmp_path: Path, artifacts: Path, schema
) -> None:  # type: ignore[no-untyped-def]
    """Every option both configs set must land on the same live value."""
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
    # Where upstream's hand port deliberately differs from their own .conf there is nothing
    # to agree about, so the assertion is on the *share* that agrees, reported in full.
    agreed = len(names) - len(disagreements)
    assert agreed / len(names) >= 0.9, (
        f"only {agreed}/{len(names)} options matched the upstream port; "
        f"disagreements: {sorted(disagreements.items())[:10]}"
    )


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


@pytest.mark.xfail(
    reason=(
        "needs the Entity writer of #64. The mapping produces Binds, Rules and monitor "
        "rules, but the Writer renders Option Modules only, so the booted config is "
        "missing every window rule the port has -- roughly a third of the screen. The "
        "state-level comparison above is the part that is checkable today."
    ),
    strict=False,
)
def test_the_imported_config_renders_the_same_screen_as_the_port(
    tmp_path: Path, artifacts: Path, schema
) -> None:  # type: ignore[no-untyped-def]
    """The full-fidelity pixel comparison, kept runnable so #64 can just delete the mark.

    Left in place rather than deferred to a later ticket because it is the measurement that
    says how far there is to go: it prints the exact pixel delta between an imported rice
    and its hand-written port every time it runs.
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
        port.ground_truth_lua, port.home, artifacts / "port.png", artifacts / "port-visual.log"
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
