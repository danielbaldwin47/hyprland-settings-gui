"""Writing a file so that a reader sees the old bytes or the new, never half (#153 review).

One helper for every write outside the Writer and the Journal, which keep their own because
each needs a hook between the write and the rename: a theming tool's config (`bridge/wire`),
a Preset and its image (`presets`), and a theme file (`presets_archive`).

And one rule for a hand edit the app is about to replace: `keep_edited_copy` puts its bytes
where the user can find them first (Replace file, Roll back, Restore).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .paths import ConfigPaths


def failure_reason(error: BaseException) -> str:
    """Why something failed, in words for the user: an `OSError`'s message without its
    number or path ("permission denied"), else a plain sentence. Python's own spelling
    ("[Errno 5] Input/output error") belongs in the log (review m1 F12, #282)."""
    if isinstance(error, OSError) and error.strerror:
        return error.strerror[:1].lower() + error.strerror[1:]
    return "something unexpected went wrong; the log has the details"


def write_atomic(path: Path, content: str | bytes) -> None:
    """Write `content` to `path` through a temporary beside it, then one rename.

    The temporary has a unique name, so two writers never share one; its bytes reach the
    disk before the rename; an existing file's permissions carry over. Raises `OSError`, and
    leaves no temporary behind when it does.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            with contextlib.suppress(OSError):
                os.fsync(stream.fileno())
        with contextlib.suppress(OSError):
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def keep_edited_copy(paths: ConfigPaths, source: Path, name: str) -> Path | None:
    """Copy `source` to `edited-copies/<stamp>[-n]/<name>` before the app replaces it.

    A directory of its own per copy, so a second copy in the same second never lands on the
    first, and written through `write_atomic`, so a copy is whole or absent. `None` when
    `source` does not exist: there is nothing of the user's to copy. Raises `OSError` when
    the copy cannot be made; the caller decides what that stops, and says it.
    """
    if not source.exists():
        return None
    copy = free_stamped_folder(paths.edited_copies_dir, name) / name
    write_atomic(copy, source.read_bytes())
    return copy


def free_stamped_folder(parent: Path, name: str, now: datetime | None = None) -> Path:
    """`parent/<stamp>[-n]`, the first in which `name` is free.

    A folder of its own per copy, so two made in one second never meet: an edited copy, a
    rolled-back App dir. `now` is the caller's clock where it has one to inject.
    """
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    base = parent / stamp
    suffix = 1
    while (base / name).exists():
        suffix += 1
        base = parent / f"{stamp}-{suffix}"
    return base
