"""The Scripting inventory's static scan of `user.lua` and `legacy.lua` (ADR-0018, #173).

Every test writes real files under a `ConfigPaths.rooted_at(tmp_path)` layout and calls
`scan_scripting` the way the Scripting Page does, then compares the hits against literal
`(kind, name, file, line)` tuples.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.scripting import (
    CallKind,
    IndirectUse,
    ScriptingScan,
    UnfinishedText,
    discovered_layouts,
    scan_scripting,
)

USER_LUA = """\
-- My own Lua, beside the app's modules.
hl.on("workspace.active", function(ws)
  print(ws)
end)

hl.timer(function()
  local a, b = 1, 2
  if a < b then
    print("tick")
  end
end, { timeout = 500, type = "repeat" })

hl.layout.register("columns", {
  recalculate = function(ctx) end,
})
hl.plugin.load("/usr/lib/hyprland/libhyprexpo.so")
"""

LEGACY_LUA = """\
hl.on "window.open" (function() end)
hl.on ("monitor.added", function() end)
hl.timer(function() end, {type = 'oneshot', timeout = 2000})
hl.layout.register([[spiral]], {})
hl.plugin.load("/a.so", "/b.so")
"""


def paths_with(
    tmp_path: Path, *, user: str | None = None, legacy: str | None = None
) -> ConfigPaths:
    paths = ConfigPaths.rooted_at(tmp_path)
    if user is not None:
        paths.user_lua.parent.mkdir(parents=True, exist_ok=True)
        paths.user_lua.write_text(user)
    if legacy is not None:
        paths.legacy_lua.parent.mkdir(parents=True, exist_ok=True)
        paths.legacy_lua.write_text(legacy)
    return paths


def hits(scan: ScriptingScan) -> list[tuple[str, str, str, int]]:
    return [(hit.kind.value, hit.name, hit.path.name, hit.line) for hit in scan.hits]


def test_the_inventory_lists_all_four_call_kinds_with_file_and_line(tmp_path: Path) -> None:
    scan = scan_scripting(paths_with(tmp_path, user=USER_LUA, legacy=LEGACY_LUA))

    assert hits(scan) == [
        ("on", "workspace.active", "user.lua", 2),
        ("timer", "500 ms, repeat", "user.lua", 6),
        ("layout", "columns", "user.lua", 13),
        ("plugin_load", "/usr/lib/hyprland/libhyprexpo.so", "user.lua", 16),
        ("on", "window.open", "legacy.lua", 1),
        ("on", "monitor.added", "legacy.lua", 2),
        ("timer", "2000 ms, oneshot", "legacy.lua", 3),
        ("layout", "spiral", "legacy.lua", 4),
        ("plugin_load", "/a.so", "legacy.lua", 5),
        ("plugin_load", "/b.so", "legacy.lua", 5),
    ]
    assert scan.unreadable == ()
    assert scan.gaps == ()


def test_the_hits_carry_the_files_full_path(tmp_path: Path) -> None:
    paths = paths_with(tmp_path, user='hl.on("x", f)\n', legacy='hl.on("y", f)\n')

    scan = scan_scripting(paths)

    assert [hit.path for hit in scan.hits] == [paths.user_lua, paths.legacy_lua]
    assert [hit.kind for hit in scan.hits] == [CallKind.ON, CallKind.ON]


def test_missing_files_are_an_empty_result(tmp_path: Path) -> None:
    assert scan_scripting(ConfigPaths.rooted_at(tmp_path)) == ScriptingScan()


def test_calls_inside_comments_and_strings_are_not_hits(tmp_path: Path) -> None:
    user = """\
-- hl.on("comment", f)
--[[ hl.timer(f, { timeout = 1, type = "repeat" })
hl.layout.register("in block comment", {}) ]]
--[==[ hl.plugin.load("/x.so") ]] still comment ]==]
local s = 'hl.on("single", f)'
local d = "hl.on(\\"double\\", f)"
local l = [[ hl.on("long", f)
]]
local m = [=[ hl.on("leveled", f) ]] ]=]
hl.on("real", f) -- hl.on("trailing", f)
"""
    scan = scan_scripting(paths_with(tmp_path, user=user))

    assert hits(scan) == [("on", "real", "user.lua", 10)]


def test_line_numbers_survive_multi_line_comments_and_strings(tmp_path: Path) -> None:
    user = """\
--[[
one
two
]]
local text = [[
three
]]
local escaped = "four\\
five"
hl.layout.register("after", {})
"""
    scan = scan_scripting(paths_with(tmp_path, user=user))

    assert hits(scan) == [("layout", "after", "user.lua", 10)]


def test_a_name_that_is_not_written_as_text_is_still_a_hit_with_no_name(tmp_path: Path) -> None:
    user = """\
