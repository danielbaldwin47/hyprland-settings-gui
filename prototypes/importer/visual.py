#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Visual equivalence check: same windows, same steps,
`.conf` vs generated `.lua`, screenshots compared pixel-by-pixel.

A headless 1920x1080 output is created inside the nested Hyprland so the canvas
does not depend on how large the host session tiled the nested window.
`debug:suppress_errors` and a pinned wallpaper are appended to the entry .conf
BEFORE conversion, so both sides get them and neither the error bar nor the
random default wallpaper can move pixels around.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(os.path.dirname(HERE))
CORPUS = os.path.join(REPO, "tests", "corpus")

from nested import Nested  # noqa: E402
from structural import EXEC_RE  # noqa: E402

RICES = ["hyprland-default", "end-4", "hyde", "jakoolit", "ml4w", "hyprv", "local"]
OUTPUT = "HEADLESS-1"
TOAST_WAIT = float(os.environ.get("TOAST_WAIT", "16"))

SUFFIX = """
# [prototype] deterministic visual-diff harness
debug {{
    suppress_errors = true
}}
misc {{
    force_default_wallpaper = 0
    disable_hyprland_logo = true
    disable_splash_rendering = true
}}
monitor = {out}, 1920x1080@60, auto, 1
"""

WINDOWS = [("probe.one", "0.85,0.20,0.20,1.0"),
           ("probe.two", "0.20,0.55,0.85,1.0"),
           ("probe.three", "0.25,0.75,0.35,0.55")]


