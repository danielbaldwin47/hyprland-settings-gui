#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Convert every rice in tests/corpus and verify the output.

Each rice is staged under a synthetic $HOME so that its `source = ~/.config/hypr/...`
and `$XDG_*` lines resolve inside the corpus (see tests/corpus/<rice>/ROOT).
Then `Hyprland --verify-config` is run against BOTH the original .conf tree and the
generated .lua, so "does 0.56.2 accept it" is answered for each side.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CORPUS = os.path.join(REPO, "tests", "corpus")
RICES = ["hyprland-default", "end-4", "hyde", "jakoolit", "ml4w", "hyprv", "local"]


def stage(rice, root):
    """Build a synthetic HOME whose ~/.config/hypr is this rice."""
    home = os.path.join(root, rice, "home")
    if os.path.exists(home):
        shutil.rmtree(home)
    os.makedirs(os.path.join(home, ".config"), exist_ok=True)
    src = os.path.join(CORPUS, rice)
    os.symlink(src, os.path.join(home, ".config", "hypr"))
    extra = os.path.join(src, "_home")
    if os.path.isdir(extra):
        shutil.copytree(extra, home, dirs_exist_ok=True, symlinks=True)
    for sub in (".local/share", ".local/state", ".cache"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    return home


def verify(path, env):
    p = subprocess.run(["Hyprland", "--verify-config", "-c", path],
                       capture_output=True, text=True, env=env, timeout=120)
    out = p.stdout
    marker = "======== Config parsing result:"
    body = out.split(marker, 1)[1].strip() if marker in out else out.strip()
    ok = body == "config ok"
    errs = [] if ok else [l for l in body.splitlines() if l.strip()]
    return ok, errs


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
    os.makedirs(root, exist_ok=True)
    sys.path.insert(0, HERE)
    summary = {}
    for rice in RICES:
        home = stage(rice, root)
        env = dict(os.environ)
        env["HOME"] = home
        env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
        env["XDG_DATA_HOME"] = os.path.join(home, ".local", "share")
        env["XDG_STATE_HOME"] = os.path.join(home, ".local", "state")
        env["XDG_CACHE_HOME"] = os.path.join(home, ".cache")
        entry = os.path.join(home, ".config", "hypr", "hyprland.conf")

        # convert in a subprocess so the synthetic $HOME applies to expanduser too
        outdir = os.path.join(root, rice)
        os.makedirs(outdir, exist_ok=True)
        lua = os.path.join(outdir, "hyprland.lua")
        rep = os.path.join(outdir, "report.json")
        p = subprocess.run([sys.executable, os.path.join(HERE, "convert.py"), entry,
                            "-o", lua, "--json", rep, "--quiet"],
                           capture_output=True, text=True, env=env, timeout=300)
        if p.returncode != 0:
            summary[rice] = {"convert_failed": p.stderr[-2000:]}
            print(f"{rice}: CONVERT FAILED\n{p.stderr[-2000:]}")
            continue
        report = json.load(open(rep))
        conf_ok, conf_errs = verify(entry, env)
        lua_ok, lua_errs = verify(lua, env)
        summary[rice] = {
            "files": len(report["files"]),
            "counts": report["counts"],
            "codes": report["codes"],
            "vars": report["vars"],
            "rule_order": report["rule_order"],
            "conf_verify_ok": conf_ok,
            "conf_errors": conf_errs,
            "lua_verify_ok": lua_ok,
            "lua_errors": lua_errs,
            "lua": lua,
            "entry": entry,
        }
        print(f"{rice}: files={len(report['files'])} conf_ok={conf_ok}"
              f"({len(conf_errs)} err) lua_ok={lua_ok}({len(lua_errs)} err)")
    with open(os.path.join(root, "corpus-summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print("wrote", os.path.join(root, "corpus-summary.json"))


if __name__ == "__main__":
    main()
