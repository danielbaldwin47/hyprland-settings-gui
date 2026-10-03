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
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

PARENTED = "release() needs a widget already removed from its parent, not a {} still in it"


def release(widget: Gtk.Widget) -> None:
    """Dispose `widget` and every widget under it that Python still holds.

    Disposing a widget drops its handlers and its children. Event controllers outlive
    dispose, so each held widget then loses them too, after every dispose has run: a dispose
    may remove a controller it added to another widget. A window's actions hold handlers as
    well. The walk keeps only weak references, so a widget whose wrapper is gone is GTK's
    alone, goes with its parent, and is never touched here.

    Everything under `widget` goes, so a control the caller keeps and shows again (a
    button reused across refreshes, a row a later page re-adds) must be detached from the
    tree first, or it comes back with no handlers.

    `widget` must be out of the tree already. Raises `ValueError` on one that still has a
    parent: stripping the controllers of live UI would leave it on screen and dead, a
    defect no test sees unless it clicks, and a raise makes the caller's ordering mistake
    fail where it is made.
    """
    if widget.get_parent() is not None:
        raise ValueError(PARENTED.format(type(widget).__name__))
    if isinstance(widget, Gio.ActionMap):
        for name in widget.list_actions():
            widget.remove_action(name)
    held = [weakref.ref(each) for each in _preorder(widget)]
    for ref in held:
        live = ref()
        # Parentless only: a parent holds its children, and disposing a held child is a GTK
        # error. Preorder puts each parent first, so its dispose frees its children in turn.
        if live is None or live.get_parent() is not None:
            continue
        if isinstance(live, Adw.ComboRow):
            _unhook_combo_row(live)
        elif isinstance(live, Adw.ToastOverlay):
            _unhook_toast_overlay(live)
        else:
            live.run_dispose()
    for ref in held:
        live = ref()
        if live is not None:
            for controller in list(live.observe_controllers()):
                live.remove_controller(controller)


def release_when_unparented(widget: Gtk.Widget) -> None:
    """Release `widget` from an idle once it is out of its parent, now or when it leaves.

    For a widget libadwaita takes out only after an animation: a dialog that is closing, a
    navigation page that was popped (F18 of the #148 review). An idle either way: every
    handler of the removal must still find the widget whole.
    """
    if widget.get_parent() is None:
        GLib.idle_add(release, widget)
        return

    def out(current: Gtk.Widget, _pspec: GObject.ParamSpec) -> None:
        if current.get_parent() is None:
            current.disconnect(handler)
            GLib.idle_add(release, current)

    handler = widget.connect("notify::parent", out)


def _unhook_combo_row(row: Adw.ComboRow) -> None:
    """What disposing `row` would let go of, without disposing it (#228).

    `Adw.ComboRow`'s dispose runs again when the row is finalized, and the second run hands
    `gtk_list_view_set_model` the list views the first one cleared: two Gtk-CRITICALs per
    row (libadwaita 1.9). So the row keeps its children and goes whole when its wrapper
    does, once nothing in it calls back into Python: handlers go first, so clearing the
    model emits nothing the app would hear.
    """
    for each in _preorder(row):
        GObject.signal_handlers_destroy(each)
    row.set_expression(None)
    row.set_factory(None)
    row.set_list_factory(None)
    row.set_header_factory(None)
    row.set_model(None)


def _unhook_toast_overlay(overlay: Adw.ToastOverlay) -> None:
    """Let `overlay`'s child go, without disposing the overlay itself.

    `Adw.ToastOverlay`'s dispose frees its queue of waiting toasts, and runs again when the
    overlay is finalized: a window closed with two toasts up aborted on a double free
    (libadwaita 1.9, found in #272). Its child is parentless then, and released in turn.
    """
    GObject.signal_handlers_destroy(overlay)
    overlay.set_child(None)


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
