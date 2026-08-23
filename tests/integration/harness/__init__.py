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

**Scope.** The rig, plus both directions. The *apply* direction -- a config this app wrote,
booted and verified -- shipped with the harness. The *import* direction (prototype #9 §3)
arrived with the mapping half of the Importer in #61 as `test_import_matches_port.py`:
a rice's `.conf` tree staged, converted, booted, and diffed against the hand-written
`hyprland.lua` its own author ships. The pieces that direction needed were kept here from
the start and all three earned their keep: `stage` disarms `.conf` and `.lua` alike so both
sides run inert, `option_value` reads the value out of a `getoption` reply under either
engine's key naming, and `ImageComparison.visually_identical` carries the GPU-blend
tolerance that a cross-engine screenshot diff needs and a same-engine one does not.

The tier is slow by construction (~45 s per config). It stays out of the per-commit run by
living outside `testpaths`, and the `hyprland` marker is what makes the compositor-bound half
skip cleanly on a machine that cannot host one::

    pytest tests/integration              # the whole tier; skips what this machine cannot run
    pytest tests/integration -m hyprland  # only the tests that need a compositor

**Run it by hand before merging a change to the Importer or the Writer.** CI cannot: a
GitHub runner has no seat, so the job could only ever skip and report green, which ADR-0011
§tier-3 rates worse than no job at all. That makes `test_import_matches_port.py` the only
end-to-end proof that a real rice still converts to a config Hyprland accepts, and nothing
automatic will notice when it stops being true. Nightly is blocked on the virtual-seat spike
the ADR names (`seatd` + `vkms`, or nesting inside a headless sway/cage).
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
