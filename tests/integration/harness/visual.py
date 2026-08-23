"""Screenshots of a nested compositor, and what it takes to make them comparable.

State tells you the compositor *stored* `rounding = 12`; only a screenshot tells you it
rendered a rounded corner. Prototype #9 §5 got this to byte-identical output across five of
seven rices, which is the standard this module keeps -- but only because determinism is
forced. Left alone, Hyprland moves pixels for reasons that have nothing to do with the
config, and each one had to be pinned:

- **the error banner** reserves screen space that scales with the error count, shifting every
  window below it (`debug:suppress_errors`);
- **the default wallpaper** is picked at random from three (`misc:force_default_wallpaper`);
- **the splash line** is random text (`misc:disable_splash_rendering`);
- **startup toasts** are transient and one of them is engine-specific, so they are waited out
  rather than suppressed;
- **the output itself**: screenshotting whatever the host session gave the nested window
  makes every result depend on the developer's monitor, so a headless 1920x1080 output is
  created *inside* the nested compositor and everything is framed against that.

The determinism settings are written as `user.lua`, which the Writer is forbidden to
overwrite (`ConfigPaths.protected`) and the Entrypoint requires last -- so they win over the
generated modules without the harness having to edit generated files.

**numpy and Pillow are optional.** They are needed only to compare images, are not runtime
dependencies of the app, and a machine without them can still run the whole state half of the
tier. `compare` raises `HarnessUnavailable`, which the conftest turns into a skip.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .nested import HarnessUnavailable, NestedHyprland

HEADLESS_OUTPUT = "HEADLESS-1"
HEADLESS_MODE = "1920x1080@60"

#: Run as a subprocess, never imported: it needs GTK, and this package must stay importable
#: on a machine that has none (the corpus fixture tests do exactly that).
PROBE_SCRIPT = Path(__file__).parent / "probe_window.py"

#: Hyprland shows transient toasts at startup. One of them -- "you are using the .conf config
#: format" -- appears only under hyprlang, so it would dominate every conf-vs-lua diff. They
#: expire on their own; waiting is more honest than suppressing something only one side shows.
TOAST_SETTLE_SECONDS = 16.0

#: Animations are left enabled and simply allowed to finish. Disabling them would make the
#: screenshots easier to compare and would stop testing the thing rices differ most in.
ANIMATION_SETTLE_SECONDS = 2.5

WINDOW_SETTLE_SECONDS = 2.2
DISPATCH_SETTLE_SECONDS = 0.5

#: One translucent probe, so the compositor's blur is exercised rather than just its borders.
PROBE_WINDOWS = (
    ("probe.one", "0.85,0.20,0.20,1.0"),
    ("probe.two", "0.20,0.55,0.85,1.0"),
    ("probe.three", "0.25,0.75,0.35,0.55"),
)

DETERMINISM_LUA = """\
-- Written by the integration Harness (ADR-0011 tier 3). Pins everything Hyprland would
-- otherwise vary between runs, so a screenshot diff shows config changes and nothing else.
hl.config({{
  debug = {{ suppress_errors = true }},
  misc = {{
    force_default_wallpaper = 0,
    disable_hyprland_logo = true,
    disable_splash_rendering = true,
  }},
}})

