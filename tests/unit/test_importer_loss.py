"""The Loss report: classification, rendering, and surviving a round trip to disk.

"Persisted and reloadable after the fact" is a promise about the *record*, not the import:
a user is offered "view the last import" long after the wizard closed, on an app that has
been restarted, so a report that cannot be read back is a report that does not exist.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyprtweaker.engine.importer.loss import (
    CLASS_ORDER,
    LOSS_CODES,
    LossClass,
    LossCode,
    LossContext,
    LossItem,
    LossReport,
    describe,
    rescue_command,
    rescue_line,
)
from hyprtweaker.engine.importer.scalars import bool_prefix, direction, number, truthy
from hyprtweaker.engine.paths import ConfigPaths


@pytest.fixture
def paths(tmp_path: Path) -> ConfigPaths:
    return ConfigPaths.rooted_at(tmp_path)


def _report() -> LossReport:
    report = LossReport(source="/home/tester/.config/hypr/hyprland.conf")
    report.add(
        LossCode.MODS_SPELLING,
        "modifiers respelled",
        origin="hyprland.conf:4",
        source="bind = SUPER_SHIFT, Q, killactive",
        replacement="SHIFT + SUPER + Q",
    )
    report.add(LossCode.UNBIND_BY_STRING, "unbind matches by string", origin="hyprland.conf:9")
    report.add(
        LossCode.LEGACY_DISPATCH_CALL,
        "command runs hyprctl dispatch",
        origin="hyprland.conf:12",
        source="exec-once = hyprctl dispatch exit",
    )
    return report


class TestClassification:
    def test_every_code_has_a_description_and_a_default_class(self) -> None:
        for code in LossCode:
            assert code in LOSS_CODES, f"{code} has no entry in LOSS_CODES"
            assert describe(code)
            assert LOSS_CODES[code].default_class in CLASS_ORDER

    def test_an_item_takes_its_codes_class_by_default(self) -> None:
        item = LossItem(LossCode.MODS_SPELLING, "x")
        assert item.severity is LossClass.INFO

    def test_an_item_may_override_its_class(self) -> None:
        """The same code covers a faithful rename and a value that will be rejected, so
        the class belongs to the finding, not only to the code."""
        item = LossItem(LossCode.RULE_VALUE_TYPE, "x", loss_class=LossClass.BREAKAGE)
        assert item.severity is LossClass.BREAKAGE

    def test_counts_cover_all_three_classes_even_when_empty(self) -> None:
        counts = LossReport().counts()
        assert set(counts) == set(CLASS_ORDER)
        assert sum(counts.values()) == 0

    def test_breakage_is_the_class_that_needs_showing(self) -> None:
        report = _report()
        assert [str(i.code) for i in report.breakage] == ["L29"]

    def test_clean_means_nothing_beyond_info(self) -> None:
        info_only = LossReport()
        info_only.add(LossCode.MODS_SPELLING, "x")
        assert info_only.clean is True
        assert _report().clean is False

    def test_code_counts_tally_by_code(self) -> None:
        report = LossReport()
        report.add(LossCode.MODS_SPELLING, "a")
        report.add(LossCode.MODS_SPELLING, "b")
        assert report.code_counts()[LossCode.MODS_SPELLING] == 2


class TestLossContext:
    """One wrapper for "file this against the keyword I am reading"."""

    def test_a_note_carries_the_contexts_origin_and_source(self) -> None:
        report = LossReport()
        context = LossContext(report=report, origin="hyprland.conf:4", source="bind = ...")
        context.note(LossCode.MODS_SPELLING, "respelled")
        item = report.items[0]
        assert (item.origin, item.source) == ("hyprland.conf:4", "bind = ...")

    def test_at_repoints_the_same_report(self) -> None:
        report = LossReport()
        first = LossContext(report=report, origin="a:1", source="a")
        first.at(origin="b:2", source="b").note(LossCode.MODS_SPELLING, "x")
        first.note(LossCode.MODS_SPELLING, "y")
        assert [(i.origin, i.message) for i in report] == [("b:2", "x"), ("a:1", "y")]


class TestScalars:
    """hyprlang's readings, which are looser than Python's and differ from each other."""

    def test_truthy_follows_the_prefix_rule(self) -> None:
        assert truthy("on") and truthy("1") and truthy("yes please")
        assert not truthy("off") and not truthy("0") and not truthy("")

    def test_bool_prefix_can_say_not_a_boolean(self) -> None:
        """The difference that matters: `truthy` answers False for both `off` and `4`,
        which is right for a rule effect and wrong for a config value."""
        assert bool_prefix("yes, please :)") is True
        assert bool_prefix("off") is False
        assert bool_prefix("4") is None
        assert truthy("4") is False

    def test_number_prefers_int_but_falls_back_to_float(self) -> None:
        assert number("3") == 3
        assert isinstance(number("3"), int)
        assert number("3.5") == 3.5
        assert number("0x1f") == 31
        assert number("nope") is None

    def test_direction_keeps_only_the_first_letter(self) -> None:
        assert direction("left") == "l"
        assert direction("l") == "l"
        assert direction("") == ""


class TestRendering:
    def test_markdown_groups_by_class_worst_first(self) -> None:
        text = _report().render()
        assert text.index("## Breakage") < text.index("## Needs review")
        assert text.index("## Needs review") < text.index("## Info")

    def test_an_item_shows_what_it_was_and_what_it_became(self) -> None:
        text = _report().render()
        assert "was: `bind = SUPER_SHIFT, Q, killactive`" in text
        assert "now: `SHIFT + SUPER + Q`" in text

    def test_an_empty_report_says_so_rather_than_rendering_nothing(self) -> None:
        text = LossReport().render()
        assert "Nothing was lost in conversion." in text

    def test_the_tty_rescue_line_is_in_every_report(self) -> None:
        """Including a clean one: the reader who needs it cannot open the app to look it
        up, and a report that only carries the escape hatch when trouble was predicted is
        missing the case where the prediction was wrong (ADR-0009)."""
        assert "rm ~/.config/hypr/hyprland.lua" in LossReport().render()
        assert "rm ~/.config/hypr/hyprland.lua" in _report().render()

    def test_the_summary_line_counts_every_class(self) -> None:
        assert "3 findings -- 1 breakage, 1 needs review, 1 info." in _report().render()


class TestPersistence:
    def test_a_saved_report_reloads_identically(self, paths: ConfigPaths) -> None:
        original = _report()
        path = original.save(paths)
        reloaded = LossReport.load(path)

        def fields(report: LossReport) -> list[tuple[str, ...]]:
            return [
                (str(i.code), str(i.severity), i.message, i.origin, i.source, i.replacement)
                for i in report
            ]

        assert fields(reloaded) == fields(original)
        assert reloaded.source == original.source
        assert reloaded.created == original.created

    def test_the_stored_class_is_the_one_that_was_decided_at_import(
        self, paths: ConfigPaths
    ) -> None:
        """Every item persists its resolved class, not a reference to the code's default --
        so changing a default in a later release cannot silently reclassify a report a
        user already read."""
        report = LossReport()
        report.add(LossCode.MODS_SPELLING, "x")
        record = json.loads(report.save(paths).read_text(encoding="utf-8"))
        assert record["items"][0]["class"] == str(LossClass.INFO)

    def test_saving_writes_both_a_json_record_and_a_readable_copy(
        self, paths: ConfigPaths
    ) -> None:
        path = _report().save(paths)
        markdown = path.with_suffix(".md")
        assert markdown.is_file()
        assert markdown.read_text(encoding="utf-8").startswith("# Import loss report")

    def test_reports_land_in_the_state_dir_not_the_config_dir(self, paths: ConfigPaths) -> None:
        """A report is a record of what happened, not config -- putting it in the hypr dir
        would drop it into the user's dotfile repo."""
        path = _report().save(paths)
        assert paths.state_dir in path.parents
        assert paths.hypr_dir not in path.parents

    def test_latest_returns_the_most_recent_report(self, paths: ConfigPaths) -> None:
        older = LossReport(source="old")
        older.add(LossCode.MODS_SPELLING, "old")
        older.save(paths, now=datetime(2026, 1, 1, 10, 0, tzinfo=UTC))
        newer = LossReport(source="new")
        newer.add(LossCode.MODS_SPELLING, "new")
        newer.save(paths, now=datetime(2026, 2, 2, 11, 0, tzinfo=UTC))

        latest = LossReport.latest(paths)
        assert latest is not None
        assert latest.source == "new"
        assert len(LossReport.stored(paths)) == 2

    def test_latest_is_none_before_any_import(self, paths: ConfigPaths) -> None:
        assert LossReport.latest(paths) is None
        assert LossReport.stored(paths) == []

    def test_an_unknown_format_version_is_refused_rather_than_guessed(
        self, paths: ConfigPaths
    ) -> None:
        path = _report().save(paths)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["format"] = 99
        path.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported loss report format"):
            LossReport.load(path)

    def test_an_overridden_class_survives_the_round_trip(self, paths: ConfigPaths) -> None:
        """The override is the whole difference between "retyped" and "will be rejected",
        so losing it on reload would silently downgrade a Breakage to an Info."""
        report = LossReport()
        report.add(LossCode.RULE_VALUE_TYPE, "out of range", loss_class=LossClass.BREAKAGE)
        reloaded = LossReport.load(report.save(paths))
        assert reloaded.items[0].severity is LossClass.BREAKAGE