def stage(rice, root, side="conf"):
    """One pristine HOME per side: Hyprland's first-launch state
    (~/.local/share/hyprland/lastVersion) is consumed by whichever engine runs
    first, and first launch changes behaviour (env, permission, donate screen)."""
    home = os.path.join(root, rice, f"home-{side}")
    if os.path.exists(home):
        shutil.rmtree(home)
    os.makedirs(os.path.join(home, ".config"), exist_ok=True)
    shutil.copytree(os.path.join(CORPUS, rice),
                    os.path.join(home, ".config", "hypr"), symlinks=True)
    extra = os.path.join(home, ".config", "hypr", "_home")
    if os.path.isdir(extra):
        shutil.copytree(extra, home, dirs_exist_ok=True, symlinks=True)
    for sub in (".local/share", ".local/state", ".cache"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    for dirpath, _d, files in os.walk(home):
        for f in files:
            if not f.endswith(".conf"):
                continue
            p = os.path.join(dirpath, f)
            try:
                lines = open(p, errors="replace").read().splitlines()
            except OSError:
                continue
            out, hit = [], False
            for ln in lines:
                if EXEC_RE.match(ln):
                    out.append("# [prototype: exec disabled] " + ln)
                    hit = True
                else:
                    out.append(ln)
            if hit:
                open(p, "w").write("\n".join(out) + "\n")
    entry = os.path.join(home, ".config", "hypr", "hyprland.conf")
    with open(entry, "a") as fh:
        fh.write(SUFFIX.format(out=OUTPUT))
    return home, entry


def env_for(home):
    env = dict(os.environ)
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env["XDG_DATA_HOME"] = os.path.join(home, ".local", "share")
    env["XDG_STATE_HOME"] = os.path.join(home, ".local", "state")
    env["XDG_CACHE_HOME"] = os.path.join(home, ".cache")
    return env


def shoot(config, home, tag, outdir):
    shots = {}
    with Nested(config, home=home, log=os.path.join(outdir, f"{tag}.log")) as n:
        n.ctl("output", "create", "headless", js=False)
        # Hyprland shows transient toasts at startup (including a legacy-only
        # ".conf will be removed in 0.57" one); wait them out before shooting.
        time.sleep(TOAST_WAIT)
        mons = [m["name"] for m in n.ctl("monitors")]
        target = OUTPUT if OUTPUT in mons else next(
            (m for m in mons if m.lower().startswith("headless")), None)
        if target is None:
            raise RuntimeError(f"no headless output; monitors={mons}")
        _sweep(n)                       # kill anything that is not ours (donate nag)
        n.dispatch("focusmonitor", target)
        time.sleep(0.6)
        ws = _ws_of(n, target)
        shots["empty"] = _grab(n, target, outdir, tag, "empty")

        procs = []
        for app_id, colour in WINDOWS:
            p = subprocess.Popen([sys.executable, os.path.join(HERE, "winspawn.py"),
                                  app_id, colour], env=n.env(),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append(p)
            time.sleep(2.2)
            _sweep(n)
            _corral(n, ws)
            n.dispatch("focusmonitor", target)
        time.sleep(2.5)
        _sweep(n)
        _corral(n, ws)
        time.sleep(1.5)
        shots["tiled"] = _grab(n, target, outdir, tag, "tiled")

        n.dispatch("togglefloating")
        time.sleep(0.5)
        n.dispatch("resizeactive", "exact 700 450")
        time.sleep(0.5)
        n.dispatch("centerwindow")
        time.sleep(2.5)
        shots["floating"] = _grab(n, target, outdir, tag, "floating")

        geom = [{k: c.get(k) for k in ("class", "at", "size", "floating", "workspace")}
                for c in n.ctl("clients")]
        with open(os.path.join(outdir, f"clients-{tag}.json"), "w") as fh:
            json.dump(geom, fh, indent=1)
        for p in procs:
            p.terminate()
    return shots, geom


def _sweep(n):
    """Close any window we did not spawn (e.g. Hyprland's donate screen), which
    would otherwise steal focus and shift every other window."""
    for c in (n.ctl("clients") or []):
        if not str(c.get("class", "")).startswith("probe."):
            n.dispatch("closewindow", f"address:{c['address']}")
            time.sleep(0.3)


def _ws_of(n, target):
    for m in n.ctl("monitors"):
        if m["name"] == target:
            return m["activeWorkspace"]["id"]
    return None


def _corral(n, ws):
    """Make sure every probe window really is on the headless output's workspace."""
    if ws is None:
        return
    for c in (n.ctl("clients") or []):
        if str(c.get("class", "")).startswith("probe.") and \
                c.get("workspace", {}).get("id") != ws:
            n.dispatch("movetoworkspacesilent", f"{ws},address:{c['address']}")
            time.sleep(0.3)


def _grab(n, target, outdir, tag, name):
    path = os.path.join(outdir, f"{tag}-{name}.png")
    ok, err = n.grim_output(target, path)
    if not ok:
        raise RuntimeError(f"grim failed: {err}")
    return path


def compare(a, b, diff_path):
    ia = np.asarray(Image.open(a).convert("RGB")).astype(np.int16)
    ib = np.asarray(Image.open(b).convert("RGB")).astype(np.int16)
    if ia.shape != ib.shape:
        return {"identical": False, "reason": "size mismatch",
                "shape_conf": list(ia.shape), "shape_lua": list(ib.shape)}
    d = np.abs(ia - ib)
    per_px = d.max(axis=2)
    differing = int((per_px > 0).sum())
    total = int(per_px.size)
    strong = int((per_px > 8).sum())
    if differing:
        heat = np.zeros(ia.shape[:2] + (3,), dtype=np.uint8)
        heat[..., 0] = np.clip(per_px * 8, 0, 255)
        Image.fromarray(heat).save(diff_path)
    return {
        "identical": differing == 0,
        # a delta of 1-2/255 on a handful of pixels is GPU blend rounding, not a
        # config difference; anything a human could see is >8 on many pixels
        "visually_identical": bool(d.max() <= 2),
        "pixels_total": total,
        "pixels_differing": differing,
        "pct_differing": round(100.0 * differing / total, 4),
        "pixels_differing_gt8": strong,
        "pct_differing_gt8": round(100.0 * strong / total, 4),
        "max_channel_delta": int(d.max()),
        "rmse": round(float(np.sqrt((d.astype(np.float64) ** 2).mean())), 4),
    }


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "visual")
    only = sys.argv[2:] or RICES
    os.makedirs(root, exist_ok=True)
    summary = {}
    for rice in only:
        outdir = os.path.join(root, rice)
        os.makedirs(outdir, exist_ok=True)
        home_conf, entry = stage(rice, root, "conf")
        home_lua, _ = stage(rice, root, "lua")
        env = env_for(home_conf)
        lua = os.path.join(outdir, "hyprland.lua")
        p = subprocess.run([sys.executable, os.path.join(HERE, "convert.py"), entry,
                            "-o", lua, "--json", os.path.join(outdir, "report.json"),
                            "--quiet"], capture_output=True, text=True, env=env,
                           timeout=300)
        if p.returncode != 0:
            summary[rice] = {"error": "convert failed"}
            print(rice, "CONVERT FAILED")
            continue
        try:
            sc, gc = shoot(entry, home_conf, "conf", outdir)
            sl, gl = shoot(lua, home_lua, "lua", outdir)
        except Exception as exc:
            summary[rice] = {"error": f"{type(exc).__name__}: {exc}"}
            print(rice, "RUN FAILED:", exc)
            continue
        res = {}
        for frame in sc:
            res[frame] = compare(sc[frame], sl[frame],
                                 os.path.join(outdir, f"diff-{frame}.png"))
        res["geometry_identical"] = (json.dumps(gc, sort_keys=True) ==
                                     json.dumps(gl, sort_keys=True))
        summary[rice] = res
        def mark(v):
            if v.get("identical"):
                return "EXACT"
            if v.get("visually_identical"):
                return "~ok"
            return str(v.get("pct_differing", v.get("reason")))
        line = " ".join(f"{k}:{mark(v)}" for k, v in res.items()
                        if isinstance(v, dict))
        print(f"{rice}: {line} geom={res['geometry_identical']}")
    with open(os.path.join(root, "visual-summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print("wrote", os.path.join(root, "visual-summary.json"))


if __name__ == "__main__":
    main()
