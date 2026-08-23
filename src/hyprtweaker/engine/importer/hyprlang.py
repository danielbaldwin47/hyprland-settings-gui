"""The hyprlang grammar: a legacy `.conf` tree in, a typed keyword stream out.

Emulates hyprlang v0.6.8 plus Hyprland v0.56.2's legacy `ConfigManager` closely enough to
read a real config tree faithfully. The grammar is transcribed from research #4 §1, which
cites `hyprlang/src/config.cpp` and `src/config/legacy/ConfigManager.cpp` line by line;
every non-obvious rule below carries the citation it came from. Ported from the #9
prototype, whose output was verified end to end -- seven rice configs converted, all seven
accepted by `Hyprland --verify-config`, and nested-compositor state diffs with zero
unexplained differences.

This module is the *whole* grammar and nothing beyond it. It does not know that `bind`
takes a dispatcher or that `monitor` takes a mode string: hyprlang itself never splits a
value (`config.cpp:872`), each handler does its own comma splitting with its own arity, and
that per-keyword value grammar is the mapping half's job. Keeping the seam here is what
lets the grammar be tested exhaustively against synthetic fixtures while the messy mapping
rules are tested against real rices.

Four deliberate deviations from hyprlang, each because faithfulness would be useless:

- **`source =` cycles are guarded.** hyprlang has no cycle guard and recurses until it
  crashes. A file already open in the current chain is refused with a diagnostic.
- **Errors never abort a line.** hyprlang collects per-line errors and continues
  (`throwAllErrors=true`); so does this, and any line it cannot parse still reaches the
  stream as an `UnparsedLine` so nothing is lost.
- **Division by zero in `{{ }}` is diagnosed**, not evaluated to infinity. The C++ float
  division would emit `inf`, which every downstream value parser rejects anyway; a
  diagnostic names the real problem.
- **Variable expansion is bounded by length**, not only by round count -- see
  `MAX_EXPANSION_LENGTH`.

Where research #4 §1 was ambiguous or wrong, the behaviour was settled by probing the
installed `libhyprlang.so.0.6.8` directly rather than by reading the prose twice; the four
rules that needed it say so at their definition (eager variable capture, verbatim category
names, `##` not being a directive, and nested blocks inside a special category).

Usage::

    result = parse(Path("~/.config/hypr/hyprland.conf").expanduser())
    for keyword in result.keywords:
        ...
"""

from __future__ import annotations

import glob
import operator
import os
import re
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .keywords import (
    Assignment,
    Diagnostic,
    DiagnosticCode,
    Handler,
    Keyword,
    Origin,
    Severity,
    SourceEnter,
    SourceLeave,
    SpecialCategory,
    SpecialField,
    UnparsedLine,
    VariableDefinition,
)

__all__ = ["ParseResult", "Parser", "parse"]


SPECIAL_CATEGORIES: dict[str, str | None] = {
    "device": "name",
    "monitorv2": "output",
    "windowrule": "name",
    "layerrule": "name",
    "plugin": None,
}
"""The five keyed categories Hyprland registers, and the field that identifies an instance.
`plugin` is static rather than keyed, and `ignoreMissing`, which is why `plugin:foo:bar` for
an unloaded plugin is not an error (`ConfigManager.cpp:518,567,591,595,626`)."""

FLAG_HANDLERS: tuple[str, ...] = ("bind", "gesture", "env")
"""Handlers registered with `allowFlags=true`: the left-hand side may carry trailing flag
letters (`bindle`, `envd`), and matching is by prefix provided the LHS holds no `:`
(`config.cpp:844-873`)."""

EXACT_HANDLERS: frozenset[str] = frozenset(
    {
        "exec",
        "execr",
        "exec-once",
        "execr-once",
        "exec-shutdown",
        "monitor",
        "unbind",
        "workspace",
        "windowrule",
        "layerrule",
        "bezier",
        "animation",
        "source",
        "submap",
        "plugin",
        "permission",
        "windowrulev2",
        "layerrulev2",
    }
)
"""Handlers matched by exact left-hand side (`ConfigManager.cpp:601-623`)."""

