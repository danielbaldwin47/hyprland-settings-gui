"""PROTOTYPE — throwaway. Faithful-enough hyprlang parser.

Emulates hyprlang v0.6.8 + Hyprland v0.56.2's legacy ConfigManager well enough to
turn a `.conf` tree into an ordered event stream. Grammar transcribed from
docs/research/hyprlang-to-lua.md section 1 (which cites hyprlang/src/config.cpp
and src/config/legacy/ConfigManager.cpp line by line).

The stream is *flat*: `source =` is inlined at the point it appears, exactly as
hyprlang does, with SOURCE_ENTER / SOURCE_LEAVE markers so an emitter can
re-derive the file structure if it wants to.
"""
import glob
import os
import re

# Hyprland's special (keyed) categories — ConfigManager.cpp:518,567,591,595,626
SPECIAL_CATEGORIES = {
    "device": "name",
    "monitorv2": "output",
    "windowrule": "name",
    "layerrule": "name",
    "plugin": None,  # static, ignoreMissing
}

# Handlers with allowFlags=true — LHS may carry trailing flag letters
FLAG_HANDLERS = ("bind", "gesture", "env")

# Handlers matched exactly — ConfigManager.cpp:601-623
EXACT_HANDLERS = {
    "exec", "execr", "exec-once", "execr-once", "exec-shutdown",
    "monitor", "unbind", "workspace", "windowrule", "layerrule",
    "bezier", "animation", "source", "submap", "plugin", "permission",
    "windowrulev2", "layerrulev2",
}

TRUTHY_PREFIXES = ("true", "on", "yes", "enable", "enabled", "set")
FALSY_PREFIXES = ("false", "off", "no")


class Event:
    __slots__ = ("kind", "key", "value", "file", "line", "extra")

    def __init__(self, kind, key=None, value=None, file=None, line=0, extra=None):
        self.kind = kind      # set | handler | special | var | source_enter | source_leave | error
        self.key = key
        self.value = value
        self.file = file
        self.line = line
        self.extra = extra or {}

    def __repr__(self):
        return f"<{self.kind} {self.key!r}={self.value!r} @{os.path.basename(self.file or '')}:{self.line}>"


class Diag:
    def __init__(self, level, code, message, file, line, text=""):
        self.level = level      # error | warn | info
        self.code = code        # e.g. "L13", "PARSE"
        self.message = message
        self.file = file
        self.line = line
        self.text = text

    def as_dict(self):
        return {
            "level": self.level, "code": self.code, "message": self.message,
            "file": self.file, "line": self.line, "text": self.text,
        }


def _join_continuations(raw_lines):
    """config.cpp:44-65 — a trailing `\\` appends the next physical line verbatim."""
    out = []
    buf = None
    start = 0
    for i, line in enumerate(raw_lines, 1):
        line = line.rstrip("\n").rstrip("\r")
        if buf is None:
            buf, start = line, i
        else:
            buf += line
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip(" \t")
        else:
            out.append((start, buf))
            buf = None
    if buf is not None:
        out.append((start, buf))
    return out


def _strip_comment(line):
    """config.cpp:674-706. `##` escapes to a literal `#`; first bare `#` truncates."""
    out = []
    i = 0
    while i < len(line):
        c = line[i]
        if c == "#":
            if i + 1 < len(line) and line[i + 1] == "#":
                out.append("#")
                i += 2
                continue
            break
        out.append(c)
        i += 1
    return "".join(out)


_EXPR = re.compile(r"\{\{([^{}]*)\}\}")


def _eval_expr(text, variables, diags, file, line):
    """config.cpp:619-667 — exactly `A op B`, three whitespace-separated tokens."""
    for _ in range(100):
        m = _EXPR.search(text)
        if not m:
            break
        # an escaped `\{{` is skipped
        if m.start() > 0 and text[m.start() - 1] == "\\":
            break
        parts = m.group(1).split()
        val = None
        if len(parts) == 3:
            a, op, b = parts
            try:
                av = float(variables.get(a, a))
                bv = float(variables.get(b, b))
                val = {"+": av + bv, "-": av - bv, "*": av * bv,
                       "/": (av / bv if bv else 0.0)}.get(op)
            except (ValueError, TypeError):
                val = None
        if val is None:
            diags.append(Diag("error", "L27", f"bad {{{{ }}}} expression: {m.group(1)!r}",
                              file, line, text))
            return text
        text = text[:m.start()] + _fmt_float(val) + text[m.end():]
    return text


