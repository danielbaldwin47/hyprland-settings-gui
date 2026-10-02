"""The dispatcher catalog, and the call forms 0.56.2 actually accepts (#64, ADR-0007).

The `positional` flag decides whether the editor emits `hl.dsp.exec_cmd("kitty")` or
`hl.dsp.exec_cmd{ command = "kitty" }`, and the compositor refuses the wrong one with a
config error rather than ignoring it. These are the forms a probe of a nested 0.56.2
returned; they are asserted here so a plausible-looking edit to the catalog cannot quietly
make the "Run command" door emit binds that will not load.
"""

from __future__ import annotations

import pytest

from hyprtweaker.engine.dispatchers import (
    CATALOG,
    EXEC_PATH,
    NAMESPACE_LABELS,
    Dispatcher,
    coverage,
    lookup,
    namespaces,
)
from hyprtweaker.engine.importer.dispatchers import translate_dispatcher
from hyprtweaker.engine.importer.loss import LossReport


def entry_for(path: str) -> Dispatcher:
    entry = lookup(path)
    assert entry is not None, f"{path} is not in the catalog"
    return entry


def shape(path: str) -> tuple[bool, list[tuple[str, str, bool]]]:
    """`(positional, [(name, type, required)])`: the whole form an entry offers."""
    entry = entry_for(path)
    return entry.positional, [(a.name, a.type, a.required) for a in entry.args]


class TestProbedCallForms:
    def test_exec_takes_a_bare_string(self) -> None:
        """`hl.dsp.exec_cmd{...}` is "bad argument 1: expected string, got table"."""
        entry = lookup(EXEC_PATH)
        assert entry is not None
        assert entry.positional, "the Run command door would emit a table Hyprland refuses"

    def test_exec_raw_takes_a_bare_string(self) -> None:
        entry = lookup("exec_raw")
        assert entry is not None and entry.positional

    def test_submap_takes_a_bare_string(self) -> None:
        entry = lookup("submap")
        assert entry is not None and entry.positional

    def test_window_tag_takes_a_table(self) -> None:
        """The opposite direction: `hl.dsp.window.tag("x")` is refused."""
        entry = lookup("window.tag")
        assert entry is not None
        assert not entry.positional
        assert [spec.name for spec in entry.args] == ["window", "tag"]

    def test_workspace_move_takes_a_required_monitor(self) -> None:
        """It requires `monitor`, not the `workspace` its name suggests; `workspace` is read."""
        assert shape("workspace.move") == (
            False,
            [("monitor", "string", True), ("workspace", "workspace", False)],
        )


class TestProbedShapes:
    """Each shape below is what a nested 0.56.2 accepted and read (#126).

    `tests/golden/dispatcher-probe-0.56.2.json` holds the probe's record; the integration
    tier regenerates it. These pin the curated reading of it, one dispatcher per case, so a
    plausible edit cannot quietly turn a shape the compositor accepts into one it refuses.
    """

    def test_pass_requires_a_window(self) -> None:
        """`hl.dsp.pass()` is "expected a table { window }": the old plain entry emitted it."""
        assert shape("pass") == (False, [("window", "window", True)])

    def test_force_idle_takes_a_bare_number(self) -> None:
        """`hl.dsp.force_idle()` is "expected number, got nil"; a table is "got table"."""
        assert shape("force_idle") == (True, [("seconds", "int", True)])

    def test_event_and_layout_take_a_bare_string(self) -> None:
        assert shape("event") == (True, [("data", "string", True)])
        assert shape("layout") == (True, [("message", "string", True)])

    def test_toggle_special_takes_an_optional_bare_name(self) -> None:
        """`('x')` opens `special:x`; `{ name = 'x' }` is ignored and opens the default."""
        assert shape("workspace.toggle_special") == (True, [("name", "string", False)])

    def test_float_pin_and_pseudo_take_an_action_and_a_window(self) -> None:
        for path in ("window.float", "window.pin", "window.pseudo"):
            assert shape(path) == (
                False,
                [("window", "window", False), ("action", "string", False)],
            ), path

    def test_dpms_takes_an_action_and_a_monitor(self) -> None:
        assert shape("dpms") == (
            False,
            [("action", "string", False), ("monitor", "string", False)],
        )

    def test_send_key_state_wants_mods_key_and_state(self) -> None:
        assert shape("send_key_state") == (
            False,
            [
                ("mods", "string", True),
                ("key", "string", True),
                ("state", "string", True),
                ("window", "window", False),
            ],
        )

    def test_cursor_move_wants_two_integers(self) -> None:
        assert shape("cursor.move") == (False, [("x", "int", True), ("y", "int", True)])

    def test_cycle_next_reads_next_tiled_and_floating(self) -> None:
        assert shape("window.cycle_next") == (
            False,
            [
                ("window", "window", False),
                ("next", "bool", False),
                ("tiled", "bool", False),
                ("floating", "bool", False),
            ],
        )

    def test_workspace_rename_wants_a_workspace_and_may_name_it(self) -> None:
        assert shape("workspace.rename") == (
            False,
            [("workspace", "workspace", True), ("name", "string", False)],
        )

    @pytest.mark.parametrize(
        "path", ["window.drag", "window.toggle_swallow", "window.bring_to_top"]
    )
    def test_these_never_read_a_window(self, path: str) -> None:
        """`{ window = {} }` raises wherever `window` is read, and does not for these."""
        assert shape(path) == (False, [])

    def test_group_lock_keeps_the_raw_table_for_the_action_the_importer_writes(self) -> None:
        """Its `action` shows only when fired at a group, so no form can claim it; a plain
        entry dropped the key on edit."""
        assert entry_for("group.lock").free_form
        assert entry_for("group.lock_active").free_form

    def test_fullscreen_reads_an_action_as_well_as_a_mode(self) -> None:
        """The Importer writes `action`; the curated entry listed only `window` and `mode`."""
        assert shape("window.fullscreen") == (
            False,
            [
                ("window", "window", False),
                ("mode", "string", False),
                ("action", "string", False),
            ],
        )

    def test_group_toggle_next_and_prev_read_a_window(self) -> None:
        for path in ("group.toggle", "group.next", "group.prev"):
            assert shape(path) == (False, [("window", "window", False)]), path