DEPRECATED_HANDLERS: frozenset[str] = frozenset({"windowrulev2", "layerrulev2"})
"""Accepted by the grammar and then refused by Hyprland 0.56.2 outright -- these are the
pre-0.54 rule spellings (`ConfigManager.cpp:431-435`). Kept in the stream, because a config
full of them is exactly the config a user most needs converted."""

NOERROR_TRUTHY: frozenset[str] = frozenset({"", "true", "yes", "enable", "enabled", "set"})
"""`# hyprlang noerror X` enables suppression for these; anything else re-enables error
recording (`config.cpp:579-585`)."""

MAX_ITERATIONS = 100
"""hyprlang's own cap on variable-expansion and expression rounds (`config.cpp:733`)."""

MAX_EXPANSION_LENGTH = 1 << 16
"""Ceiling on one expanded line. hyprlang caps the *rounds* but not the growth, so a
self-referential `$a = $a$a` reaches 2^100 characters before its cap fires. No real config
line is 64 KiB, so this bounds the damage without bounding anything legitimate."""

_EXPRESSION = re.compile(r"\{\{([^{}]*)\}\}")
_INLINE_KEYED = re.compile(r"^([A-Za-z0-9_]+)\[([^\]]*)\]:(.+)$")

_OPERATORS: dict[str, Callable[[float, float], float]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
}
"""The four operators `{{ A op B }}` accepts -- no precedence, no nesting, no chaining
(`config.cpp:627-662`). Membership here is also what rejects a bad operator."""


def _to_float32(value: float) -> float:
    """Round a double to the nearest float, which is the width hyprlang computes in."""
    return float(struct.unpack("f", struct.pack("f", value))[0])


def _format_number(value: float) -> str:
    """Shortest text that round-trips as a float, matching `std::format("{}", float)`.

    Without the float32 round trip, `{{ 0.1 + 0.2 }}` would substitute
    `0.30000000000000004` where Hyprland substitutes `0.3`.
    """
    if value == int(value) and abs(value) < 1e16:
        return str(int(value))
    for precision in range(1, 10):
        text = f"{value:.{precision}g}"
        if _to_float32(float(text)) == value:
            return text
    return repr(value)


def _join_continuations(lines: list[str]) -> tuple[list[tuple[int, str]], bool]:
    """Fold backslash continuations into logical lines (`config.cpp:44-65`).

    Trailing spaces and tabs before the backslash are dropped; the next physical line is
    appended verbatim, keeping its leading whitespace. The reported line number is the
    first physical line. Returns the logical lines and whether the file ended mid
    continuation, which hyprlang reports as "Last line ends with backslash".
    """
    logical: list[tuple[int, str]] = []
    buffer: str | None = None
    start = 0
    dangling = False

    for number, raw in enumerate(lines, 1):
        text = raw.rstrip("\n").rstrip("\r")
        if buffer is None:
            buffer, start = text, number
        else:
            buffer += text
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip(" \t")
            dangling = True
        else:
            logical.append((start, buffer))
            buffer = None
            dangling = False

    if buffer is not None:
        logical.append((start, buffer))
    return logical, dangling


def _strip_comment(line: str) -> str:
    """Truncate at the first bare `#`; `##` escapes to one literal `#`.

    Runs *before* variable expansion, so a `#` arriving inside a variable's value does not
    start a comment (`config.cpp:674-706`).
    """
    out: list[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "#":
            if line.startswith("##", index):
                out.append("#")
                index += 2
                continue
            break
        out.append(char)
        index += 1
    return "".join(out)


def _is_escaped(text: str, position: int) -> bool:
    """True when an odd run of backslashes immediately precedes `position`."""
    backslashes = 0
    index = position - 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


@dataclass(slots=True)
class ParseResult:
    """Everything one parse produced: the stream, the findings, and the final variables."""

    keywords: list[Keyword] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)
    """Every file actually read, in the order it was entered."""

    @property
    def errors(self) -> list[Diagnostic]:
        """Findings hyprlang itself would have recorded -- `# hyprlang noerror` excluded."""
        return [
            diagnostic
            for diagnostic in self.diagnostics
            if diagnostic.severity is Severity.ERROR and not diagnostic.suppressed
        ]


