"""Can Hyprland enter a submap whose block holds no live bind? (#208, from #111)

`engine/binds_analysis.empty_submaps` says a submap is inert unless one of its binds
renders as live Lua, and the Binds Page flags it and every bind that enters it. That is a
claim about the compositor, so it is asserted here against the compositor: a later Hyprland
that registers an empty `hl.define_submap` fails this file, and the flag then tells the user
something false.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest tests/integration/test_empty_submap_probe.py \\
        -m hyprland

Three submaps, one per shape the predicate separates: a block with nothing in it, a block
holding only a commented-out bind (what a disabled bind renders as), and a block with one
live bind as the control. Each is entered through `hl.dsp.submap` and the active submap is
read back with `hyprctl submap`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from harness import REQUIRE_VARIABLE, HarnessUnavailable, NestedHyprland, make_home

pytestmark = pytest.mark.hyprland

CONFIG = """
hl.define_submap("nothing", function() end)

hl.define_submap("disabled_only", function()
  -- hl.bind("right", hl.dsp.exec_cmd("true"))
end)

hl.define_submap("live", function()
  hl.bind("escape", hl.dsp.submap("reset"))
end)
"""


@pytest.fixture(scope="module")
def nested(tmp_path_factory: pytest.TempPathFactory) -> Iterator[NestedHyprland]:
    scratch = tmp_path_factory.mktemp("empty-submap")
    home = make_home(scratch / "home")
    config = home / "hyprland.lua"
    config.write_text(CONFIG)
    compositor = NestedHyprland(config, home=home, log=scratch / "nested.log")
    try:
        compositor.start()
    except HarnessUnavailable as unavailable:
        if os.environ.get(REQUIRE_VARIABLE) == "1":
            raise
        pytest.skip(f"Harness tier: {unavailable}")
    try:
        yield compositor
    finally:
        compositor.stop()


def enter(nested: NestedHyprland, name: str) -> tuple[str, str]:
    """The reply to `hl.dsp.submap(name)` and the submap active afterwards."""
    nested.dispatch('hl.dsp.submap("reset")')
    reply = nested.dispatch(f'hl.dsp.submap("{name}")')
    return reply, nested.hyprctl_text("submap").strip()


def test_a_submap_holding_a_live_bind_is_entered(nested: NestedHyprland) -> None:
    assert nested.config_errors() == (), nested.config_errors()
    reply, active = enter(nested, "live")
    assert (active, "doesn't exist" in reply) == ("live", False), reply


@pytest.mark.parametrize("name", ["nothing", "disabled_only"])
def test_a_submap_with_no_live_bind_is_refused(nested: NestedHyprland, name: str) -> None:
    reply, active = enter(nested, name)
    assert "submap doesn't exist" in reply, f"expected the refusal, got {reply!r}"
    assert active != name
