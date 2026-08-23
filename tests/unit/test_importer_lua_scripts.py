"""Reading a closure back out of its source file.

`debug.getinfo` gives a line range, not an expression: the last line of a function usually
has more on it than the closing `end`. So the extractor scans for the `end` that closes the
opening `function` -- and every test here is a shape that broke a naive version of that
scan, either in the prototype or while porting it.

The lifted text is checked by *running* it wherever it matters: an extract that parses but
means something else would pass a string comparison and fail a user.
"""

from __future__ import annotations

import subprocess

import pytest
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.importer.lua import Consent, import_lua, lua_binary
from hyprtweaker.engine.importer.lua.scripts import luac_binary
from hyprtweaker.engine.schema import load_schema

pytestmark = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter installed")

GRANTED = Consent(evaluate=True)


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema(SAMPLE_VERSION, SCHEMA_DIR)


@pytest.fixture
def lifted(tmp_path, schema):  # type: ignore[no-untyped-def]
    """Import a config and hand back what landed in `legacy.lua`."""

    def run(body: str) -> str:
        entry = tmp_path / "hyprland.lua"
        entry.write_text(body, encoding="utf-8")
        return import_lua(entry, schema, consent=GRANTED).legacy

    return run


def parses(text: str) -> bool:
    """Whether Lua itself accepts the lifted text -- the only opinion that counts."""
    binary = luac_binary()
    if binary is None:  # pragma: no cover - luac ships with lua everywhere we test
        pytest.skip("no luac to check with")
    completed = subprocess.run(
        [binary, "-p", "-"], input=text, capture_output=True, text=True, check=False
    )
    return completed.returncode == 0


def test_the_lifted_file_is_valid_lua(lifted) -> None:  # type: ignore[no-untyped-def]
    text = lifted('hl.on("hyprland.start", function() hl.exec_cmd("waybar") end)\n')

    assert parses(text), text


def test_a_handler_ending_mid_line_is_cut_at_its_own_end(lifted) -> None:  # type: ignore[no-untyped-def]
    """The line range ends on a line that continues past the function."""
    text = lifted(
        'local handlers = { start = function() hl.exec_cmd("waybar") end, other = 1 }\n'
        'hl.on("hyprland.start", handlers.start)\n'
    )

    assert parses(text), text
    assert "other = 1" not in text, "the extract ran past the end of the function"


def test_the_word_end_inside_a_string_does_not_end_the_function(lifted) -> None:  # type: ignore[no-untyped-def]
    text = lifted(
        'hl.on("hyprland.start", function()\n'
        "  hl.exec_cmd(\"notify-send 'the end'\")\n"
        '  hl.exec_cmd("second")\n'
        "end)\n"
    )

    assert parses(text), text
    assert "second" in text, "the extract stopped at an 'end' inside a string"


def test_a_comment_marker_inside_a_long_bracket_does_not_start_a_comment(lifted) -> None:  # type: ignore[no-untyped-def]
    """`--` inside `[[ ... ]]` is text, not a comment. Treating it as one blanks the rest
    of the line, taking the function's own `end` with it.

    One line, for the reason spelled out in the `for` test below: a function ending on its
    own line hides a broken scan behind the raw-line-range fallback.
    """
    text = lifted(
        'hl.on("start", function() local s = [[ echo -- not a comment ]] hl.exec_cmd(s) end) '
        "hl.config({ decoration = { rounding = 3 } })\n"
    )

    assert parses(text), text
    assert "hl.exec_cmd(s)" in text
    assert "hl.config" not in text, "the scan lost the function's end inside a long bracket"


def test_a_for_loop_is_one_block_and_not_two(lifted) -> None:  # type: ignore[no-untyped-def]
    """`for ... do` opens one block. Counting `for` and `do` separately leaves the depth
    permanently off, so the closing `end` is never recognised.

    Written on one line on purpose. When the scan finds no `end` the extractor falls back
    to the raw `debug` line range -- which is *correct* for a function ending on a line of
    its own, and would let a broken scan pass unnoticed. Here the line continues past the
    function, so only a working scan cuts in the right place.
    """
    text = lifted(
        'hl.on("start", function() for i = 1, 3 do hl.exec_cmd("t" .. i) end end) '
        "hl.config({ decoration = { rounding = 3 } })\n"
    )

    assert parses(text), text
    assert "hl.exec_cmd" in text, "the loop body was lost -- the scan never found its end"
    assert "hl.config" not in text, "the extract ran past the function into the next call"


