"""The Manifest on its own: hashing, hand-edit detection, and reading a damaged file.

The Writer tests cover the happy path through a real write. What is worth isolating here
is the unhappy one -- a Manifest that is missing, truncated, or from a future format --
because "the app refuses to start" is the wrong answer to every one of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.state import FORMAT_VERSION, Manifest, ModuleRecord, content_hash

VERSIONS = {"app_version": "0.1.0", "schema_version": "0.56.2"}


class TestModuleRecord:
    def test_a_record_matches_the_bytes_it_was_made_from(self, tmp_path: Path) -> None:
        text = "hl.config({})\n"
        path = tmp_path / "module.lua"
        path.write_text(text, encoding="utf-8")

        assert ModuleRecord.of(text).matches(path)

    def test_any_edit_breaks_the_match(self, tmp_path: Path) -> None:
        record = ModuleRecord.of("hl.config({})\n")
        path = tmp_path / "module.lua"
        path.write_text("hl.config({ })\n", encoding="utf-8")

        assert not record.matches(path)

    def test_a_missing_file_does_not_match(self, tmp_path: Path) -> None:
        assert not ModuleRecord.of("x").matches(tmp_path / "gone.lua")

    def test_the_hash_is_of_the_utf8_bytes(self) -> None:
        assert ModuleRecord.of("é").sha256 == content_hash("é".encode())


class TestRoundTrip:
    def test_render_then_load_preserves_everything(self, tmp_path: Path) -> None:
        manifest = Manifest(
            app_version="0.1.0",
            schema_version="0.56.2",
            entrypoint=ModuleRecord.of("-- entry\n"),
            modules={"options/general.lua": ModuleRecord.of("hl.config({})\n")},
            migration={"date": "2026-08-22"},
        )
        path = tmp_path / "manifest.json"
        path.write_text(manifest.render(), encoding="utf-8")

        assert Manifest.load(path, **VERSIONS) == manifest

    def test_it_renders_as_readable_json(self, tmp_path: Path) -> None:
        """A user's App dir may well be in a dotfile repo; a one-line blob is hostile there."""
        rendered = Manifest(**VERSIONS).render()

        assert rendered.startswith("{\n")
        assert rendered.endswith("\n")
        assert f'"format_version": {FORMAT_VERSION}' in rendered


class TestDamagedFiles:
    def test_a_missing_manifest_reads_as_empty(self, tmp_path: Path) -> None:
        manifest = Manifest.load(tmp_path / "nope.json", **VERSIONS)

        assert manifest.modules == {}
        assert manifest.app_version == "0.1.0"

    def test_a_truncated_manifest_reads_as_empty_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        """An unrelated crash mid-write must not brick the app on next launch."""
        path = tmp_path / "manifest.json"
        path.write_text('{"format_version": 1, "modu', encoding="utf-8")

        assert Manifest.load(path, **VERSIONS).modules == {}

    def test_an_unknown_format_version_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text('{"format_version": 99, "modules": {"a.lua": {}}}', encoding="utf-8")

        assert Manifest.load(path, **VERSIONS).modules == {}

    def test_a_malformed_module_entry_is_dropped_not_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            '{"format_version": 1, "modules": {"a.lua": {"sha256": 5}, '
            '"b.lua": {"sha256": "ab", "size": 2}}}',
            encoding="utf-8",
        )

        assert set(Manifest.load(path, **VERSIONS).modules) == {"b.lua"}


class TestHandEditDetection:
    @pytest.fixture
    def paths(self, tmp_path: Path) -> ConfigPaths:
        config = ConfigPaths.rooted_at(tmp_path)
        config.app_dir.mkdir(parents=True)
        return config

    def test_it_names_only_the_changed_modules(self, paths: ConfigPaths) -> None:
        untouched, edited = "hl.config({})\n", "hl.config({ a = 1 })\n"
        (paths.app_dir / "a.lua").write_text(untouched, encoding="utf-8")
        (paths.app_dir / "b.lua").write_text("-- someone else\n", encoding="utf-8")

        manifest = Manifest(
            **VERSIONS,
            modules={
                "a.lua": ModuleRecord.of(untouched),
                "b.lua": ModuleRecord.of(edited),
            },
        )

        assert manifest.hand_edited(paths) == ("b.lua",)

    def test_a_deleted_module_counts_as_edited(self, paths: ConfigPaths) -> None:
        """Deletion is a change the app should surface, not silently undo."""
        manifest = Manifest(**VERSIONS, modules={"gone.lua": ModuleRecord.of("x")})

        assert manifest.hand_edited(paths) == ("gone.lua",)

    def test_a_hand_edited_entrypoint_is_detected_too(self, paths: ConfigPaths) -> None:
        """ADR-0016's Entrypoint refusal: the stored hash has to be compared to something."""
        text = "-- generated\n"
        paths.entrypoint.write_text("-- mine now\n", encoding="utf-8")
        manifest = Manifest(**VERSIONS, entrypoint=ModuleRecord.of(text))

        assert manifest.hand_edited(paths) == ("hyprland.lua",)

    def test_an_untouched_entrypoint_is_not_reported(self, paths: ConfigPaths) -> None:
        text = "-- generated\n"
        paths.entrypoint.write_text(text, encoding="utf-8")
        manifest = Manifest(**VERSIONS, entrypoint=ModuleRecord.of(text))

        assert manifest.hand_edited(paths) == ()
