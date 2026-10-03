"""What the escape hatch holds: a static scan of `user.lua` and `legacy.lua` (ADR-0018).

Hyprland keeps event handlers, timers and Lua layouts inside its own VM, and no IPC query
lists them, so the only way to show a user what their Lua does is to read the files. This
module reads them as **text**: it tokenizes Lua (comments, short and long strings, names,
numbers, punctuation) and looks for four direct calls -- `hl.on`, `hl.timer`,
`hl.layout.register` and `hl.plugin.load`. It never executes or evaluates the Lua.

Best-effort by design. A call made through another name (`local on = hl.on`), built in a
loop, or spelled `hl["on"]` is not a hit; where the file names one of the four functions
without calling it, the scan reports an `IndirectUse` so the Page can point at the line.
A string or comment that never closes stops the scan of that file and is reported as
`UnfinishedText`; every hit before it is kept. Only these two files are read: each
`require`, `dofile` or `loadfile` in them is reported as a `LoadsFile`, since the calls in
the file it loads are never seen. No content makes the scan raise: a file the scanner
cannot follow is reported as an `UnsearchedFile`, and the other file is still read.

Read by the Scripting Page and by the layout pickers (`discovered_layouts`, #175), and by
nothing that writes: a miss or a crash here cannot change a byte the Writer emits.
"""

from __future__ import annotations

import enum
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from hyprtweaker.engine.paths import ConfigPaths

_log = logging.getLogger(__name__)


class CallKind(enum.StrEnum):
    """The four calls the inventory lists."""

    ON = "on"
    TIMER = "timer"
    LAYOUT = "layout"
    PLUGIN_LOAD = "plugin_load"


@dataclass(frozen=True, slots=True)
class ScriptingHit:
    """One call found in a file.

    `name` is the event for `hl.on`, the layout name for `hl.layout.register`, the path
    for `hl.plugin.load`, and the timer's literal `timeout` and `type` (`"500 ms, repeat"`)
    for `hl.timer`. It is empty when the file computes it instead of writing it out.
    """

    kind: CallKind
    name: str
    path: Path
    line: int


@dataclass(frozen=True, slots=True)
class IndirectUse:
    """The file names one of the four functions without calling it (`local on = hl.on`):
    whatever it does with that name later is invisible to a text scan."""

    kind: CallKind
    path: Path
    line: int


@dataclass(frozen=True, slots=True)
class UnfinishedText:
    """A string or comment opens on `line` and never closes, so the rest of the file was
    not searched. Lua refuses such a file too."""

    path: Path
    line: int


@dataclass(frozen=True, slots=True)
class LoadsFile:
    """`require`, `dofile` or `loadfile` on `line`: the file it loads is not read, so the
    calls in it are not listed."""

    path: Path
    line: int


@dataclass(frozen=True, slots=True)
class UnsearchedFile:
    """The scanner failed on this file, so nothing in it is listed. A bug of the scanner's,
    never the user's: Lua may well accept the file."""

    path: Path


ScanGap = IndirectUse | UnfinishedText | LoadsFile | UnsearchedFile


@dataclass(frozen=True, slots=True)
class ScriptingScan:
    """Everything the scan found, plus what it could not read.

    `unreadable` lists files that exist but could not be opened (permissions, a
    directory in the file's place). A missing file is not unreadable: it simply holds
    nothing.
    """

    hits: tuple[ScriptingHit, ...] = ()
    unreadable: tuple[Path, ...] = ()
    gaps: tuple[ScanGap, ...] = ()


def scan_scripting(paths: ConfigPaths) -> ScriptingScan:
    """Scan `user.lua`, then `legacy.lua`. Never raises, whatever the files hold."""
    hits: list[ScriptingHit] = []
    unreadable: list[Path] = []
    gaps: list[ScanGap] = []
    for path in (paths.user_lua, paths.legacy_lua):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError:
            unreadable.append(path)
            continue
        # Replacement rather than refusal: Lua is byte-oriented, and a stray Latin-1 byte
        # in a comment is no reason to hide every call in the file.
        try:
            file_hits, file_gaps = _scan_text(data.decode("utf-8", errors="replace"), path)
        except Exception:  # the user's own file: no content may break a page or a picker
            _log.exception("scanning %s failed", path)
            gaps.append(UnsearchedFile(path=path))
            continue
        hits.extend(file_hits)
        gaps.extend(file_gaps)
    return ScriptingScan(hits=tuple(hits), unreadable=tuple(unreadable), gaps=tuple(gaps))


