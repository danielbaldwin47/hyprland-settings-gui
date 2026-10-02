"""ADR-0012 §Retirement: a removed Option stops being emitted and its value is kept.

Every test starts where a real session would: an App dir the Writer wrote from the pinned
sample model, its Manifest, and a Schema (plus, sometimes, a live snapshot) that no longer
holds one of the Options the user set. No sockets: the live snapshot is a plain value.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from _support import SAMPLE_APP_VERSION, sample_model, sample_schema

from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import Schema
from hyprtweaker.engine.state import Manifest, RetiredValue
from hyprtweaker.engine.state.retirement import Retirement, capture, detect, retire
from hyprtweaker.engine.writer import Writer

ACTIVE_BORDER = {"colors": ["rgba(33ccffee)", "rgba(00ff99ee)"], "angle": 45}
"""`general:col.active_border` as the sample model's Module spells it, read back."""


@dataclass(frozen=True)
class Live:
    """A live snapshot as the Session holds one: a parsable version and the option names."""

    version: str
    names: frozenset[str]


def live(version: str, *, without: tuple[str, ...] = ()) -> Live:
    return Live(version, frozenset(o.name for o in sample_schema()) - set(without))


def schema_without(*names: str, version: str) -> Schema:
    """The sample Schema as a later release that removed `names` would ship it."""
    base = sample_schema()
    return Schema(
        hyprland_version=version,
        options=tuple(o for o in base if o.name not in names),
        sections=base.sections,
    )


def schema_renaming(old: str, new: str, *, version: str) -> Schema:
    """The sample Schema as a release that renamed `old` to `new` would ship it."""
    base = sample_schema()
    path = tuple(new.replace(":", ".").split("."))
    return Schema(
        hyprland_version=version,
        options=tuple(
            replace(o, name=new, lua_key=".".join(path), path=path, renamed_from=old)
            if o.name == old
            else o
            for o in base
        ),
        sections=base.sections,
    )


@pytest.fixture
def paths(tmp_path: Path) -> ConfigPaths:
    paths = ConfigPaths.rooted_at(tmp_path)
    Writer(paths, SAMPLE_APP_VERSION).write(sample_model())
    return paths


def load(paths: ConfigPaths) -> Manifest:
    return Manifest.load(paths.manifest, app_version="x", schema_version="x")


class TestDetect:
    def test_an_owned_option_the_new_schema_lacks_is_retired(self, paths: ConfigPaths) -> None:
        """Trigger (a): the app's update ships a schema without the Option.

        `general:allow_tearing` goes too, but the user never set it, so nothing is retired."""
        schema = schema_without(
            "general:resize_on_border", "general:allow_tearing", version="0.57.0"
        )

        assert detect(load(paths), schema, None) == (
            Retirement("general:resize_on_border", "options/general.lua", "0.57.0"),
        )

    def test_a_live_snapshot_names_the_release(self, paths: ConfigPaths) -> None:
        schema = schema_without("general:resize_on_border", version="0.57.0")

        assert detect(load(paths), schema, live("0.57.1")) == (
            Retirement("general:resize_on_border", "options/general.lua", "0.57.1"),
        )

    def test_an_option_a_newer_hyprland_dropped_is_retired(self, paths: ConfigPaths) -> None:
        """Trigger (b): the schema still holds it; the running, newer compositor does not."""
        found = detect(
            load(paths),
            sample_schema(),
            live("0.57.0", without=("misc:disable_hyprland_logo",)),
        )
        assert found == ()

        found = detect(
            load(paths), sample_schema(), live("0.57.0", without=("decoration:rounding",))
        )
        assert found == (Retirement("decoration:rounding", "options/decoration.lua", "0.57.0"),)

    def test_an_older_hyprland_lacking_an_option_is_not_retirement(
        self, paths: ConfigPaths
    ) -> None:
        """Older than the schema, an absent name is an Option not added yet, not one removed."""
        found = detect(
            load(paths), sample_schema(), live("0.56.0", without=("decoration:rounding",))
        )

        assert found == ()


class TestRetire:
    """AC 1: retiring a set Option persists its value and retired-in version."""

    def test_a_schema_update_keeps_the_value_on_disk(self, paths: ConfigPaths) -> None:
        schema = schema_without(
            "general:col.active_border", "general:resize_on_border", version="0.57.0"
        )
        found = detect(load(paths), schema, None)

        retired = retire(load(paths), found, capture(paths.app_dir, found)).retired
        Writer(paths, SAMPLE_APP_VERSION).set_retired(ConfigModel(schema), retired)

        assert load(paths).retired == {
            "general:col.active_border": RetiredValue("0.57.0", ACTIVE_BORDER),
            "general:resize_on_border": RetiredValue("0.57.0", True),
        }

    def test_a_newer_hyprland_keeps_the_value_through_the_next_write(
        self, paths: ConfigPaths
    ) -> None:
        """Trigger (b), then the write that stops emitting the key: the value stays."""
        writer = Writer(paths, SAMPLE_APP_VERSION)
        found = detect(
            load(paths), sample_schema(), live("0.57.0", without=("decoration:shadow:offset",))
        )
        writer.set_retired(
            sample_model(), retire(load(paths), found, capture(paths.app_dir, found)).retired
        )

        model = sample_model()
        model.unset("decoration:shadow:offset")
        writer.write(model)

        assert load(paths).retired == {
            "decoration:shadow:offset": RetiredValue("0.57.0", [0, 2]),
        }
        assert "offset" not in (paths.app_dir / "options/decoration.lua").read_text()
        assert (
            "decoration:shadow:offset"
            not in load(paths).modules["options/decoration.lua"].options
        )

    def test_a_value_the_module_no_longer_holds_is_not_invented(
        self, paths: ConfigPaths
    ) -> None:
        """Hand-deleted Module: nothing to keep, so nothing is recorded as kept."""
        found = detect(
            load(paths), schema_without("misc:force_default_wallpaper", version="0.57.0"), None
        )
        (paths.app_dir / "options/misc.lua").unlink()

        assert found == (
            Retirement("misc:force_default_wallpaper", "options/misc.lua", "0.57.0"),
        )
        assert capture(paths.app_dir, found) == {}
        assert retire(load(paths), found, {}).retired == {}
