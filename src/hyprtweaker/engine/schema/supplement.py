"""Options the running compositor has and no shipped schema does (ADR-0012 §Pinning).

On a Hyprland newer than every shipped schema, the app loads the newest shipped one and adds
what the live `hyprctl -j descriptions` reply describes beyond it: minimal records inferred
from each record's shape alone (`infer.build_option` with no stub and no source facts), so
conservative controls, rendered flagged in a *New in <version>* group. The supplement is a
degradation state, not a schema source: `Schema.hyprland_version` stays the shipped version
the Manifest records, and the next release check replaces every one of these records.

`plugin:*` options are the same mechanism with a different kind and gate (#175).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .infer import build_option
from .resolve import Schema, available_versions, resolve_option, version_key
from .sources import SourceFacts
from .types import OptionType, ResolvedOption, Supplement, SupplementKind

_PLUGIN_PREFIX = "plugin:"

_HEX8 = re.compile(r"[0-9a-fA-F]{8}")
_COLOUR_HINT = "For example: rgba(33ccffee)"
"""The subtitle hint on a text entry whose default is printed as a colour (#213).

A supplemented colour types as STRING: its `descriptions` record has the same four keys as a
string's, and only the C++ source tells them apart. Its default prints as bare `aarrggbb`,
which Hyprland 0.56.2's Lua rejects (`invalid color "ee33ccff"`), while the string
`"rgba(33ccffee)"` loads and reads back. The hint shows the form that works. It is a hint and
not a colour control because a string whose default merely looks like a colour would be
written as a number by one; the read-back half is `values.ColorText`."""


def is_plugin_option(name: str) -> bool:
    """Whether `name` is a plugin's setting: Hyprland keeps every one under `plugin:`."""
    return name.startswith(_PLUGIN_PREFIX)


def newer_than_shipped(version: str, directory: Path | None = None) -> bool:
    """Whether `version` is newer than every shipped schema: the supplement's gate.

    Between two shipped schemas the nearer lower one is loaded and nothing is added: its
    options the compositor lacks are marked instead, and what it adds is a release the app
    already skipped on purpose (ADR-0012's support window).
    """
    newest = max(available_versions(directory), key=version_key)
    return version_key(version) > version_key(newest)


def supplement(
    schema: Schema,
    records: Iterable[dict[str, Any]],
    *,
    version: str,
    kind: SupplementKind = SupplementKind.NEWER_VERSION,
) -> Schema:
    """`schema` plus an Option for each of `kind`'s `records` it lacks.

    `records` are raw `descriptions` records (`LiveHyprland.descriptions`); `version` is the
    running Hyprland's. A newer-version supplement takes every non-`plugin:*` record and
    stamps `added_in = version`, so the Tasks view's `New in` grouping (#123) picks it up
    unchanged; a plugin supplement takes only `plugin:*` records and stamps none. Added
    Options follow every existing one in declaration order, in the reply's order.

    A record whose shape the inference rules cannot read is skipped: a newer compositor
    may print a type this app has never seen, and one unreadable setting must not cost the
    user every other one.
    """
    flag = Supplement(kind, version)
    order = max((option.order for option in schema.options), default=-1)
    added: list[ResolvedOption] = []
    for record in records:
        name = str(record.get("name", ""))
        if name in schema or name.startswith(_PLUGIN_PREFIX) != (kind is SupplementKind.PLUGIN):
            continue
        try:
            generated = build_option(record, order + 1, stub_types={}, facts=SourceFacts())
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        order += 1
        if kind is SupplementKind.NEWER_VERSION:
            generated = replace(generated, added_in=version)
        if generated.type is OptionType.STRING and _HEX8.fullmatch(str(record["default"])):
            hinted = f"{generated.description.strip()} {_COLOUR_HINT}".strip()
            generated = replace(generated, description=hinted)
        resolved = resolve_option(generated, None, schema.sections.get(generated.section))
        added.append(replace(resolved, supplement=flag))

    if not added:
        return schema
    return replace(schema, options=(*schema.options, *added))
