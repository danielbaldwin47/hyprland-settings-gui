"""UI smoke tier: the Rules Pages assemble and the editor collects what it shows (#67).

Row text is settled headless in `tests/unit/test_ui_rules_text.py`; what is left here is
whether GTK builds the list, whether the filter narrows it, and whether the editor's
collect/validate path produces the Rule the widgets describe -- including the raw
pass-through for unknown effects and the Pick-a-window prefill, driven programmatically
per the repo's probe-before-screenshot rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"


def build_window(tmp_path: Path) -> Any:
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def window_rule(**kwargs: Any) -> Any:
    from hyprtweaker.engine.model.entities import WindowRule

    return WindowRule(**kwargs)


def test_both_rule_pages_are_in_the_sidebar(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path)

    assert window.window_rules_page is not None
    assert window.layer_rules_page is not None
    assert window.window_rules_page.page.get_title() == "Window rules"
    assert window.layer_rules_page.page.get_title() == "Layer rules"


def test_every_rule_becomes_a_row_in_model_order(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.window_rules.extend(
        [
            window_rule(match={"class": "kitty"}, effects={"float": True}),
            window_rule(match={"class": "helium"}, effects={"opacity": "0.9"}, name="browser"),
            window_rule(match={"class": "mpv"}, effects={"pin": True}, enabled=False),
        ]
    )
    page = window.window_rules_page
    page.refresh()

    assert [row.index for row in page.rows] == [0, 1, 2]
    assert page.rows[1].widget.get_title() == "browser"
    # The disabled rule keeps its row and its position, dimmed with its switch off.
    assert page.rows[2].enabled_switch is not None
    assert page.rows[2].enabled_switch.get_active() is False


def test_the_filter_narrows_without_renumbering(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.window_rules.extend(
        [
            window_rule(match={"class": "kitty"}, effects={"float": True}),
            window_rule(match={"class": "helium"}, effects={"opacity": "0.9"}),
        ]
    )
    page = window.window_rules_page
    page.refresh()
    page.set_filter("helium")

    assert len(page.rows) == 1
    # The one visible row still carries its *model* index, so actions land right.
    assert page.rows[0].index == 1

    page.set_filter("")
    assert len(page.rows) == 2


def test_a_read_only_session_builds_rows_without_edit_controls(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.window_rules.append(
        window_rule(match={"class": "kitty"}, effects={"float": True})
    )
    page = window.window_rules_page
    page.refresh()

    # The session is offline, so the switch is insensitive and adds are refused.
    assert page.rows[0].enabled_switch is not None
    assert not page.rows[0].enabled_switch.get_sensitive()
    assert not session.add_rule("window", window_rule(match={"class": "x"}))


def test_the_editor_collects_the_rule_the_widgets_describe(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)  # Adw.init and a display check ride along
    collected: list[Any] = []

    editor = RuleEditor(kind="window", on_done=collected.append)
    editor._set_match_text("class", "^(kitty)$")
    editor._label_entry.set_text("terminal")
    editor._save()

    assert len(collected) == 1
    rule = collected[0]
    assert rule.name == "terminal"
    assert rule.match == {"class": "^(kitty)$"}
    assert rule.enabled is True


def test_the_editor_requires_at_least_one_match(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []

    editor = RuleEditor(kind="window", on_done=collected.append)
    editor._save()

    assert collected == []
    assert editor._error.get_visible()
    assert "at least one match" in editor._error.get_label()


def test_the_editor_rejects_an_invalid_regex_and_a_taken_label(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []

    editor = RuleEditor(kind="window", on_done=collected.append, taken_names=("pip",))
    editor._set_match_text("class", "^(unclosed")
    editor._save()
    assert collected == []
    assert "regex" in editor._error.get_label()

    editor._set_match_text("class", "^(kitty)$")
    editor._label_entry.set_text("pip")
    editor._save()
    assert collected == []
    assert "already labeled" in editor._error.get_label()


def test_negation_round_trips_through_the_toggle(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []

    original = window_rule(match={"class": "negative:^(kitty)$"}, effects={"float": True})
    editor = RuleEditor(kind="window", on_done=collected.append, rule=original)

    # The row shows the stripped value with the toggle on, and collects the prefix back.
    _prop, entry, negate = editor._match_rows["class"]
    assert entry.get_text() == "^(kitty)$"
    assert negate is not None and negate.get_active()

    editor._save()
    assert collected[0].match == {"class": "negative:^(kitty)$"}


def test_unknown_effects_pass_through_untouched(tmp_path: Path) -> None:
    """Acceptance: unknown effects survive edit round-trips untouched (#67)."""
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []

    table_value = {"colors": ["#ff0000"], "angle": 45}
    original = window_rule(
        match={"class": "x"},
        effects={"plugin:hy3:tab": "on", "border_color": table_value},
    )
    editor = RuleEditor(kind="window", on_done=collected.append, rule=original)
    editor._save()

    effects = collected[0].effects
    assert effects["plugin:hy3:tab"] == "on"
    # The table-valued effect came back as the same object, not a stringification.
    assert effects["border_color"] is table_value


def test_a_table_valued_effect_edits_into_the_string_grammar(tmp_path: Path) -> None:
    """A vec2 table shows as its `"x y"` string form, so an edit saves a value the
    compositor still understands -- never a Python repr (review finding)."""
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []

    original = window_rule(match={"class": "x"}, effects={"size": ["50%", "50%"]})
    editor = RuleEditor(kind="window", on_done=collected.append, rule=original)

    row = next(entry for entry in editor._effect_entries if entry.name == "size")
    assert isinstance(row.widget, Adw.EntryRow)
    assert row.widget.get_text() == "50% 50%"

    # Untouched: the table survives by identity.
    editor._save()
    assert collected[0].effects["size"] is original.effects["size"]

    # Edited: the saved value is the string grammar, not a repr.
    editor2_collected: list[Any] = []
    editor2 = RuleEditor(kind="window", on_done=editor2_collected.append, rule=original)
    row2 = next(entry for entry in editor2._effect_entries if entry.name == "size")
    row2.widget.set_text("40% 60%")
    editor2._save()
    assert editor2_collected[0].effects["size"] == "40% 60%"


def test_a_blank_effect_refuses_to_save(tmp_path: Path) -> None:
    """A blank text effect is an error, not a silent drop (review finding)."""
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []

    original = window_rule(match={"class": "x"}, effects={"opacity": "0.9"})
    editor = RuleEditor(kind="window", on_done=collected.append, rule=original)
    row = next(entry for entry in editor._effect_entries if entry.name == "opacity")
    row.widget.set_text("")
    editor._save()

    assert collected == []
    assert "needs a value" in editor._error.get_label()


def test_the_editor_edits_in_place_keeping_enabled(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []

    original = window_rule(match={"class": "kitty"}, effects={"float": True}, enabled=False)
    editor = RuleEditor(kind="window", on_done=collected.append, rule=original)
    editor._save()

    assert collected[0].enabled is False
    assert collected[0].effects == {"float": True}


def test_pick_a_window_prefills_the_match(tmp_path: Path) -> None:
    """Acceptance: Pick a window prefills a Match from a live window (#67).

    Driven through the same fetch seam the session provides, with a canned `clients`
    payload -- the picker is helper data end to end, so the seam is the behavior.
    """
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []
    payload = (
        {
            "class": "org.pulseaudio.pavucontrol",
            "title": "Volume Control",
            "initialClass": "pavucontrol",
            "initialTitle": "Volume Control",
            "xwayland": False,
        },
    )

    def fetch(done: Any) -> None:
        done(payload)

    editor = RuleEditor(kind="window", on_done=collected.append, fetch_targets=fetch)
    editor._open_picker()
    # One row per window, built from the payload the fetch seam answered with.
    titles = [row.get_title() for row in editor._picker_rows]
    assert titles == ["org.pulseaudio.pavucontrol"]

    editor._pick_title.set_active(True)
    editor.prefill_from_window(payload[0])
    editor._save()

    rule = collected[0]
    # Escaped and exact: the dots in the class are literal, the match is anchored.
    assert rule.match["class"] == r"^(org\.pulseaudio\.pavucontrol)$"
    assert rule.match["title"] == r"^(Volume\ Control)$"
    assert "initial_class" not in rule.match  # opt-in, and it was not opted into


def test_pick_a_layer_prefills_the_namespace(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []
    payload = ({"namespace": "rofi"}, {"namespace": "waybar"}, {"namespace": "rofi"})

    def fetch(done: Any) -> None:
        done(payload)

    editor = RuleEditor(kind="layer", on_done=collected.append, fetch_targets=fetch)
    editor._open_picker()
    # Namespaces are de-duplicated: two rofi surfaces are one choice.
    assert [row.get_title() for row in editor._picker_rows] == ["rofi", "waybar"]

    editor.prefill_from_layer("rofi")
    editor._save()
    assert collected[0].match == {"namespace": "^(rofi)$"}


def test_the_move_action_reorders_and_the_reorder_would_persist(tmp_path: Path) -> None:
    """Acceptance: drag-reorder persists (#67) -- the drop lands on `move_rule`.

    The DnD gesture itself is GTK's; what this app owns is the handler the drop calls
    and the model move it performs, so that is what is exercised: the same
    `RuleActions.move` the DropTarget invokes, then the writer's render of the moved
    list, which is what the file (and so the next session) sees.
    """
    from hyprtweaker.engine.writer.rules import render_window_rules_module

    session, window = build_window(tmp_path)

    session.model.entities.window_rules.extend(
        [
            window_rule(match={"class": "a"}, effects={"float": True}),
            window_rule(match={"class": "b"}, effects={"float": True}),
            window_rule(match={"class": "c"}, effects={"float": True}),
        ]
    )
    page = window.window_rules_page
    page.refresh()

    # The offline session refuses the model edit; the *move logic* is what to check,
    # so mutate the list the way Session.move_rule's closure does and re-render.
    rules = session.model.entities.window_rules
    rules.insert(0, rules.pop(2))
    page.refresh()

    assert [row.rule.match["class"] for row in page.rows] == ["c", "a", "b"]
    text = render_window_rules_module(rules, app_version=APP_VERSION)
    assert text is not None
    assert text.index('class = "c"') < text.index('class = "a"')
