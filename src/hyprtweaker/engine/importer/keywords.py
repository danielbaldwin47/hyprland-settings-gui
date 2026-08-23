"""The vocabulary of the hyprlang keyword stream: what a parsed `.conf` tree becomes.

The Importer is two halves. This package's grammar core (`hyprlang.py`) turns a legacy
config tree into an ordered stream of the records below; model mapping turns those records
into Options and Entities. The split is deliberate: hyprlang is a tiny line-oriented format
whose whole grammar fits in one module, while the hard, lossy work lives in the per-keyword
*value* grammars that Hyprland's legacy handlers implement (research #4 §1 vs §2). Nothing
here knows what a `bind` means -- only that a handler named `bind`, carrying flags `le`, was
invoked with a given raw value at a given file and line.

Two properties this stream guarantees, both of which downstream Loss reporting depends on:

**Lossless.** Every non-blank, non-comment line produces exactly one record. A line the
grammar cannot make sense of becomes an `UnparsedLine` carrying its verbatim text -- it is
never silently dropped (ADR-0009's Loss report cannot report what the parser threw away).

**Flat and ordered.** `source =` is inlined at the point it appears, exactly as hyprlang
does it, bracketed by `SourceEnter`/`SourceLeave` so a consumer can re-derive the file
structure. Prototype #9 found this is what makes submaps spanning files, cross-file `$var`
scoping and named-before-anonymous rule precedence trivially correct.

Diagnostics are separate from the stream. A `Diagnostic` mirrors what hyprlang itself would
have recorded as a config error, so the wizard can tell a user their config was already
broken before conversion; the stream stays complete either way.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Assignment",
    "Diagnostic",
    "DiagnosticCode",
    "Handler",
    "Keyword",
    "Origin",
    "Severity",
    "SourceEnter",
    "SourceLeave",
    "SpecialCategory",
    "SpecialField",
    "UnparsedLine",
    "VariableDefinition",
]


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a record came from: the file, and the *first* physical line of it.

    A line continued with a trailing backslash reports the line it started on, which is
    what hyprlang reports too (`config.cpp:44-65`), and what a user needs to find it.
    """

    file: Path
    line: int

    def __str__(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass(frozen=True, slots=True)
class Assignment:
    """A config value: `key = value`, with the category stack already folded into `key`.

    `general { border_size = 2 }` and `general:border_size = 2` both arrive here as
    `key="general:border_size"`, because hyprlang treats them as the same thing
    (`config.cpp:280-285`).
    """

    key: str
    value: str
    origin: Origin
    orphan: bool = False
    """True for a bare top-level key with no category (`workspace_swipe = true` at file
    scope). hyprlang rejects these as "config option does not exist" and does not apply the
    value (`config.cpp:446-449`); the local config in prototype #9 had two. Kept in the
    stream so the Loss report can say the setting was already dead."""


@dataclass(frozen=True, slots=True)
class Handler:
    """A keyword handler invocation: `bind = ...`, `exec-once = ...`, `monitor = ...`.

    hyprlang never splits the value -- each handler does its own comma splitting with its
    own arity (`config.cpp:872`), so `value` is the raw right-hand side and parsing it is
    the mapping half's job.
    """

    name: str
    """The canonical handler name (`bind`, not `bindle`)."""

    value: str
    origin: Origin
    flags: str = ""
    """Trailing flag letters for the three `allowFlags` handlers -- `bindle` -> `"le"`.
    Which letters are legal is a keybind question, not a grammar one, so they are carried
    verbatim rather than validated here (research #4 §1.7)."""


@dataclass(frozen=True, slots=True)
class VariableDefinition:
    """A `$NAME = value` definition, recorded in order.

    Variables are also *applied* by the parser (hyprlang substitutes textually at parse
    time), so every later record already has them expanded. The definitions are kept
    because ADR-0009 writes them to `vars.lua`, and because the Loss report shows the user
    which of their variables survived.
    """

    name: str
    value: str
    origin: Origin


@dataclass(frozen=True, slots=True)
class SpecialField:
    """One `key = value` inside a special-category block."""

    key: str
    value: str
    origin: Origin


@dataclass(frozen=True, slots=True)
class SpecialCategory:
    """A keyed-category instance: `device { name = x, ... }` or `device[x]:key = v`.

    Hyprland registers five of these (`ConfigManager.cpp:518-626`). Keyed ones identify an
    instance by the value of one field; two blocks naming the same key target the same
    instance with per-field last-wins. That merge is a model concern, so the stream emits
    one record per block and leaves the merging to the mapping half.
    """

    category: str
    fields: tuple[SpecialField, ...]
    origin: Origin
    key_field: str | None = None
    """The field that identifies the instance -- `name` for `device`, `output` for
    `monitorv2`. None for `plugin`, which is static rather than keyed."""

    key_value: str | None = None
    inline: bool = False
    """True for the `device[x]:key = v` spelling, which carries exactly one field."""


@dataclass(frozen=True, slots=True)
class SourceEnter:
    """Opens the records that came from a `source =`d file (or the root file)."""

    file: Path
    origin: Origin | None = None
    """The `source =` line that pulled this file in; None for the entry file."""


@dataclass(frozen=True, slots=True)
class SourceLeave:
    """Closes the matching `SourceEnter`."""

    file: Path


@dataclass(frozen=True, slots=True)
class UnparsedLine:
    """A line the grammar could not make sense of, preserved verbatim.

    This is the record that makes the stream lossless. hyprlang would report a config error
    and move on, dropping the line; the Importer keeps it so the Loss report can show the
    user the exact text, file and line of everything conversion could not carry over
    (ADR-0009's Breakage class).
    """

    text: str
    origin: Origin
    code: DiagnosticCode


Keyword = (
    Assignment
    | Handler
    | VariableDefinition
    | SpecialCategory
    | SourceEnter
    | SourceLeave
    | UnparsedLine
)
"""One record in the keyword stream."""


class Severity(enum.StrEnum):
    ERROR = "error"
    """hyprlang would have recorded a config error here."""

    WARNING = "warning"
    """Parsed, but something was lost or decided in a way the user must see."""


class DiagnosticCode(enum.StrEnum):
    """Closed set of grammar-level findings.

    Deliberately separate from the `L*` lossy-case codes of research #4 §2.11, which
    describe *mapping* losses (bind flags, rule syntax, dispatcher arguments). These are
    the things that can go wrong while a config tree is still just text.
    """

    EMPTY_LHS = "empty-lhs"
    INVALID_LINE = "invalid-line"
    STRAY_CATEGORY_CLOSE = "stray-category-close"
    UNCLOSED_CATEGORY = "unclosed-category"
    TRAILING_BACKSLASH = "trailing-backslash"
    ORPHAN_KEY = "orphan-key"
    DEPRECATED_KEYWORD = "deprecated-keyword"

    VARIABLE_RECURSION = "variable-recursion"
    BAD_EXPRESSION = "bad-expression"

    STRAY_ENDIF = "stray-endif"
    CONDITIONAL_BAKED = "conditional-baked"
    """A `# hyprlang if` was evaluated against the environment the Importer ran in, and its
    branch baked into the output. Lua has no equivalent, so the wizard must show the user
    each condition and the branch taken (ADR-0009 "Needs review")."""

    SOURCE_NO_MATCH = "source-no-match"
    SOURCE_PATH_TOO_SHORT = "source-path-too-short"
    SOURCE_NOT_A_FILE = "source-not-a-file"
    SOURCE_UNREADABLE = "source-unreadable"
    SOURCE_CYCLE = "source-cycle"

    SPECIAL_KEY_NOT_FIRST = "special-key-not-first"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One finding, mirroring a hyprlang config error or an Importer decision."""

    severity: Severity
    code: DiagnosticCode
    message: str
    origin: Origin
    text: str = ""
    suppressed: bool = False
    """True when `# hyprlang noerror` was in force. hyprlang suppresses *recording* the
    error but still parses the line (`config.cpp:1034`), so the finding is kept and flagged
    rather than discarded -- the user asked their config not to complain, not for the
    Importer to go blind."""
