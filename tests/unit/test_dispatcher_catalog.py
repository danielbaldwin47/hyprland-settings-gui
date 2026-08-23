"""The dispatcher catalog, and the call forms 0.56.2 actually accepts (#64, ADR-0007).

The `positional` flag decides whether the editor emits `hl.dsp.exec_cmd("kitty")` or
`hl.dsp.exec_cmd{ command = "kitty" }`, and the compositor refuses the wrong one with a
config error rather than ignoring it. These are the forms a probe of a nested 0.56.2
returned; they are asserted here so a plausible-looking edit to the catalog cannot quietly
make the "Run command" door emit binds that will not load.
"""

from __future__ import annotations

from hyprtweaker.engine.dispatchers import (
    CATALOG,
    EXEC_PATH,
    NAMESPACE_LABELS,
    coverage,
    lookup,
    namespaces,
)


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

    def test_workspace_move_stays_free_form(self) -> None:
        """It requires `monitor`, not the `workspace` its name suggests -- so do not guess."""
        entry = lookup("workspace.move")
        assert entry is not None and entry.free_form


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

    def test_coverage_is_reported_honestly(self) -> None:
        report = coverage()
        assert report.total == len(CATALOG)
        assert report.formed + len(report.free_form) == report.total
        assert report.free_form, "if every shape became known, update this test"