def _fmt_float(v):
    if v == int(v):
        return str(int(v))
    return repr(v)


def truthy(value):
    """MiscFunctions.cpp:829-843 + hyprlang INT prefix rule (config.cpp:259-271)."""
    v = value.strip().lower()
    if v == "1" or v.startswith(TRUTHY_PREFIXES):
        return True
    if v == "0" or v.startswith(FALSY_PREFIXES):
        return False
    return None


class Parser:
    def __init__(self, env=None, follow_source=True, missing_ok=False):
        self.vars = dict(os.environ if env is None else env)
        self.vars.setdefault("HYPRLAND_V_0_53", "1")
        self.events = []
        self.diags = []
        self.follow_source = follow_source
        self.missing_ok = missing_ok
        self._cats = []            # category stack
        self._special = None       # dict when inside a special-category block
        self._noerror = False
        self._ifstack = []
        self._seen = set()         # cycle guard for source=
        self._var_defs = []        # (name, value, file, line) in order

    # ---- variable substitution (config.cpp:733-753) -------------------------
    def _expand(self, text):
        for _ in range(100):
            before = text
            for name in sorted(self.vars, key=len, reverse=True):
                token = "$" + name
                if token in text:
                    text = text.replace(token, self.vars[name])
            if text == before:
                break
        return text

    @staticmethod
    def _unescape(text):
        return text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")

    # ---- entry point --------------------------------------------------------
    def parse_file(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        if path in self._seen:
            self.diags.append(Diag("error", "PARSE", "source= cycle", path, 0))
            return
        self._seen.add(path)
        try:
            with open(path, "r", errors="replace") as fh:
                raw = fh.readlines()
        except OSError as exc:
            if not (self._noerror or self.missing_ok):
                self.diags.append(Diag("error", "PARSE", f"cannot read: {exc}", path, 0))
            return
        self.events.append(Event("source_enter", file=path))
        for lineno, line in _join_continuations(raw):
            try:
                self._line(line, path, lineno)
            except Exception as exc:  # prototype: never die on one bad line
                self.diags.append(Diag("error", "PARSE", f"{type(exc).__name__}: {exc}",
                                       path, lineno, line))
        self.events.append(Event("source_leave", file=path))
        self._seen.discard(path)

    # ---- one logical line ---------------------------------------------------
    def _line(self, line, file, lineno):
        stripped = line.strip()
        if not stripped:
            return
        if stripped.startswith("#"):
            self._directive(stripped, file, lineno)
            return
        if self._ifstack and not self._ifstack[-1]:
            return                                     # inside a failed `if`
        body = _strip_comment(stripped).strip()
        if not body:
            return

        if "=" in body:
            lhs, rhs = body.split("=", 1)
            lhs, rhs = lhs.strip(), rhs.strip()
            is_vardef = lhs.startswith("$")
            rhs = self._expand(rhs)
            rhs = _eval_expr(rhs, self.vars, self.diags, file, lineno)
            if not is_vardef:
                lhs = self._expand(lhs)
                rhs = self._unescape(rhs)
            if is_vardef:
                name = lhs[1:]
                self.vars[name] = rhs
                self._var_defs.append((name, rhs, file, lineno))
                self.events.append(Event("var", key=name, value=rhs, file=file, line=lineno))
                return
            if not lhs:
                self.diags.append(Diag("error", "PARSE", "empty lhs", file, lineno, line))
                return
            self._assign(lhs, rhs, file, lineno, line)
            return

        if body.endswith("{"):
            name = body[:-1].strip()
            self._open(name, file, lineno)
            return
        if body == "}":
            self._close(file, lineno)
            return
        self.diags.append(Diag("error", "PARSE", "invalid config line", file, lineno, line))

    # ---- `# hyprlang ...` (config.cpp:567-617) ------------------------------
    def _directive(self, line, file, lineno):
        text = line.lstrip("#").strip()
        if not text.startswith("hyprlang"):
            return
        parts = text.split()
        if len(parts) < 2:
            return
        verb = parts[1]
        if verb == "noerror":
            arg = parts[2].lower() if len(parts) > 2 else ""
            self._noerror = arg in ("", "true", "yes", "enable", "enabled", "set")
        elif verb == "if":
            cond = parts[2] if len(parts) > 2 else ""
            neg = cond.startswith("!")
            name = cond[1:] if neg else cond
            val = self.vars.get(name, "")
            ok = bool(val)
            self._ifstack.append((not ok) if neg else ok)
            self.diags.append(Diag("warn", "L27", f"`# hyprlang if {cond}` evaluated {self._ifstack[-1]}"
                                                  " at conversion time; Lua has no equivalent",
                                   file, lineno, line))
        elif verb == "endif":
            if self._ifstack:
                self._ifstack.pop()

    # ---- category open/close ------------------------------------------------
    def _open(self, name, file, lineno):
        low = name.lower()
        if not self._cats and low in SPECIAL_CATEGORIES and self._special is None:
            self._special = {"cat": low, "fields": [], "file": file, "line": lineno, "sub": None}
            return
        if self._special is not None and self._special["cat"] == "plugin":
            self._special["sub"] = name
            return
        self._cats.append(name)

    def _close(self, file, lineno):
        if self._special is not None:
            if self._special["cat"] == "plugin" and self._special["sub"] is not None:
                self._special["sub"] = None
                return
            self.events.append(Event("special", key=self._special["cat"],
                                     value=self._special["fields"],
                                     file=self._special["file"], line=self._special["line"]))
            self._special = None
            return
        if self._cats:
            self._cats.pop()
        else:
            self.diags.append(Diag("error", "PARSE", "stray category close", file, lineno))

    # ---- key = value --------------------------------------------------------
    def _assign(self, lhs, rhs, file, lineno, raw):
        if self._special is not None:
            key = lhs
            if self._special["cat"] == "plugin" and self._special["sub"]:
                key = f"{self._special['sub']}:{lhs}"
            self._special["fields"].append((key, rhs, lineno))
            return

        handler = self._handler_for(lhs)
        if handler is not None:
            name, flags = handler
            if name == "source":
                self._source(rhs, file, lineno)
                return
            self.events.append(Event("handler", key=name, value=rhs, file=file, line=lineno,
                                     extra={"flags": flags, "raw": raw}))
            return

        # inline keyed special: device[NAME]:key = v
        m = re.match(r"^([A-Za-z0-9_]+)\[([^\]]+)\]:(.+)$", lhs)
        if m and m.group(1).lower() in SPECIAL_CATEGORIES:
            cat, keyval, field = m.group(1).lower(), m.group(2), m.group(3)
            keyname = SPECIAL_CATEGORIES[cat] or "name"
            self.events.append(Event("special", key=cat,
                                     value=[(keyname, keyval, lineno), (field, rhs, lineno)],
                                     file=file, line=lineno))
            return

        full = ":".join(self._cats + [lhs]) if self._cats else lhs
        # a top-level `plugin:NAME:key` line
        if full.startswith("plugin:"):
            self.events.append(Event("handler", key="plugin_value", value=rhs, file=file,
                                     line=lineno, extra={"path": full}))
            return
        self.events.append(Event("set", key=full, value=rhs, file=file, line=lineno,
                                 extra={"orphan": not self._cats and ":" not in lhs}))

    def _handler_for(self, lhs):
        if ":" in lhs:
            return None
        low = lhs
        if low in EXACT_HANDLERS:
            return (low, "")
        for h in FLAG_HANDLERS:
            if low.startswith(h) and len(low) > len(h):
                return (h, low[len(h):])
            if low == h:
                return (h, "")
        return None

    # ---- source = -----------------------------------------------------------
    def _source(self, rhs, file, lineno):
        if not self.follow_source:
            self.events.append(Event("handler", key="source", value=rhs, file=file, line=lineno))
            return
        base = os.path.dirname(file)
        path = os.path.expanduser(rhs.strip())
        if not os.path.isabs(path):
            path = os.path.join(base, path)
        matches = sorted(glob.glob(path))
        if not matches:
            if not self._noerror:
                self.diags.append(Diag("error", "PARSE", f"source= found no match: {rhs}",
                                       file, lineno, rhs))
            self.events.append(Event("handler", key="source_missing", value=rhs,
                                     file=file, line=lineno))
            return
        for m in matches:
            if os.path.isfile(m):
                self.parse_file(m)


def parse(path, env=None, follow_source=True):
    p = Parser(env=env, follow_source=follow_source)
    p.parse_file(path)
    return p
