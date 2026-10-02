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


def shown(widget: Any) -> list[str]:
    """Every label's text as drawn under `widget`: markup that fails to parse draws ''."""
    from gi.repository import Gtk

    texts: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        texts.extend(shown(child))
        child = child.get_next_sibling()
    return texts


def test_rule_text_with_an_ampersand_shows_as_typed(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    session.model.entities.window_rules.extend(
        [
            window_rule(match={"title": "Tom & Jerry"}, effects={"float": True}),
            window_rule(match={"class": "a&b"}, effects={"pin": True}, name="R&D <tools>"),
        ]
    )
    page = window.window_rules_page
    page.refresh()

    unnamed, named = (shown(row.widget) for row in page.rows)
    assert any("Tom & Jerry" in text for text in unnamed)
    assert "R&D <tools>" in named
    assert any("a&b" in text for text in named)


def test_the_window_picker_shows_a_title_with_an_ampersand(tmp_path: Path) -> None:
    """Window titles are other programs' text, and `&` and `<` are common in them."""
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    payload = ({"class": "mpv", "title": "Tom & Jerry <1940>"},)
    editor = RuleEditor(
        kind="window", on_done=lambda _rule: None, fetch_targets=lambda done: done(payload)
    )
    editor._open_picker()

    assert "Tom & Jerry <1940>" in shown(editor._picker_rows[0])


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

    original = window_rule(match={"class": "x"}, effects={"animation": "popin 80%"})
    editor = RuleEditor(kind="window", on_done=collected.append, rule=original)
    row = next(entry for entry in editor._effect_entries if entry.name == "animation")
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


# --- filter chips (#113) ------------------------------------------------------------------


def chip_rules(session: Any) -> None:
    session.model.entities.window_rules.extend(
        [
            window_rule(match={"class": "kitty"}, effects={"float": True}),
            window_rule(
                match={"class": "helium", "title": "Setup"}, effects={"opacity": "0.9"}
            ),
            window_rule(match={"class": "mpv"}, effects={"float": True, "pin": True}),
        ]
    )


def chip(group: str, name: str) -> Any:
    from hyprtweaker.engine.rule_filter import Chip, ChipGroup

    return Chip(ChipGroup(group), name)


def test_the_chips_are_the_props_and_effects_the_list_uses(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    chip_rules(session)
    page = window.window_rules_page
    page.refresh()

    assert [(c.group.value, c.name) for c in page.chip_buttons] == [
        ("match", "class"),
        ("match", "title"),
        ("effect", "float"),
        ("effect", "opacity"),
        ("effect", "pin"),
    ]
    assert [b.get_label() for b in page.chip_buttons.values()] == [
        "Class",
        "Title",
        "Float",
        "Opacity",
        "Pin",
    ]


def test_clicking_a_chip_narrows_the_list_and_clicking_again_widens_it(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    chip_rules(session)
    page = window.window_rules_page
    page.refresh()

    page.chip_buttons[chip("effect", "float")].set_active(True)
    assert [row.index for row in page.rows] == [0, 2]

    page.chip_buttons[chip("effect", "pin")].set_active(True)
    assert [row.index for row in page.rows] == [2]

    page.chip_buttons[chip("effect", "float")].set_active(False)
    page.chip_buttons[chip("effect", "pin")].set_active(False)
    assert [row.index for row in page.rows] == [0, 1, 2]


def test_chips_and_free_text_compose(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    chip_rules(session)
    page = window.window_rules_page
    page.refresh()

    page.chip_buttons[chip("effect", "float")].set_active(True)
    page.set_filter("mpv")

    assert [row.index for row in page.rows] == [2]


def test_a_chip_whose_rules_are_gone_disappears_and_stops_filtering(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    chip_rules(session)
    page = window.window_rules_page
    page.refresh()
    page.chip_buttons[chip("match", "title")].set_active(True)
    assert [row.index for row in page.rows] == [1]

    del session.model.entities.window_rules[1]
    page.refresh()

    assert chip("match", "title") not in page.chip_buttons
    assert [row.index for row in page.rows] == [0, 1]  # not stranded on an empty list


def test_a_list_with_no_rules_shows_no_chips(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path)

    assert window.window_rules_page.chip_buttons == {}
    assert not window.window_rules_page.chip_box.get_visible()


def test_chips_that_match_nothing_with_the_text_say_so_and_can_be_cleared(
    tmp_path: Path,
) -> None:
    session, window = build_window(tmp_path)
    chip_rules(session)
    page = window.window_rules_page
    page.refresh()

    page.chip_buttons[chip("match", "title")].set_active(True)
    page.set_filter("kitty")

    assert page.rows == ()
    assert any("Clear the search or chips" in text for text in shown(page.page))


def test_the_layer_page_has_chips_too(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import LayerRule

    session, window = build_window(tmp_path)
    session.model.entities.layer_rules.append(
        LayerRule(match={"namespace": "waybar"}, effects={"blur": True})
    )
    page = window.layer_rules_page
    page.refresh()

    assert [(c.group.value, c.name) for c in page.chip_buttons] == [
        ("match", "namespace"),
        ("effect", "blur"),
    ]


# --- keyboard reorder (#113, the Binds page's Alt+Up / Alt+Down) ---------------------------


def test_alt_up_and_down_move_a_rule_past_its_visible_neighbours(tmp_path: Path) -> None:
    from gi.repository import Gtk

    from hyprtweaker.ui.pages.rules import RuleActions, RuleRow

    calls: list[tuple[str, int, int]] = []
    actions = RuleActions(
        add=lambda: None,
        edit=lambda _i: None,
        remove=lambda _i: None,
        enable=lambda _i, _on: None,
        move=lambda i, to: calls.append(("move", i, to)),
    )
    build_window(tmp_path)
    row = RuleRow(
        window_rule(match={"class": "kitty"}),
        4,
        actions=actions,
        editable=True,
        neighbours=(1, 6),
    )

    pressed = {}
    for controller in row.widget.observe_controllers():
        if isinstance(controller, Gtk.ShortcutController):
            for each in controller:
                pressed[each.get_trigger().to_string()] = each.get_action().activate(
                    Gtk.ShortcutActionFlags(0), row.widget, None
                )

    assert pressed == {"<Alt>Up": True, "<Alt>Down": True}
    assert calls == [("move", 4, 1), ("move", 4, 6)]


def test_after_alt_down_the_moved_rule_keeps_the_focus(tmp_path: Path) -> None:
    """The move rebuilds every row; without this a second Alt+Down needs a Tab back first.
    A filter the user set stays set: focusing is not revealing."""
    import main_loop
    from _live_window import live_entity_window
    from gi.repository import Gtk

    session, window, applier = live_entity_window(tmp_path)
    for name in ("kitty", "firefox", "mpv"):
        session.add_rule("window", window_rule(match={"class": name}))
        applier.settle()
    page = window.window_rules_page
    page.refresh()
    window.present()
    window._select_section(page.section)
    main_loop.settle("the Rules page to show")
    row = page.rows[0]
    row.widget.grab_focus()

    for controller in row.widget.observe_controllers():
        if isinstance(controller, Gtk.ShortcutController):
            for each in controller:
                if each.get_trigger().to_string() == "<Alt>Down":
                    each.get_action().activate(Gtk.ShortcutActionFlags(0), row.widget, None)
    applier.settle()
    main_loop.settle("the rebuild after the move")

    assert [r.rule.match["class"] for r in page.rows] == ["firefox", "kitty", "mpv"]
    focus = window.get_focus()
    moved = page.rows[1].widget
    assert focus is not None and (focus is moved or focus.is_ancestor(moved))
    window.close()


def test_the_page_hands_each_row_the_neighbours_it_is_shown_next_to(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    chip_rules(session)
    page = window.window_rules_page
    page.refresh()
    page.chip_buttons[chip("effect", "float")].set_active(True)  # shows rules 0 and 2

    assert [row.neighbours for row in page.rows] == [(None, 2), (0, None)]


# --- matches N, and the implicit anchors, in the editor (#113) ----------------------------


def captured_windows() -> tuple[Any, ...]:
    """The nested-compositor capture: probe.tiled, probe.float (floating, pinned, tagged
    demo), probe.fs (fullscreen) -- all on workspace 1."""
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "unit"))
    from _fake_hyprland import CLIENTS

    return tuple(json.loads(CLIENTS))


def editor_over(payload: Any, *, rule: Any = None, kind: str = "window") -> Any:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    return RuleEditor(
        kind=kind,
        on_done=lambda _rule: None,
        rule=rule,
        fetch_targets=lambda done: done(payload),
    )


def badge(editor: Any) -> str | None:
    """The badge as drawn: its text, or `None` when it is not shown."""
    label = editor.match_badge
    return label.get_label() if label.get_visible() else None


def test_the_badge_counts_the_open_windows_the_match_would_catch(tmp_path: Path) -> None:
    build_window(tmp_path)
    editor = editor_over(
        captured_windows(), rule=window_rule(match={"class": r"^(probe\.float)$"})
    )

    assert badge(editor) == "Matches 1 open window"


def test_the_badge_follows_every_edit_of_the_match_rows(tmp_path: Path) -> None:
    build_window(tmp_path)
    editor = editor_over(captured_windows(), rule=window_rule(match={"class": "probe"}))
    assert badge(editor) == "Matches 0 open windows"

    editor._set_match_text("class", r"probe\..*")
    assert badge(editor) == "Matches 3 open windows"

    editor._set_match_bool("float", True)
    assert badge(editor) == "Matches 1 open window"

    editor._match_rows["float"][1].set_active(False)
    assert badge(editor) == "Matches 2 open windows"

    editor._match_rows["class"][2].set_active(True)  # the Not toggle
    assert badge(editor) == "Matches 0 open windows"

    editor._on_remove_match(None, "class", editor._match_rows["class"][1])
    assert badge(editor) == "Matches 2 open windows"


def test_a_number_row_counts_too(tmp_path: Path) -> None:
    build_window(tmp_path)
    editor = editor_over(
        captured_windows(), rule=window_rule(match={"fullscreen_state_internal": 2})
    )
    assert badge(editor) == "Matches 1 open window"

    editor._match_rows["fullscreen_state_internal"][1].set_value(0)
    assert badge(editor) == "Matches 2 open windows"


def test_the_badge_names_what_it_could_not_check(tmp_path: Path) -> None:
    build_window(tmp_path)
    editor = editor_over(
        captured_windows(), rule=window_rule(match={"float": True, "modal": True})
    )

    assert badge(editor) == "Matches 1 open window, not checking modal"


def test_no_compositor_means_no_badge_not_zero(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    offline = RuleEditor(
        kind="window",
        on_done=lambda _rule: None,
        rule=window_rule(match={"class": "kitty"}),
        fetch_targets=None,
    )
    silent = editor_over(None, rule=window_rule(match={"class": "kitty"}))

    assert badge(offline) is None
    assert badge(silent) is None


def test_a_compositor_with_no_windows_is_a_real_zero(tmp_path: Path) -> None:
    build_window(tmp_path)
    editor = editor_over((), rule=window_rule(match={"class": "kitty"}))

    assert badge(editor) == "Matches 0 open windows"


def test_the_badge_waits_for_the_answer(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    pending: list[Any] = []
    editor = RuleEditor(
        kind="window",
        on_done=lambda _rule: None,
        rule=window_rule(match={"class": r"probe\.fs"}),
        fetch_targets=pending.append,
    )
    assert badge(editor) is None

    pending[0](captured_windows())

    assert badge(editor) == "Matches 1 open window"


def test_one_fetch_serves_every_edit(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    asked: list[int] = []

    def fetch(done: Any) -> None:
        asked.append(1)
        done(captured_windows())

    editor = RuleEditor(
        kind="window",
        on_done=lambda _rule: None,
        rule=window_rule(match={"class": "probe"}),
        fetch_targets=fetch,
    )
    for text in ("p", "pr", "probe.*"):
        editor._set_match_text("class", text)

    assert len(asked) == 1
    assert badge(editor) == "Matches 3 open windows"


def test_an_invalid_regex_hides_the_badge_instead_of_raising(tmp_path: Path) -> None:
    build_window(tmp_path)
    editor = editor_over(captured_windows(), rule=window_rule(match={"class": "probe.*"}))
    assert badge(editor) == "Matches 3 open windows"

    editor._set_match_text("class", "(unclosed")

    assert badge(editor) is None


def test_a_rule_with_no_match_has_no_badge(tmp_path: Path) -> None:
    build_window(tmp_path)

    assert badge(editor_over(captured_windows())) is None


def test_a_layer_rule_counts_layer_surfaces(tmp_path: Path) -> None:
    build_window(tmp_path)
    surfaces = ({"namespace": "wallpaper"}, {"namespace": "waybar"}, {"namespace": "waybar"})
    editor = editor_over(
        surfaces, kind="layer", rule=window_rule(match={"namespace": "waybar"})
    )

    assert badge(editor) == "Matches 2 layer surfaces"


def test_a_regex_entry_shows_the_anchors_hyprland_applies_without_storing_them(
    tmp_path: Path,
) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []
    editor = RuleEditor(
        kind="window",
        on_done=collected.append,
        rule=window_rule(match={"class": "kitty", "tag": "demo", "float": True}),
    )

    assert "^(" in shown(editor._match_rows["class"][1])
    assert ")$" in shown(editor._match_rows["class"][1])
    # Anchors belong to a regex: a tag is a name, a switch has no text.
    assert ")$" not in shown(editor._match_rows["tag"][1])
    assert ")$" not in shown(editor._match_rows["float"][1])

    editor._save()
    assert collected[0].match["class"] == "kitty"  # the stored value gains no anchors
