"""No GSettings anywhere in the app (#78, ADR-0019).

GSettings needs a dconf daemon, and without one its memory backend accepts every write and
drops it: preferences that vanish on a minimal Hyprland box. App preferences go in the Prefs
file (`engine/prefs.py`). This reads the code, not the prose, so a docstring that explains
why GSettings is absent does not trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def gsettings_uses(source: str) -> list[int]:
    """The line of every `Gio.Settings*` reference or import in `source`."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "Gio"
            and node.attr.startswith("Settings")
        )
        or (isinstance(node, ast.ImportFrom) and node.module == "gi.repository.Gio")
    )


def test_the_fence_sees_code_and_ignores_prose() -> None:
    source = (
        '"""Plain JSON, never Gio.Settings."""\n'
        "from gi.repository import Gio\n"
        "# Gio.Settings would drop writes without dconf\n"
        'settings = Gio.Settings.new("io.example")\n'
        "from gi.repository.Gio import Settings\n"
    )

    assert gsettings_uses(source) == [4, 5]


def test_nothing_under_src_uses_gsettings() -> None:
    offenders = {
        str(path.relative_to(SRC)): found
        for path in sorted(SRC.rglob("*.py"))
        if (found := gsettings_uses(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}, "app preferences go in the Prefs file (ADR-0019), never GSettings"
