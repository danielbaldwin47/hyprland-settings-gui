"""The wallpaper, read and set through the daemon the user already runs (ADR-0014 §Wallpaper).

The app never configures or starts a wallpaper daemon (ADR-0006): it finds one that is
installed and running, and asks it. Supported are **awww** and **swww**, the same program
under its new and old names, with the same commands. Read from upstream at the tags named,
since neither is installed where this was written:

- awww v0.12.1 (codeberg.org/LGFae/awww, `common/src/ipc/socket.rs`, `client/src/cli.rs`):
  socket `$XDG_RUNTIME_DIR/<display>-awww-daemon[.<namespace>].sock`.
- swww v0.11.2 (github.com/LGFae/swww, archived; same files): socket
  `$XDG_RUNTIME_DIR/<display>-swww-daemon.<namespace>.sock`, so `..sock` by default.
- swww v0.9.5 (`utils/src/ipc.rs`): socket `$XDG_RUNTIME_DIR/swww-<display>.socket`.

`<display>` is the last component of `WAYLAND_DISPLAY` (default `wayland-0`). All three take
`img [--outputs a,b] <image>` and answer `query` with one line per output,
`[<namespace>: ]<output>: <w>x<h>, scale: <s>, currently displaying: image: <path>`.

**hyprpaper is detected and named, never driven.** hyprpaper 0.8 (v0.8.4,
`src/ipc/IPC.cpp`) speaks Hyprwire, a binary protocol, on
`$XDG_RUNTIME_DIR/hypr/<signature>/.hyprpaper.sock`; its text commands are reached through
`hyprctl`, which the engine never runs. So a hyprpaper user is told why the wallpaper does
not change rather than left with a control that does nothing.

Everything goes through `engine/tools.py`'s seam: `find` and `run` are injected, and default
to `find_tool` and `run_tool`. `query` and `img` run a process, so they are called off the
main loop; `detect` reads only the tool path and the runtime directory.
"""

from __future__ import annotations

import glob
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hyprtweaker.engine.tools import ToolRefused, ToolRun, ToolTimedOut, find_tool, run_tool

DAEMONS = ("awww", "swww")
"""Supported daemons, in the order one is preferred when several run."""

TIMEOUT = 15.0
"""Seconds a call may take: `img` decodes and scales the image in the client."""

_QUERY_LINE = re.compile(
    r"^(?:.*?: )?(?P<output>[^:\s][^:]*): \d+x\d+, scale: [^,]*, "
    r"currently displaying: image: (?P<image>.+)$"
)


class WallpaperError(Exception):
    """A daemon call that did not do what was asked; the message is a sentence for the user."""


@dataclass(frozen=True, slots=True)
class Shown:
    """The image one output shows."""

    output: str
    image: Path


@dataclass(frozen=True, slots=True)
class Daemon:
    """A running wallpaper daemon this app can drive, and the client program that drives it."""

    name: str
    binary: Path
    run: Callable[..., ToolRun] = field(repr=False, compare=False)

    def current(self) -> tuple[Shown, ...]:
        """What each output shows now; an output showing a plain colour is left out."""
        reply = self._call("query")
        shown = []
        for line in reply.stdout.splitlines():
            match = _QUERY_LINE.match(line.strip())
            # Only an absolute path is an image this app can name again: anything else could
            # be read as an option when it is handed back (finding 6 of the #153 review).
            if match is not None and Path(match["image"]).is_absolute():
                shown.append(Shown(match["output"], Path(match["image"])))
        return tuple(shown)

    def set(self, image: Path) -> None:
        """Show `image` on every output: one call."""
        self._call("img", _absolute(image))

    def show(self, shown: Sequence[Shown]) -> None:
        """Put each output back to its image: one call per distinct image."""
        outputs: dict[Path, list[str]] = {}
        for each in shown:
            outputs.setdefault(each.image, []).append(each.output)
        for image, names in outputs.items():
            self._call("img", "--outputs", ",".join(names), _absolute(image))

    def _call(self, *args: str) -> ToolRun:
        try:
            reply = self.run([str(self.binary), *args], timeout=TIMEOUT)
        except ToolTimedOut:
            raise WallpaperError(f"{self.name} did not answer in time.") from None
        except ToolRefused:
            raise WallpaperError(
                f"{self.name} could not be run: it is not where this app looks for it."
            ) from None
        except OSError as error:
            why = (error.strerror or str(error)).lower()
            raise WallpaperError(f"{self.name} could not be run ({why}).") from None
        if reply.returncode != 0:
            said = next((line for line in reply.stderr.splitlines() if line.strip()), "")
            raise WallpaperError(
                f"{self.name} said: {said.strip()}" if said else f"{self.name} failed."
            )
        return reply


def _absolute(image: Path) -> str:
    if not image.is_absolute():
        # A relative path is read against the daemon's working directory, not ours.
        raise ValueError(f"a wallpaper must be an absolute path, not {image}")
    return str(image)


@dataclass(frozen=True, slots=True)
class Wallpapers:
    """Where daemons are looked for: the tool seam and the environment, injectable."""

    find: Callable[[str], Path | None] = find_tool
    run: Callable[..., ToolRun] = run_tool
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def detect(self) -> Daemon | None:
        """The supported daemon installed and running for this display, or `None`.

        Reads the tool path and the runtime directory only: no process is started, so it
        is safe on the main loop. `absent_reason` says why it is `None`.
        """
        for name in DAEMONS:
            binary = self.find(name)
            if binary is not None and self._socket_of(name):
                return Daemon(name, binary, self.run)
        return None

    def absent_reason(self) -> str:
        """Why `detect` found nothing, as a sentence for the user."""
        if self.find("hyprpaper") is not None and self._hyprpaper_runs():
            return (
                "hyprpaper is running, and this app cannot change its wallpaper yet. "
                "awww and swww are supported."
            )
        for name in DAEMONS:
            if self.find(name) is not None:
                return (
                    f"{name} is installed, but its daemon is not running. "
                    f"Start {name}-daemon to change the wallpaper from this app."
                )
        return (
            "No wallpaper daemon is running. This app changes the wallpaper through "
            "awww or swww."
        )

    def _socket_of(self, name: str) -> bool:
        runtime = self.environ.get("XDG_RUNTIME_DIR")
        if not runtime or not os.path.isabs(runtime):
            return False
        display = Path(self.environ.get("WAYLAND_DISPLAY") or "wayland-0").name
        directory = Path(runtime)
        return (directory / f"{name}-{display}.socket").exists() or any(
            directory.glob(f"{glob.escape(display)}-{name}-daemon*.sock")
        )

    def _hyprpaper_runs(self) -> bool:
        runtime = self.environ.get("XDG_RUNTIME_DIR")
        signature = self.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not runtime or not signature:
            return False
        return (Path(runtime) / "hypr" / signature / ".hyprpaper.sock").exists()


def detect() -> Daemon | None:
    """The running wallpaper daemon, from the real tool path and environment."""
    return Wallpapers().detect()
