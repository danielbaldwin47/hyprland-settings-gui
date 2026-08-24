"""Export: the whole config as one self-contained `hyprland.lua` (ADR-0009).

Modules concatenated in require order, `vars` inlined, header comment -- a file that runs
on any Hyprland >= 0.56 with this app not installed. Export to a USB stick, import on the
other machine; interop, not lock-in.

Two things make a flattened file behave like the require chain it replaces, and both are
about the fact that `require` is not textual inclusion:

- **Each inlined file keeps its own scope.** A chunk is wrapped in a function that is called
  immediately, so a `local` at the top of `user.lua` stays local and a top-level `return`
  ends *that* chunk rather than truncating the export at the first module that has one.
  Pasting the bodies end to end would do both wrong, and the second one silently: everything
  after the first `return` would simply not be config any more.
- **Requires between the inlined files still resolve.** A shim consults what has already
  been inlined before falling back to the real `require`, so a `legacy.lua` that reads
  `vars` finds the copy in this file rather than a path that exists only on the machine the
  export came from.

Structure is deliberately lost: portability over structure. Re-importing an export gives
back a normal App dir.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..model import ConfigModel
from ..paths import ConfigPaths
from ..writer import ModuleSet, Writer

HEADER = """\
-- Hyprland config exported by hyprtweaker {version} on {when}.
--
-- Self-contained: every module this config was assembled from is inlined below, in the
-- order Hyprland loaded them. It needs no hyprtweaker installation and no other file.
-- Drop it in as ~/.config/hypr/hyprland.lua on any Hyprland {minimum} or newer.
--
-- Importing it back into hyprtweaker restores the usual per-section module layout.
"""

MINIMUM_HYPRLAND = "0.56"

SHIM = """\
-- Lets the inlined chunks below require each other, exactly as they did as files.
local __inlined = {}
local __host_require = require
local function require(name)
  local found = __inlined[name]
  if found ~= nil then
    return found
  end
  return __host_require(name)
end
"""


@dataclass(frozen=True, slots=True)
class ExportResult:
    """A rendered export and an account of what went into it."""

    text: str
    inlined: tuple[str, ...]
    """The require paths inlined, in load order."""

    missing: tuple[str, ...]
    """Requires the Entrypoint names that could not be read, so are absent from the output.

    Reported rather than raised: a Bridge module owned by an uninstalled tool is a normal
    thing to find, and an export that silently dropped it would be a config the user
    believes is complete.
    """

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.text, encoding="utf-8")
        return path


def _chunk(require_path: str, body: str) -> str:
    """One inlined file: its own scope, registered under the name it used to be required by."""
    text = body if body.endswith("\n") else f"{body}\n"
    indented = "".join(f"  {line}" if line.strip() else line for line in text.splitlines(True))
    return (
        f"-- >>> {require_path}\n"
        f'__inlined["{require_path}"] = (function()\n'
        f"{indented}"
        f"end)() or true\n"
        f"-- <<< {require_path}\n"
    )


def render(
    model: ConfigModel,
    paths: ConfigPaths,
    *,
    app_version: str,
    now: datetime | None = None,
) -> ExportResult:
    """Flatten the model plus everything the Entrypoint requires into one Lua file.

    The generated Modules are rendered from `model` rather than read from the App dir, so an
    export is of what the app currently holds -- not of whatever was last written to disk.
    The files the app does *not* own (`vars`, `legacy`, Bridges, `user`) have no in-memory
    form and are read from disk, which is also the only honest source for them.
    """
    writer = Writer(paths, app_version=app_version)
    rendered = writer.render_modules(model)
    by_require = {
        paths.require_path(paths.app_dir / relpath): text for relpath, text in rendered.items()
    }
    module_set = ModuleSet.discover(paths, tuple(rendered))

    order: Sequence[str] = (
        *module_set.modules,
        *([module_set.legacy] if module_set.legacy else []),
        *module_set.bridges,
        *([module_set.user] if module_set.user else []),
    )

    chunks: list[str] = []
    inlined: list[str] = []
    missing: list[str] = []
    for require_path in order:
        body = by_require.get(require_path)
        if body is None:
            try:
                body = (paths.hypr_dir / f"{require_path}.lua").read_text(encoding="utf-8")
            except OSError:
                missing.append(require_path)
                continue
        chunks.append(_chunk(require_path, body))
        inlined.append(require_path)

    when = (now or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    header = HEADER.format(version=app_version, when=when, minimum=MINIMUM_HYPRLAND)
    if missing:
        header += (
            "--\n-- Not included, because these files could not be read when exporting:\n"
            + "".join(f"--   {name}\n" for name in missing)
        )

    text = "\n".join([header.rstrip("\n"), "", SHIM, *chunks]).rstrip("\n") + "\n"
    return ExportResult(text=text, inlined=tuple(inlined), missing=tuple(missing))