def test_a_while_loop_and_a_repeat_both_close_properly(lifted) -> None:  # type: ignore[no-untyped-def]
    text = lifted(
        'hl.on("hyprland.start", function()\n'
        "  local i = 0\n"
        "  while i < 2 do i = i + 1 end\n"
        "  repeat i = i - 1 until i == 0\n"
        "end)\n"
    )

    assert parses(text), text


def test_a_named_function_becomes_an_anonymous_expression(lifted) -> None:  # type: ignore[no-untyped-def]
    """A handler defined as `function M.start()` has to be lifted as a value, not as a
    statement that would redefine a table nothing here declares."""
    text = lifted(
        "local M = {}\n"
        'function M.start() hl.exec_cmd("waybar") end\n'
        'hl.on("hyprland.start", M.start)\n'
    )

    assert parses(text), text
    assert "function M.start" not in text


def test_nested_closures_bring_their_own_upvalues(lifted) -> None:  # type: ignore[no-untyped-def]
    text = lifted(
        """
        local terminal = "kitty"
        local function launch() hl.exec_cmd(terminal) end
        hl.on("hyprland.start", function() launch() end)
        """
    )

    assert parses(text), text
    assert 'local terminal = "kitty"' in text
    assert "local launch =" in text


def test_a_table_upvalue_is_carried_over_whole(lifted) -> None:  # type: ignore[no-untyped-def]
    text = lifted(
        """
        local apps = { "waybar", "dunst" }
        hl.on("hyprland.start", function()
          for _, app in ipairs(apps) do hl.exec_cmd(app) end
        end)
        """
    )

    assert parses(text), text
    assert '"waybar"' in text and '"dunst"' in text


# --- foreign globals ----------------------------------------------------------------------


@pytest.mark.skipif(luac_binary() is None, reason="no luac to read bytecode with")
def test_a_closure_reading_a_foreign_global_needs_review(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    """The one hole the ADR does not claim to close: the lifted text still names something
    that was a global of the *original* config, and lifting cannot bring it along."""
    entry = tmp_path / "hyprland.lua"
    entry.write_text(
        "theme = { terminal = 'kitty' }\n"
        'hl.on("hyprland.start", function() hl.exec_cmd(theme.terminal) end)\n',
        encoding="utf-8",
    )

    result = import_lua(entry, schema, consent=GRANTED)

    assert "L33" in {item.code.value for item in result.loss}
    assert any("theme" in item.message for item in result.loss)


@pytest.mark.skipif(luac_binary() is None, reason="no luac to read bytecode with")
def test_a_closure_reading_only_its_upvalues_and_hl_is_clean(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    """The detector must not cry wolf: `hl` and the stdlib are always there, and an
    upvalue is materialised right above the body."""
    entry = tmp_path / "hyprland.lua"
    entry.write_text(
        "local terminal = 'kitty'\n"
        'hl.on("hyprland.start", function()\n'
        "  hl.exec_cmd(string.format('%s -e tmux', terminal))\n"
        "end)\n",
        encoding="utf-8",
    )

    result = import_lua(entry, schema, consent=GRANTED)

    assert "L33" not in {item.code.value for item in result.loss}


@pytest.mark.skipif(luac_binary() is None, reason="no luac to read bytecode with")
def test_a_name_only_mentioned_in_a_string_is_not_a_foreign_global(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    """Reading the bytecode rather than the text is what buys this."""
    entry = tmp_path / "hyprland.lua"
    entry.write_text(
        'hl.on("hyprland.start", function() hl.exec_cmd("echo theme") end)\n',
        encoding="utf-8",
    )

    result = import_lua(entry, schema, consent=GRANTED)

    assert "L33" not in {item.code.value for item in result.loss}