class TestCoverage:
    def test_the_split_after_probing(self) -> None:
        report = coverage()
        assert (len(report.curated), len(report.plain), len(report.free_form)) == (36, 7, 8)

    def test_what_stays_free_form_is_what_no_form_can_say_truthfully(self) -> None:
        """One-of or alternative shapes `ArgSpec` cannot express, and keys nothing confirmed."""
        assert coverage().free_form == (
            "focus",
            "group.lock",
            "group.lock_active",
            "group.move_window",
            "window.deny_from_group",
            "window.move",
            "window.resize",
            "window.swap",
        )

    def test_every_free_form_entry_says_why_in_one_line(self) -> None:
        for path in coverage().free_form:
            reason = entry_for(path).free_form_reason
            assert reason and "\n" not in reason, f"{path} has no one-line justification"

    def test_a_curated_entry_has_no_free_form_reason(self) -> None:
        for entry in CATALOG:
            if not entry.free_form:
                assert not entry.free_form_reason, entry.path

    def test_the_three_groups_partition_the_catalog(self) -> None:
        report = coverage()
        every = [*report.curated, *report.plain, *report.free_form]
        assert sorted(every) == sorted(entry.path for entry in CATALOG)


class TestCatalogShape:
    def test_every_path_is_unique(self) -> None:
        paths = [entry.path for entry in CATALOG]
        assert len(paths) == len(set(paths))

    def test_a_positional_dispatcher_declares_the_argument_it_takes(self) -> None:
        for entry in CATALOG:
            if entry.positional:
                assert entry.args, f"{entry.path} is positional but names no argument"

    def test_free_form_dispatchers_declare_no_arguments(self) -> None:
        """Free-form means "shape unknown"; args would be a guess wearing a form."""
        for entry in CATALOG:
            if entry.free_form:
                assert not entry.args, f"{entry.path} is free-form but declares args"

    def test_every_namespace_has_a_label(self) -> None:
        for name in namespaces():
            assert name in NAMESPACE_LABELS, f"namespace {name!r} has no picker label"

    def test_namespaces_cover_the_catalog(self) -> None:
        assert sum(len(entries) for entries in namespaces().values()) == len(CATALOG)

    def test_an_unknown_path_is_none_not_an_error(self) -> None:
        """A plugin or a newer Hyprland: the caller renders it read-only (ADR-0012)."""
        assert lookup("plugin.whatever") is None


IMPORTED_LINES = [
    ("exec", "kitty"),
    ("execr", "kitty"),
    ("submap", "resize"),
    ("global", "quickshell:toggle"),
    ("event", "custom"),
    ("layoutmsg", "togglesplit"),
    ("forceidle", "5"),
    ("pass", "class:discord"),
    ("dpms", "off HDMI-A-1"),
    ("sendshortcut", "SUPER, a, class:kitty"),
    ("sendkeystate", "SUPER, a, down, class:kitty"),
    ("movecursor", "10 20"),
    ("movecursortocorner", "1"),
    ("changegroupactive", "3"),
    ("togglegroup", ""),
    ("alterzorder", "top, class:kitty"),
    ("cyclenext", "prev tiled"),
    ("fullscreenstate", "1 2"),
    ("fullscreen", "1 set"),
    ("setprop", "class:kitty opaque 1"),
    ("togglefloating", "class:kitty"),
    ("setfloating", ""),
    ("pin", "class:kitty"),
    ("pseudo", ""),
    ("renameworkspace", "3 web"),
    ("movecurrentworkspacetomonitor", "DP-1"),
    ("swapactiveworkspaces", "DP-1 DP-2"),
    ("togglespecialworkspace", "magic"),
    ("togglespecialworkspace", ""),
    ("tagwindow", "+games class:steam"),
    ("signal", "9"),
    ("toggleswallow", ""),
    ("bringactivetotop", ""),
]


@pytest.mark.parametrize(("name", "args"), IMPORTED_LINES)
def test_a_call_the_importer_writes_survives_the_form_that_edits_it(
    name: str, args: str
) -> None:
    """The bind editor rebuilds a call from the keys an entry lists, so any key the Importer
    writes that the entry omits would be dropped from the saved bind on its next edit."""
    report = LossReport(source="hyprland.conf")
    call = translate_dispatcher(name, args, origin="hyprland.conf:1", report=report)
    assert call is not None, f"{name} {args!r} was not translated"
    entry = entry_for(call.path)
    if entry.free_form:
        return
    listed = {spec.name for spec in entry.args}
    assert set(call.args) <= listed, f"{call.path} drops {set(call.args) - listed} on edit"
    assert bool(call.positional) == entry.positional or not call.positional
    required = {spec.name for spec in entry.args if spec.required}
    if entry.positional:
        assert len(call.positional) <= 1
        assert not required or call.positional
    else:
        assert required <= set(call.args), f"{call.path} lacks required {required}"
