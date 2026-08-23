"""The Harness testing itself: does the nested compositor exist, obey, and stay contained?

Every other test in this tier trusts these properties, and two of them are safety rather than
correctness. The developer running this suite is almost certainly *sitting in* a Hyprland
session; a harness that leaked one `hyprctl` call into it would reload their compositor,
close their windows, or -- through a rice's autostart -- rewrite their dotfiles. So isolation
is asserted directly rather than assumed from the environment variables being set.

The last test here is the one that makes the rest of the tier mean anything: two captures of
an *unchanged* compositor must diff to nothing. Without that calibration a non-empty diff
elsewhere proves only that the harness is noisy.

    pytest tests/integration/test_harness_nested.py -m hyprland
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from harness import Canvas, NestedHyprland, capture, diff, option_names
from harness.visual import HEADLESS_OUTPUT, write_determinism_preamble

pytestmark = pytest.mark.hyprland

#: A whole config in five lines: enough to prove values land, small enough that a failure is
#: unambiguously the harness rather than the Writer.
MINIMAL_CONFIG = """\
hl.config({
  general = { border_size = 7, gaps_in = 3 },
  decoration = { rounding = 11 },
})
"""

#: A handful of options, so the self-test stays quick. The full 353-option sweep belongs to
#: the end-to-end test, where an unexpected neighbour changing is a real finding.
WATCHED = ("general:border_size", "general:gaps_in", "decoration:rounding")


def write_config(home: Path, body: str) -> Path:
    """A bare Lua config plus the determinism preamble, both loaded by Hyprland."""
    hypr = home / ".config" / "hypr"
    hypr.mkdir(parents=True, exist_ok=True)
    preamble = write_determinism_preamble(hypr / "user.lua")
    entrypoint = hypr / "hyprland.lua"
    entrypoint.write_text(f'{body}\nrequire("{preamble.stem}")\n')
    return entrypoint


def test_the_nested_compositor_is_not_the_one_we_are_sitting_in(
    harness_home: Path, artifacts: Path
) -> None:
    """The isolation guarantee, asserted rather than assumed.

    A nested compositor that inherited the host's signature would take every command this
    package sends -- and the first one a test sends is a reload.
    """
    entrypoint = write_config(harness_home, MINIMAL_CONFIG)
    host_signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")

    with NestedHyprland(entrypoint, home=harness_home, log=artifacts / "nested.log") as nested:
        assert nested.signature, "nested compositor never reported an instance signature"
        assert nested.signature != host_signature
        assert nested.env["WAYLAND_DISPLAY"] != os.environ.get("WAYLAND_DISPLAY")
        assert nested.instance.command_socket.is_socket()
        # The engine's own Instance must point at the nested sockets, since that is what
        # the end-to-end test drives the real Applier through.
        assert nested.signature in str(nested.instance.directory)


def test_values_written_in_lua_are_readable_back_out(
    harness_home: Path, artifacts: Path
) -> None:
    """The whole premise of the tier: what the file said is what the compositor holds."""
    entrypoint = write_config(harness_home, MINIMAL_CONFIG)

    with NestedHyprland(entrypoint, home=harness_home, log=artifacts / "nested.log") as nested:
        state = capture(nested, options=WATCHED)

    assert state.config_errors == (), f"nested config had errors: {state.config_errors}"
    assert state.option("general:border_size") == 7
    assert state.option("decoration:rounding") == 11
    # `gaps_in` is a css-gap shorthand: one number in the config, four out of `getoption`.
    assert state.option("general:gaps_in") == "3 3 3 3"


def test_a_broken_config_surfaces_as_config_errors_not_as_a_crash(
    harness_home: Path, artifacts: Path
) -> None:
    """A config error must stay a readable answer.

    `debug:suppress_errors` hides the on-screen banner so it cannot move pixels, and it
    would be easy to assume it also hides the errors from IPC. It does not -- which is what
    lets a screenshot comparison and an error assertion coexist in one run.
    """
    entrypoint = write_config(
        harness_home, 'hl.config({ general = { border_size = "not-a-number" } })'
    )

    with NestedHyprland(entrypoint, home=harness_home, log=artifacts / "nested.log") as nested:
        errors = nested.config_errors()

    assert errors, "a config with a type error reported no configerrors"


def test_the_headless_canvas_is_a_fixed_size_whatever_the_host_screen_is(
    harness_home: Path, artifacts: Path
) -> None:
    """Screenshots must not depend on the developer's monitor."""
    image_module = pytest.importorskip("PIL.Image", reason="screenshot checks need Pillow")
    entrypoint = write_config(harness_home, MINIMAL_CONFIG)

    with (
        NestedHyprland(entrypoint, home=harness_home, log=artifacts / "nested.log") as nested,
        Canvas(nested) as canvas,
    ):
        assert canvas.output.lower().startswith("headless")
        shot = canvas.screenshot(artifacts / "empty.png")

    with image_module.open(shot) as image:
        assert image.size == (1920, 1080)


def test_an_unchanged_compositor_diffs_to_nothing(harness_home: Path, artifacts: Path) -> None:
    """Calibration: the diff must be silent when nothing changed.

    Run over the whole 353-option schema and every state surface, because a field that
    drifts on its own -- a timestamp, a live workspace id -- would make every later
    comparison in this tier fail for reasons no config caused.
    """
    entrypoint = write_config(harness_home, MINIMAL_CONFIG)

    with NestedHyprland(entrypoint, home=harness_home, log=artifacts / "nested.log") as nested:
        names = option_names()
        before = capture(nested, options=names)
        after = capture(nested, options=names)

    delta = diff(before, after)
    assert delta.empty, f"an untouched compositor reported changes:\n{delta.describe()}"


def test_the_output_name_the_preamble_pins_is_the_one_we_shoot(
    harness_home: Path, artifacts: Path
) -> None:
    """The monitor rule and the created output have to agree, or the mode never applies."""
    entrypoint = write_config(harness_home, MINIMAL_CONFIG)

    with (
        NestedHyprland(entrypoint, home=harness_home, log=artifacts / "nested.log") as nested,
        Canvas(nested) as canvas,
    ):
        assert canvas.output == HEADLESS_OUTPUT
        monitors = {m["name"]: m for m in nested.hyprctl("monitors") or []}

    assert monitors[HEADLESS_OUTPUT]["width"] == 1920
    assert monitors[HEADLESS_OUTPUT]["height"] == 1080
