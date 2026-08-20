#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Load each rice's .conf and our generated .lua in a
nested Hyprland and diff the resulting compositor state.

Both sides run from the same staged tree with `exec*` lines commented out, so no
autostart command from a rice can touch the host session, and both engines see
exactly the same input. A fixed monitor line is appended to the entry .conf
*before* conversion, so both sides get the same output geometry.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(os.path.dirname(HERE))
CORPUS = os.path.join(REPO, "tests", "corpus")

from nested import Nested  # noqa: E402

RICES = ["hyprland-default", "end-4", "hyde", "jakoolit", "ml4w", "hyprv", "local"]
MONITOR_LINE = "monitor = WAYLAND-1, 1920x1080@60, 0x0, 1"
EXEC_RE = re.compile(r"^(\s*)(exec|execr|exec-once|execr-once|exec-shutdown)\s*=",
                     re.IGNORECASE)

# `hyprctl binds` cannot report a Lua bind's action (dispatcher is always "__lua"
# and arg is a registry index), so those two fields are compared separately.
BIND_KEYS = ["locked", "mouse", "release", "repeat", "longPress", "non_consuming",
             "auto_consuming", "has_description", "modmask", "submap",
             "submap_universal", "key", "keycode", "catch_all", "description",
             "allow_input_capture"]


def option_names():
    descs = json.load(open(os.path.join(HERE, "descriptions.json")))
    return [d["name"] for d in descs]


