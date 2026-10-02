"""Does a `submap_universal` bind declared inside a never-entered submap fire? (#111)

`engine/binds_analysis.unreachable_submaps` treats a universal bind as always live, even
when the submap that declares it is itself unreachable ("an entry bind that lives inside a
submap nothing enters can itself never fire" is its rule for every other bind). That is an
assumption about the compositor, so it is asserted here against the compositor, not cited:
a later Hyprland that reads `submap_universal` differently fails this file, and the
fixpoint in `unreachable_submaps` is then the thing to revisit.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest tests/integration/test_universal_bind_probe.py \\
        -m hyprland

**How a bind is fired.** The windowless Harness has no input devices, so the keys come from
`wtype`'s virtual keyboard, aimed at the nested compositor only (`nested.env`, checked
before every call; never `ydotool`, which writes the host's `/dev/uinput`). One quirk shapes
every key below: Hyprland does not read wtype's keymap, it reads keycodes through its own
US layout, and wtype numbers the distinct keys of one invocation 1, 2, 3 ... from the start,
so the first key it sends is always evdev 1, which Hyprland sees as `escape`. The probe
therefore binds `escape` throughout and tells its cases apart by modifier.

**What fired.** An `exec_cmd` bind touches a marker file; the entry bind's effect is the
`keybinds.submap` event, which a Lua handler appends to a file (the same event the engine
reads from socket2). `hyprctl binds` is the registration witness: it shows each bind's
`submap` and `submap_universal`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from harness import REQUIRE_VARIABLE, HarnessUnavailable, NestedHyprland, make_home

pytestmark = pytest.mark.hyprland

#: Seconds to let an `exec_cmd` or a submap event land before reading what it left.
SETTLE_SECONDS = 1.0

#: One modifier per case; the key is always the first one wtype sends (see the module doc).
MODIFIER_FLAG = {"SUPER": "logo", "ALT": "alt", "CTRL": "ctrl", "SHIFT": "shift"}


@dataclass(frozen=True)
class Observed:
    """What the compositor did, per case; `version` is `hyprctl version`'s first line."""

    version: str
    registered: list[dict[str, object]]
    root_at_root: bool
    plain_at_root: bool
    universal_at_root: bool
    root_in_other: bool
    universal_in_other: bool
    submaps_after_entry: list[str]


def config_for(marks: Path, events: Path) -> str:
    def touch(name: str) -> str:
        return f'hl.dsp.exec_cmd("touch {marks}/{name}")'

    return f"""
hl.on("keybinds.submap", function(name)
  local f = io.open("{events}", "a"); f:write(tostring(name) .. "\\n"); f:close()
end)

hl.bind("SUPER + escape", {touch("root")})

hl.define_submap("island", function()
  hl.bind("ALT + escape", {touch("universal")}, {{ submap_universal = true }})
  hl.bind("CTRL + escape", {touch("plain")})
  hl.bind("SHIFT + escape", hl.dsp.submap("target"), {{ submap_universal = true }})
end)

-- A submap with no bind is never registered ("Cannot set submap ..., submap doesn't
-- exist"), so `target` and `other` each hold one bind nothing here presses.
hl.define_submap("target", function()
  hl.bind("CTRL + SHIFT + escape", hl.dsp.submap("reset"))
end)
hl.define_submap("other", function()
  hl.bind("CTRL + SHIFT + escape", hl.dsp.submap("reset"))
end)
"""


def press(nested: NestedHyprland, modifier: str) -> None:
    """`MODIFIER + escape` through a virtual keyboard that only the nested compositor sees."""
    environment = nested.env
    assert environment["WAYLAND_DISPLAY"] == nested.wayland_display, "not the nested display"
    assert environment["WAYLAND_DISPLAY"] != os.environ.get("WAYLAND_DISPLAY"), (
        "the keystroke would reach the host session"
    )
    flag = MODIFIER_FLAG[modifier]
    subprocess.run(
        ["wtype", "-M", flag, "-k", "F9", "-m", flag, "-s", "100"],
        env=environment,
        check=True,
        timeout=20,
    )
    time.sleep(SETTLE_SECONDS)


