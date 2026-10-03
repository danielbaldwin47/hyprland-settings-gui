"""One home for the `entity:<kind>` Page id (#200).

The prefix keeps an Entity Page's stack name apart from a Section of the same name (`binds`,
`animations`, `gestures`: #70, #120). Spelled by hand at every site it was a string to get
wrong; now `entity_page_id` owns it, and this audit fails the build when a literal returns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from hyprtweaker.ui.pages.tasks import entity_page_id

REPO = Path(__file__).resolve().parents[2]
TREES = (REPO / "src", REPO / "data")

# A quoted string that starts with the prefix and a kind. The `"entity":` JSON key is not one.
LITERAL = re.compile(r"""["']entity:[A-Za-z_]""")


def test_entity_page_id_prefixes_the_kind() -> None:
    assert entity_page_id("binds") == "entity:binds"
    assert entity_page_id("autostart") == "entity:autostart"


def test_no_entity_id_literal_is_left_outside_the_helper() -> None:
    hits = [
        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
        for tree in TREES
        for path in sorted(tree.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".json", ".build"}
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if LITERAL.search(line)
    ]

    assert hits == [], "spell Entity Page ids with entity_page_id(kind):\n" + "\n".join(hits)


def test_tasks_json_entity_entries_carry_bare_kinds() -> None:
    mapping = json.loads((REPO / "data/schema/tasks.json").read_text())
    kinds = [
        page["entity"]
        for category in mapping["categories"]
        for page in category["pages"]
        if "entity" in page
    ]

    assert kinds, "tasks.json lists no entity Page"
    assert [kind for kind in kinds if ":" in kind] == []
