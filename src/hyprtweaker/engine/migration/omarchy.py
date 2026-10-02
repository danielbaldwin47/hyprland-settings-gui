"""Whether the config being imported is Omarchy's (#234).

The Lua Importer follows every `require` and `dofile`, so Omarchy's chain is flattened into
the app's own Modules. After the switch the app owns the colours, and Omarchy's theme menu
and its updates no longer reach Hyprland. The wizard says so, and this is how it knows to.

A source-text check, nothing evaluated: the entrypoint is read as text and matched for the
two lines Omarchy's shipped `hyprland.lua` carries. A source that is not a `.lua` file (a
hyprlang `.conf` has no `require`) never counts.
"""

from __future__ import annotations

import re
from pathlib import Path

_COMMENT_LINE = re.compile(r"^[ \t]*--.*$", re.MULTILINE)

_BOOTSTRAP = re.compile(
    r"""\bdofile\s*\(                  # the call
        (?:[^()]|\([^()]*\))*?         # its leading arguments, one level of calls allowed
        ["'][^"']*/default/hypr/bootstrap\.lua["']   # ending in the bootstrap path
        \s*\)                          # nothing after it: it is the last string literal
    """,
    re.VERBOSE,
)

_MODULE = re.compile(r"""\brequire\s*\(?\s*["']default(?:\.hypr\.|/hypr/)omarchy["']""")


def is_omarchy_source(source: Path | None) -> bool:
    """Whether `source` `dofile`s Omarchy's bootstrap or requires its module."""
    if source is None or source.suffix != ".lua":
        return False
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    text = _COMMENT_LINE.sub("", text)
    return bool(_BOOTSTRAP.search(text) or _MODULE.search(text))
