"""An interrupted switch, a refused Roll back and a finished one, on a real compositor (#268).

The unit tier proves each Roll back decision by bytes. This journey proves the bytes and the
live session agree at every turn: a switch stopped after its marker leaves the running
config alone and a relaunch puts every file back; a Roll back that cannot keep a copy of a
hand edit changes nothing, files or live state; once it can, the edit is kept, every file is
back, and `reload full-reset` lands the session on the config it started on.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest \\
        tests/integration/test_migration_recovery_live.py -m hyprland
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION, CompositorState, capture, diff

from hyprtweaker.engine.ipc import CommandClient
from hyprtweaker.engine.migration.flow import MigrationFlow, fresh_start
from hyprtweaker.engine.model.values import CssGaps
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"
IMPORTED = "general {\n    gaps_in = 9\n}\n"
MARK = "\n-- hand edit during the countdown: marker-268-6f1c\n"
OPTIONS = ("general:gaps_in", "general:gaps_out", "decoration:rounding")


class _Crash(BaseException):
    """The app stopping here: nothing after it runs."""


def _files(paths: ConfigPaths) -> dict[str, str]:
    return {
        str(item.relative_to(paths.hypr_dir)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(paths.hypr_dir.rglob("*"))
        if item.is_file()
    }


def _flow(paths: ConfigPaths, nested: NestedHyprland) -> MigrationFlow:
    return MigrationFlow(
        paths=paths,
        schema=load_schema(SCHEMA_VERSION, SCHEMA_DIR),
        app_version=APP_VERSION,
        client=CommandClient(nested.instance),
    )


def _settled(nested: NestedHyprland, target: CompositorState) -> CompositorState:
    """The live state once a reload has landed: polled until it matches `target`, 5 s."""
    deadline = time.monotonic() + 5.0
    while True:
        now = capture(nested, options=OPTIONS)
        if diff(target, now).empty or time.monotonic() > deadline:
            return now
        time.sleep(0.2)


def test_interrupted_refused_and_finished_roll_backs_agree_with_the_live_session(
    harness_home: Path, artifacts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = ConfigPaths(
        hypr_dir=harness_home / ".config" / "hypr",
        state_dir=harness_home / ".local" / "state" / "hyprtweaker",
    )
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)
    schema = load_schema(SCHEMA_VERSION, SCHEMA_DIR)
    model = fresh_start(paths, schema, app_version=APP_VERSION)
    model.set("general:gaps_in", CssGaps(3, 3, 3, 3))
    Writer(paths, app_version=APP_VERSION).write(model)
    source = harness_home / "other.conf"
    source.write_text(IMPORTED, encoding="utf-8")
    before = _files(paths)

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested.log"
    ) as nested:
        original = capture(nested, options=OPTIONS)
        original.write(artifacts / "original.json")

        # 1. Interrupted after the marker, before the App dir moved: nothing reloaded.
        first = _flow(paths, nested)
        first.detect()
        first.build_preview(source)
        first.back_up()
        real_replace = os.replace

        def stop_at_app_dir(src: object, dst: object) -> None:
            if Path(str(src)) == paths.app_dir:
                raise _Crash
            real_replace(src, dst)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "replace", stop_at_app_dir)
        with pytest.raises(_Crash):
            asyncio.run(first.switch())
        monkeypatch.undo()
        assert diff(original, capture(nested, options=OPTIONS)).empty

        relaunched = _flow(paths, nested)
        pending = relaunched.pending_switch()
        assert pending is not None
        outcome = asyncio.run(relaunched.roll_back_live(pending))
        assert outcome.complete, outcome.rescue
        assert _files(paths) == before
        assert not paths.sentinel.exists()
        landed = _settled(nested, original)
        assert diff(original, landed).empty, diff(original, landed).describe()

        # 2. A whole switch goes live; a hand edit lands during the countdown.
        second = _flow(paths, nested)
        second.detect()
        second.build_preview(source)
        second.back_up()
        result = asyncio.run(second.switch())
        assert result.ok, result.checks
        switched_live = capture(nested, options=OPTIONS)
        switched_live.write(artifacts / "switched.json")
        assert not diff(original, switched_live).empty, "the import changed nothing live"
        with paths.entrypoint.open("a", encoding="utf-8") as handle:
            handle.write(MARK)
        edited = paths.entrypoint.read_bytes()
        switched = _files(paths)

        # 3. Refused: no copy of the edit can be kept, so nothing changes, files or live.
        paths.edited_copies_dir.write_text("in the way", encoding="utf-8")
        refused = asyncio.run(second.roll_back_live())
        assert not refused.complete
        assert refused.rescue.startswith("Nothing was rolled back:")
        assert _files(paths) == switched
        assert paths.sentinel.exists()
        still = _settled(nested, switched_live)
        assert diff(switched_live, still).empty, diff(switched_live, still).describe()

        # 4. Relaunched with room for the copy: the edit is kept, every file is back, and
        # the session reloads onto the config it started on.
        paths.edited_copies_dir.unlink()
        again = _flow(paths, nested)
        done = asyncio.run(again.roll_back_live(again.pending_switch()))
        assert done.complete, done.rescue
        assert done.edited_copy is not None
        assert done.edited_copy.read_bytes() == edited
        assert _files(paths) == before
        assert not paths.sentinel.exists()
        back = _settled(nested, original)
        back.write(artifacts / "rolled-back.json")
        assert diff(original, back).empty, diff(original, back).describe()
