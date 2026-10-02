"""Presets, headless: Capture scopes against the Schema, and the store's file (#168).

What ADR-0014 §Presets promises that needs no Session: which Options each scope captures,
that a file holds Option names and JSON-native values that read back through `parse_value`,
and that the store reads hand-broken files tolerantly. The Session tier
(`test_session_presets.py`) proves capture, apply and undo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from _support import sample_schema

from hyprtweaker.engine.model.values import CssGaps, Gradient, parse_value
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.presets import (
    CaptureScope,
    Preset,
    PresetStore,
    scoped_options,
    stored_value,
)

CREATED = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)


def preset(**overrides: object) -> Preset:
    base: dict[str, object] = dict(
        name="Nord evening",
        created=CREATED,
        scopes=frozenset({CaptureScope.GAPS_LAYOUT}),
        options={"general:border_size": 3, "general:gaps_in": "5 10 5 10"},
        app_version="0.1.0",
        hyprland_version="0.56.2",
    )
    base.update(overrides)
    return Preset(**base)  # type: ignore[arg-type]


# --- Capture scopes -----------------------------------------------------------------------


def names(scope: CaptureScope) -> set[str]:
    return {option.name for option in scoped_options(sample_schema(), scope)}


def test_each_scope_captures_its_own_options_on_0_56_2() -> None:
    assert len(names(CaptureScope.COLORS)) == 22
    assert "general:col.active_border" in names(CaptureScope.COLORS)
    assert names(CaptureScope.GAPS_LAYOUT) == {
        "general:gaps_in",
        "general:gaps_out",
        "general:gaps_workspaces",
        "general:border_size",
        "decoration:rounding",
        "decoration:rounding_power",
    }
    assert names(CaptureScope.ANIMATION_SWITCHES) == {
        "animations:enabled",
        "animations:workspace_wraparound",
    }
    assert names(CaptureScope.FONTS_CURSOR) == {
        "group:groupbar:font_family",
        "group:groupbar:font_size",
        "group:groupbar:font_weight_active",
        "group:groupbar:font_weight_inactive",
        "misc:font_family",
        "misc:splash_font_family",
    }
    assert names(CaptureScope.WALLPAPER) == set()


def test_no_option_belongs_to_two_scopes() -> None:
    seen: dict[str, CaptureScope] = {}
    for scope in CaptureScope:
        for name in names(scope):
            assert name not in seen, f"{name} is in {seen.get(name)} and {scope}"
            seen[name] = scope


# --- values -------------------------------------------------------------------------------


def test_a_stored_value_reads_back_through_parse_value() -> None:
    schema = sample_schema()
    gradient = parse_value(schema["general:col.active_border"].type, "rgba(33ccffee) 45deg")
    gaps = CssGaps(5, 10, 5, 10)

    assert stored_value(gradient) == "ee33ccff 45deg"
    assert stored_value(gaps) == "5 10 5 10"
    assert stored_value(3) == 3
    assert stored_value(True) is True
    assert stored_value("JetBrains Mono") == "JetBrains Mono"
    assert isinstance(
        parse_value(schema["general:col.active_border"].type, "ee33ccff 45deg"), Gradient
    )
    assert parse_value(schema["general:gaps_in"].type, "5 10 5 10") == gaps


# --- the store ----------------------------------------------------------------------------


def test_presets_live_in_the_app_dir(tmp_path: Path) -> None:
    paths = ConfigPaths.rooted_at(tmp_path)
    assert paths.presets_dir == paths.app_dir / "presets"


def test_a_written_preset_is_one_json_file_of_the_adr_fields(tmp_path: Path) -> None:
    store = PresetStore(tmp_path / "presets")
    store.write("nord-evening", preset(wallpaper="/home/u/nord.png"))

    assert json.loads((tmp_path / "presets" / "nord-evening.json").read_text()) == {
        "format": 1,
        "name": "Nord evening",
        "created": "2026-10-02T09:30:00+00:00",
        "scopes": ["gaps-layout"],
        "options": {"general:border_size": 3, "general:gaps_in": "5 10 5 10"},
        "app_version": "0.1.0",
        "hyprland_version": "0.56.2",
        "wallpaper": "/home/u/nord.png",
    }


def test_the_store_round_trips_a_preset(tmp_path: Path) -> None:
    store = PresetStore(tmp_path / "presets")
    store.write("nord-evening", preset())

    assert store.load("nord-evening") == preset()
    assert store.list() == (("nord-evening", preset()),)


def test_listing_skips_what_is_not_a_preset_and_sorts_by_name(tmp_path: Path) -> None:
    store = PresetStore(tmp_path / "presets")
    store.write("zz", preset(name="alpha"))
    store.write("aa", preset(name="Beta"))
    (tmp_path / "presets" / "broken.json").write_text("{not json")
    (tmp_path / "presets" / "no-format.json").write_text('{"name": "x", "options": {}}')
    (tmp_path / "presets" / "bad-options.json").write_text(
        json.dumps({"format": 1, "name": "x", "options": [1, 2]})
    )

    assert [slug for slug, _ in store.list()] == ["zz", "aa"]
    assert store.load("broken") is None
    assert store.load("missing") is None


def test_a_newer_format_reads_the_keys_it_knows(tmp_path: Path) -> None:
    """ADR-0014 §Sharing: a newer app's file warns and skips, never fails."""
    (tmp_path / "presets").mkdir()
    (tmp_path / "presets" / "new.json").write_text(
        json.dumps(
            {
                "format": 2,
                "name": "From the future",
                "created": "2026-10-02T09:30:00+00:00",
                "scopes": ["colors", "sparkles"],
                "options": {"general:border_size": 3},
                "curves": {"easeOut": [0.1, 0.2]},
            }
        )
    )

    loaded = PresetStore(tmp_path / "presets").load("new")

    assert loaded is not None
    assert loaded.name == "From the future"
    assert loaded.scopes == frozenset({CaptureScope.COLORS})
    assert loaded.options == {"general:border_size": 3}
    assert loaded.app_version is None and loaded.wallpaper is None


def test_every_write_and_delete_moves_the_revision(tmp_path: Path) -> None:
    store = PresetStore(tmp_path / "presets")
    first = store.revision
    store.write("a", preset())
    second = store.revision
    store.delete("a")

    assert first < second < store.revision
    assert store.list() == ()


def test_a_write_leaves_no_scratch_file(tmp_path: Path) -> None:
    store = PresetStore(tmp_path / "presets")
    store.write("a", preset())
    store.write("a", preset(name="Again"))

    assert sorted(p.name for p in (tmp_path / "presets").iterdir()) == ["a.json"]
    assert store.load("a") == preset(name="Again")
