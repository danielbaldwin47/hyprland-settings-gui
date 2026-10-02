"""Rule matching against the live window list (#113, ADR-0008).

The windows are the nested-compositor capture in `_fake_hyprland.CLIENTS`: `probe.tiled`
(tiled), `probe.float` (floating, pinned, tagged `demo*`) and `probe.fs` (fullscreen 2/2),
all on workspace 1. Expected counts are read off that capture, not recomputed.
"""

from __future__ import annotations

import json
from typing import Any

from _fake_hyprland import CLIENTS

from hyprtweaker.engine.rule_matching import MatchCount, badge_text, count_matches

WINDOWS: tuple[dict[str, Any], ...] = tuple(json.loads(CLIENTS))


def windows(match: dict[str, Any]) -> MatchCount | None:
    return count_matches("window", match, WINDOWS)


def test_a_regex_must_match_the_whole_value() -> None:
    assert windows({"class": r"probe\.float"}) == MatchCount(1, ())
    assert windows({"class": "probe"}) == MatchCount(0, ())
    assert windows({"class": r"probe\..*"}) == MatchCount(3, ())


def test_an_exact_pick_prefill_counts_the_one_window() -> None:
    assert windows({"class": r"^(probe\.float)$"}) == MatchCount(1, ())


def test_negative_inverts_the_regex() -> None:
    assert windows({"class": r"negative:probe\.tiled"}) == MatchCount(2, ())


def test_every_prop_must_hold_at_once() -> None:
    assert windows({"class": r"probe\..*", "float": True}) == MatchCount(1, ())
    assert windows({"class": r"probe\.tiled", "float": True}) == MatchCount(0, ())


def test_booleans_read_the_windows_state() -> None:
    assert windows({"float": True}) == MatchCount(1, ())
    assert windows({"pin": False}) == MatchCount(2, ())
    assert windows({"xwayland": False}) == MatchCount(3, ())
    assert windows({"fullscreen": True}) == MatchCount(1, ())
    assert windows({"group": False}) == MatchCount(3, ())


def test_fullscreen_state_ints_read_internal_and_client_separately() -> None:
    assert windows({"fullscreen_state_internal": 2}) == MatchCount(1, ())
    assert windows({"fullscreen_state_client": 0}) == MatchCount(2, ())


def test_a_workspace_by_id_or_name() -> None:
    assert windows({"workspace": "1"}) == MatchCount(3, ())
    assert windows({"workspace": "name:1"}) == MatchCount(3, ())
    assert windows({"workspace": "2"}) == MatchCount(0, ())


def test_a_tag_matches_a_rule_set_tag_with_or_without_its_star() -> None:
    assert windows({"tag": "demo"}) == MatchCount(1, ())
    assert windows({"tag": "demo*"}) == MatchCount(1, ())
    assert windows({"tag": "other"}) == MatchCount(0, ())


def test_content_and_xdg_tag_are_regexes_over_their_names() -> None:
    assert windows({"content": "none"}) == MatchCount(3, ())
    assert windows({"xdg_tag": "dock"}) == MatchCount(0, ())


def test_a_prop_the_payload_cannot_answer_is_skipped_and_named() -> None:
    assert windows({"class": r"probe\.float", "modal": True}) == MatchCount(1, ("modal",))
    assert windows({"focus": True, "float": True}) == MatchCount(1, ("focus",))


def test_selectors_and_numeric_content_ids_are_skipped_rather_than_guessed() -> None:
    assert windows({"float": True, "workspace": "r[1-3]"}) == MatchCount(1, ("workspace",))
    assert windows({"float": True, "workspace": "negative:2"}) == MatchCount(1, ("workspace",))
    assert windows({"float": True, "content": "0"}) == MatchCount(1, ("content",))
    assert windows({"float": True, "tag": "negative:demo"}) == MatchCount(1, ("tag",))


def test_a_plugin_prop_nobody_knows_is_skipped() -> None:
    assert windows({"float": True, "plugin:thing": "x"}) == MatchCount(1, ("plugin:thing",))


def test_a_field_missing_from_the_windows_counts_as_unanswerable() -> None:
    bare = ({"class": "kitty", "title": "~"},)
    assert count_matches("window", {"class": "kitty", "float": True}, bare) == MatchCount(
        1, ("float",)
    )


def test_when_nothing_can_be_checked_there_is_no_count_at_all() -> None:
    assert windows({"modal": True}) is None
    assert windows({"focus": True, "modal": False}) is None


def test_an_invalid_regex_is_no_count_not_an_exception() -> None:
    assert windows({"class": "(unclosed"}) is None
    assert windows({"class": "negative:[bad"}) is None


def test_no_match_props_is_no_count() -> None:
    assert windows({}) is None


def test_no_open_windows_is_a_real_zero() -> None:
    assert count_matches("window", {"class": "kitty"}, ()) == MatchCount(0, ())


def test_a_layer_rule_counts_namespaces_over_the_flattened_surfaces() -> None:
    surfaces = [
        {"namespace": "wallpaper"},
        {"namespace": "forest-shell:keep-awake"},
        {"namespace": "waybar"},
    ]
    assert count_matches("layer", {"namespace": "^(waybar)$"}, surfaces) == MatchCount(1, ())
    assert count_matches("layer", {"namespace": "forest-.*"}, surfaces) == MatchCount(1, ())
    assert count_matches("layer", {"namespace": "negative:waybar"}, surfaces) == MatchCount(
        2, ()
    )


def test_badge_text_names_the_noun_and_what_was_skipped() -> None:
    assert badge_text("window", MatchCount(3, ())) == "Matches 3 open windows"
    assert badge_text("window", MatchCount(1, ())) == "Matches 1 open window"
    assert badge_text("window", MatchCount(0, ())) == "Matches 0 open windows"
    assert (
        badge_text("window", MatchCount(3, ("tag",)))
        == "Matches 3 open windows, not checking tag"
    )
    assert (
        badge_text("window", MatchCount(2, ("initial_class", "modal")))
        == "Matches 2 open windows, not checking initial class and modal"
    )
    assert badge_text("layer", MatchCount(1, ())) == "Matches 1 layer surface"
    assert badge_text("layer", MatchCount(2, ())) == "Matches 2 layer surfaces"
