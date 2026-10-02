"""The Arch package, the meson build and the app agree on one floor, one id, one license.

Each fact is written down in more than one file (ADR-0019): the PyGObject floor in the
PKGBUILD, `meson.build` and `ui/shell/runtime.py`; the App id in the desktop entry, the
icons, the metainfo and `hyprtweaker.APP_ID`; the license in the PKGBUILD, `meson.build`
and the metainfo. A drift between them builds and installs fine and shows up only as a
package that installs on a machine the app refuses, or a launcher with no icon.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from configparser import ConfigParser

from _support import ROOT

from hyprtweaker import APP_ID
from hyprtweaker.ui.shell.runtime import MINIMUM_PYGOBJECT

PKGBUILD = ROOT / "packaging" / "PKGBUILD"
TOP_MESON = ROOT / "meson.build"
DATA_MESON = ROOT / "data" / "meson.build"
DESKTOP = ROOT / "data" / f"{APP_ID}.desktop"
METAINFO = ROOT / "data" / f"{APP_ID}.metainfo.xml"
ICONS = (
    f"icons/hicolor/scalable/apps/{APP_ID}.svg",
    f"icons/hicolor/symbolic/apps/{APP_ID}-symbolic.svg",
)
LICENSE = "GPL-3.0-or-later"


def pkgbuild_array(name: str) -> list[str]:
    """The words of a `name=(...)` array, quotes stripped. Read as text, never sourced."""
    found = re.search(rf"^{name}=\((.*?)\)", PKGBUILD.read_text(), re.DOTALL | re.MULTILINE)
    assert found is not None, f"no {name}=(...) array in {PKGBUILD}"
    return [word.strip("'\"") for word in found.group(1).split()]


def test_the_pkgbuild_pins_the_pygobject_floor_the_app_checks() -> None:
    assert f"python-gobject>={MINIMUM_PYGOBJECT}" in pkgbuild_array("depends")


def test_meson_setup_refuses_below_the_floor_the_app_checks() -> None:
    text = TOP_MESON.read_text()
    assert "'import gi.events'" in text, "meson.build no longer probes gi.events"
    errors = re.findall(r"error\(\s*'([^']*)'", text)
    assert any(f"PyGObject >= {MINIMUM_PYGOBJECT}" in message for message in errors), errors


def test_the_pkgbuild_depends_on_zstd_for_theme_archives() -> None:
    """#169's Theme archive compression falls back to the `zstd` binary below Python 3.14."""
    assert "zstd" in pkgbuild_array("depends")


def test_the_pkgbuild_meson_and_metainfo_agree_on_the_license() -> None:
    meson_license = re.search(r"license:\s*'([^']+)'", TOP_MESON.read_text())
    metainfo = ElementTree.parse(METAINFO).getroot()

    assert pkgbuild_array("license") == [LICENSE]
    assert meson_license is not None and meson_license.group(1) == LICENSE
    assert metainfo.findtext("project_license") == LICENSE


def test_the_desktop_entry_carries_the_fields_adr_0019_lists() -> None:
    parser = ConfigParser(interpolation=None)
    parser.optionxform = str  # keys are case-sensitive in a desktop entry
    parser.read(DESKTOP, encoding="utf-8")
    entry = parser["Desktop Entry"]

    assert {key: entry[key] for key in ("Name", "Exec", "Icon", "Categories", "Keywords")} == {
        "Name": "Hyprtweaker",
        "Exec": "hyprtweaker",
        "Icon": "io.github.danielbaldwin47.Hyprtweaker",
        "Categories": "Settings;",
        "Keywords": "Hyprland;Wayland;compositor;",
    }
    assert entry["StartupNotify"] == "true"


def test_the_metainfo_launches_the_desktop_entry_of_the_app_id() -> None:
    metainfo = ElementTree.parse(METAINFO).getroot()

    assert metainfo.findtext("id") == "io.github.danielbaldwin47.Hyprtweaker"
    assert metainfo.findtext("launchable[@type='desktop-id']") == DESKTOP.name


def test_the_desktop_entry_metainfo_and_both_icons_are_installed() -> None:
    """Separate from the `schema_files` list, which test_schema_data_installed diffs."""
    named = re.findall(r"'([^']+\.(?:desktop|metainfo\.xml|svg))'", DATA_MESON.read_text())
    shipped = {DESKTOP.name, METAINFO.name, *ICONS}

    assert sorted(named) == sorted(shipped)
    for name in shipped:
        assert (ROOT / "data" / name).is_file(), f"data/{name} is installed but missing"
