"""The UI tier's log gate: a test fails when the toolkit complains while it runs (#228).

GTK and libadwaita report a misuse (a dispose run twice, a child removed from a box it was
never in, markup that does not parse) as a GLib CRITICAL or WARNING, print it to stderr and
carry on. PyGObject does the same with a Python exception raised in a signal handler or an
idle: it prints the traceback and returns. A test around either passes, which is how #156's,
#169's and the #151 review's defects reached review unseen.

The gate funnels all three into one per-process record, and `conftest.py` fails the test
phase (setup, call or teardown) during which a record landed, naming each message:

- GLib's structured log writer, which every `g_log` call reaches, for any domain's
  CRITICAL or WARNING. Installed once per process: GLib aborts on a second install.
- PyGObject turns the `GLib`, `GLib-GObject` and `GThread` domains into Python warnings
  before the writer sees them; a handler of our own on those domains, which wins because
  GLib asks the newest handler first, sends them back to the default handler and so to
  the writer.
- `sys.excepthook`, which PyGObject's `PyErr_Print` calls for an exception a Python
  callback raised inside the main loop. The previous hook still prints it.

A record is attributed to the phase that was running when it landed, so an idle a test
left queued and a later test ran is that later test's. The tier's `released_windows`
fixture settles the loop at each test's teardown, which keeps that rare.
"""

from __future__ import annotations

import ctypes
import sys
import traceback
from types import TracebackType

GATED = ("CRITICAL", "WARNING")
REDIRECTED_BY_PYGOBJECT = ("GLib", "GLib-GObject", "GThread")

_records: list[str] = []
_installed = False


def install() -> None:
    """Start recording, once per process; later calls do nothing."""
    global _installed
    if _installed:
        return
    from gi.repository import GLib

    gated = GLib.LogLevelFlags.LEVEL_CRITICAL | GLib.LogLevelFlags.LEVEL_WARNING
    for domain in REDIRECTED_BY_PYGOBJECT:
        GLib.log_set_handler(domain, gated, _to_default_handler, None)
    GLib.log_set_writer_func(_writer, None)
    previous = sys.excepthook

    def hook(kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
        _records.append(
            "Python exception in a callback, printed and swallowed:\n"
            + "".join(traceback.format_exception(kind, value, tb)).rstrip()
        )
        previous(kind, value, tb)

    sys.excepthook = hook
    _installed = True


def take() -> list[str]:
    """Every record since the last `take`, oldest first, and forget them."""
    taken = _records[:]
    del _records[: len(taken)]  # a thread's append after the copy stays for the next take
    return taken


def _to_default_handler(domain: str, level: object, message: str, _data: object) -> None:
    from gi.repository import GLib

    GLib.log_default_handler(domain, level, message, None)


def _writer(level: object, fields: list[object], *_rest: object) -> object:
    from gi.repository import GLib

    name = _gated_level(level)
    if name is not None:
        values = {field.key: _field_text(field) for field in fields}  # type: ignore[attr-defined]
        domain = values.get("GLIB_DOMAIN") or "(no domain)"
        _records.append(f"{domain}-{name}: {values.get('MESSAGE', '')}")
    return GLib.log_writer_default(level, fields, None)


def _gated_level(level: object) -> str | None:
    from gi.repository import GLib

    for name in GATED:
        if int(level) & int(getattr(GLib.LogLevelFlags, f"LEVEL_{name}")):  # type: ignore[call-overload]
            return name
    return None


def _field_text(field: object) -> str:
    """A `GLogField`'s value: PyGObject hands it over as a raw pointer and a length."""
    pointer, length = field.value, field.length  # type: ignore[attr-defined]
    if not pointer:
        return ""
    raw = ctypes.string_at(pointer) if length < 0 else ctypes.string_at(pointer, length)
    return raw.decode("utf-8", errors="replace")
