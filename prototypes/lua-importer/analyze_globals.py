#!/usr/bin/env python3
# PROTOTYPE (wayfinder #30) — throwaway. For each captured script (legacy
# extract), list the globals its bytecode reads (GETTABUP _ENV) that are not
# hl/stdlib — each is a latent hole the extraction must carry or flag.
import json
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from emit_model import Emitter  # noqa: E402

SAFE = {
    "hl", "string", "table", "math", "os", "io", "utf8", "coroutine", "require",
    "pairs", "ipairs", "next", "type", "tostring", "tonumber", "select", "print",
    "error", "assert", "pcall", "xpcall", "setmetatable", "getmetatable",
    "rawget", "rawset", "rawequal", "rawlen", "load", "package", "arg", "_G",
}


def globals_of(expr: str) -> set[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f:
        f.write("return " + expr)
        path = f.name
    r = subprocess.run(["luac5.5", "-l", "-p", path], capture_output=True, text=True)
    pathlib.Path(path).unlink()
    names = set()
    for m in re.finditer(r'GETTABUP\s+\d+\s+\d+\s+\d+k?\s*;\s*_ENV\s+"([^"]+)"', r.stdout):
        names.add(m.group(1))
    return names


def main():
    model_path, basedir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    model = json.loads(model_path.read_text())
    em = Emitter(model, basedir)
    flagged = 0
    for s in model.get("scripts", []):
        expr = em.extract_function_expr(s)
        upnames = {u["name"] for u in s.get("upvalues", [])}
        ext = globals_of(expr) - SAFE - upnames
        if ext:
            flagged += 1
            print(f"{s['source']}:{s['from']}-{s['to']} ctx={s.get('context')}: "
                  f"reads globals {sorted(ext)}")
    print(f"{flagged}/{len(model.get('scripts', []))} scripts read non-stdlib globals")


if __name__ == "__main__":
    main()
