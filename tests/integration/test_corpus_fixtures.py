"""The rice corpus as Harness fixtures: pinned, stageable, and disarmed.

`tests/corpus/` is seven real config trees pinned to upstream commits (issue #17). These
tests guard the properties the Harness relies on when it boots one, and most of them are
about the corpus staying *reproducible* -- a fixture nobody can re-fetch is a fixture that
quietly becomes a local artefact the moment someone edits it.

Only the last test needs a compositor. The rest are metadata and file-tree checks, so they
run on any machine that collects this directory and give a developer without Hyprland a
reason to have run the tier at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import NestedHyprland, capture, rice, rices, rices_with_ground_truth, stage
from harness.corpus import (
    COMMENT_PREFIX,
    CORPUS_DIR,
    DISARM_MARKER,
    LUA_EXEC_LINE,
    exec_line_pattern,
    load_lock,
)

#: end-4 and ML4W were captured mid-migration and ship upstream's own hand-written Lua beside
#: the `.conf` at the same commit. They are the only human translations of these configs that
#: exist -- prototype #9 §7 judged the mechanical conversion against them and beat both.
EXPECTED_GROUND_TRUTH = {"end-4", "ml4w"}

#: The one rice with no upstream: it is the box the corpus was captured on.
UNPINNED = {"local"}


def test_every_locked_rice_is_on_disk() -> None:
    """The lock file and the checked-in trees must not drift apart."""
    locked = set(load_lock())
    present = {candidate.name for candidate in rices()}
    assert locked == present, f"lock file and corpus disagree: {locked ^ present}"


def test_every_rice_on_disk_is_in_the_lock_file() -> None:
    """A tree nobody pinned cannot be re-fetched, and silently becomes local-only."""
    locked = set(load_lock())
    on_disk = {
        path.name
        for path in CORPUS_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    missing = on_disk - locked
    assert not missing, f"corpus directories missing from the lock file: {missing}"


def test_every_upstream_rice_is_pinned_to_a_commit() -> None:
    """`fetch.sh` reproduces a tree from its commit; without one the fixture is frozen."""
    unpinned = {candidate.name for candidate in rices() if not candidate.pinned}
    assert unpinned == UNPINNED, f"unexpectedly unpinned rices: {unpinned - UNPINNED}"


def test_every_rice_has_the_entrypoint_the_harness_will_boot() -> None:
    """`hyprland.conf` at the rice root is the corpus's layout convention."""
    missing = [candidate.name for candidate in rices() if not candidate.entrypoint.is_file()]
    assert not missing, f"rices with no hyprland.conf: {missing}"


def test_ground_truth_lua_ports_are_found_where_upstream_shipped_them() -> None:
    """Absence is a fact about the corpus, so it is asserted rather than tolerated."""
    found = {candidate.name for candidate in rices_with_ground_truth()}
    assert found == EXPECTED_GROUND_TRUTH, (
        f"ground-truth Lua ports changed: expected {EXPECTED_GROUND_TRUTH}, found {found}"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_GROUND_TRUTH))
def test_a_ground_truth_port_sits_beside_the_conf_it_translates(name: str) -> None:
    """Reference output, not input: the pair must be the same tree at the same commit."""
    candidate = rice(name)
    port = candidate.ground_truth_lua
    assert port is not None and port.is_file()
    assert port.parent == candidate.entrypoint.parent


def test_staging_reproduces_the_home_layout_a_rice_expects(tmp_path: Path) -> None:
    """`source = ~/.config/hypr/...` has to resolve to the fixture, not to the developer."""
    staged = stage(rice("hyde"), tmp_path / "home")

    assert staged.entrypoint.is_file()
    assert staged.hypr_dir == staged.home / ".config" / "hypr"
    # HyDE's real entry config lives outside ~/.config/hypr; the corpus keeps it under
    # `_home/` and staging must put it back where the config's source= lines look for it.
    assert (staged.home / ".local" / "share" / "hyde" / "hyprland.conf").is_file()


def surviving_exec_lines(home: Path) -> list[str]:
    """Every load-time exec still live under a staged home, in either config language."""
    live = []
    for suffix in COMMENT_PREFIX:
        pattern = exec_line_pattern(suffix)
        assert pattern is not None
        live.extend(
            f"{path}:{number}: {line.strip()}"
            for path in home.rglob(f"*{suffix}")
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
            if pattern.match(line)
        )
    return live


