"""Importer: hyprlang -> model, foreign Lua -> model, and the Loss report.

The hyprlang half is two stages with a seam between them (ADR-0009). `hyprlang.py` is the
**grammar core**: it reads a legacy `.conf` tree into the typed keyword stream defined in
`keywords.py`, knowing nothing about what any keyword means. Model mapping -- turning
`bind = SUPER, Q, exec, foo` into a Bind Entity -- comes after, and is where the ~20 lossy
cases of research #4 §2.11 live.

The seam is what makes the grammar testable on its own: synthetic fixtures cover the
grammar edges the rice corpus never exercises, while the corpus proves the two stages
survive real configs.

Lua import (#62) and the Loss report land here too.
"""

from __future__ import annotations

from .binds import canonical_key, canonical_mods, map_bind, map_submap, map_unbind
from .dispatchers import LEGACY_DISPATCHERS, translate_dispatcher
from .hyprlang import Parser, ParseResult, parse
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
from .loss import (
    LOSS_CODES,
    LossClass,
    LossCode,
    LossItem,
    LossReport,
)
from .mapping import ImportResult, import_config, map_keywords
from .monitors import map_monitor, map_monitor_block
from .rules import map_layer_rule, map_rule_block, map_window_rule, map_workspace_rule

__all__ = [
    "LEGACY_DISPATCHERS",
    "LOSS_CODES",
    "Assignment",
    "Diagnostic",
    "DiagnosticCode",
    "Handler",
    "ImportResult",
    "Keyword",
    "LossClass",
    "LossCode",
    "LossItem",
    "LossReport",
    "Origin",
    "ParseResult",
    "Parser",
    "Severity",
    "SourceEnter",
    "SourceLeave",
    "SpecialCategory",
    "SpecialField",
    "UnparsedLine",
    "VariableDefinition",
    "canonical_key",
    "canonical_mods",
    "import_config",
    "map_bind",
    "map_keywords",
    "map_layer_rule",
    "map_monitor",
    "map_monitor_block",
    "map_rule_block",
    "map_submap",
    "map_unbind",
    "map_window_rule",
    "map_workspace_rule",
    "parse",
    "translate_dispatcher",
]
