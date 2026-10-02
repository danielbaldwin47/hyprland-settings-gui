"""Whether the config a migration reads is Omarchy's (#234).

A source-text check: the file is read as text and never evaluated. The fixtures below are
written under the per-test home from what the research (`docs/research/theming-tools.md`)
and the ticket record of Omarchy's shipped `hyprland.lua` (a `dofile` of
`default/hypr/bootstrap.lua` and a `require` of `default.hypr.omarchy`); none of them was
read from a machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyprtweaker.engine.migration import is_omarchy_source

SHIPPED = """\
-- Omarchy's Hyprland config: defaults first, then yours.
local omarchy = os.getenv("OMARCHY_PATH") or "/usr/share/omarchy"
dofile(omarchy .. "/default/hypr/bootstrap.lua")

require("default.hypr.omarchy")

hl.config({ general = { gaps_in = 5 } })
"""

DOFILE_WITH_A_CALL = """\
dofile(os.getenv("OMARCHY_PATH") .. "/default/hypr/bootstrap.lua")
hl.config({ general = { gaps_in = 5 } })
"""


def source(tmp_path: Path, text: str, name: str = "hyprland.lua") -> Path:
    path = tmp_path / "hypr" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(SHIPPED, id="the shipped entrypoint shape"),
        pytest.param(DOFILE_WITH_A_CALL, id="a dofile whose path is built by a call"),
        pytest.param('require("default.hypr.omarchy")\n', id="require, dotted, parentheses"),
        pytest.param("require('default/hypr/omarchy')\n", id="require, slashed, single quotes"),
        pytest.param('require "default.hypr.omarchy"\n', id="require without parentheses"),
        pytest.param("require 'default/hypr/omarchy'\n", id="require, bare, single quotes"),
        pytest.param('dofile("/usr/share/omarchy/default/hypr/bootstrap.lua")\n', id="dofile"),
        pytest.param("dofile ( '/x/default/hypr/bootstrap.lua' )\n", id="dofile, spaced"),
    ],
)
def test_an_omarchy_entrypoint_counts(tmp_path: Path, text: str) -> None:
    assert is_omarchy_source(source(tmp_path, text)) is True


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("hl.config({ general = { gaps_in = 7 } })\n", id="plain lua"),
        pytest.param('require("conf.monitor")\nrequire("colors")\n', id="another rice's chain"),
        pytest.param('dofile("/etc/hypr/extra.lua")\n', id="a dofile of something else"),
        pytest.param('require("default.hypr.omarchy.extra")\n', id="a longer module name"),
        pytest.param(
            'dofile("/default/hypr/bootstrap.lua", "other")\n', id="not the last string literal"
        ),
        pytest.param(
            '-- require("default.hypr.omarchy")\n-- dofile("/default/hypr/bootstrap.lua")\n',
            id="only in comments",
        ),
        pytest.param('print("default.hypr.omarchy")\n', id="named in a string, not required"),
    ],
)
def test_another_lua_config_does_not(tmp_path: Path, text: str) -> None:
    assert is_omarchy_source(source(tmp_path, text)) is False


def test_a_hyprlang_conf_never_counts_even_when_it_names_omarchy(tmp_path: Path) -> None:
    conf = source(
        tmp_path,
        'source = ~/omarchy/default/hypr/omarchy\n# require("default.hypr.omarchy")\n',
        "hyprland.conf",
    )
    assert is_omarchy_source(conf) is False


def test_a_missing_or_absent_source_does_not(tmp_path: Path) -> None:
    assert is_omarchy_source(tmp_path / "hypr" / "hyprland.lua") is False
    assert is_omarchy_source(None) is False


def test_the_file_is_read_and_never_run(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    text = f'os.execute("touch {marker}")\n' + SHIPPED
    assert is_omarchy_source(source(tmp_path, text)) is True
    assert not marker.exists()


def test_bytes_that_are_not_utf8_do_not_stop_the_check(tmp_path: Path) -> None:
    path = tmp_path / "hypr" / "hyprland.lua"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00 -- caf\xe9\nrequire('default.hypr.omarchy')\n")
    assert is_omarchy_source(path) is True
    path.write_bytes(b"\xff\xfe\x00 -- caf\xe9\nrequire('conf.colors')\n")
    assert is_omarchy_source(path) is False
