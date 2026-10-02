#!/usr/bin/env python3
"""Run the app against a nested Hyprland with a throwaway config, never the desktop session.

The developer running this is sitting in their own Hyprland, whose `~/.config/hypr` the app
would otherwise import, rewrite and reload. Here both halves are fenced off: the app and the
nested compositor share a sandbox `$HOME` (every `XDG_*` repointed inside it), and the app's
`HYPRLAND_INSTANCE_SIGNATURE` and `WAYLAND_DISPLAY` name the nested instance. This reuses the
Harness tier's `NestedHyprland`, so its isolation is the one `test_harness_nested.py` asserts.

    .venv/bin/python tools/sandbox.py --shot out.png        # screenshot after --wait, then exit
    .venv/bin/python tools/sandbox.py --config tests/corpus/end-4 --shot out.png
    .venv/bin/python tools/sandbox.py --home DIR            # keep state across runs
    .venv/bin/python tools/sandbox.py --window              # a host window to click around in

By default the run is **windowless**: the nested Hyprland gets a spare monitor-less card
(`HARNESS_DRM_CARD`, or the first one the harness accepts; on the owner's machine the Intel
iGPU) and no host display, so nothing maps on the desktop. Without `--shot` it runs until
SIGTERM, for `hyprctl` probes with the printed signature. `--window` is the owner's
interactive mode: the nested compositor opens as a host window, on whatever workspace is
focused.

Two more fences, because both processes still share the host's session bus and
`XDG_RUNTIME_DIR`. The app runs non-unique (`HYPRTWEAKER_NON_UNIQUE=1`), so two sandboxes, or a
sandbox beside an installed copy, never hand their launch to each other. And a copied
`--config` has its top-level `hl.exec_cmd(...)` and `hl.env(...)` lines commented out, so a
rice's autostart (keyrings, idle daemons, `dbus-update-activation-environment`, `systemctl
--user`) never runs against the owner's session. Bind actions (`hl.dsp.exec_cmd`) stay: they
run only on a key press inside the nested window. SIGTERM and SIGHUP end the run the same way
closing the app does, so the nested compositor is always stopped.

"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "integration"))

from harness import NestedHyprland, make_home, unavailable_reason  # noqa: E402
from harness.nested import DRM_CARD_VARIABLE, drm_card_problem  # noqa: E402

#: Enough for Hyprland to start and for the app to find a config it did not write, which is
#: the first-run path every fresh user takes.
MINIMAL_CONFIG = """\
hl.config({
  general = { border_size = 2, gaps_in = 5 },
  decoration = { rounding = 8 },
})
"""

#: A top-level autostart or environment call in a Lua config. Every one in `tests/corpus` is a
#: single line, so commenting the line out cannot leave a half-call behind.
AUTOSTART = re.compile(r"^(\s*)(hl\.exec_cmd\(|hl\.env\()", re.MULTILINE)


def neuter_autostart(hypr: Path) -> int:
    """Comment out `hl.exec_cmd(` / `hl.env(` lines under `hypr`; return how many."""
    count = 0
    for lua in hypr.rglob("*.lua"):
        if lua.is_symlink() or not lua.is_file():
            continue
        text = lua.read_text(errors="replace")
        new, n = AUTOSTART.subn(r"\1-- sandbox: \2", text)
        if n:
            lua.write_text(new)
            count += n
    return count


def windowless_card() -> Path | None:
    """`HARNESS_DRM_CARD` if set, else the first card this user may open that the harness
    accepts (not NVIDIA, no monitor connected, a render node, `bwrap` present)."""
    if os.environ.get(DRM_CARD_VARIABLE):
        return Path(os.environ[DRM_CARD_VARIABLE])
    for card in sorted(Path("/dev/dri").glob("card[0-9]*")):
        if os.access(card, os.R_OK | os.W_OK) and drm_card_problem(card.resolve()) is None:
            return card
    return None


def prepare_home(home: Path, config: Path | None) -> Path:
    """The sandbox `$HOME`, with `config`'s files (or the minimal config) as its hypr dir."""
    make_home(home)
    hypr = home / ".config" / "hypr"
    if config is not None:
        if hypr.exists():
            print(f"--config ignored: {hypr} already exists (reused --home)", file=sys.stderr)
        else:
            shutil.copytree(config, hypr, symlinks=True)
            print(f"autostart lines commented out: {neuter_autostart(hypr)}")
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
    parser.add_argument(
        "--window", action="store_true", help="show the nested Hyprland as a host window"
    )
    args = parser.parse_args()

    if args.window:
        os.environ.pop(DRM_CARD_VARIABLE, None)
    else:
        card = windowless_card()
        if card is None:
            print(
                "sandbox unavailable windowless: no card the harness accepts "
                f"(set {DRM_CARD_VARIABLE}, see docs/agents/local-checks.md); "
                "--window shows it as a host window instead",
                file=sys.stderr,
            )
            return 2
        os.environ[DRM_CARD_VARIABLE] = str(card)
        print(f"windowless on {card}")

    def stop(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)

    reason = unavailable_reason()
    if reason:
        print(f"sandbox unavailable: {reason}", file=sys.stderr)
        return 2

    home = (args.home or Path(tempfile.mkdtemp(prefix="hyprtweaker-sandbox-"))).resolve()
    entrypoint = prepare_home(home, args.config)

    with NestedHyprland(entrypoint, home=home, log=home / "nested.log") as nested:
        env = dict(nested.env)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        env["HYPRTWEAKER_NON_UNIQUE"] = "1"
        for name in ("HOME", "HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY"):  # nested values
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
                try:
                    app.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    app.kill()
                    app.wait()
            log.close()


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    sys.exit(main())
