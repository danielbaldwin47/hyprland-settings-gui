"""The one writer for `data/schema/overlay.json`: entries edited as text, in its layout.

Several branches curate the Overlay at once (#157, #158, #194, #129). `json.dump` of the whole
file rewrites every line, so each branch would conflict with every other. This writer
replaces only the entries whose values change, writes each the way the file already does,
and leaves every other byte alone; a conflict then resolves by taking either side and
rerunning the script that called it.

The layout, read off the file: an entry fits on one line (`"key": { "a": 1, "b": "x" }`)
when that line is at most `WIDTH` columns before its trailing comma; otherwise each key
gets a line of its own, and a nested object or list follows the same rule one level in.
A Section entry always takes a line per key. New keys are appended after the existing
ones, which keep their order.

    import overlay_text
    text = overlay_text.set_fields(text, "options", {"input:kb_layout": {"group": "Typing"}})

`tools/curate_overlay.py` is the caller for the curated tables; use it rather than this
module directly where a table exists for the field.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal

Block = Literal["sections", "options"]

WIDTH = 100
"""The widest line an entry or nested value is written on, before its trailing comma."""

_DECODER = json.JSONDecoder()
_ENTRY_INDENT = 4
_ENTRY_KEY = re.compile(r'^ {4}("(?:[^"\\]|\\.)*"): ', re.MULTILINE)


def _block_span(text: str, block: Block) -> tuple[int, int]:
    match = re.search(rf'^  "{block}": ', text, re.MULTILINE)
    if match is None:
        raise KeyError(f"overlay has no {block!r} block")
    _, end = _DECODER.raw_decode(text, match.end())
    return match.end(), end


def entry_spans(text: str, block: Block) -> dict[str, tuple[int, int]]:
    """Each entry of `block` by name: where its `"name": {...}` text starts and ends.

    The span excludes the comma after it and the newline, so replacing it keeps both.
    """
    start, end = _block_span(text, block)
    spans: dict[str, tuple[int, int]] = {}
    for match in _ENTRY_KEY.finditer(text, start, end):
        _, value_end = _DECODER.raw_decode(text, match.end())
        spans[json.loads(match.group(1))] = (match.start(), value_end)
    return spans


def _inline(value: Any) -> str:
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        items = ", ".join(f"{_dumps(key)}: {_inline(item)}" for key, item in value.items())
        return f"{{ {items} }}"
    if isinstance(value, list):
        return "[" + ", ".join(_inline(item) for item in value) + "]"
    return _dumps(value)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _lines(
    key: str | None, value: Any, indent: int, comma: str, *, wrap: bool = False
) -> list[str]:
    head = " " * indent + (f"{_dumps(key)}: " if key is not None else "")
    line = head + _inline(value)
    if not isinstance(value, Mapping | list) or not value or (len(line) <= WIDTH and not wrap):
        return [line + comma]

    if isinstance(value, Mapping):
        items: list[tuple[str | None, Any]] = list(value.items())
        opening, closing = "{", "}"
    else:
        items = [(None, item) for item in value]
        opening, closing = "[", "]"
    lines = [head + opening]
    for index, (child_key, child) in enumerate(items):
        lines += _lines(child_key, child, indent + 2, "," if index < len(items) - 1 else "")
    return [*lines, " " * indent + closing + comma]


def format_entry(block: Block, name: str, value: Mapping[str, Any]) -> str:
    """One entry as the file writes it, without the comma that may follow it."""
    return "\n".join(_lines(name, value, _ENTRY_INDENT, "", wrap=block == "sections"))


def set_fields(text: str, block: Block, updates: Mapping[str, Mapping[str, Any]]) -> str:
    """`text` with each named entry's fields set; a field set to `None` is removed.

    An entry whose values come out unchanged is left byte-identical, so a second run with
    the same updates changes nothing. Raises `KeyError` for an entry the file lacks: a new
    Option's entry is a curation decision (its title above all), not a side effect.
    """
    payload = json.loads(text)[block]
    spans = entry_spans(text, block)
    edits: list[tuple[int, int, str]] = []
    for name, fields in updates.items():
        if name not in spans:
            raise KeyError(f"overlay has no {block} entry {name!r}")
        current = payload[name]
        merged = {
            key: value for key, value in current.items() if fields.get(key, value) is not None
        }
        for key, value in fields.items():
            if value is not None:
                merged[key] = value
        if merged != current or list(merged) != list(current):
            start, end = spans[name]
            edits.append((start, end, format_entry(block, name, merged)))

    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text
