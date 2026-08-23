"""Lifting script constructs back out of their source files, verbatim.

ADR-0009 says an event handler or a function-valued action is "extracted by source range
into `legacy.lua`". This module is that extraction, and the two hard parts are worth
naming, because both were found by the prototype rather than reasoned out:

* **Where the function ends.** `debug.getinfo` gives a line range, but a line range is not
  an expression -- the last line usually has more on it than the closing `end`. So the
  text is scanned for the `end` that closes the opening `function`, over a copy with
  strings and comments blanked out, because `-- ]]` inside a long bracket and the word
  `end` inside a string both broke the naive version.
* **What the function needs.** A closure is not its source text: it also closed over
  names. Those come back as upvalues and are re-materialised as `local` definitions ahead
  of the body. The one case that has no answer is a closure reading a *foreign global* --
  the text references a name that was never an upvalue and is not stdlib, so lifting the
  text lifts a dangling reference. Those are detected here and flagged Needs review; the
  ADR does not claim they can be fixed.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from ...model.values import lua_string
from ...writer.syntax import luac_command
from .sandbox import Recording, Script

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
LUA_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LUA_KEYWORDS = frozenset(
    [
        "and",
        "break",
        "do",
        "else",
        "elseif",
        "end",
        "false",
        "for",
        "function",
        "goto",
        "if",
        "in",
        "local",
        "nil",
        "not",
        "or",
        "repeat",
        "return",
        "then",
        "true",
        "until",
        "while",
    ]
)

#: Names a lifted closure may read without anything being wrong: the stub itself, the
#: standard library, and the pieces of it the sandbox environment provides.
SAFE_GLOBALS = frozenset(
    {
        "hl",
        "_G",
        "arg",
        "package",
        "require",
        "__require",
        "string",
        "table",
        "math",
        "utf8",
        "coroutine",
        "os",
        "io",
        "print",
        "pairs",
        "ipairs",
        "next",
        "type",
        "tostring",
        "tonumber",
        "select",
        "error",
        "assert",
        "pcall",
        "xpcall",
        "setmetatable",
        "getmetatable",
        "rawget",
        "rawset",
        "rawequal",
        "rawlen",
        "load",
        "loadstring",
        "dofile",
        "collectgarbage",
    }
)

UNEXTRACTABLE = "function() end --[[ could not be read from source ]]"

#: `luac -l` disassembly line for a global read: `GETTABUP  R[0] U[0] K[1]:"name"`.
_GETTABUP = re.compile(r"GETTABUP.*_ENV.*\"([A-Za-z_][A-Za-z0-9_]*)\"")


def luac_binary() -> str | None:
    """The bytecode lister used for foreign-global detection, if one is installed.

    The writer's syntax gate already had to answer "where is luac"; two candidate lists
    would drift, and this one would be the stale one.
    """
    return luac_command()


def lua_value(value: Any, scripts: ScriptSource | None = None) -> str:
    """A captured value as Lua source.

    `scripts` lets a captured function inside a value render as its extracted body, which
    is what makes a "hybrid" call -- `hl.bind(keys, function() ... end)` -- reproducible.
    """
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}" if value == int(value) else repr(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, list):
        return "{ " + ", ".join(lua_value(item, scripts) for item in value) + " }"
    if isinstance(value, dict):
        if isinstance(value.get("__dsp"), str):
            args = value.get("args") or []
            if isinstance(args, dict):
                args = [args]
            rendered = ", ".join(lua_value(arg, scripts) for arg in args)
            return f"hl.dsp.{value['__dsp']}({rendered})"
        if "__fn" in value:
            if scripts is None:
                return UNEXTRACTABLE
            return scripts.expression(int(value["__fn"]))
        parts = []
        for key, item in value.items():
            text = str(key)
            rendered = lua_value(item, scripts)
            if LUA_IDENT.match(text) and text not in LUA_KEYWORDS:
                parts.append(f"{text} = {rendered}")
            elif text.lstrip("-").isdigit():
                parts.append(f"[{text}] = {rendered}")
            else:
                parts.append(f"[{lua_string(text)}] = {rendered}")
        return "{ " + ", ".join(parts) + " }" if parts else "{}"
    return "nil"


def _scrub(text: str) -> str:
    """The same text with string and comment *contents* blanked, lengths preserved.

    Only used to find token positions, never emitted: keeping the length means an offset
    into the scrubbed copy is an offset into the real text.
    """
    out: list[str] = []
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char == "-" and text[index : index + 2] == "--":
            match = re.match(r"--\[(=*)\[", text[index:])
            if match:
                close = "]" + match.group(1) + "]"
                found = text.find(close, index + match.end())
                stop = size if found < 0 else found + len(close)
            else:
                found = text.find("\n", index)
                stop = size if found < 0 else found
            out.append("".join("\n" if c == "\n" else " " for c in text[index:stop]))
            index = stop
            continue
        if char == "[":
            match = re.match(r"\[(=*)\[", text[index:])
            if match:
                close = "]" + match.group(1) + "]"
                found = text.find(close, index + match.end())
                stop = size - len(close) if found < 0 else found
                body = text[index + match.end() : stop]
                out.append("[" + match.group(1) + "[")
                out.append("".join("\n" if c == "\n" else " " for c in body))
                out.append(close)
                index = stop + len(close)
                continue
        if char in "'\"":
            quote, cursor = char, index + 1
            buffer = [char]
            while cursor < size:
                if text[cursor] == "\\":
                    buffer.append("  ")
                    cursor += 2
                    continue
                if text[cursor] == quote:
                    buffer.append(quote)
                    cursor += 1
                    break
                buffer.append("\n" if text[cursor] == "\n" else " ")
                cursor += 1
            out.append("".join(buffer))
            index = cursor
            continue
        out.append(char)
        index += 1
    return "".join(out)


class ScriptSource:
    """Reads captured closures back out of the files they came from."""

    def __init__(self, recording: Recording) -> None:
        self.recording = recording
        self.notes: list[tuple[str, str]] = []
        """`(origin, message)` rather than one string: the caller needs the origin, and
        recovering it by splitting on the first colon turned `upvalue foo: ...` into an
        origin of `upvalue foo`."""
        self._files: dict[str, list[str] | None] = {}
        self._expressions: dict[int, str] = {}
        self._globals: dict[int, tuple[str, ...]] = {}

    def _lines(self, relative: str) -> list[str] | None:
        if relative not in self._files:
            try:
                text = (self.recording.basedir / relative).read_text(encoding="utf-8")
            except OSError:
                self._files[relative] = None
            else:
                self._files[relative] = text.splitlines()
        return self._files[relative]

    def expression(self, script_id: int) -> str:
        """`function(...) ... end` for a captured script, read from its source file."""
        if script_id in self._expressions:
            return self._expressions[script_id]
        script = self.recording.script(script_id)
        text = UNEXTRACTABLE if script is None else self._extract(script)
        self._expressions[script_id] = text
        return text

    def extractable(self, script_id: int) -> bool:
        return self.expression(script_id) != UNEXTRACTABLE

    def _extract(self, script: Script) -> str:
        lines = self._lines(script.source)
        if lines is None:
            self.notes.append((script.source, "source file could not be read"))
            return UNEXTRACTABLE
        if not (0 < script.start <= script.end <= len(lines)):
            self.notes.append(
                (f"{script.source}:{script.start}", "line range outside the file")
            )
            return UNEXTRACTABLE

        body = lines[script.start - 1 : script.end]
        first = body[0]
        opening = re.search(r"\bfunction\b", first)
        if opening is None:
            self.notes.append((f"{script.source}:{script.start}", "no 'function' to extract"))
            return UNEXTRACTABLE
        # `function name(a)` and `function M.name(a)` both become an anonymous expression.
        body[0] = re.sub(
            r"^function\s+[A-Za-z_][\w.:]*\s*\(", "function(", first[opening.start() :]
        )
        text = "\n".join(body)

        depth, cut, pending_do = 0, None, False
        for token in WORD.finditer(_scrub(text)):
            word = token.group(0)
            if word in ("for", "while"):
                # `for ... do` is one block, not two: the `do` that follows is its own.
                depth += 1
                pending_do = True
            elif word == "do":
                if pending_do:
                    pending_do = False
                else:
                    depth += 1
            elif word in ("function", "if", "repeat"):
                depth += 1
            elif word in ("end", "until"):
                depth -= 1
                if depth == 0:
                    cut = token.end()
                    break
        if cut is None:
            self.notes.append((f"{script.source}:{script.start}", "no matching 'end'"))
            return UNEXTRACTABLE
        return text[:cut]

    def upvalue_definitions(self, script: Script, seen: set[str] | None = None) -> list[str]:
        """`local` definitions for everything the closure closed over, innermost first."""
        seen = set() if seen is None else seen
        definitions: list[str] = []
        for upvalue in script.upvalues:
            if upvalue.name in seen:
                continue
            seen.add(upvalue.name)
            if upvalue.type in ("string", "number", "boolean", "table"):
                definitions.append(f"local {upvalue.name} = {lua_value(upvalue.value, self)}")
            elif upvalue.type == "function" and isinstance(upvalue.value, dict):
                nested = self.recording.script(int(upvalue.value.get("__fn", 0)))
                if nested is not None:
                    definitions = self.upvalue_definitions(nested, seen) + definitions
                definitions.append(f"local {upvalue.name} = {lua_value(upvalue.value, self)}")
            else:
                self.notes.append(
                    (
                        "",
                        f"upvalue {upvalue.name} of type {upvalue.type} cannot be carried over",
                    )
                )
                definitions.append(
                    f"local {upvalue.name} = nil --[[ {upvalue.type} not carried over ]]"
                )
        return definitions

    def foreign_globals(self, script: Script) -> tuple[str, ...]:
        """Globals the lifted text reads that nothing will define for it.

        Compiles the extracted expression and reads the global loads straight out of the
        bytecode, so a name mentioned in a comment or a string cannot register. Returns
        empty when there is no `luac` to ask -- a missing tool must not invent findings.
        """
        if script.id in self._globals:
            return self._globals[script.id]
        result = self._detect_globals(script)
        self._globals[script.id] = result
        return result

    def _detect_globals(self, script: Script) -> tuple[str, ...]:
        expression = self.expression(script.id)
        if expression == UNEXTRACTABLE:
            return ()
        binary = luac_binary()
        if binary is None:
            return ()
        known = SAFE_GLOBALS | {upvalue.name for upvalue in script.upvalues}
        try:
            completed = subprocess.run(
                [binary, "-l", "-p", "-"],
                input=f"return {expression}\n",
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        if completed.returncode != 0:
            return ()
        found = {name for name in _GETTABUP.findall(completed.stdout) if name not in known}
        return tuple(sorted(found))


def render_legacy(entries: list[str], *, source: str) -> str:
    """`legacy.lua`: the constructs the GUI cannot represent, kept verbatim.

    Written once by the Importer and never rewritten (`ConfigPaths.protected`), so the
    header says so rather than the usual "do not edit" -- this one is the user's now.
    """
    header = [
        f"-- Imported by hyprtweaker from {source}.",
        "--",
        "-- Constructs the settings app cannot represent, kept exactly as they were. The app",
        "-- lists these read-only and never rewrites this file: it is yours to edit.",
        "",
    ]
    return "\n".join([*header, *entries]) + "\n" if entries else "\n".join(header) + "\n"