@dataclass(slots=True)
class _SpecialBlock:
    """An open `device { ... }`-style block, accumulating fields until its `}`."""

    category: str
    origin: Origin
    fields: list[SpecialField] = field(default_factory=list)
    subcategories: list[str] = field(default_factory=list)
    """Categories opened *inside* the block. hyprlang folds these into the field path --
    `device { nested { k = 1 } }` addresses `device:nested:k` -- and the block itself
    survives the inner `}`. Verified against libhyprlang 0.6.8 directly."""


class Parser:
    """A single parse run: one entry file plus everything it `source =`s.

    Parser state is shared across sourced files exactly as hyprlang shares it -- the
    category stack, the variables, and the `noerror`/`if` flags all carry across a
    `source =` boundary (`config.cpp:1004-1056` never resets them). That is why the parser
    is a stateful object rather than a pure function over one file.
    """

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        follow_source: bool = True,
    ) -> None:
        self.environment: dict[str, str] = dict(os.environ if env is None else env)
        # Hyprland exports this around `parse()` (`ConfigManager.cpp:710-745`); rices test
        # it with `# hyprlang if` to branch on the config generation.
        self.environment.setdefault("HYPRLAND_V_0_53", "1")

        self.result = ParseResult()
        self.variables: dict[str, str] = dict(self.environment)
        """hyprlang seeds its variable list from `environ` at parse start, so `$HOME` and
        `$XDG_CONFIG_HOME` expand anywhere (`config.cpp:543-551`)."""

        self._follow_source = follow_source
        self._categories: list[str] = []
        self._special: _SpecialBlock | None = None
        self._noerror = False
        self._conditions: list[bool] = []
        self._open_files: list[Path] = []

    # -- diagnostics ------------------------------------------------------------------

    def _report(
        self,
        severity: Severity,
        code: DiagnosticCode,
        message: str,
        origin: Origin,
        text: str = "",
    ) -> None:
        self.result.diagnostics.append(
            Diagnostic(
                severity=severity,
                code=code,
                message=message,
                origin=origin,
                text=text,
                suppressed=self._noerror and severity is Severity.ERROR,
            )
        )

    def _unparsed(self, text: str, origin: Origin, code: DiagnosticCode, message: str) -> None:
        """Record a line the grammar rejected -- in the stream *and* in the diagnostics."""
        self.result.keywords.append(UnparsedLine(text=text, origin=origin, code=code))
        self._report(Severity.ERROR, code, message, origin, text)

    # -- substitution -----------------------------------------------------------------

    def _expand(self, text: str, origin: Origin) -> str:
        """Replace every `$NAME`, longest name first, until the text stops changing.

        No delimiters: `$NAMEbcd` substitutes `$NAME` when that is the longest known name
        matching. Undefined names are left as literal text -- hyprlang only replaces names
        it knows and reports nothing (`config.cpp:733-753`).
        """
        if "$" not in text:
            return text
        names = sorted(self.variables, key=len, reverse=True)
        for _ in range(MAX_ITERATIONS):
            before = text
            for name in names:
                token = "$" + name
                if token in text:
                    text = text.replace(token, self.variables[name])
            if text == before:
                return text
            if len(text) > MAX_EXPANSION_LENGTH:
                # `$a = $a$a` doubles the text every round, so hyprlang's plain
                # iteration cap is not a bound at all -- 100 rounds is 2^100 characters.
                # A length ceiling is what actually stops it, and the last bounded text
                # is more useful in a Loss report than a truncated megabyte.
                text = before
                break
        self._report(
            Severity.ERROR,
            DiagnosticCode.VARIABLE_RECURSION,
            "variable expansion did not settle; the definition is probably self-referential",
            origin,
            text,
        )
        return text

    def _evaluate_expressions(self, text: str, origin: Origin) -> str:
        """Substitute every `{{ A op B }}`, left to right (`config.cpp:619-667`)."""
        for _ in range(MAX_ITERATIONS):
            match = self._next_expression(text)
            if match is None:
                return text
            value = self._evaluate(match.group(1), origin, text)
            if value is None:
                return text
            text = text[: match.start()] + value + text[match.end() :]
        return text

    def _next_expression(self, text: str) -> re.Match[str] | None:
        for match in _EXPRESSION.finditer(text):
            if not _is_escaped(text, match.start()):
                return match
        return None

    def _evaluate(self, body: str, origin: Origin, text: str) -> str | None:
        """`A op B`, three whitespace-separated tokens, no precedence and no nesting.

        A and B are variable names *without* the `$`, or numeric literals
        (`config.cpp:627-662`).
        """
        parts = body.split()
        if len(parts) != 3 or parts[1] not in _OPERATORS:
            self._report(
                Severity.ERROR,
                DiagnosticCode.BAD_EXPRESSION,
                f"expected `A op B` with op in + - * /, got {body.strip()!r}",
                origin,
                text,
            )
            return None

        left, symbol, right = parts
        try:
            first = _to_float32(float(self.variables.get(left, left)))
            second = _to_float32(float(self.variables.get(right, right)))
        except ValueError:
            self._report(
                Severity.ERROR,
                DiagnosticCode.BAD_EXPRESSION,
                f"operand does not look like a number: {body.strip()!r}",
                origin,
                text,
            )
            return None

        if symbol == "/" and second == 0:
            self._report(
                Severity.ERROR,
                DiagnosticCode.BAD_EXPRESSION,
                f"division by zero: {body.strip()!r}",
                origin,
                text,
            )
            return None

        return _format_number(_to_float32(_OPERATORS[symbol](first, second)))

    @staticmethod
    def _unescape(text: str) -> str:
        """Only `\\{`, `\\}` and `\\\\`, right-hand side only (`config.cpp:805-827`)."""
        return text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")

    # -- files ------------------------------------------------------------------------

    def parse_file(self, path: Path, origin: Origin | None = None) -> None:
        """Read one file into the stream, following its `source =` lines."""
        resolved = Path(os.path.abspath(os.path.expanduser(str(path))))
        where = origin or Origin(resolved, 0)

        if resolved in self._open_files:
            self._report(
                Severity.ERROR,
                DiagnosticCode.SOURCE_CYCLE,
                f"source= cycle: {resolved} is already being parsed",
                where,
                str(resolved),
            )
            return

        try:
            raw = resolved.read_text(errors="replace").splitlines(keepends=True)
        except OSError as error:
            self._report(
                Severity.ERROR,
                DiagnosticCode.SOURCE_UNREADABLE,
                f"cannot read {resolved}: {error.strerror or error}",
                where,
                str(resolved),
            )
            return

        self._open_files.append(resolved)
        self.result.files.append(resolved)
        self.result.keywords.append(SourceEnter(file=resolved, origin=origin))

        depth_at_entry = len(self._categories)
        logical, dangling = _join_continuations(raw)
        if dangling:
            last = logical[-1][0] if logical else len(raw)
            self._report(
                Severity.ERROR,
                DiagnosticCode.TRAILING_BACKSLASH,
                "last line ends with a backslash",
                Origin(resolved, last),
            )

        for number, line in logical:
            self._line(line, Origin(resolved, number))

        if len(self._categories) > depth_at_entry:
            unclosed = self._categories[depth_at_entry:]
            self._report(
                Severity.ERROR,
                DiagnosticCode.UNCLOSED_CATEGORY,
                f"category not closed before end of file: {':'.join(unclosed)}",
                Origin(resolved, len(raw)),
            )
            del self._categories[depth_at_entry:]

        self.result.keywords.append(SourceLeave(file=resolved))
        self._open_files.pop()
        self.result.variables = dict(self.variables)

    # -- one logical line --------------------------------------------------------------

    def _line(self, line: str, origin: Origin) -> None:
        stripped = line.strip()
        if not stripped:
            return

        # Comment lines are still scanned for directives inside a failed `if`, which is why
        # this runs before the condition check (`config.cpp:676-684`).
        if stripped.startswith("#"):
            self._directive(stripped, origin)
            return

        if self._conditions and not self._conditions[-1]:
            return

        body = _strip_comment(stripped).strip()
        if not body:
            return

        if "=" in body:
            self._assignment(body, origin, line)
            return
        if body.endswith("{"):
            self._open_category(body[:-1].strip(), origin)
            return
        if body == "}":
            self._close_category(origin, line)
            return
        self._unparsed(line, origin, DiagnosticCode.INVALID_LINE, "invalid config line")

    def _assignment(self, body: str, origin: Origin, raw: str) -> None:
        """`key = value`, split at the first `=`; later `=` stay in the value.

        A variable's value is expanded and evaluated **eagerly**, at the point of
        definition, so a later redefinition of what it referenced does not change it.
        Verified against libhyprlang 0.6.8 directly: `$a = 1 / $b = $a / $a = 2` leaves
        `$b` as `1`, not `2`. Arithmetic runs on these lines too (`$d = {{ g * 2 }}`
        stores `10`); only unescaping is skipped (`config.cpp:730,805`).
        """
        lhs, rhs = body.split("=", 1)
        lhs, rhs = lhs.strip(), rhs.strip()
        defines_variable = lhs.startswith("$")

        rhs = self._expand(rhs, origin)
        rhs = self._evaluate_expressions(rhs, origin)

        if defines_variable:
            # A `$VAR = ...` line returns before unescaping, and its own LHS is not
            # expanded (`config.cpp:730,805`).
            name = lhs[1:]
            self.variables[name] = rhs
            self.result.keywords.append(VariableDefinition(name=name, value=rhs, origin=origin))
            return

        lhs = self._expand(lhs, origin)
        rhs = self._unescape(rhs)

        if not lhs:
            self._unparsed(raw, origin, DiagnosticCode.EMPTY_LHS, "empty lhs")
            return
        self._dispatch(lhs, rhs, origin, raw)

    def _dispatch(self, lhs: str, rhs: str, origin: Origin, raw: str) -> None:
        if self._special is not None:
            key = ":".join([*self._special.subcategories, lhs])
            self._special.fields.append(SpecialField(key=key, value=rhs, origin=origin))
            return

        handler = self._handler_for(lhs)
        if handler is not None:
            name, flags = handler
            if name == "source":
                self._source(rhs, origin)
                return
            if name in DEPRECATED_HANDLERS:
                self._report(
                    Severity.ERROR,
                    DiagnosticCode.DEPRECATED_KEYWORD,
                    f"`{name}` is the pre-0.54 spelling and is refused by Hyprland 0.56.2",
                    origin,
                    raw,
                )
            self.result.keywords.append(
                Handler(name=name, value=rhs, origin=origin, flags=flags)
            )
            return

        inline = _INLINE_KEYED.match(lhs)
        if inline is not None and inline.group(1) in SPECIAL_CATEGORIES:
            self._inline_special(inline, rhs, origin)
            return

        key = ":".join([*self._categories, lhs]) if self._categories else lhs
        orphan = not self._categories and ":" not in lhs
        if orphan:
            self._report(
                Severity.ERROR,
                DiagnosticCode.ORPHAN_KEY,
                f"`{lhs}` sits outside any category, so hyprlang never applied it",
                origin,
                raw,
            )
        self.result.keywords.append(
            Assignment(key=key, value=rhs, origin=origin, orphan=orphan)
        )

    def _handler_for(self, lhs: str) -> tuple[str, str] | None:
        """Match a handler by exact name, or by prefix for the three `allowFlags` ones.

        A `:` anywhere in the LHS disqualifies both forms, which is what keeps
        `binds:workspace_back_and_forth` a config value rather than a `bind` invocation
        (`config.cpp:844-873`).

        hyprlang calls *every* matching handler rather than stopping at the first. No two
        entries in `EXACT_HANDLERS` and `FLAG_HANDLERS` can match one LHS today -- the flag
        prefixes are mutually exclusive and none is a prefix of an exact name -- so first
        match and all matches are the same set. Adding a handler that overlaps another
        would break that, and is what would make the loop below need to return a list.
        """
        if ":" in lhs:
            return None
        if lhs in EXACT_HANDLERS:
            return (lhs, "")
        for name in FLAG_HANDLERS:
            if lhs == name:
                return (name, "")
            if lhs.startswith(name):
                return (name, lhs[len(name) :])
        return None

    def _inline_special(self, match: re.Match[str], rhs: str, origin: Origin) -> None:
        """`device[NAME]:sensitivity = 1` -- the spelling `hyprctl keyword` uses."""
        category = match.group(1)
        key_value = match.group(2)
        field_name = match.group(3)
        key_field = SPECIAL_CATEGORIES[category] or "name"
        self.result.keywords.append(
            SpecialCategory(
                category=category,
                fields=(SpecialField(key=field_name, value=rhs, origin=origin),),
                origin=origin,
                key_field=key_field,
                key_value=key_value,
                inline=True,
            )
        )

    # -- categories ---------------------------------------------------------------------

    def _open_category(self, name: str, origin: Origin) -> None:
        """Open a category, a special-category block, or a block nested inside one.

        Category names match verbatim: `Device { }` is a plain category, not the `device`
        special category. Confirmed against libhyprlang 0.6.8, which rejects it with
        "config option <Device:name> does not exist".
        """
        if not self._categories and self._special is None and name in SPECIAL_CATEGORIES:
            self._special = _SpecialBlock(category=name, origin=origin)
            return
        if self._special is not None:
            # `plugin { hyprbars { ... } }`, `device { nested { ... } }` -- any depth,
            # folded into the field path rather than closing the block.
            self._special.subcategories.append(name)
            return
        self._categories.append(name)

    def _close_category(self, origin: Origin, raw: str) -> None:
        if self._special is not None:
            if self._special.subcategories:
                self._special.subcategories.pop()
                return
            self._emit_special(self._special)
            self._special = None
            return
        if self._categories:
            self._categories.pop()
            return
        self._unparsed(raw, origin, DiagnosticCode.STRAY_CATEGORY_CLOSE, "stray category close")

    def _emit_special(self, block: _SpecialBlock) -> None:
        """Close a keyed block, checking that its first field was the key.

        hyprlang refuses a keyed block whose first field is not the key with "special
        category's first value must be the key" (`config.cpp:434-437`). The record is
        emitted either way -- the block still describes a device the user configured.
        """
        key_field = SPECIAL_CATEGORIES[block.category]
        key_value: str | None = None
        if key_field is not None:
            if not block.fields or block.fields[0].key != key_field:
                self._report(
                    Severity.ERROR,
                    DiagnosticCode.SPECIAL_KEY_NOT_FIRST,
                    f"`{block.category}` block must open with `{key_field} = ...`",
                    block.origin,
                )
            for entry in block.fields:
                if entry.key == key_field:
                    key_value = entry.value
                    break

        self.result.keywords.append(
            SpecialCategory(
                category=block.category,
                fields=tuple(block.fields),
                origin=block.origin,
                key_field=key_field,
                key_value=key_value,
            )
        )

    # -- directives ----------------------------------------------------------------------

    def _directive(self, line: str, origin: Origin) -> None:
        """`# hyprlang noerror|if|endif`; anything else is an ordinary comment.

        Exactly one `#` is stripped, so `## hyprlang endif` is a comment and not a
        directive -- confirmed against libhyprlang 0.6.8, where the `##` form leaves the
        open `if` block unclosed.
        """
        text = line[1:].strip()
        parts = text.split()
        if len(parts) < 2 or parts[0] != "hyprlang":
            return
        verb = parts[1]

        if verb == "noerror":
            argument = parts[2].lower() if len(parts) > 2 else ""
            self._noerror = argument in NOERROR_TRUTHY
        elif verb == "if":
            self._push_condition(parts[2] if len(parts) > 2 else "", origin, line)
        elif verb == "endif":
            if self._conditions:
                self._conditions.pop()
            else:
                self._report(
                    Severity.ERROR, DiagnosticCode.STRAY_ENDIF, "stray endif", origin, line
                )

    def _push_condition(self, condition: str, origin: Origin, line: str) -> None:
        """`# hyprlang if VAR` / `if !VAR`: truthy means defined and non-empty.

        The name is looked up in the environment first, then in the config's own variables
        (`config.cpp:594-614`). There is no `==` and no other operator.
        """
        negated = condition.startswith("!")
        name = condition[1:] if negated else condition
        value = self.environment.get(name, self.variables.get(name, ""))
        taken = (not bool(value)) if negated else bool(value)
        self._conditions.append(taken)
        self._report(
            Severity.WARNING,
            DiagnosticCode.CONDITIONAL_BAKED,
            f"`# hyprlang if {condition}` evaluated {taken} while importing and was baked "
            "in; Lua has no equivalent",
            origin,
            line,
        )

    # -- source = --------------------------------------------------------------------------

    def _source(self, rhs: str, origin: Origin) -> None:
        """Glob a `source =` and inline every regular file it matched.

        The path is resolved against the file currently being parsed, so a nested relative
        `source =` resolves relative to *its own* file (`ConfigManager.cpp:1802-1855`).
        """
        if not self._follow_source:
            self.result.keywords.append(Handler(name="source", value=rhs, origin=origin))
            return

        raw = rhs.strip()
        if len(raw) < 2:
            self._report(
                Severity.ERROR,
                DiagnosticCode.SOURCE_PATH_TOO_SHORT,
                f"source= path is too short: {raw!r}",
                origin,
                rhs,
            )
            return

        pattern = os.path.expanduser(raw)
        if not os.path.isabs(pattern):
            pattern = os.path.join(str(origin.file.parent), pattern)

        # glob(3) sorts by default and Hyprland does not pass GLOB_NOSORT, so the order
        # rices rely on for `animations/*.conf` is alphabetical.
        matches = sorted(glob.glob(pattern))
        if not matches:
            self._report(
                Severity.ERROR,
                DiagnosticCode.SOURCE_NO_MATCH,
                f"source= matched no file: {raw}",
                origin,
                rhs,
            )
            self.result.keywords.append(
                UnparsedLine(
                    text=f"source = {raw}",
                    origin=origin,
                    code=DiagnosticCode.SOURCE_NO_MATCH,
                )
            )
            return

        for match in matches:
            candidate = Path(match)
            if candidate.is_file():
                self.parse_file(candidate, origin)
            else:
                self._report(
                    Severity.WARNING,
                    DiagnosticCode.SOURCE_NOT_A_FILE,
                    f"source= skipped a non-regular file: {match}",
                    origin,
                    rhs,
                )


def parse(
    path: Path,
    env: Mapping[str, str] | None = None,
    *,
    follow_source: bool = True,
) -> ParseResult:
    """Parse a config tree from its entry file.

    `env` replaces the process environment as the seed for variable expansion and for
    `# hyprlang if` -- pass one explicitly when parsing someone else's config tree, so the
    importing machine's environment cannot leak into the result.
    """
    parser = Parser(env=env, follow_source=follow_source)
    parser.parse_file(path)
    return parser.result
