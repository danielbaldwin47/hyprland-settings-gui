"""A workspace rule with a `layout_opts` table and an unknown key, written and read back (#160).

`layout_opts` values are stringified by Hyprland, but the file may hold a number or a
boolean (`lua-api-surface.md` §9): what the editor leaves untouched must come back as it
was written, and a key the catalog does not name is still a field of the rule.
"""

from __future__ import annotations

from hyprtweaker.engine.model.entities import WorkspaceRule
from hyprtweaker.engine.writer.monitors import (
    parse_monitors_module,
    render_workspace_rule,
    render_workspace_rules_module,
)

FIELDS = {
    "monitor": "DP-1",
    "frobnicate": "x y",
    "layout": "master",
    "layout_opts": {"orientation": "top", "mfact": 0.6, "new_status": False, "count": 3},
    "gaps_out": {"top": 4},
}


def test_a_rule_with_layout_opts_and_an_unknown_key_reads_back_as_written() -> None:
    rule = WorkspaceRule(workspace="special:notes", fields=FIELDS)

    text = render_workspace_rules_module([rule], app_version="0.0.0-test")
    assert text is not None
    parsed = parse_monitors_module(text, module="workspace_rules.lua")

    assert parsed.errors == ()
    assert [(r.workspace, dict(r.fields)) for r in parsed.workspace_rules] == [
        ("special:notes", FIELDS)
    ]


def test_the_layout_opts_table_is_written_in_the_order_it_was_held() -> None:
    line = render_workspace_rule(WorkspaceRule(workspace="3", fields=FIELDS))

    assert line == (
        'hl.workspace_rule({ workspace = "3", monitor = "DP-1", frobnicate = "x y", '
        'layout = "master", layout_opts = { orientation = "top", mfact = 0.6, '
        "new_status = false, count = 3 }, gaps_out = { top = 4 } })"
    )