@pytest.mark.parametrize("name", ["jakoolit", "end-4", "ml4w"])
def test_staging_disarms_every_exec_line(name: str, tmp_path: Path) -> None:
    """A rice's autostart must never run: it would reach out of the nested compositor.

    Several corpus rices autostart tools that rewrite `~/.config`, so this is the check that
    keeps the tier from editing the developer's own dotfiles.
    """
    staged = stage(rice(name), tmp_path / "home")
    live = surviving_exec_lines(staged.home)
    assert not live, f"exec lines survived staging: {live[:5]}"


@pytest.mark.parametrize("name", sorted(EXPECTED_GROUND_TRUTH))
def test_staging_disarms_autostart_in_the_lua_port_too(name: str, tmp_path: Path) -> None:
    """The regression guard for a real bug: the booted file is the `.lua`, not the `.conf`.

    Both ground-truth ports carry `hl.exec_cmd` autostart -- `gnome-keyring-daemon`,
    `hypridle`, `wl-paste --watch`, a wallpaper restorer. A sweep that globbed `*.conf` only
    left exactly the file `test_a_ground_truth_lua_port_boots_in_the_nested_compositor`
    boots fully armed, so this asserts the Lua half specifically rather than trusting the
    combined check above to have covered it.
    """
    staged = stage(rice(name), tmp_path / "home")

    lua_files = list(staged.home.rglob("*.lua"))
    assert lua_files, "no Lua files staged at all -- the sweep would pass vacuously"

    live = [
        f"{path}:{number}"
        for path in lua_files
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
        if LUA_EXEC_LINE.match(line)
    ]
    assert not live, f"Lua autostart survived staging: {live[:5]}"


def test_the_disarm_sweep_leaves_bind_dispatchers_alone(tmp_path: Path) -> None:
    """Disarming must not silently rewrite what the config *declares*.

    `hl.dsp.exec_cmd(...)` inside `hl.bind(...)` is a dispatcher factory that runs only when
    a key is pressed. Commenting it out would drop keybinds from the config while disarming
    nothing, quietly changing the very state this tier diffs.
    """
    staged = stage(rice("end-4"), tmp_path / "home")

    lines = [
        line
        for path in staged.home.rglob("*.lua")
        for line in path.read_text(errors="replace").splitlines()
    ]
    binds_with_exec = [
        line for line in lines if "hl.dsp.exec_cmd" in line and "hl.bind" in line
    ]
    assert binds_with_exec, "fixture no longer exercises this: no bind-with-exec lines found"

    # The staged tree already contains binds upstream itself commented out, so "starts with
    # --" proves nothing. The harness marker is what identifies a line *this sweep* touched.
    disarmed_binds = [line for line in binds_with_exec if DISARM_MARKER in line]
    assert not disarmed_binds, f"the sweep commented out keybinds: {disarmed_binds[:3]}"


def test_staging_copies_rather_than_boots_the_checked_in_tree(tmp_path: Path) -> None:
    """The corpus is checked in; a harness that mutated it in place would dirty the repo."""
    source = rice("jakoolit").entrypoint
    original = source.read_bytes()
    stage(rice("jakoolit"), tmp_path / "home")
    assert source.read_bytes() == original


@pytest.mark.hyprland
def test_a_ground_truth_lua_port_boots_in_the_nested_compositor(
    tmp_path: Path, artifacts: Path
) -> None:
    """The corpus fixtures and the nested compositor actually meet.

    Upstream's own Lua port is a real-world config far larger than anything this suite
    writes, so booting one is the check that the Harness handles a config it did not author.
    Its *contents* are not asserted -- fidelity is the importer's question (#56), not the
    harness's; that it loads and answers is this tier's.
    """
    staged = stage(rice("end-4"), tmp_path / "home")
    assert staged.ground_truth_lua is not None

    with NestedHyprland(
        staged.ground_truth_lua, home=staged.home, log=artifacts / "end-4.log"
    ) as nested:
        state = capture(nested, options=("general:border_size", "general:gaps_in"))
        binds = nested.hyprctl("binds")

    assert isinstance(binds, list) and binds, "upstream's own port registered no keybinds"
    assert state.option("general:border_size") is not None
