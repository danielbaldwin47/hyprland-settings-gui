"""The sandbox's non-unique switch: one env var, read when the application is built.

Toolkit imports sit inside the tests, as elsewhere in this tier, so a machine without
PyGObject skips rather than errors at collection.
"""

from __future__ import annotations

import pytest


def test_the_app_claims_its_id_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from gi.repository import Gio

    from hyprtweaker.application import NON_UNIQUE_ENV, HyprtweakerApplication

    monkeypatch.delenv(NON_UNIQUE_ENV, raising=False)
    app = HyprtweakerApplication()
    assert not app.get_flags() & Gio.ApplicationFlags.NON_UNIQUE


def test_a_sandboxed_app_is_non_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    from gi.repository import Gio

    from hyprtweaker.application import NON_UNIQUE_ENV, HyprtweakerApplication

    monkeypatch.setenv(NON_UNIQUE_ENV, "1")
    app = HyprtweakerApplication()
    assert app.get_flags() & Gio.ApplicationFlags.NON_UNIQUE
