"""Fences for the unit tier: no test here reaches the session's own compositor."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_ambient_compositor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the signature naming the compositor this suite was started under.

    A `Session` built without `connect` asks `Instance.current()` which Hyprland is running
    (#176's startup read). From a developer's shell that is their own desktop: read-only,
    but a test that reaches it depends on whatever that desktop runs. Unset here, it answers
    `NoInstance`, whatever a test forgets to pass. A test that wants a signature sets one.
    """
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
