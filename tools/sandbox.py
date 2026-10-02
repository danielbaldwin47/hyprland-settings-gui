#!/usr/bin/env python3
"""Run the app against a nested Hyprland with a throwaway config, never the desktop session.

The developer running this is sitting in their own Hyprland, whose `~/.config/hypr` the app
would otherwise import, rewrite and reload. Here both halves are fenced off: the app and the
nested compositor share a sandbox `$HOME` (every `XDG_*` repointed inside it), and the app's
`HYPRLAND_INSTANCE_SIGNATURE` and `WAYLAND_DISPLAY` name the nested instance. The app's tool
search path (`HYPRTWEAKER_TOOL_PATH`) is `<home>/bin`, created empty, so it finds no theming
tool or wallpaper daemon of the owner's; a stub script written there shows a detected one.
Both the nested compositor and the app run with refusing stand-ins first on `PATH`
(`<home>/refused-bin`, rewritten on every start, reused `--home` included): a theming tool,
a wallpaper daemon, a bar or `systemctl` that a copied rice's autostart or a key binding
names logs itself to `<home>/refused.log` and exits instead of running the owner's real one.
This reuses the Harness tier's `NestedHyprland`, so its isolation is the one
`test_harness_nested.py` asserts.

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
`--config` has its top-level `hl.exec_cmd(...)` and `hl.env(...)` lines commented out, which
covers every rice in `tests/corpus` today; a config that spawns through `os.execute` or an
alias is not caught. In the default windowless mode the harness's `bwrap` also hides the host's
session bus and systemd user manager from the compositor, so what does run cannot reach them.
Bind actions (`hl.dsp.exec_cmd`) stay: they run only on a key press inside the nested
window. SIGTERM and SIGHUP end the run the same way closing the app does, so the nested
compositor is always stopped.

"""

from __future__ import annotations

import argparse
import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "integration"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from harness import NestedHyprland, make_home, unavailable_reason  # noqa: E402
from harness.nested import DRM_CARD_VARIABLE, drm_card_problem  # noqa: E402

from hermetic import FENCE_LOG_ENV, REFUSED_TOOLS, TOOL_PATH_ENV, install_refusals  # noqa: E402
from ui.private_display import start_bus  # noqa: E402

TOOL_DIR = "bin"
REFUSED_DIR = "refused-bin"
REFUSED_LOG = "refused.log"

#: What a copied rice's autostart starts that must not run from the owner's `PATH`: the
#: tests' refused names, plus the session programs a migrated `exec-once` brings along.
SANDBOX_REFUSED = (*REFUSED_TOOLS, "systemctl", "waybar", "hypridle", "hyprlock")

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
    """`HARNESS_DRM_CARD` if set, else the first PCI card this user may open that the harness
    accepts (not NVIDIA, no monitor connected, a render node, `bwrap` present)."""
    if os.environ.get(DRM_CARD_VARIABLE):
        return Path(os.environ[DRM_CARD_VARIABLE])
    for card in sorted(Path("/dev/dri").glob("card[0-9]*")):
        bus = (Path("/sys/class/drm") / card.name / "device" / "subsystem").resolve().name
        if bus != "pci":
            continue  # a virtual KMS device is nobody's spare GPU; name it explicitly if wanted
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
    (home / TOOL_DIR).mkdir(exist_ok=True)
    entrypoint = hypr / "hyprland.lua"
    if not entrypoint.exists():
        entrypoint.write_text(MINIMAL_CONFIG)
    return entrypoint


def fence_environment(home: Path, path: str) -> dict[str, str]:
    """`PATH` with the refusing stand-ins first, and the log they write to. Installed anew
    on every start, so a reused `--home` gets them as a fresh one does."""
    install_refusals(home / REFUSED_DIR, SANDBOX_REFUSED)
    return {
        "PATH": os.pathsep.join([str(home / REFUSED_DIR), path]),
        FENCE_LOG_ENV: str(home / REFUSED_LOG),
    }


def argument_problem(*, window: bool, home: Path | None) -> str | None:
    """Why these arguments are refused, or `None`.

    `--window` has no `bwrap` between the nested compositor and the session bus or user
    manager, and a reused home holds the `autostart.lua` the app wrote when it migrated a
    `.conf` rice, whose exec lines are not commented out: restarting on it would run them.
    """
    if window and home is not None and (home / ".config" / "hypr").exists():
        return (
            "--window with a reused --home is refused: the home's own autostart would run "
            "with the host's session bus in reach. Use a fresh --home, or drop --window."
        )
    return None


def app_environment(nested: Mapping[str, str], home: Path, bus: str) -> dict[str, str]:
    """What the app runs with: the nested compositor's environment, plus this checkout.

    `nested` already names the sandbox `HOME` and `XDG_*` homes and the nested instance.
    The tool search path is `<home>/bin`, empty unless someone put a stub there, so the
    app finds no theming tool or wallpaper daemon of the owner's (`engine/tools.py`, #233).

    GTK talks to the nested Wayland display and nothing of the desktop's (F13 of the #148
    review, hand-test 15): Wayland only and no `DISPLAY`, so a failed nested connect fails
    rather than falling back to the desktop's Xwayland; `bus`, a session bus of the
    sandbox's own, rather than the owner's; no accessibility bus, no settings portal and
    GSettings in memory; and no Vulkan probe of a render node the sandbox cannot open.
    """
    environment = {
        **nested,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "HYPRTWEAKER_NON_UNIQUE": "1",
        TOOL_PATH_ENV: str(home / TOOL_DIR),
        "GDK_BACKEND": "wayland",
        "GTK_A11Y": "none",
        "DBUS_SESSION_BUS_ADDRESS": bus,
        "ADW_DISABLE_PORTAL": "1",
        "GSETTINGS_BACKEND": "memory",
        "GDK_DISABLE": "vulkan",
    }
    for name in ("DISPLAY", "AT_SPI_BUS_ADDRESS"):
        environment.pop(name, None)
    return environment


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

    problem = argument_problem(window=args.window, home=args.home)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
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
    # Read by `home_environment`, so the compositor and the app both start with it.
    os.environ.update(fence_environment(home, os.environ.get("PATH", "")))
    print(f"refusals log to {home / REFUSED_LOG}")

    dbus_daemon = shutil.which("dbus-daemon")
    bus = start_bus(dbus_daemon) if dbus_daemon is not None else None
    if bus is None:
        print("sandbox unavailable: no private session bus (dbus-daemon)", file=sys.stderr)
        return 2
    atexit.register(bus.stop)

    with NestedHyprland(entrypoint, home=home, log=home / "nested.log") as nested:
        env = app_environment(nested.env, home, bus.address)
        for name in ("HOME", "HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", TOOL_PATH_ENV):
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
            ran = home / REFUSED_LOG
            if ran.exists():
                print(f"refused: {ran.read_text().strip()}", file=sys.stderr)


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    sys.exit(main())
