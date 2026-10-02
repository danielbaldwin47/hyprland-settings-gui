"""Letting go of widgets the app is done with, so Python can collect them (#219).

A Python object that owns a widget, and whose callable a handler inside that widget holds
(`lambda _row: self._choose(...)`, a bound method connected on a child or on one of its
event controllers), is a cycle that runs through C. Python's collector sees only its Python
half, and GTK frees nothing a live wrapper still holds. So a closed dialog stayed whole, a
closed window kept every Page in it, and each Binds Page refresh kept the rows it replaced.

`release` cuts the C half. Call it on a widget the app has closed or removed and will not
show again.
"""

from __future__ import annotations

import weakref
from collections.abc import Iterator

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, Gtk  # noqa: E402


def release(widget: Gtk.Widget) -> None:
    """Dispose `widget` and every widget under it that Python still holds.

    Disposing a widget drops its handlers and its children. Event controllers outlive
    dispose, so each held widget then loses them too, after every dispose has run: a dispose
    may remove a controller it added to another widget. A window's actions hold handlers as
    well. The walk keeps only weak references, so a widget whose wrapper is gone is GTK's
    alone, goes with its parent, and is never touched here.
    """
    if isinstance(widget, Gio.ActionMap):
        for name in widget.list_actions():
            widget.remove_action(name)
    held = [weakref.ref(each) for each in _preorder(widget)]
    for ref in held:
        live = ref()
        # Parentless only: a parent holds its children, and disposing a held child is a GTK
        # error. Preorder puts each parent first, so its dispose frees its children in turn.
        if live is not None and live.get_parent() is None:
            live.run_dispose()
    for ref in held:
        live = ref()
        if live is not None:
            for controller in list(live.observe_controllers()):
                live.remove_controller(controller)


def _preorder(widget: Gtk.Widget) -> Iterator[Gtk.Widget]:
    """`widget` and every widget under it, each parent before its children."""
    pending = [widget]
    while pending:
        current = pending.pop()
        yield current
        children = []
        child = current.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        pending.extend(reversed(children))
