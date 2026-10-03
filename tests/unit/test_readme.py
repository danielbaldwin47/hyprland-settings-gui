"""The root README and the v1 acceptance record say what the code and the package say (#257).

The README states several facts that live elsewhere: the Hyprland, PyGObject and libadwaita
floors, the schemas that ship, the package's dependencies, the project's links. A number
written twice drifts; these tests pin each copy to its source, so a floor raised in
`runtime.py` or a schema added to `data/` fails here rather than leaving a public claim
behind. They read text only and need no toolkit.
"""

from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as ElementTree

from _support import ROOT

from hyprtweaker import APP_ID
from hyprtweaker.engine.schema.resolve import MINIMUM_HYPRLAND
from hyprtweaker.ui.shell.runtime import MINIMUM_LIBADWAITA, MINIMUM_PYGOBJECT

README = ROOT / "README.md"
ACCEPTANCE = ROOT / "docs" / "v1-acceptance.md"
PKGBUILD = ROOT / "packaging" / "PKGBUILD"
METAINFO = ROOT / "data" / f"{APP_ID}.metainfo.xml"
SCHEMAS = ROOT / "data" / "schema"

CHECKS = (
    "Installed package lifecycle",
    "Spare-account or VM conversion",
    "Changed recovery paths",
    "Real colour and wallpaper integrations",
    "Multi-monitor Keep and Revert",
    "HDR",
    "Plugins",
)
"""The seven checks the ticket's acceptance matrix lists, one row each."""


def readme() -> str:
    return README.read_text()


def pkgbuild_array(name: str) -> list[str]:
    found = re.search(rf"^{name}=\((.*?)\)", PKGBUILD.read_text(), re.DOTALL | re.MULTILINE)
    assert found is not None, f"no {name}=(...) array in {PKGBUILD}"
    return shlex.split(found.group(1))


def package_names(array: list[str]) -> list[str]:
    """Package names of a PKGBUILD array: `python-gobject>=3.50` is `python-gobject`, and an
    optdepends `hyprland: the compositor` is `hyprland`."""
    return [re.split(r"[>=<:]", word)[0] for word in array]


def test_the_readme_states_the_floors_the_app_checks() -> None:
    text = readme()
    assert f"Hyprland {MINIMUM_HYPRLAND}" in text
    assert f"python-gobject {MINIMUM_PYGOBJECT}" in text
    assert f"libadwaita {MINIMUM_LIBADWAITA}" in text


def test_the_readme_names_the_schemas_that_ship_and_no_others() -> None:
    shipped = sorted(
        re.search(r"hyprland-(.+)\.json", path.name).group(1)  # type: ignore[union-attr]
        for path in SCHEMAS.glob("hyprland-*.json")
    )
    text = readme()
    for version in shipped:
        assert version in text, f"the README does not name the shipped schema {version}"
    named = sorted(set(re.findall(rf"{re.escape(MINIMUM_HYPRLAND)}\.\d+", text)))
    assert named == shipped, f"the README names Hyprland {named}, the package ships {shipped}"


def test_the_readme_lists_every_dependency_and_optional_tool_of_the_package() -> None:
    text = readme()
    for name in package_names(pkgbuild_array("depends")) + package_names(
        pkgbuild_array("optdepends")
    ):
        assert f"`{name}`" in text, f"the README does not name the package dependency {name}"


def test_the_readme_links_where_the_package_says_the_project_is() -> None:
    root = ElementTree.parse(METAINFO).getroot()
    text = readme()
    for kind in ("homepage", "bugtracker"):
        url = next(node.text for node in root.iter("url") if node.get("type") == kind)
        assert url is not None and url in text, f"the README does not link the {kind} {url}"


def test_every_relative_link_in_the_readme_and_the_record_leads_somewhere() -> None:
    for page in (README, ACCEPTANCE):
        for target in re.findall(r"\]\((?!https?:|#)([^)#]+)", page.read_text()):
            assert (page.parent / target).exists(), (
                f"{page.name} links {target}, which is missing"
            )


def test_the_readme_links_the_acceptance_record() -> None:
    assert "docs/v1-acceptance.md" in readme()


def test_the_readme_promises_nothing_the_evidence_does_not_hold() -> None:
    """#253 proves one rice byte-identical to its own port, not a pixel-lossless conversion;
    #257 puts the AUR out of scope; ADR-0019 defers Flatpak. Each banned promise is paired
    with the sentence that says the truth instead, so an empty README fails (review m1
    F18)."""
    text = readme().lower()
    for claim, truth in (
        ("pixel-lossless", "conversion is not guaranteed lossless."),
        ("lossless conversion", "conversion is not guaranteed lossless."),
        ("yay -s", "there is no aur package"),
        ("paru -s", "there is no aur package"),
        ("flatpak install", "and no flatpak."),
    ):
        assert claim not in text, f"the README promises {claim!r}"
        assert truth in text, f"the README no longer says {truth!r}"


def test_the_record_has_one_row_per_check_with_a_result_that_is_never_invented() -> None:
    rows = {
        line.split("|")[1].strip(): line
        for line in ACCEPTANCE.read_text().splitlines()
        if line.startswith("| ") and not line.startswith("| Check")
    }
    for check in CHECKS:
        found = [row for name, row in rows.items() if name.startswith(check)]
        assert found, f"the record has no row for {check!r}"
    for name, row in rows.items():
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        who, result, date = cells[2], cells[4], cells[5]
        if who == "owner":
            # The owner replaces "Not yet run" with the result and the date (the record's
            # own instruction, review m1 F16); an agent never fills an owner row.
            filled = re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) is not None
            assert result.startswith("Not yet run") or filled, (
                f"{name}: an owner check carries {result!r} with no date"
            )