class TestRescueLine:
    """The TTY escape hatch has to match the migration it is printed on (ADR-0009, #131).

    One constant served both importers, and it said `rm ~/.config/hypr/hyprland.lua`. On the
    legacy path that removes the file the app just generated and leaves `hyprland.conf` in
    charge -- the intended rescue. On the Lua path `hyprland.lua` *is* the user's only
    config, renamed aside and contested by the generated one, so the same line deletes the
    config and tells the user it restored it.
    """

    def test_a_migration_that_displaced_nothing_removes_the_generated_entrypoint(self) -> None:
        line = rescue_line(False)
        assert "rm ~/.config/hypr/hyprland.lua" in line
        assert ".bak" not in line

    def test_a_migration_that_displaced_a_lua_restores_it_instead_of_deleting(self) -> None:
        line = rescue_line(True)
        assert "mv ~/.config/hypr/hyprland.lua.bak ~/.config/hypr/hyprland.lua" in line
        assert "rm " not in line

    def test_an_undecided_migration_never_offers_a_bare_delete(self) -> None:
        """A report whose migration is not yet known still gets a line, and it leads with
        the restore -- guessing wrong towards `rm` is the failure this class is about."""
        line = rescue_line(None)
        assert "hyprland.lua.bak" in line
        assert line.index("mv ") < line.index("rm ")

    def test_the_rescue_names_the_backup_that_was_actually_made(self) -> None:
        """A second migration finds `.bak` taken and stamps the new one. Naming the plain
        `.bak` then restores a config two migrations old -- not the one just displaced."""
        stamped = "hyprland.lua.bak.20260824-120000"
        assert rescue_command(True, backup=stamped) == (
            f"mv ~/.config/hypr/{stamped} ~/.config/hypr/hyprland.lua"
        )
        assert stamped in rescue_line(True, backup=stamped)

    def test_the_command_form_carries_no_markdown(self) -> None:
        """It goes in a GTK Row, which renders backticks and asterisks literally."""
        for answer in (True, False, None):
            command = rescue_command(answer)
            assert "`" not in command
            assert "*" not in command

    def test_a_displacing_report_renders_the_restoring_line(self) -> None:
        report = LossReport(source="/home/tester/.config/hypr/hyprland.lua")
        report.restore_backup = True
        rendered = report.render()
        assert "hyprland.lua.bak" in rendered
        assert "rm ~/.config/hypr/hyprland.lua" not in rendered

    def test_a_legacy_report_renders_the_removing_line(self) -> None:
        report = LossReport(source="/home/tester/.config/hypr/hyprland.conf")
        report.restore_backup = False
        assert "rm ~/.config/hypr/hyprland.lua" in report.render()

    def test_every_report_carries_one_even_when_nothing_was_lost(self) -> None:
        assert "If Hyprland will not start" in LossReport().render()

    def test_the_rescue_survives_the_round_trip_to_disk(self, paths: ConfigPaths) -> None:
        """The report outlives the app that wrote it, and a rescue that reloads as the
        *other* path's is worse than none -- it is read by someone already locked out."""
        report = LossReport(source="/x/hyprland.lua")
        report.restore_backup = True
        reloaded = LossReport.load(report.save(paths))
        assert reloaded.restore_backup is True
        assert reloaded.rescue_line == report.rescue_line
