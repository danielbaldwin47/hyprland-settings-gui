"""A live MainWindow over a real Session, for UI tests that need steps recorded (#189).

Shared by the undo and the display-countdown tests. Not a test module: the leading
underscore keeps pytest from collecting it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from started_app import started_application

APP_VERSION = "0.0.0-test"


def live_entity_window(tmp_path: Path) -> Any:
    """A real Session made live by an applier that reports entity commits on `settle()`.

    The UI tier has no compositor, so the applier stands where `_go_live` puts the real one,
    and `settle()` hands the session the verdict the queue would have -- which is where an
    entity step is recorded and the toast is raised.
    """
    from gi.repository import Adw

    from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult
    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    class SettlingApplier:
        serial = 0
        reported = 0

        def commit_entities(self) -> int:
            self.serial += 1
            return self.serial

        def settle(self) -> None:
            if self.serial > self.reported:
                self.reported = self.serial
                session._applied(ApplyResult(ApplyOutcome.OK, entities=self.serial))

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    applier = SettlingApplier()
    session._applier = applier
    session._offline_reason = None
    app = started_application()
    window = MainWindow(session, application=app)
    session.on_recorded = window.offer_undo
    return session, window, applier