def discovered_layouts(paths: ConfigPaths) -> tuple[str, ...]:
    """The `lua:<name>` layouts the user's files register, de-duplicated and sorted."""
    names = {
        hit.name
        for hit in scan_scripting(paths).hits
        if hit.kind is CallKind.LAYOUT and hit.name
    }
    return tuple(f"{LUA_LAYOUT}{name}" for name in sorted(names))


LUA_LAYOUT = "lua:"
"""The prefix Hyprland gives a layout a Lua file registers: `lua:<name>`."""

LAYOUT_OPTION = "general:layout"
"""The Option whose choices include the discovered layouts (ADR-0018 §Custom layouts)."""


def layout_label(value: str, *, found: bool) -> str:
    """A layout choice in words, the same in every picker that offers one (#175).

    `lua:foo` reads "foo (Lua layout)" when the user's files register it, and "foo (not
    found)" when they do not: the value is kept, but no file registers it. Any other value
    (a built-in, or a plugin's layout) reads as itself.
    """
    if not value.startswith(LUA_LAYOUT):
        return value
    name = value.removeprefix(LUA_LAYOUT)
    return f"{name} (Lua layout)" if found else f"{name} (not found)"


# --- tokens -----------------------------------------------------------------------------


class _Tok(enum.Enum):
    NAME = enum.auto()
    STRING = enum.auto()
    NUMBER = enum.auto()
    OP = enum.auto()


@dataclass(frozen=True, slots=True)
class _Token:
    kind: _Tok
    text: str
    """The name, the operator, the number as written, or a string's decoded value."""
    line: int


class _Unfinished(Exception):
    def __init__(self, line: int) -> None:
        super().__init__(line)
        self.line = line


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(
    r"0[xX][0-9a-fA-F]*(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?"
    r"|\d+\.?\d*(?:[eE][+-]?\d+)?"
    r"|\.\d+(?:[eE][+-]?\d+)?",
    re.ASCII,  # Lua's digits are 0-9: `²` or `٣` is no number, and falls to an operator
)
_LONG_OPEN = re.compile(r"\[(=*)\[")
_OPS = ("...", "..", "::", "==", "~=", "<=", ">=", "//", "<<", ">>")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _tokens(source: str) -> Iterator[_Token]:
    """Lua tokens with their line numbers; comments and whitespace dropped.

    Raises `_Unfinished` at a long string or comment that never closes. A short string
    that runs into a newline ends there: Lua rejects it, but the lines after it are still
    the user's, and still worth listing.
    """
    pos = 0
    line = 1
    end = len(source)
    while pos < end:
        char = source[pos]
        if char == "\n":
            line += 1
            pos += 1
        elif char.isspace():
            pos += 1
        elif source.startswith("--", pos):
            opener = _LONG_OPEN.match(source, pos + 2)
            if opener is not None:
                pos, line = _skip_long(source, opener, line)
            else:
                newline = source.find("\n", pos)
                pos = end if newline < 0 else newline
        elif char == "[" and (opener := _LONG_OPEN.match(source, pos)) is not None:
            start_line = line
            after, line = _skip_long(source, opener, line)
            body = source[opener.end() : after - len(opener.group(1)) - 2]
            # Lua drops a newline that immediately follows the opening bracket.
            if body.startswith("\r\n"):
                body = body[2:]
            elif body.startswith("\n"):
                body = body[1:]
            yield _Token(_Tok.STRING, body, start_line)
            pos = after
        elif char in "\"'":
            start_line = line
            text, pos, line = _short_string(source, pos, line)
            yield _Token(_Tok.STRING, text, start_line)
        elif (match := _NAME.match(source, pos)) is not None:
            yield _Token(_Tok.NAME, match.group(), line)
            pos = match.end()
        elif (number := _NUMBER.match(source, pos)) is not None:
            yield _Token(_Tok.NUMBER, number.group(), line)
            pos = number.end()
        else:
            op = next((op for op in _OPS if source.startswith(op, pos)), char)
            yield _Token(_Tok.OP, op, line)
            pos += len(op)


