"""Run only by `test_log_gate.py`, in a child pytest, so the gate's failures stay there.

Not named `test_*.py`, so the suite never collects it; the child run names it explicitly.
Each test but the first does one thing the toolkit complains about and would pass without
the gate.
"""

from __future__ import annotations

from collections.abc import Iterator

import main_loop
import pytest


def test_a_quiet_test_passes() -> None:
    from gi.repository import Gtk

    box = Gtk.Box()
    box.append(Gtk.Label())


def test_removing_a_child_the_box_does_not_hold() -> None:
    from gi.repository import Gtk

    Gtk.Box().remove(Gtk.Label())


def test_a_signal_handler_that_raises() -> None:
    from gi.repository import Gtk

    def broken(_button: Gtk.Button) -> None:
        raise ValueError("the handler broke")

    button = Gtk.Button()
    button.connect("clicked", broken)
    button.emit("clicked")


def test_an_idle_that_raises() -> None:
    from gi.repository import GLib

    def broken() -> bool:
        raise ValueError("the idle broke")

    GLib.idle_add(broken)
    main_loop.settle("the idle")


def test_notifying_a_property_the_object_lacks() -> None:
    from gi.repository import GObject

    GObject.Object().notify("no-such-property")


@pytest.fixture
def complains_on_teardown() -> Iterator[None]:
    from gi.repository import Gtk

    yield
    Gtk.Box().remove(Gtk.Label())


def test_a_fixture_that_complains_on_teardown(complains_on_teardown: None) -> None:
    pass
