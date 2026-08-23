"""End to end: a model edit becomes pixels, through every shipping component in between.

This is the run ADR-0011 tier 3 exists for, and the one acceptance criterion that no other
tier can stand in for. Every layer is the real one:

    ConfigModel  ->  Writer  ->  hyprland.lua on disk
                                      |
                              nested Hyprland loads it
                                      |
    ConfigModel  ->  Applier ->  reload over the real socket  ->  Read-back
                                      |
                       hyprctl state  +  grim screenshot  ->  diffed

Nothing is stubbed. The `Applier` here is the object the app holds, driving the compositor
through `CommandClient` and `EventStream` over a real Unix socket -- possible only because
`Instance` is a frozen dataclass over a socket directory, so a nested compositor is a
first-class instance rather than something to monkeypatch around.

The two halves answer different questions, and the second is the reason for the whole tier:

- **state** proves the value the model held is the value the compositor holds;
- **screenshots** prove the compositor *rendered* it. A `rounding` that is stored and
  ignored passes the first check and fails the second.

The unit tier cannot reach either: it has no compositor. The static tier proves only that
Hyprland would accept the file.

    pytest tests/integration/test_apply_end_to_end.py -m hyprland
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from harness import Canvas, NestedHyprland, capture, compare, diff, option_names
from harness.visual import write_determinism_preamble

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.apply import Applier, ApplyOutcome  # noqa: E402
from hyprtweaker.engine.ipc import CommandClient, EventStream  # noqa: E402
from hyprtweaker.engine.model import ConfigModel  # noqa: E402
from hyprtweaker.engine.paths import ConfigPaths  # noqa: E402
from hyprtweaker.engine.schema import load_schema  # noqa: E402
from hyprtweaker.engine.writer import Writer  # noqa: E402

pytestmark = pytest.mark.hyprland

SCHEMA_DIR = ROOT / "data" / "schema"
SCHEMA_VERSION = "0.56.2"
APP_VERSION = "0.0.0-harness"

#: The config the compositor starts on. Thin borders, square corners, tight gaps.
BEFORE = {
    "general:border_size": 2,
    "general:gaps_in": 2,
    "general:gaps_out": 4,
    "decoration:rounding": 0,
}

#: The edit under test. Every one of these changes what a window looks like, so a compositor
#: that stored the values without re-rendering fails the screenshot half.
AFTER = {
    "general:border_size": 20,
    "general:gaps_in": 24,
    "general:gaps_out": 40,
    "decoration:rounding": 18,
}


def build(home: Path, values: dict[str, object]) -> tuple[ConfigPaths, ConfigModel, Writer]:
    """Model plus Writer, rooted in the nested compositor's own `$HOME`.

    The determinism preamble is written *before* the first `Writer.write`: the Entrypoint
    only requires files that exist when it is rendered, so a `user.lua` created afterwards is
    never loaded and every pixel-pinning setting in it silently does nothing.
    """
    paths = ConfigPaths.rooted_at(home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)

    model = ConfigModel(load_schema(SCHEMA_VERSION, SCHEMA_DIR))
    for name, value in values.items():
        model.set(name, value)

    return paths, model, Writer(paths, app_version=APP_VERSION)


async def apply_edit(instance: object, model: ConfigModel, writer: Writer, names: list[str]):
    """Run one real Apply transaction against the nested compositor."""
    async with EventStream(instance) as events:  # type: ignore[arg-type]
        client = CommandClient(instance)  # type: ignore[arg-type]
        foreign = []
        async with Applier(
            model=model,
            writer=writer,
            client=client,
            events=events,
            on_foreign_reload=lambda: foreign.append(True),
        ) as applier:
            return await applier.apply(*names)


def test_a_model_edit_reaches_the_compositor_and_the_screen(
    harness_home: Path, artifacts: Path
) -> None:
    """The acceptance run: model -> Writer -> Applier -> state diff + screenshot diff."""
    paths, model, writer = build(harness_home, BEFORE)
    result = writer.write(model)
    assert result.written, "the Writer produced no files"

    names = option_names(SCHEMA_VERSION)

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested.log"
    ) as nested:
        assert nested.config_errors() == (), (
            f"the written config did not load cleanly: {nested.config_errors()}"
        )

        with Canvas(nested) as canvas:
            canvas.spawn_probes()

            before_state = capture(nested, options=names)
            before_shot = canvas.screenshot(artifacts / "before.png")
            before_geometry = canvas.client_geometry()

            # --- the edit under test -------------------------------------------------
            for name, value in AFTER.items():
                model.set(name, value)
            applied = asyncio.run(apply_edit(nested.instance, model, writer, list(AFTER)))

            assert applied.outcome is ApplyOutcome.OK, (
                f"apply did not confirm: {applied.outcome} {applied.mismatches}"
            )

            after_state = capture(nested, options=names)
            after_shot = canvas.screenshot(artifacts / "after.png")
            after_geometry = canvas.client_geometry()

        before_state.write(artifacts / "state-before.json")
        after_state.write(artifacts / "state-after.json")

    # --- state: exactly the options we edited moved ----------------------------------
    delta = diff(before_state, after_state)
    changed = {option.name for option in delta.options}
    assert set(AFTER) <= changed, (
        f"edited options did not change in the compositor: {set(AFTER) - changed}"
    )
    assert after_state.option("general:border_size") == AFTER["general:border_size"]
    assert after_state.option("decoration:rounding") == AFTER["decoration:rounding"]
    assert after_state.config_errors == ()

    # --- pixels: the compositor re-rendered ------------------------------------------
    comparison = compare(before_shot, after_shot, heatmap=artifacts / "diff.png")
    assert not comparison.identical, (
        "the screenshot did not change: the values were stored but never rendered"
    )
    assert comparison.pixels_differing_strongly > 0, (
        f"only imperceptible pixel noise changed ({comparison}); "
        "a border and gap change of this size must be plainly visible"
    )
    assert before_geometry != after_geometry, (
        "window geometry was unchanged despite a 20px gap increase"
    )


def test_the_state_sweep_notices_a_neighbour_that_should_not_have_moved(
    harness_home: Path, artifacts: Path
) -> None:
    """Guards the test above, whose `set(AFTER) <= changed` also passes if *everything* moved.

    Sweeping all 353 options is only worth its runtime if an unrelated option moving is
    something the tier would report, so the same run is asserted from the other side --
    the options that changed are the ones that were edited, and nothing else.
    """
    paths, model, writer = build(harness_home, BEFORE)
    writer.write(model)
    names = option_names(SCHEMA_VERSION)

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested.log"
    ) as nested:
        before_state = capture(nested, options=names)
        for name, value in AFTER.items():
            model.set(name, value)
        applied = asyncio.run(apply_edit(nested.instance, model, writer, list(AFTER)))
        assert applied.outcome is ApplyOutcome.OK
        after_state = capture(nested, options=names)

    changed = {option.name for option in diff(before_state, after_state).options}
    unexpected = changed - set(AFTER)
    assert not unexpected, f"options nobody edited changed as well: {sorted(unexpected)}"
