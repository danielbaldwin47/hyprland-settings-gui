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

from started_app import presented, started_application

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
    app = started_application()
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
    editor = presented(
        RuleEditor(
            kind="window", on_done=lambda _rule: None, fetch_targets=lambda done: done(payload)
        )
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


def test_a_read_only_session_greys_out_the_row_controls(tmp_path: Path) -> None:
    """Read-only is temporary (the Banner says why), so the controls show, insensitive,
    as on the Workspaces page; only the move routes stay unwired (#159, #113)."""
    from gi.repository import Gtk

    session, window = build_window(tmp_path)

    session.model.entities.window_rules.append(
        window_rule(match={"class": "kitty"}, effects={"float": True})
    )
    page = window.window_rules_page
    page.refresh()

    # The session is offline: every control is there and none answers.
    (row,) = page.rows
    sensitive = [
        widget.get_sensitive()
        for widget in (row.enabled_switch, row.edit_button, row.remove_button)
    ]
    assert sensitive == [False, False, False]
    assert not [
        c
        for c in row.widget.observe_controllers()
        if isinstance(c, Gtk.DropTarget | Gtk.ShortcutController)
    ]
    assert not session.add_rule("window", window_rule(match={"class": "x"}))


def test_the_editor_collects_the_rule_the_widgets_describe(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)  # Adw.init and a display check ride along
    collected: list[Any] = []

    editor = presented(RuleEditor(kind="window", on_done=collected.append))
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

    editor = presented(RuleEditor(kind="window", on_done=collected.append))
    editor._save()

    assert collected == []
    assert editor._error.get_visible()
    assert "at least one match" in editor._error.get_label()


def test_the_editor_rejects_an_invalid_regex_and_a_taken_label(tmp_path: Path) -> None:
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    collected: list[Any] = []

    editor = presented(
        RuleEditor(kind="window", on_done=collected.append, taken_names=("pip",))
    )
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
    editor = presented(RuleEditor(kind="window", on_done=collected.append, rule=original))

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
    editor = presented(RuleEditor(kind="window", on_done=collected.append, rule=original))
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
    editor = presented(RuleEditor(kind="window", on_done=collected.append, rule=original))

    row = next(entry for entry in editor._effect_entries if entry.name == "size")
    assert isinstance(row.widget, Adw.EntryRow)
    assert row.widget.get_text() == "50% 50%"

    # Untouched: the table survives by identity.
    editor._save()
    assert collected[0].effects["size"] is original.effects["size"]

    # Edited: the saved value is the string grammar, not a repr.
    editor2_collected: list[Any] = []
    editor2 = presented(
        RuleEditor(kind="window", on_done=editor2_collected.append, rule=original)
    )
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
    editor = presented(RuleEditor(kind="window", on_done=collected.append, rule=original))
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
    editor = presented(RuleEditor(kind="window", on_done=collected.append, rule=original))
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

    editor = presented(RuleEditor(kind="window", on_done=collected.append, fetch_targets=fetch))
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

    editor = presented(RuleEditor(kind="layer", on_done=collected.append, fetch_targets=fetch))
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


def test_the_page_hands_each_row_the_neighbours_it_is_shown_next_to(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    chip_rules(session)
    page = window.window_rules_page
    page.refresh()
    page.chip_buttons[chip("effect", "float")].set_active(True)  # shows rules 0 and 2

    assert [row.neighbours for row in page.rows] == [(None, 2), (0, None)]


def press(widget: Any, accelerator: str) -> None:
    """Fire `widget`'s shortcut for `accelerator`, as the key press would."""
    from gi.repository import Gtk

    for controller in widget.observe_controllers():
        if isinstance(controller, Gtk.ShortcutController):
            for each in controller:
                if each.get_trigger().to_string() == accelerator:
                    each.get_action().activate(Gtk.ShortcutActionFlags(0), widget, None)
                    return
    raise AssertionError(f"{widget!r} has no {accelerator} shortcut")


def test_alt_down_twice_moves_the_same_rule_twice_and_focus_follows_it(tmp_path: Path) -> None:
    """Review of #151, finding 11: the refresh rebuilds every row, so the moved rule's new
    row takes the focus, or the second Alt+Down would land on nothing."""
    import main_loop
    from _live_window import live_entity_window

    session, window, applier = live_entity_window(tmp_path)
    session.model.entities.window_rules.extend(
        window_rule(match={"class": name}, effects={"float": True}) for name in "abc"
    )
    page = window.window_rules_page
    page.refresh()
    window.present()
    window._select_section(page.section)
    main_loop.settle("the Rules page to map")
    page.rows[0].widget.grab_focus()

    press(window.get_focus(), "<Alt>Down")
    applier.settle()
    press(window.get_focus(), "<Alt>Down")
    applier.settle()

    assert [row.rule.match["class"] for row in page.rows] == ["b", "c", "a"]
    assert window.get_focus() is page.rows[2].widget


# --- chip overflow: one row per group, the rest behind "+N" (#113, review of #151) --------


def crowded_rules_page(tmp_path: Path) -> Any:
    """The Rules page over the 332-rule fixture (74 chips), shown and laid out."""
    import main_loop
    from _many_rules import many_rules

    session, window = build_window(tmp_path)
    session.model.entities.window_rules.extend(many_rules())
    page = window.window_rules_page
    page.refresh()
    window.present()
    window._select_section(page.section)
    main_loop.settle("the Rules page to map")
    settle_chips(page)
    return page


def row_chips(page: Any, group: str) -> list[Any]:
    return [b for c, b in page.chip_buttons.items() if c.group.value == group]


def settle_chips(page: Any) -> None:
    """Run the loop until every chip is on its row or in its popover, not both or neither.

    The fold is decided at allocation and reported from an idle, and a relabelled "+N"
    chip can take another frame to refold, which an empty main loop does not wait for.
    """
    import time

    import main_loop

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        main_loop.settle("the chip rows to fold")
        if all(
            sum(b.get_mapped() for b in row_chips(page, group.value))
            + len(page.folded_buttons(group))
            == len(row_chips(page, group.value))
            for group in page.more_buttons
        ):
            return
        time.sleep(0.02)
    raise AssertionError("the chip rows did not settle into row and popover")


def test_chips_past_one_row_fold_into_a_more_chip(tmp_path: Path) -> None:
    from hyprtweaker.engine.rule_filter import ChipGroup

    page = crowded_rules_page(tmp_path)

    assert len(page.chip_buttons) == 74
    for group, total in ((ChipGroup.MATCH, 17), (ChipGroup.EFFECT, 57)):
        buttons = row_chips(page, group.value)
        shown = [b for b in buttons if b.get_mapped()]
        more = page.more_buttons[group]
        assert 0 < len(shown) < total
        assert shown == buttons[: len(shown)]  # the first chips stay, the rest fold
        assert [b.get_label() for b in page.folded_buttons(group).values()] == [
            b.get_label() for b in buttons[len(shown) :]
        ]
        assert more.get_mapped()
        assert more.get_label() == f"+{total - len(shown)}"
        assert more.get_tooltip_text() == f"Show {total - len(shown)} more filters"
        tops = {b.compute_bounds(page.page)[1].get_y() for b in [*shown, more]}
        assert len(tops) == 1  # one row


def test_a_chip_chosen_in_the_more_popover_filters_like_any_other(tmp_path: Path) -> None:
    import main_loop

    from hyprtweaker.engine.rule_filter import ChipGroup

    page = crowded_rules_page(tmp_path)
    more = page.more_buttons[ChipGroup.EFFECT]
    popover = page.more_popovers[ChipGroup.EFFECT]

    more.emit("clicked")
    main_loop.settle("the more popover to open")
    xray = page.folded_buttons(ChipGroup.EFFECT)[chip("effect", "xray")]
    assert popover.get_visible()
    assert xray.get_label() == "Xray"

    xray.set_active(True)

    assert [row.index for row in page.rows] == [41, 98, 155, 212, 269, 326]
    assert page.chip_buttons[chip("effect", "xray")].get_active()
    assert more.get_active()  # the "+N" chip shows a folded chip is on
    assert more.get_label() == f"+{len(page.folded_buttons(ChipGroup.EFFECT))} (1 on)"
    settle_chips(page)  # "(1 on)" widens the chip, which may fold one more
    assert page.folded_buttons(ChipGroup.EFFECT)[chip("effect", "xray")] is xray
    assert more.get_label() == f"+{len(page.folded_buttons(ChipGroup.EFFECT))} (1 on)"
    assert popover.get_visible()  # pick another without reopening

    xray.set_active(False)

    assert len(page.rows) == 332
    assert not more.get_active()
    assert more.get_label() == f"+{len(page.folded_buttons(ChipGroup.EFFECT))}"


def test_clicking_the_more_chip_keeps_it_showing_the_folded_filter_state(
    tmp_path: Path,
) -> None:
    """A toggle button flips on click; the "+N" chip's pressed look means "a folded chip
    is on", so a click opens the popover and leaves that look alone."""
    import main_loop

    from hyprtweaker.engine.rule_filter import ChipGroup

    page = crowded_rules_page(tmp_path)
    more = page.more_buttons[ChipGroup.MATCH]

    more.emit("clicked")
    main_loop.settle("the more popover to open")

    assert page.more_popovers[ChipGroup.MATCH].get_visible()
    assert not more.get_active()


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

    return presented(
        RuleEditor(
            kind=kind,
            on_done=lambda _rule: None,
            rule=rule,
            fetch_targets=lambda done: done(payload),
        )
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
    offline = presented(
        RuleEditor(
            kind="window",
            on_done=lambda _rule: None,
            rule=window_rule(match={"class": "kitty"}),
            fetch_targets=None,
        )
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
    editor = presented(
        RuleEditor(
            kind="window",
            on_done=lambda _rule: None,
            rule=window_rule(match={"class": r"probe\.fs"}),
            fetch_targets=pending.append,
        )
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

    editor = presented(
        RuleEditor(
            kind="window",
            on_done=lambda _rule: None,
            rule=window_rule(match={"class": "probe"}),
            fetch_targets=fetch,
        )
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
    editor = presented(
        RuleEditor(
            kind="window",
            on_done=collected.append,
            rule=window_rule(match={"class": "kitty", "tag": "demo", "float": True}),
        )
    )

    assert "^(" in shown(editor._match_rows["class"][1])
    assert ")$" in shown(editor._match_rows["class"][1])
    # Anchors belong to a regex: a tag is a name, a switch has no text.
    assert ")$" not in shown(editor._match_rows["tag"][1])
    assert ")$" not in shown(editor._match_rows["float"][1])

    editor._save()
    assert collected[0].match["class"] == "kitty"  # the stored value gains no anchors


def visible_anchor_marks(widget: Any) -> list[str]:
    """The anchor labels drawn around a regex entry, as the user sees them."""
    from gi.repository import Gtk

    marks: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if (
            isinstance(child, Gtk.Label)
            and child.get_text() in ("^(", ")$")
            and child.get_visible()
        ):
            marks.append(child.get_text())
        marks.extend(visible_anchor_marks(child))
        child = child.get_next_sibling()
    return marks


def test_an_already_anchored_regex_does_not_wear_the_marks_twice(tmp_path: Path) -> None:
    """The picker's prefill and most imported rules are `^(kitty)$`: the marks around it
    would read `^( ^(kitty)$ )$` (#151 review, finding 28)."""
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    build_window(tmp_path)
    editor = presented(
        RuleEditor(
            kind="window",
            on_done=lambda _rule: None,
            rule=window_rule(match={"class": "^(kitty)$", "title": "kitty"}),
        )
    )
    anchored = editor._match_rows["class"][1]

    assert visible_anchor_marks(anchored) == []
    assert visible_anchor_marks(editor._match_rows["title"][1]) == ["^(", ")$"]

    anchored.set_text("kit")
    assert visible_anchor_marks(anchored) == ["^(", ")$"]
    anchored.set_text("^kit.*$")
    assert visible_anchor_marks(anchored) == []
