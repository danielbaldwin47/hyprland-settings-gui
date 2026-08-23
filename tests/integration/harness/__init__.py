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

**Scope.** What ships here is the rig plus the *apply* direction: a config this app wrote,
booted and verified. The *import* direction -- staging a rice's `.conf`, converting it, and
diffing the two engines against each other (prototype #9 §3) -- needs an importer to diff,
and belongs with the ticket that builds one. The pieces that direction will want are already
here and deliberately kept: `stage` disarms `.conf` and `.lua` alike so both sides run
inert, `option_value` reads the value out of a `getoption` reply under either engine's key
naming, and `ImageComparison.visually_identical` carries the GPU-blend tolerance that a
cross-engine screenshot diff needs and a same-engine one does not.

The tier is slow by construction (~45 s per config). It stays out of the per-commit run by
living outside `testpaths`, and the `hyprland` marker is what makes the compositor-bound half
skip cleanly on a machine that cannot host one::

    pytest tests/integration              # the whole tier; skips what this machine cannot run
    pytest tests/integration -m hyprland  # only the tests that need a compositor
"""

from __future__ import annotations

import sys
from pathlib import Path

# The one place `src` reaches sys.path. Importing any submodule runs this first (it is the
# package's own __init__), so the engine imports below and in every submodule resolve
# without each of them repeating the bootstrap.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from .corpus import Rice, StagedRice, rice, rices, rices_with_ground_truth, stage  # noqa: E402
from .nested import (  # noqa: E402
    HarnessUnavailable,
    NestedHyprland,
    NestedHyprlandError,
    home_environment,
    hyprland_binary,
    make_home,
    unavailable_reason,
)
from .state import (  # noqa: E402
    CompositorState,
    ListDelta,
    OptionDelta,
    StateDiff,
    capture,
    diff,
    option_names,
    option_value,
)
from .visual import (  # noqa: E402
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