def _skip_long(source: str, opener: re.Match[str], line: int) -> tuple[int, int]:
    """Past a long bracket's closing `]=*]`: the new position and line."""
    closer = "]" + opener.group(1) + "]"
    close = source.find(closer, opener.end())
    if close < 0:
        raise _Unfinished(line)
    after = close + len(closer)
    return after, line + source.count("\n", opener.start(), after)


def _short_string(source: str, pos: int, line: int) -> tuple[str, int, int]:
    """A quoted string's value, the position after it, and the line it ends on."""
    quote = source[pos]
    pos += 1
    out: list[str] = []
    while pos < len(source):
        char = source[pos]
        if char == quote:
            return "".join(out), pos + 1, line
        if char == "\n":
            return "".join(out), pos, line  # unfinished: end at the line, as noted above
        if char == "\\" and pos + 1 < len(source):
            escaped = source[pos + 1]
            if escaped == "\n":
                line += 1
                out.append("\n")
            elif escaped == "z":
                # `\z` skips the whitespace after it, newlines included.
                skip = pos + 2
                while skip < len(source) and source[skip].isspace():
                    line += source[skip] == "\n"
                    skip += 1
                pos = skip
                continue
            else:
                out.append(_ESCAPES.get(escaped, escaped))
            pos += 2
            continue
        out.append(char)
        pos += 1
    return "".join(out), pos, line


# --- calls ------------------------------------------------------------------------------

_PATHS: dict[tuple[str, ...], CallKind] = {
    ("on",): CallKind.ON,
    ("timer",): CallKind.TIMER,
    ("layout", "register"): CallKind.LAYOUT,
    ("plugin", "load"): CallKind.PLUGIN_LOAD,
}
_LOADERS = {"require", "dofile", "loadfile"}
_OPENERS = {"(", "{", "[", "function", "if", "do", "repeat"}
_CLOSERS = {")", "}", "]", "end", "until"}

_Arg = list[_Token]


def _scan_text(source: str, path: Path) -> tuple[list[ScriptingHit], list[ScanGap]]:
    tokens: list[_Token] = []
    gaps: list[ScanGap] = []
    try:
        for token in _tokens(source):
            tokens.append(token)
    except _Unfinished as unfinished:
        gaps.append(UnfinishedText(path=path, line=unfinished.line))

    hits: list[ScriptingHit] = []
    found: list[ScanGap] = []
    for index, token in enumerate(tokens):
        if token.kind is not _Tok.NAME or token.text not in ("hl", *_LOADERS):
            continue
        if index > 0 and (_is(tokens[index - 1], ".") or _is(tokens[index - 1], ":")):
            continue  # `x.hl` is some other table's field
        if token.text in _LOADERS:
            # `{ require = 1 }` or `local require = f` names a key or a local, not a load.
            if not (index + 1 < len(tokens) and _is(tokens[index + 1], "=")):
                found.append(LoadsFile(path=path, line=token.line))
            continue
        matched = _match_path(tokens, index + 1)
        if matched is None:
            continue
        kind, after = matched
        args = _call_args(tokens, after)
        if args is None:
            found.append(IndirectUse(kind=kind, path=path, line=token.line))
            continue
        hits.extend(
            ScriptingHit(kind=kind, name=name, path=path, line=token.line)
            for name in _names(kind, args)
        )
    return hits, found + gaps


def _match_path(tokens: list[_Token], start: int) -> tuple[CallKind, int] | None:
    """Which of the four functions `hl` is followed by, and the index after its name."""
    for names, kind in _PATHS.items():
        index = start
        for name in names:
            if not (
                index + 1 < len(tokens)
                and _is(tokens[index], ".")
                and tokens[index + 1].kind is _Tok.NAME
                and tokens[index + 1].text == name
            ):
                break
            index += 2
        else:
            if index < len(tokens) and _is(tokens[index], "."):
                return None  # `hl.on.x`: a field of the function, not the function
            return kind, index
    return None