local event = "window.open"
hl.on(event, function() end)
hl.timer(function() end, opts)
hl.plugin.load(home .. "/x.so")
"""
    scan = scan_scripting(paths_with(tmp_path, user=user))

    assert hits(scan) == [
        ("on", "", "user.lua", 2),
        ("timer", "", "user.lua", 3),
        ("plugin_load", "", "user.lua", 4),
    ]


def test_a_call_through_a_local_name_or_a_loop_is_a_documented_miss(tmp_path: Path) -> None:
    """ADR-0018: arbitrary code can hide calls. The scan never runs the Lua, so a call made
    through another name, or built in a loop, is not a hit. Where the file *names* one of
    the four functions without calling it, the scan says so, so the user can look there."""
    user = """\
local on = hl.on
on("window.open", function() end)
for _, name in ipairs({ "a", "b" }) do
  hl["layout"].register(name, {})
end
"""
    scan = scan_scripting(paths_with(tmp_path, user=user))

    assert scan.hits == ()
    assert [
        (gap.kind, gap.path.name, gap.line) for gap in scan.gaps if isinstance(gap, IndirectUse)
    ] == [(CallKind.ON, "user.lua", 1)]


def test_an_unfinished_comment_keeps_the_hits_before_it_and_names_the_line(
    tmp_path: Path,
) -> None:
    user = """\
hl.on("before", f)
--[[ this comment never closes
hl.on("after", f)
"""
    scan = scan_scripting(paths_with(tmp_path, user=user))

    assert hits(scan) == [("on", "before", "user.lua", 1)]
    assert scan.gaps == (UnfinishedText(path=tmp_path / "hypr" / "user.lua", line=2),)


def test_an_unfinished_short_string_ends_at_its_line(tmp_path: Path) -> None:
    """Lua refuses the file, but everything the user wrote after the bad line is still
    worth listing: the inventory is how they find out what that file holds."""
    user = """\
local broken = "no closing quote
hl.on("after", f)
"""
    scan = scan_scripting(paths_with(tmp_path, user=user))

    assert hits(scan) == [("on", "after", "user.lua", 2)]


def test_an_unclosed_call_still_counts(tmp_path: Path) -> None:
    scan = scan_scripting(paths_with(tmp_path, user="hl.timer(function() end, { timeout = 9"))

    assert hits(scan) == [("timer", "9 ms", "user.lua", 1)]


def test_a_member_named_hl_is_not_the_hl_table(tmp_path: Path) -> None:
    scan = scan_scripting(paths_with(tmp_path, user='x.hl.on("no", f)\nself:hl.on("no", f)\n'))

    assert scan.hits == ()


def test_an_unreadable_file_is_listed_and_the_other_still_scanned(tmp_path: Path) -> None:
    paths = paths_with(tmp_path, legacy='hl.on("kept", f)\n')
    paths.user_lua.mkdir(parents=True)  # a directory where the file should be

    scan = scan_scripting(paths)

    assert scan.unreadable == (paths.user_lua,)
    assert hits(scan) == [("on", "kept", "legacy.lua", 1)]


def test_bytes_that_are_not_utf8_do_not_hide_the_file(tmp_path: Path) -> None:
    paths = paths_with(tmp_path)
    paths.user_lua.parent.mkdir(parents=True)
    paths.user_lua.write_bytes(b'-- caf\xe9\nhl.on("still", f)\n')

    scan = scan_scripting(paths)

    assert hits(scan) == [("on", "still", "user.lua", 2)]
    assert scan.unreadable == ()


def test_discovered_layouts_are_lua_names_deduplicated_and_sorted(tmp_path: Path) -> None:
    user = 'hl.layout.register("zig", {})\nhl.layout.register("alpha", {})\n'
    legacy = 'hl.layout.register("zig", {})\nhl.layout.register(name, {})\n'

    assert discovered_layouts(paths_with(tmp_path, user=user, legacy=legacy)) == (
        "lua:alpha",
        "lua:zig",
    )


@pytest.mark.parametrize("raising", ["scan_scripting", "discovered_layouts"])
def test_a_scanner_crash_leaves_the_writers_output_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raising: str
) -> None:
    """ADR-0018: the inventory is informational, never state the Writer depends on."""
    from _support import SAMPLE_APP_VERSION, sample_model

    from hyprtweaker.engine import scripting
    from hyprtweaker.engine.writer import Writer

    paths = paths_with(tmp_path, user=USER_LUA, legacy=LEGACY_LUA)
    model = sample_model()
    before = Writer(paths, SAMPLE_APP_VERSION).render_modules(model)

    def crash(*_args: object) -> object:
        raise RuntimeError("scanner bug")

    monkeypatch.setattr(scripting, raising, crash)
    monkeypatch.setattr(scripting, "_tokens", crash)

    after = Writer(paths, SAMPLE_APP_VERSION).render_modules(model)

    assert after == before
    assert len(after) > 0


def test_nothing_that_writes_imports_the_scanner() -> None:
    """The structural half of "scan misses never affect writes": the Writer, the Apply
    pipeline and the Session cannot read a scan they never import."""
    from _support import SRC

    package = SRC / "hyprtweaker"
    writers = [
        *sorted((package / "engine" / "writer").rglob("*.py")),
        *sorted((package / "engine" / "apply").rglob("*.py")),
        package / "session.py",
    ]

    importers = [path.name for path in writers if "scripting" in path.read_text()]

    assert len(writers) > 3
    assert importers == []
