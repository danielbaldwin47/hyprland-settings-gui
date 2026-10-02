"""Writing a file so that a reader sees the old bytes or the new, never half (#153 review).

One helper for every write outside the Writer and the Journal, which keep their own because
each needs a hook between the write and the rename: a theming tool's config (`bridge/wire`),
a Preset and its image (`presets`), and a theme file (`presets_archive`).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path


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