def _call_args(tokens: list[_Token], index: int) -> list[_Arg] | None:
    """The call's arguments, split at top-level commas; None if this is not a call.

    Lua's call sugar counts: `f "x"` and `f { ... }` are calls with one argument. A call
    that never closes keeps the arguments read so far.
    """
    if index >= len(tokens):
        return None
    first = tokens[index]
    if first.kind is _Tok.STRING:
        return [[first]]
    if _is(first, "{"):
        return [_balanced(tokens, index)]
    if not _is(first, "("):
        return None

    args: list[_Arg] = []
    current: _Arg = []
    depth = 0
    for token in tokens[index + 1 :]:
        if token.kind in (_Tok.NAME, _Tok.OP):
            if depth == 0 and token.text == ")":
                break
            if depth == 0 and token.text == ",":
                args.append(current)
                current = []
                continue
            if token.text in _OPENERS:
                depth += 1
            elif token.text in _CLOSERS:
                depth -= 1
        current.append(token)
    if current or args:
        args.append(current)
    return args


def _balanced(tokens: list[_Token], index: int) -> _Arg:
    """The tokens of one bracketed expression starting at `index`, brackets included."""
    out: _Arg = []
    depth = 0
    for token in tokens[index:]:
        out.append(token)
        if token.kind in (_Tok.NAME, _Tok.OP):
            if token.text in _OPENERS:
                depth += 1
            elif token.text in _CLOSERS:
                depth -= 1
        if depth == 0:
            break
    return out


def _is(token: _Token, text: str) -> bool:
    """Whether `token` is this name or operator -- never a string that happens to hold it."""
    return token.kind in (_Tok.NAME, _Tok.OP) and token.text == text


def _literal(arg: _Arg) -> str:
    """The argument's text if it is one string literal, else empty."""
    if len(arg) == 1 and arg[0].kind is _Tok.STRING:
        return arg[0].text
    return ""


def _names(kind: CallKind, args: list[_Arg]) -> list[str]:
    """One name per hit: `hl.plugin.load` takes any number of paths."""
    if kind is CallKind.PLUGIN_LOAD:
        paths = [_literal(arg) for arg in args if _literal(arg)]
        return paths or [""]
    if kind is CallKind.TIMER:
        return [_timer_text(args[1] if len(args) > 1 else [])]
    return [_literal(args[0]) if args else ""]


def _timer_text(opts: _Arg) -> str:
    """`"500 ms, repeat"` from `{ timeout = 500, type = "repeat" }`, whatever is literal."""
    if not opts or not _is(opts[0], "{"):
        return ""
    fields: dict[str, _Token] = {}
    depth = 0
    for index, token in enumerate(opts):
        if token.kind in (_Tok.NAME, _Tok.OP) and token.text in _OPENERS:
            depth += 1
        elif token.kind in (_Tok.NAME, _Tok.OP) and token.text in _CLOSERS:
            depth -= 1
        elif (
            depth == 1
            and token.kind is _Tok.NAME
            and index + 2 < len(opts)
            and _is(opts[index + 1], "=")
            and any(_is(opts[index - 1], sep) for sep in ("{", ",", ";"))
        ):
            fields[token.text] = opts[index + 2]
    parts: list[str] = []
    timeout = fields.get("timeout")
    if timeout is not None and timeout.kind is _Tok.NUMBER:
        parts.append(f"{timeout.text} ms")
    timer_type = fields.get("type")
    if timer_type is not None and timer_type.kind is _Tok.STRING:
        parts.append(timer_type.text)
    return ", ".join(parts)


__all__ = [
    "LUA_LAYOUT",
    "CallKind",
    "IndirectUse",
    "LoadsFile",
    "ScanGap",
    "ScriptingHit",
    "ScriptingScan",
    "UnfinishedText",
    "UnsearchedFile",
    "discovered_layouts",
    "layout_label",
    "scan_scripting",
]
