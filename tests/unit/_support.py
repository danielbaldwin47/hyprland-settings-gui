"""Shared helpers for the unit tier.

A plain module rather than `conftest.py`: `tests/unit` is not a package, so a conftest is
reachable as fixtures but not as an import. pytest puts this directory on `sys.path`,
which makes `from _support import ...` the working form.

Two things several schema tests need: where the shipped schema files are, and a way to
read the explicit file lists out of a `meson.build`. Both `src/meson.build` (Python
sources) and `data/meson.build` (schema data) list their files by hand -- meson says
nothing about a file no list names -- so both need the same "declared vs on disk" check,
and it is one helper rather than two copies of the same parser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hyprtweaker.engine.model import ConfigModel

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SCHEMA_DIR = ROOT / "data" / "schema"
GOLDEN_DIR = ROOT / "tests" / "golden"

SAMPLE_VERSION = "0.56.2"
"""The schema the writer fixtures are pinned to, so a new shipped schema cannot silently
rewrite the writer goldens along with the schema ones."""

SAMPLE_APP_VERSION = "0.0.0-test"
"""A fixed version for the generated-by banner: the real one would churn every golden on
every release bump, which is noise in exactly the diffs that matter."""


def sample_model() -> ConfigModel:
    """A model touching every Option type, in five Sections, for the writer goldens.

    Chosen for coverage rather than realism -- one Option per value type, both gradient
    shapes, an explicitly-null Option, an enum-mapped int set by name, an Option set to a
    value equal to its own default, and a Section whose hyprctl name has a dash in it.
    """
    from hyprtweaker.engine.model import ConfigModel
    from hyprtweaker.engine.schema import load_schema

    model = ConfigModel(load_schema(SAMPLE_VERSION, SCHEMA_DIR))

    # general: css-gaps (uniform and per-side), gradients (one stop and two), an
    # enum-string, and `border_size` set to exactly its default.
    model.set("general:border_size", model.schema["general:border_size"].default)
    model.set("general:gaps_in", 5)
    model.set("general:gaps_out", "5 10 15 20")
    model.set("general:float_gaps", None)  # explicit null: "same as outer gaps" -> -1
    model.set("general:col.active_border", "rgba(33ccffee) rgba(00ff99ee) 45deg")
    model.set("general:col.inactive_border", "595959aa")
    model.set("general:layout", "dwindle")
    model.set("general:resize_on_border", True)

    # decoration: float, vec2, a nested subtable three levels deep.
    model.set("decoration:rounding", 10)
    model.set("decoration:active_opacity", 0.95)
    model.set("decoration:shadow:offset", "0 2")
    model.set("decoration:screen_shader", None)  # explicit null: "" is a real Lua value

    # group: font weight by name, plain colour.
    model.set("group:groupbar:font_weight_active", "bold")
    model.set("group:groupbar:text_color", "#ff8800")

    # misc: an int whose -1 is a real value ("random wallpaper"), not a null.
    model.set("misc:force_default_wallpaper", -1)

    # input-capture: the one Section whose hyprctl name is not a Lua identifier.
    model.set("input-capture:enforce_barriers", True)

    return model


def meson_quoted_names(text: str, block: str) -> set[str]:
    """Every single-quoted name inside the `block` construct of a meson.build.

    `block` is a regex with one capturing group around the region to scan, e.g. a
    `foo = files(...)` call or a `foo = {...}` dict.
    """
    found = re.search(block, text, re.DOTALL | re.MULTILINE)
    assert found is not None, f"could not find /{block}/ in the meson.build"

    names = set(re.findall(r"'([^']+)'", found.group(1)))
    assert names, f"matched /{block}/ but found no quoted names -- the parser is broken"
    return names


def assert_lists_match(declared: set[str], actual: set[str], meson_file: Path) -> None:
    """Both directions: nothing uninstalled on disk, nothing installed that is gone."""
    missing = actual - declared
    assert not missing, (
        f"these files exist but {meson_file.name} does not install them "
        f"(add them to its list): {sorted(missing)}"
    )

    stale = declared - actual
    assert not stale, f"{meson_file.name} installs files that no longer exist: {sorted(stale)}"
