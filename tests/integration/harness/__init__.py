"""The nested-headless-Hyprland Harness: ADR-0011's third test tier.

Prototype #9 proved a config can be verified to the pixel by loading it in a nested Hyprland
and diffing compositor state and screenshots, and recommended the rig become the shipping
test harness. This package is that promotion.

Four pieces, each usable on its own:

- `nested.py` -- `NestedHyprland`, a compositor of our own that cannot reach the host
  session, and `unavailable_reason`, the single answer to "can this machine run the tier";
- `state.py` -- read every option and state surface back out, and diff two captures;
- `visual.py` -- a fixed offscreen canvas, probe windows, screenshots, pixel comparison;
- `corpus.py` -- the pinned rice corpus staged into throwaway homes, with upstream's own Lua
  ports as ground truth where they exist.

The tier is slow by construction (~45 s per config). It stays out of the per-commit run by
living outside `testpaths`, and the `hyprland` marker is what makes the compositor-bound half
skip cleanly on a machine that cannot host one::

    pytest tests/integration              # the whole tier; skips what this machine cannot run
    pytest tests/integration -m hyprland  # only the tests that need a compositor
"""

from __future__ import annotations

from .corpus import Rice, StagedRice, rice, rices, rices_with_ground_truth, stage
from .nested import (
    HarnessUnavailable,
    NestedHyprland,
    NestedHyprlandError,
    home_environment,
    hyprland_binary,
    make_home,
    unavailable_reason,
)
from .state import (
    CompositorState,
    ListDelta,
    OptionDelta,
    StateDiff,
    capture,
    diff,
    option_names,
    option_value,
)
from .visual import (
    HEADLESS_OUTPUT,
    Canvas,
    ImageComparison,
    compare,
    write_determinism_preamble,
)

__all__ = [
    "HEADLESS_OUTPUT",
    "Canvas",
    "CompositorState",
    "HarnessUnavailable",
    "ImageComparison",
    "ListDelta",
    "NestedHyprland",
    "NestedHyprlandError",
    "OptionDelta",
    "Rice",
    "StagedRice",
    "StateDiff",
    "capture",
    "compare",
    "diff",
    "home_environment",
    "hyprland_binary",
    "make_home",
    "option_names",
    "option_value",
    "rice",
    "rices",
    "rices_with_ground_truth",
    "stage",
    "unavailable_reason",
    "write_determinism_preamble",
]
