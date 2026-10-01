#!/usr/bin/env python3
"""Run the app against a nested Hyprland with a throwaway config, never the desktop session.

The developer running this is sitting in their own Hyprland, whose `~/.config/hypr` the app
would otherwise import, rewrite and reload. Here both halves are fenced off: the app and the
nested compositor share a sandbox `$HOME` (every `XDG_*` repointed inside it), and the app's
`HYPRLAND_INSTANCE_SIGNATURE` and `WAYLAND_DISPLAY` name the nested instance. This reuses the
Harness tier's `NestedHyprland`, so its isolation is the one `test_harness_nested.py` asserts.

    .venv/bin/python tools/sandbox.py                       # minimal config, run until closed
    .venv/bin/python tools/sandbox.py --config tests/corpus/end-4
    .venv/bin/python tools/sandbox.py --shot out.png        # screenshot after --wait, then exit
    .venv/bin/python tools/sandbox.py --home DIR            # keep state across runs

On Hyprland 0.56.x the nested compositor opens as a window on the host (`WAYLAND-1`); the
headless-only switch is gone. Click around in it as in any window; closing the app ends the
run.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "integration"))

from harness import NestedHyprland, make_home, unavailable_reason  # noqa: E402

#: Enough for Hyprland to start and for the app to find a config it did not write, which is
#: the first-run path every fresh user takes.
MINIMAL_CONFIG = """\
hl.config({
  general = { border_size = 2, gaps_in = 5 },
  decoration = { rounding = 8 },
})
"""


def prepare_home(home: Path, config: Path | None) -> Path:
    """The sandbox `$HOME`, with `config`'s files (or the minimal config) as its hypr dir."""
    make_home(home)
    hypr = home / ".config" / "hypr"
    if config is not None and not hypr.exists():
        shutil.copytree(config, hypr, symlinks=True)
    hypr.mkdir(parents=True, exist_ok=True)
    entrypoint = hypr / "hyprland.lua"
    if not entrypoint.exists():
        entrypoint.write_text(MINIMAL_CONFIG)
    return entrypoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--config", type=Path, help="a hypr config dir to copy in")
    parser.add_argument("--home", type=Path, help="sandbox $HOME to create or reuse")
    parser.add_argument("--shot", type=Path, help="screenshot the nested output, then exit")
    parser.add_argument("--wait", type=float, default=6.0, help="seconds before --shot")
    args = parser.parse_args()

    reason = unavailable_reason()
    if reason:
        print(f"sandbox unavailable: {reason}", file=sys.stderr)
        return 2

    home = (args.home or Path(tempfile.mkdtemp(prefix="hyprtweaker-sandbox-"))).resolve()
    entrypoint = prepare_home(home, args.config)

    with NestedHyprland(entrypoint, home=home, log=home / "nested.log") as nested:
        env = dict(nested.env)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        for name in ("HOME", "HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY"):
            print(f"{name}={env[name]}")
        log = (home / "app.log").open("w")
        app = subprocess.Popen(
            [sys.executable, "-m", "hyprtweaker"], env=env, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            if args.shot is None:
                return app.wait()
            time.sleep(args.wait)
            output = nested.hyprctl("monitors")[0]["name"]
            shot = args.shot.resolve()
            subprocess.run(["grim", "-o", output, str(shot)], env=env, check=True)
            print(f"shot={shot}")
            return 0
        finally:
            if app.poll() is None:
                app.terminate()
                app.wait(timeout=10)
            log.close()


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    sys.exit(main())