def fired(nested: NestedHyprland, marks: Path, modifier: str, marker: str) -> bool:
    """Press `modifier + escape` from a clean slate; whether `marker` was touched."""
    for stale in marks.iterdir():
        stale.unlink()
    press(nested, modifier)
    return (marks / marker).exists()


@pytest.fixture(scope="module")
def observed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Observed]:
    if shutil.which("wtype") is None:
        pytest.skip("wtype is not installed, so no bind can be fired")
    scratch = tmp_path_factory.mktemp("universal-probe")
    home = make_home(scratch / "home")
    marks = scratch / "marks"
    marks.mkdir()
    events = scratch / "submap-events"
    config = home / "hyprland.lua"
    config.write_text(config_for(marks, events))
    nested = NestedHyprland(config, home=home, log=scratch / "nested.log")
    try:
        nested.start()
    except HarnessUnavailable as unavailable:
        if os.environ.get(REQUIRE_VARIABLE) == "1":
            raise
        pytest.skip(f"Harness tier: {unavailable}")
    try:
        assert nested.config_errors() == (), nested.config_errors()
        version = nested.hyprctl_text("version").splitlines()[0]
        registered = nested.hyprctl("binds")

        root_at_root = fired(nested, marks, "SUPER", "root")
        plain_at_root = fired(nested, marks, "CTRL", "plain")
        universal_at_root = fired(nested, marks, "ALT", "universal")

        nested.dispatch('hl.dsp.submap("other")')
        time.sleep(SETTLE_SECONDS)
        root_in_other = fired(nested, marks, "SUPER", "root")
        universal_in_other = fired(nested, marks, "ALT", "universal")
        nested.dispatch('hl.dsp.submap("reset")')
        time.sleep(SETTLE_SECONDS)

        events.write_text("")
        press(nested, "SHIFT")
        submaps_after_entry = events.read_text().split()

        yield Observed(
            version=version,
            registered=registered,
            root_at_root=root_at_root,
            plain_at_root=plain_at_root,
            universal_at_root=universal_at_root,
            root_in_other=root_in_other,
            universal_in_other=universal_in_other,
            submaps_after_entry=submaps_after_entry,
        )
    finally:
        nested.stop()


def test_the_compositor_registers_the_universal_bind_under_the_island(
    observed: Observed,
) -> None:
    by_key = {
        (entry["submap"], entry["modmask"]): entry["submap_universal"]
        for entry in observed.registered
    }
    # modmask: ALT 8, CTRL 4, SHIFT 1, SUPER 64; CTRL + SHIFT 5
    assert by_key == {
        ("", 64): "false",
        ("island", 8): "true",
        ("island", 4): "false",
        ("island", 1): "true",
        ("target", 5): "false",
        ("other", 5): "false",
    }


def test_firing_is_observable_at_all(observed: Observed) -> None:
    """The positive control: without it every "did not fire" below proves nothing."""
    assert observed.root_at_root, (
        "firing not observable: a root bind pressed at root through wtype did not run"
    )


def test_a_plain_bind_in_a_never_entered_submap_does_not_fire_at_root(
    observed: Observed,
) -> None:
    assert observed.root_at_root and not observed.plain_at_root


def test_a_universal_bind_in_a_never_entered_submap_fires_at_root(
    observed: Observed,
) -> None:
    assert observed.root_at_root and observed.universal_at_root


def test_a_universal_bind_in_a_never_entered_submap_fires_inside_another_submap(
    observed: Observed,
) -> None:
    # In `other` a root bind is out of force, which shows the submap really was entered.
    assert observed.universal_in_other and not observed.root_in_other


def test_a_universal_bind_in_a_never_entered_submap_enters_its_target(
    observed: Observed,
) -> None:
    assert observed.submaps_after_entry == ["target"]