-- A fixed offscreen canvas: the nested compositor's own window is whatever size the host
-- session gave it, and framing against that would make every result machine-specific.
hl.monitor({{ output = "{output}", mode = "{mode}", position = "auto", scale = 1 }})
"""


def write_determinism_preamble(
    user_lua: Path, *, output: str = HEADLESS_OUTPUT, mode: str = HEADLESS_MODE
) -> Path:
    """Write the harness `user.lua`.

    Must be called *before* `Writer.write`: the Entrypoint only requires files that exist
    when it is rendered, so a `user.lua` created afterwards is never loaded and every
    determinism setting here would silently do nothing.
    """
    user_lua.parent.mkdir(parents=True, exist_ok=True)
    user_lua.write_text(DETERMINISM_LUA.format(output=output, mode=mode))
    return user_lua


@dataclass(frozen=True, slots=True)
class ImageComparison:
    """How two screenshots differ."""

    identical: bool
    visually_identical: bool
    pixels_total: int
    pixels_differing: int
    pixels_differing_strongly: int
    max_channel_delta: int
    rmse: float

    @property
    def percent_differing(self) -> float:
        return 100.0 * self.pixels_differing / self.pixels_total if self.pixels_total else 0.0

    def __str__(self) -> str:
        if self.identical:
            return "byte-identical"
        return (
            f"{self.pixels_differing} px differ ({self.percent_differing:.4f}%), "
            f"max delta {self.max_channel_delta}/255, rmse {self.rmse}"
        )


#: A delta of 1-2/255 on a handful of pixels is GPU blend rounding, not a config difference.
#: Prototype #9 §5 measured exactly this on two of seven rices; anything a human could see
#: moves many pixels by far more.
BLEND_ROUNDING_TOLERANCE = 2


def compare(before: Path, after: Path, *, heatmap: Path | None = None) -> ImageComparison:
    """Compare two screenshots pixel by pixel, optionally writing a difference heatmap."""
    try:
        import numpy
        from PIL import Image
    except ImportError as error:  # pragma: no cover - environment-dependent
        raise HarnessUnavailable(
            f"screenshot comparison needs numpy and Pillow ({error})"
        ) from error

    first = numpy.asarray(Image.open(before).convert("RGB")).astype(numpy.int16)
    second = numpy.asarray(Image.open(after).convert("RGB")).astype(numpy.int16)
    if first.shape != second.shape:
        raise AssertionError(
            f"screenshots differ in size: {first.shape} vs {second.shape} "
            f"({before.name} vs {after.name})"
        )

    delta = numpy.abs(first - second)
    per_pixel = delta.max(axis=2)
    differing = int((per_pixel > 0).sum())

    if differing and heatmap is not None:
        canvas = numpy.zeros((*first.shape[:2], 3), dtype=numpy.uint8)
        canvas[..., 0] = numpy.clip(per_pixel * 8, 0, 255)
        Image.fromarray(canvas).save(heatmap)

    return ImageComparison(
        identical=differing == 0,
        visually_identical=bool(delta.max() <= BLEND_ROUNDING_TOLERANCE),
        pixels_total=int(per_pixel.size),
        pixels_differing=differing,
        pixels_differing_strongly=int((per_pixel > 8).sum()),
        max_channel_delta=int(delta.max()),
        rmse=round(float(numpy.sqrt((delta.astype(numpy.float64) ** 2).mean())), 4),
    )


class Canvas:
    """The nested compositor's offscreen output, and the windows arranged on it.

    Holds the probe processes so they are terminated with the canvas rather than left to the
    600-second self-destruct in `probe_window.py`.
    """

    def __init__(self, nested: NestedHyprland, *, output: str = HEADLESS_OUTPUT) -> None:
        self.nested = nested
        self.output = output
        self.workspace: int | None = None
        self._probes: list[subprocess.Popen[bytes]] = []

    def __enter__(self) -> Canvas:
        self.prepare()
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False

    def prepare(self) -> str:
        """Create the headless output, wait out the toasts, and focus it."""
        self.nested.hyprctl_text("output", "create", "headless")
        time.sleep(TOAST_SETTLE_SECONDS)

        monitors = self.nested.hyprctl("monitors") or []
        names = [monitor.get("name") for monitor in monitors]
        if self.output not in names:
            fallback = next(
                (name for name in names if str(name).lower().startswith("headless")), None
            )
            if fallback is None:
                raise AssertionError(f"no headless output was created; monitors={names}")
            self.output = fallback

        self.sweep()
        self.focus_output()
        time.sleep(DISPATCH_SETTLE_SECONDS)
        self.workspace = self._workspace_of(self.output)
        return self.output

    def spawn_probes(self) -> None:
        """Open the probe windows one at a time, corralling each onto our output."""
        for app_id, colour in PROBE_WINDOWS:
            self._probes.append(
                subprocess.Popen(
                    [sys.executable, str(PROBE_SCRIPT), app_id, colour],
                    env=self.nested.env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            time.sleep(WINDOW_SETTLE_SECONDS)
            self.sweep()
            self.corral()
            self.focus_output()
        time.sleep(ANIMATION_SETTLE_SECONDS)
        self.sweep()
        self.corral()
        time.sleep(ANIMATION_SETTLE_SECONDS / 2)

    def focus_output(self) -> None:
        """`hl.dsp.focus` takes exactly one of direction/monitor/workspace/window."""
        self.nested.dispatch(f'hl.dsp.focus({{ monitor = "{self.output}" }})')

    def sweep(self) -> None:
        """Close any window we did not spawn.

        Hyprland's own donate screen is the usual visitor. It takes focus, which re-tiles
        everything else, so a screenshot taken with it open is not a picture of the config.
        """
        for client in self.nested.hyprctl("clients") or []:
            if not str(client.get("class", "")).startswith("probe."):
                address = client.get("address")
                self.nested.dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})')
                time.sleep(0.3)

    def corral(self) -> None:
        """Move stray probes onto the headless output's workspace."""
        if self.workspace is None:
            return
        for client in self.nested.hyprctl("clients") or []:
            if not str(client.get("class", "")).startswith("probe."):
                continue
            if client.get("workspace", {}).get("id") == self.workspace:
                continue
            address = client.get("address")
            # `follow = false` is the Lua spelling of `movetoworkspacesilent`: move the
            # window without dragging focus along, which would re-tile the output.
            self.nested.dispatch(
                f"hl.dsp.window.move({{ workspace = {self.workspace}, follow = false, "
                f'window = "address:{address}" }})'
            )
            time.sleep(0.3)

    def screenshot(self, path: Path) -> Path:
        """`grim` the headless output. The output name, never the whole session."""
        path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["grim", "-o", self.output, str(path)],
            capture_output=True,
            text=True,
            env=self.nested.env,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or not path.exists():
            raise AssertionError(f"grim failed for output {self.output}: {result.stderr}")
        return path

    def client_geometry(self) -> list[dict[str, object]]:
        """Position and size per probe window -- the numeric half of a visual comparison."""
        return [
            {key: client.get(key) for key in ("class", "at", "size", "floating")}
            for client in (self.nested.hyprctl("clients") or [])
            if str(client.get("class", "")).startswith("probe.")
        ]

    def _workspace_of(self, output: str) -> int | None:
        for monitor in self.nested.hyprctl("monitors") or []:
            if monitor.get("name") == output:
                return monitor.get("activeWorkspace", {}).get("id")
        return None

    def close(self) -> None:
        for probe in self._probes:
            probe.terminate()
        for probe in self._probes:
            try:
                probe.wait(timeout=5)
            except subprocess.TimeoutExpired:
                probe.kill()
        self._probes.clear()