def stage(rice, root, disable_exec=True):
    home = os.path.join(root, rice, "home")
    if os.path.exists(home):
        shutil.rmtree(home)
    os.makedirs(os.path.join(home, ".config"), exist_ok=True)
    shutil.copytree(os.path.join(CORPUS, rice), os.path.join(home, ".config", "hypr"),
                    symlinks=True)
    extra = os.path.join(home, ".config", "hypr", "_home")
    if os.path.isdir(extra):
        shutil.copytree(extra, home, dirs_exist_ok=True, symlinks=True)
    for sub in (".local/share", ".local/state", ".cache"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    if disable_exec:
        for dirpath, _dirs, files in os.walk(home):
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
        fh.write(f"\n# [prototype] fixed nested output\n{MONITOR_LINE}\n")
    return home, entry


def env_for(home):
    env = dict(os.environ)
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env["XDG_DATA_HOME"] = os.path.join(home, ".local", "share")
    env["XDG_STATE_HOME"] = os.path.join(home, ".local", "state")
    env["XDG_CACHE_HOME"] = os.path.join(home, ".cache")
    return env


def opt_value(rec):
    """getoption returns the value under a type-dependent key; take whichever."""
    if not isinstance(rec, dict):
        return rec
    for k, v in rec.items():
        if k in ("option", "set"):
            continue
        if isinstance(v, str):
            return " ".join(v.split())
        return v
    return None


def diff_options(a, b):
    out = []
    for name in sorted(set(a) | set(b)):
        va, vb = opt_value(a.get(name)), opt_value(b.get(name))
        if isinstance(va, float) and isinstance(vb, float):
            if abs(va - vb) < 1e-6:
                continue
        if va != vb:
            out.append({"option": name, "conf": va, "lua": vb})
    return out


def canon_bind(b):
    return tuple((k, b.get(k)) for k in BIND_KEYS)


def diff_binds(a, b):
    ca = [canon_bind(x) for x in a]
    cb = [canon_bind(x) for x in b]
    only_conf = [dict(x) for x in ca if x not in cb]
    only_lua = [dict(x) for x in cb if x not in ca]
    return {"conf_total": len(ca), "lua_total": len(cb),
            "only_conf": only_conf, "only_lua": only_lua,
            "order_identical": ca == cb}


def norm(x):
    return json.loads(json.dumps(x, sort_keys=True))


def diff_list(a, b, keys=None):
    def pick(d):
        if keys and isinstance(d, dict):
            return {k: d.get(k) for k in keys}
        return d
    la = [norm(pick(x)) for x in (a or [])]
    lb = [norm(pick(x)) for x in (b or [])]
    return {"conf_total": len(la), "lua_total": len(lb),
            "only_conf": [x for x in la if x not in lb],
            "only_lua": [x for x in lb if x not in la]}


def split_anim(a):
    """`hyprctl -j animations` returns [[animation nodes], [beziers]]."""
    if isinstance(a, list) and len(a) == 2 and all(isinstance(x, list) for x in a):
        return a[0], a[1]
    return (a or []), []


MONITOR_KEYS = ["name", "width", "height", "refreshRate", "x", "y", "scale",
                "transform", "vrr", "disabled", "currentFormat", "mirrorOf",
                "activeWorkspace", "reserved"]
ANIM_KEYS = ["name", "overridden", "bezier", "enabled", "speed", "style"]


def run_side(config, home, tag, outdir, options):
    log = os.path.join(outdir, f"{tag}.log")
    with Nested(config, home=home, log=log) as n:
        state = n.dump(option_names=options)
        state["_version"] = n.ctl("version")
    with open(os.path.join(outdir, f"state-{tag}.json"), "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    return state


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "struct")
    only = sys.argv[2:] or RICES
    os.makedirs(root, exist_ok=True)
    options = option_names()
    summary = {}
    for rice in only:
        outdir = os.path.join(root, rice)
        os.makedirs(outdir, exist_ok=True)
        home, entry = stage(rice, root)
        env = env_for(home)
        lua = os.path.join(outdir, "hyprland.lua")
        rep = os.path.join(outdir, "report.json")
        p = subprocess.run([sys.executable, os.path.join(HERE, "convert.py"), entry,
                            "-o", lua, "--json", rep, "--quiet"],
                           capture_output=True, text=True, env=env, timeout=300)
        if p.returncode != 0:
            summary[rice] = {"error": "convert failed", "stderr": p.stderr[-1500:]}
            print(rice, "CONVERT FAILED")
            continue
        try:
            sconf = run_side(entry, home, "conf", outdir, options)
            slua = run_side(lua, home, "lua", outdir, options)
        except Exception as exc:
            summary[rice] = {"error": f"{type(exc).__name__}: {exc}"}
            print(rice, "RUN FAILED:", exc)
            continue
        d = {
            "options": diff_options(sconf.get("options", {}), slua.get("options", {})),
            "binds": diff_binds(sconf.get("binds", []), slua.get("binds", [])),
            "monitors": diff_list(sconf.get("monitors"), slua.get("monitors"),
                                  MONITOR_KEYS),
            "animations": diff_list(split_anim(sconf.get("animations"))[0],
                                    split_anim(slua.get("animations"))[0], ANIM_KEYS),
            "beziers": diff_list(split_anim(sconf.get("animations"))[1],
                                 split_anim(slua.get("animations"))[1]),
            "workspacerules": diff_list(sconf.get("workspacerules"),
                                        slua.get("workspacerules")),
            "devices": diff_list(sconf.get("devices"), slua.get("devices")),
            "layers": diff_list(sconf.get("layers"), slua.get("layers")),
            "configerrors": {"conf": sconf.get("configerrors"),
                             "lua": slua.get("configerrors")},
        }
        report = json.load(open(rep))
        summary[rice] = {
            "option_diffs": len(d["options"]),
            "bind_conf": d["binds"]["conf_total"], "bind_lua": d["binds"]["lua_total"],
            "bind_only_conf": len(d["binds"]["only_conf"]),
            "bind_only_lua": len(d["binds"]["only_lua"]),
            "bind_order_identical": d["binds"]["order_identical"],
            "anim_diffs": len(d["animations"]["only_conf"]) +
                          len(d["animations"]["only_lua"]),
            "bezier_diffs": len(d["beziers"]["only_conf"]) +
                            len(d["beziers"]["only_lua"]),
            "monitor_reserved_only": all(
                set(x) - {"reserved"} == set() or
                all(x.get(k) == y.get(k) for k in x if k != "reserved")
                for x, y in zip(d["monitors"]["only_conf"], d["monitors"]["only_lua"])
            ) if d["monitors"]["only_conf"] and d["monitors"]["only_lua"] else None,
            "monitor_diffs": len(d["monitors"]["only_conf"]) +
                             len(d["monitors"]["only_lua"]),
            "wsrule_diffs": len(d["workspacerules"]["only_conf"]) +
                            len(d["workspacerules"]["only_lua"]),
            "device_diffs": len(d["devices"]["only_conf"]) +
                            len(d["devices"]["only_lua"]),
            "codes": report["codes"],
        }
        with open(os.path.join(outdir, "diff.json"), "w") as fh:
            json.dump(d, fh, indent=1)
        print(f"{rice}: opts±{summary[rice]['option_diffs']} "
              f"binds {d['binds']['conf_total']}->{d['binds']['lua_total']} "
              f"(-{summary[rice]['bind_only_conf']}/+{summary[rice]['bind_only_lua']}) "
              f"anim±{summary[rice]['anim_diffs']} mon±{summary[rice]['monitor_diffs']}")
    with open(os.path.join(root, "structural-summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print("wrote", os.path.join(root, "structural-summary.json"))


if __name__ == "__main__":
    main()
