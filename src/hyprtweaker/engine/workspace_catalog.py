"""The fields of a workspace rule, headless (ADR-0008 § Workspace rules, #160).

`hl.workspace_rule` takes 16 fields and a `layout_opts` table
(`docs/research/lua-api-surface.md` §9, STUB:603-622). They are declared here by hand, the
way `rules_catalog` and `monitors_catalog` declare theirs: until entity-schema rows land
(#148 defers them), a hand-written catalog is the one place a field's type, shelf and
opening value live, and `tests/unit/test_workspace_catalog.py` keeps it level with the
Importer's legacy-name tables.

Descriptive, not prescriptive: a key the catalog does not name is still a field of the
rule. The editor shows it as a raw key/value row and writes it back untouched (ADR-0008:
unknown fields are never dropped).
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

BUILTIN_LAYOUTS: tuple[str, ...] = ("dwindle", "master", "scrolling", "monocle")
"""The layouts Hyprland ships, for a session whose schema does not list them. A rule may
also name a Lua layout (`lua:<name>`); the editor offers those the compositor reports
(#175) and keeps any layout the file already names."""


def layout_choices(known: Iterable[str]) -> tuple[str, ...]:
    """The layouts a layout row offers, from the schema's known values: without the
    `lua:<name>` placeholder, which names no layout, and the shipped ones when the schema
    lists none."""
    named = tuple(choice for choice in known if "<" not in choice)
    return named or BUILTIN_LAYOUTS


LAYOUT_OPTS = "layout_opts"
"""The table of layout-specific options (`{ orientation = "top" }`). Not a catalog field:
its keys depend on the layout, so the editor treats it as a free key/value table."""


class WorkspaceFieldType(enum.Enum):
    BOOL = "bool"
    INT = "int"
    STRING = "string"
    GAPS = "gaps"
    """A css-gap value: one number for every side, or `{top, right, bottom, left}`."""
    LAYOUT = "layout"
    """A layout name: a string picked from the layouts Hyprland offers, open to more."""


SHELVES: tuple[str, ...] = ("Placement", "Gaps", "Appearance", "Layout")
"""The editor's groups, in order."""


@dataclass(frozen=True, slots=True)
class WorkspaceField:
    """One field of a workspace rule: its Lua key, its type, where it sits in the editor."""

    name: str
    type: WorkspaceFieldType
    title: str
    shelf: str
    help: str = ""
    starts_at: Any = None
    """What the row holds when the user adds the field. A key the user adds is a key they
    want written, so this is the value they most likely came for, not the type's zero:
    nobody adds "Keep alive when empty" to switch it off."""
    minimum: int = 0
    maximum: int = 99


BOOL, INT, STRING, GAPS = (
    WorkspaceFieldType.BOOL,
    WorkspaceFieldType.INT,
    WorkspaceFieldType.STRING,
    WorkspaceFieldType.GAPS,
)

WORKSPACE_FIELDS: tuple[WorkspaceField, ...] = (
    WorkspaceField(
        "monitor", STRING, "Monitor", "Placement", "An output name such as DP-1, or desc:…", ""
    ),
    WorkspaceField(
        "default",
        BOOL,
        "Default for its monitor",
        "Placement",
        "Shown first when the monitor starts",
        True,
    ),
    WorkspaceField(
        "persistent", BOOL, "Keep when empty", "Placement", "Stays open with no windows", True
    ),
    WorkspaceField(
        "default_name",
        STRING,
        "Display name",
        "Placement",
        "What the workspace is called when it has no name",
        "",
    ),
    WorkspaceField(
        "on_created_empty",
        STRING,
        "Run when created empty",
        "Placement",
        "A command, such as [float] firefox",
        "",
    ),
    WorkspaceField(
        "enabled",
        BOOL,
        "Rule enabled",
        "Placement",
        "Turn off to keep the rule without applying it",
        False,
    ),
    WorkspaceField("gaps_in", GAPS, "Gaps between windows", "Gaps", starts_at=0),
    WorkspaceField("gaps_out", GAPS, "Gaps to the screen edge", "Gaps", starts_at=0),
    WorkspaceField("float_gaps", GAPS, "Gaps around floating windows", "Gaps", starts_at=0),
    WorkspaceField("border_size", INT, "Border size", "Appearance", starts_at=1),
    WorkspaceField("no_border", BOOL, "No borders", "Appearance", starts_at=True),
    WorkspaceField("no_rounding", BOOL, "No rounded corners", "Appearance", starts_at=True),
    WorkspaceField("no_shadow", BOOL, "No shadows", "Appearance", starts_at=True),
    WorkspaceField("decorate", BOOL, "Window decorations", "Appearance", starts_at=False),
    WorkspaceField(
        "animation", STRING, "Animation style", "Appearance", "Such as slide or slidevert", ""
    ),
    WorkspaceField(
        "layout", WorkspaceFieldType.LAYOUT, "Layout", "Layout", starts_at="dwindle"
    ),
)

_BY_NAME = {field.name: field for field in WORKSPACE_FIELDS}


def find_field(name: str) -> WorkspaceField | None:
    return _BY_NAME.get(name)


def retype_like(original: Any, text: str) -> Any:
    """Text typed over a held value, as the type the value had while it still reads as one.

    A raw row edits keys the catalog does not know, so the only evidence of a type is the
    value the file held: a number retyped as `"6"` would turn into a string in the file.
    Text that no longer reads as that type stays text, as typed.
    """
    stripped = text.strip()
    try:
        if isinstance(original, bool):
            if stripped.lower() in ("true", "false"):
                return stripped.lower() == "true"
        elif isinstance(original, int):
            return int(stripped)
        elif isinstance(original, float):
            return float(stripped)
    except ValueError:
        pass
    return stripped
