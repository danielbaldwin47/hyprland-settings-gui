#!/usr/bin/env python3
# PROTOTYPE (wayfinder #30) — throwaway. Emits a captured model back to Lua
# (gen.lua) and supports fixpoint comparison of two models.
import json
import pathlib
import re
import sys

LUA_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
}
OPENERS = {"function", "if", "do", "for", "while"}
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def lua_str(s: str) -> str:
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace(
        "\r", "\\r").replace("\t", "\\t")
    return f'"{out}"'


class Emitter:
    def __init__(self, model: dict, basedir: pathlib.Path):
        self.model = model
        self.basedir = basedir
        self.scripts = {s["id"]: s for s in model.get("scripts", [])}
        self.notes = []
        self._filecache = {}

    # ---------- source extraction ----------
    def _lines(self, relpath: str) -> list[str]:
        if relpath not in self._filecache:
            self._filecache[relpath] = (self.basedir / relpath).read_text().splitlines()
        return self._filecache[relpath]

    def _scrub(self, text: str) -> str:
        """Blank out string/comment contents, preserving length and newlines."""
        out = []
        i, n = 0, len(text)
        while i < n:
            c = text[i]
            if c == "-" and text[i:i + 2] == "--":
                m = re.match(r"--\[(=*)\[", text[i:])
                if m:
                    close = "]" + m.group(1) + "]"
                    j = text.find(close, i + m.end())
                    j = n if j < 0 else j + len(close)
                else:
                    j = text.find("\n", i)
                    j = n if j < 0 else j
                out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
                i = j
                continue
            if c == "[":
                m = re.match(r"\[(=*)\[", text[i:])
                if m:
                    close = "]" + m.group(1) + "]"
                    j = text.find(close, i + m.end())
                    j = n - len(close) if j < 0 else j
                    body = text[i + m.end():j]
                    out.append("[" + m.group(1) + "[")
                    out.append("".join("\n" if ch == "\n" else " " for ch in body))
                    out.append(close)
                    i = j + len(close)
                    continue
            if c in "'\"":
                q, j = c, i + 1
                buf = [c]
                while j < n:
                    if text[j] == "\\":
                        buf.append("  "); j += 2; continue
                    if text[j] == q:
                        buf.append(q); j += 1; break
                    buf.append("\n" if text[j] == "\n" else " ")
                    j += 1
                out.append("".join(buf))
                i = j
                continue
            out.append(c)
            i += 1
        return "".join(out)

    def _strip_comments(self, line: str) -> str:
        # crude: cut at -- unless inside a string (good enough for corpus)
        out, i, n, q = [], 0, len(line), None
        while i < n:
            c = line[i]
            if q:
                if c == "\\":
                    out.append("  "); i += 2; continue
                if c == q:
                    q = None
                    out.append(c); i += 1; continue
                out.append(" "); i += 1; continue
            if c in "'\"":
                q = c; out.append(c); i += 1; continue
            if c == "-" and line[i:i + 2] == "--":
                out.append(" " * (n - i))
                break
            out.append(c); i += 1
        return "".join(out)

    def extract_function_expr(self, script: dict) -> str:
        """Return a Lua expression `function(...) ... end` for a captured script."""
        src, frm, to = script["source"], script["from"], script["to"]
        try:
            lines = self._lines(src)
        except OSError:
            self.notes.append(f"script #{script['id']}: source {src} unreadable")
            return "function() end --[[ UNEXTRACTABLE ]]"
        text_lines = lines[frm - 1:to]
        first = text_lines[0]
        m = re.search(r"\bfunction\b", first)
        if not m:
            self.notes.append(f"script #{script['id']}: no 'function' on line {frm} of {src}")
            return "function() end --[[ UNEXTRACTABLE ]]"
        text_lines[0] = re.sub(r"^function\s+[A-Za-z_][\w.:]*\s*\(", "function(",
                               first[m.start():])
        text = "\n".join(text_lines)
        # token-scan for the matching 'end'
        depth, cut, pending_do = 0, None, False
        for tok in WORD.finditer(self._scrub(text)):
            w = tok.group(0)
            if w in ("for", "while"):
                depth += 1
                pending_do = True
            elif w == "do":
                if pending_do:
                    pending_do = False
                else:
                    depth += 1
            elif w in ("function", "if", "repeat"):
                depth += 1
            elif w == "end" or w == "until":
                depth -= 1
                if depth == 0:
                    cut = tok.end()
                    break
        if cut is None:
            self.notes.append(f"script #{script['id']}: unbalanced extract {src}:{frm}-{to}")
            return text  # hope the debug range was exact
        return text[:cut]

    def materialize_upvalues(self, script: dict, done: set) -> list[str]:
        """local defs for a script's upvalues, recursively."""
        defs = []
        for uv in script.get("upvalues", []):
            name = uv["name"]
            if name in done:
                continue
            done.add(name)
            t, val = uv["type"], uv.get("value")
            if t in ("string", "number", "boolean"):
                defs.append(f"local {name} = {self.lua_value(val)}")
            elif t == "table":
                defs.append(f"local {name} = {self.lua_value(val)}")
            elif t == "function" and isinstance(val, dict) and "__fn" in val:
                sub = self.scripts[val["__fn"]]
                defs = self.materialize_upvalues(sub, done) + defs
                defs.append(f"local {name} = {self.extract_function_expr(sub)}")
            else:
                self.notes.append(f"script #{script['id']}: upvalue {name} of type {t} not materializable")
                defs.append(f"local {name} = nil --[[ UNMATERIALIZABLE {t} ]]")
        return defs

    # ---------- value rendering ----------
    def lua_value(self, v, script_env=None) -> str:
        if v is None:
            return "nil"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return lua_str(v)
        if isinstance(v, list):
            return "{ " + ", ".join(self.lua_value(x, script_env) for x in v) + " }"
        if isinstance(v, dict):
            if "__dsp" in v:
                args = v.get("args") or []
                if isinstance(args, dict):
                    args = [args]
                path = v["__dsp"]
                return f"hl.dsp.{path}(" + ", ".join(self.lua_value(a, script_env) for a in args) + ")"
            if "__fn" in v:
                script = self.scripts[v["__fn"]]
                return self.extract_function_expr(script)
            parts = []
            for k, val in v.items():
                ks = str(k)
                if LUA_IDENT.match(ks) and ks not in LUA_KEYWORDS:
                    parts.append(f"{ks} = {self.lua_value(val, script_env)}")
                elif ks.lstrip("-").isdigit():
                    parts.append(f"[{ks}] = {self.lua_value(val, script_env)}")
                else:
                    parts.append(f"[{lua_str(ks)}] = {self.lua_value(val, script_env)}")
            return "{ " + ", ".join(parts) + " }"
        return f"nil --[[ {type(v).__name__} ]]"

    # ---------- call emission ----------
    def fns_in(self, v) -> list[int]:
        found = []
        if isinstance(v, dict):
            if "__fn" in v:
                found.append(v["__fn"])
            else:
                for x in v.values():
                    found += self.fns_in(x)
        elif isinstance(v, list):
            for x in v:
                found += self.fns_in(x)
        return found

    def emit_call(self, call: dict) -> str:
        name, args = call["call"], call.get("args")
        if name == "define_submap":
            return ""  # reconstructed from submap tags
        if name == "on":
            handler = args["handler"]
            script = self.scripts[handler["__fn"]]
            defs = self.materialize_upvalues(script, set())
            body = f"hl.on({lua_str(args['event'])}, {self.extract_function_expr(script)})"
            if defs:
                return "do -- LEGACY (script + upvalues)\n  " + "\n  ".join(defs) + "\n  " + body + "\nend"
            return "-- LEGACY (script)\n" + body
        if name == "layout_register":
            provider = self.lua_value(args["provider"])
            return f"-- LEGACY (script)\nhl.layout.register({lua_str(args['name'])}, {provider})"
        if name == "plugin_load":
            return f"hl.plugin.load({self.lua_value(args[0])})"
        if name in ("exec_cmd", "dispatch_immediate"):
            vals = args if isinstance(args, list) else [args]
            fn = "hl.exec_cmd" if name == "exec_cmd" else "hl.dispatch"
            return f"{fn}(" + ", ".join(self.lua_value(a) for a in vals) + ")"
        if name.startswith(("notification_", "plugin_set", "plugin_call")):
            return f"-- LEGACY (unmodelled hl call: {name}) {json.dumps(args)[:120]}"
        # generic declarative / hybrid
        fnids = self.fns_in(args)
        argc = call.get("argc")
        if argc == 1 or not isinstance(args, list):
            arglist = [args]
        elif argc == 0:
            arglist = []
        else:
            arglist = args
        rendered = f"hl.{name}(" + ", ".join(self.lua_value(a) for a in arglist) + ")"
        if fnids:
            defs = []
            done = set()
            for fid in fnids:
                defs += self.materialize_upvalues(self.scripts[fid], done)
            if defs:
                return "do -- HYBRID (closure + upvalues)\n  " + "\n  ".join(defs) + "\n  " + rendered + "\nend"
            return "-- HYBRID (inline closure)\n" + rendered
        return rendered

    def emit(self) -> str:
        out = ["-- generated by emit_model.py (PROTOTYPE #30) — round-trip artifact"]
        cur_submap = None
        for call in self.model.get("calls", []):
            sm = call.get("submap")
            if sm != cur_submap:
                if cur_submap is not None:
                    out.append("end)")
                if sm is not None:
                    out.append(f'hl.define_submap({lua_str(sm)}, function()')
                cur_submap = sm
            text = self.emit_call(call)
            if text:
                if sm is not None:
                    text = "  " + text.replace("\n", "\n  ")
                out.append(text)
        if cur_submap is not None:
            out.append("end)")
        return "\n".join(out) + "\n"


