#!/usr/bin/env python3
# PROTOTYPE (wayfinder #30) — throwaway. Batch: import p9-generated Lua,
# emit, re-import, fixpoint-compare each.
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
P9 = HERE / "../importer/results/lua"
RT = HERE / "results/rt"

RICES = ["end-4", "hyde", "jakoolit", "ml4w", "hyprv", "local", "hyprland-default"]

CORPUS = HERE / "../../tests/corpus"
FIX = HERE / "fixtures"
PORTS = [
    ("port-end-4", CORPUS / "end-4/hyprland.lua", CORPUS / "end-4", None, "block"),
    ("port-ml4w", CORPUS / "ml4w/hyprland.lua", CORPUS / "ml4w", None, "block"),
    ("port-hyde", FIX / "hyde/Configs/.local/share/hypr/hyde.lua",
     FIX / "hyde/Configs/.local/share/hypr", FIX / "hyde/Configs", "record"),
]


def roundtrip(name, entry, basedir, home, policy):
    entry, basedir = pathlib.Path(entry).resolve(), pathlib.Path(basedir).resolve()
    model = HERE / "results" / f"{name}.json"
    cmd = [sys.executable, HERE / "import_lua.py", entry, basedir,
           "--name", name, "--policy", policy]
    if home:
        cmd += ["--home", str(pathlib.Path(home).resolve())]
    r = subprocess.run(cmd, capture_output=True, text=True)
    line = [l for l in r.stdout.splitlines() if l.startswith(("calls:", "errors:"))]
    errs = [l for l in r.stdout.splitlines() if "ERR" in l]
    print(f"[{name}] " + " | ".join(line) + (" " + "; ".join(errs) if errs else ""))
    if r.returncode:
        print(r.stderr[-300:])
        return
    gen = RT / f"{name}-gen.lua"
    e = subprocess.run([sys.executable, HERE / "emit_model.py", "emit", model,
                        basedir, gen], capture_output=True, text=True)
    print("   " + e.stdout.strip().splitlines()[0])
    for note in e.stdout.splitlines():
        if "note:" in note:
            print("  " + note)
    subprocess.run([sys.executable, HERE / "import_lua.py", gen, RT,
                    "--name", f"{name}-rt"], capture_output=True, text=True)
    cmp = subprocess.run([sys.executable, HERE / "emit_model.py", "compare",
                          model, basedir,
                          HERE / "results" / f"{name}-rt.json", RT],
                         capture_output=True, text=True)
    print("   " + cmp.stdout.strip().splitlines()[0])
    # verify-config EXECUTES hl.exec_cmd; strip the live-session handles so
    # config-time spawns (hyprctl seterror/notify) can't reach the compositor
    venv = {k: v for k, v in __import__("os").environ.items()
            if k not in ("HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR")}
    vr = subprocess.run(["Hyprland", "--verify-config", "--config", str(gen)],
                        capture_output=True, text=True, env=venv)
    ok = "config ok" in (vr.stdout + vr.stderr)
    print("   engine verify: " + ("config ok" if ok else "FAILED"))
    if not ok:
        tail = [l for l in (vr.stdout + vr.stderr).splitlines() if l.strip()][-3:]
        print("     " + " / ".join(tail))


for name, entry, basedir, home, policy in PORTS:
    roundtrip(name, entry, basedir, home, policy)

for rice in RICES:
    src = (P9 / f"{rice}.lua").resolve()
    roundtrip(f"p9-{rice}", src, src.parent, None, "block")
