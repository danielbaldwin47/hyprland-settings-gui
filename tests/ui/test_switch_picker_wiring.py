"""UI smoke tier: every door that opens Capture hands it the same switch source (#107).

Capture is built in three places in the window (add and edit through the bind editor, rebind
from the conflict popover and "Fix trigger…"). A door that misses the source would offer
the picker a different list, or none, depending on how the user got there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _live_window import live_entity_window


def spy(monkeypatch: Any, module: Any, name: str) -> list[dict[str, Any]]:
    """Replace a dialog class on the window module with a recorder of its keyword arguments."""
    seen: list[dict[str, Any]] = []

    class Recorder:
        def __init__(self, **kwargs: Any) -> None:
            seen.append(kwargs)

        def present(self, _parent: Any) -> None:
            pass

    monkeypatch.setattr(module, name, Recorder)
    return seen


def window_with_a_bind(tmp_path: Path) -> tuple[Any, Any]:
    from hyprtweaker.engine.model.entities import Bind, DispatcherCall

    session, window, applier = live_entity_window(tmp_path)
    assert session.add_bind(
        Bind(keys="SUPER + Q", dispatcher=DispatcherCall(path="exec_cmd", positional=("true",)))
    )
    applier.settle()
    window._refresh_binds()
    return session, window


def test_add_edit_and_rebind_all_get_the_live_switch_source(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from hyprtweaker.ui.shell import window as window_module

    session, window = window_with_a_bind(tmp_path)
    editors = spy(monkeypatch, window_module, "BindEditor")
    captures = spy(monkeypatch, window_module, "CaptureDialog")

    window._add_bind()
    window._edit_bind(0)
    window._rebind_bind(0)

    assert [seen["fetch_switches"] for seen in editors + captures] == [
        session.fetch_switches
    ] * 3


def test_with_no_compositor_every_door_hands_capture_no_source(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from hyprtweaker.ui.shell import window as window_module

    session, window = window_with_a_bind(tmp_path)
    session._offline_reason = "no compositor in the UI smoke tier"
    editors = spy(monkeypatch, window_module, "BindEditor")
    captures = spy(monkeypatch, window_module, "CaptureDialog")

    window._add_bind()
    window._edit_bind(0)
    window._rebind_bind(0)

    assert [seen["fetch_switches"] for seen in editors + captures] == [None] * 3