# ---------- fixpoint comparison ----------
def normalize(model: dict, basedir: pathlib.Path):
    em = Emitter(model, basedir)

    def norm(v):
        if isinstance(v, dict):
            if "__fn" in v:
                txt = em.extract_function_expr(em.scripts[v["__fn"]])
                return {"__fntext": re.sub(r"\s+", " ", txt).strip()}
            return {k: norm(x) for k, x in v.items()}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v

    stream = []
    for c in model.get("calls", []):
        if c["call"] == "define_submap":
            continue
        stream.append({"call": c["call"], "submap": c.get("submap"),
                       "argc": c.get("argc"), "args": norm(c.get("args"))})
    return stream


def main():
    mode = sys.argv[1]
    if mode == "emit":
        model = json.loads(pathlib.Path(sys.argv[2]).read_text())
        basedir = pathlib.Path(sys.argv[3])
        outfile = pathlib.Path(sys.argv[4])
        em = Emitter(model, basedir)
        outfile.write_text(em.emit())
        decl = hybrid = script = 0
        for c in model.get("calls", []):
            if c["call"] in ("on", "layout_register"):
                script += 1
            elif em.fns_in(c.get("args")):
                hybrid += 1
            elif c["call"] != "define_submap":
                decl += 1
        print(f"emitted {outfile}: {decl} declarative, {hybrid} hybrid, {script} script calls")
        for n in em.notes:
            print("  note:", n)
    elif mode == "compare":
        m1 = json.loads(pathlib.Path(sys.argv[2]).read_text())
        b1 = pathlib.Path(sys.argv[3])
        m2 = json.loads(pathlib.Path(sys.argv[4]).read_text())
        b2 = pathlib.Path(sys.argv[5])
        s1, s2 = normalize(m1, b1), normalize(m2, b2)
        if s1 == s2:
            print(f"FIXPOINT OK: {len(s1)} calls identical")
            return
        print(f"FIXPOINT MISMATCH: {len(s1)} vs {len(s2)} calls")
        for i, (a, b) in enumerate(zip(s1, s2)):
            if a != b:
                print(f"--- first diff at call {i}:")
                print("  M1:", json.dumps(a)[:400])
                print("  M2:", json.dumps(b)[:400])
                break
        if len(s1) != len(s2):
            longer, tag = (s1, "M1") if len(s1) > len(s2) else (s2, "M2")
            print(f"  extra in {tag}:", json.dumps(longer[min(len(s1), len(s2))])[:300])
        sys.exit(1)


if __name__ == "__main__":
    main()
