"""Whose file failed: attributing `configerrors` lines by their `file:line` prefix.

ADR-0016 makes attribution the thing recovery branches on -- "each `configerrors` line is
attributed by its file prefix to an Ownership class, and the class decides the recovery" --
so the parse has to happen before any policy can. This module is that parse and nothing else:
lines in, classes out, no decisions.

Hyprland writes errors in exactly two shapes (research #5 §6, and both are in the unit
tier's scripted compositor):

* `<absolute path>:<line>: <message>` -- something inside a file the reload executed;
* `require("<module path>"): <message>` -- a `require`d file that failed to load at all,
  which is the loud one: a broken `binds.lua` means zero keybinds.

Matching is by **suffix on the app-relative name**, not by comparing absolute paths. The path
Hyprland prints is the one it opened, which may have travelled through a symlinked dotfile
directory, a bind mount, or a `$HOME` the app resolved differently -- and an attribution that
missed for any of those reasons would silently downgrade an own-write failure to "somebody
else's file", which is the one class ADR-0016 answers by *not* auto-reverting.

The one class this module refuses to guess at is `OWN_WRITE`. It is the class that authorises
an automatic write to the user's config, so it is only ever claimed for a file the caller can
show *this* transaction laid down -- `WriteResult.written`, never "a file the app owns".
"""

from __future__ import annotations

import enum
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..paths import APP_DIR_NAME, BRIDGE_DIR, ENTRYPOINT_NAME, LEGACY_MODULE, USER_MODULE

_FILE_LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):\s*(?P<message>.*)$")
"""`<path>:<line>: <message>`. Non-greedy, so a path containing a colon still splits at the
one followed by digits and a colon rather than at the first one in the string."""

_REQUIRE = re.compile(
    r'^require\((?P<quote>["\'])(?P<module>.+?)(?P=quote)\)\s*:\s*(?P<message>.*)$'
)
"""`require("hyprtweaker/options/binds"): <message>` -- a module that would not load."""


class Ownership(enum.StrEnum):
    """Who owns the file an error came from. ADR-0016's four classes, plus "no idea"."""

    OWN_WRITE = "own-write"
    """An App-dir Module *this transaction* wrote. The one class that auto-reverts."""

    APP_MODULE = "app-module"
    """An App-dir Module this transaction did not write -- a hand edit, or an older write.
    Banner with Restore last good / Open file; never an automatic write (`recovery.py`)."""

    ENTRYPOINT = "entrypoint"
    """`hyprland.lua`. App-owned and always regenerable, so the recovery is "regenerate"."""

    FOREIGN = "foreign"
    """`user.lua`, `legacy.lua`, or a Bridge module: files the app must never write. The only
    recovery it can offer is Quarantine (`recovery.py`)."""

    UNKNOWN = "unknown"
    """A line with no file in it, or a file in nobody's territory. Surfaced verbatim rather
    than assigned to whichever class looks closest -- a misattribution here is a write to a
    file the app was not asked to touch."""


@dataclass(frozen=True, slots=True)
class ConfigError:
    """One `configerrors` line, parsed but not reworded."""

    line: str
    """Verbatim, prefix included. What the error dialog shows and the log records."""

    path: str
    """The file part as Hyprland printed it, or the `require` target. `""` when neither."""

    number: int | None
    """The line number, when the shape carried one. `require` failures do not."""

    module: str | None
    """The App-dir-relative name this line implicates (`options/general.lua`), or
    `ENTRYPOINT_NAME`, or `None` for a file the app does not own."""

    ownership: Ownership


def attribute(errors: Sequence[str], *, written: Sequence[str] = ()) -> tuple[ConfigError, ...]:
    """Classify every `configerrors` line. `written` names what this transaction laid down."""
    just_written = frozenset(written)
    return tuple(_one(line, just_written) for line in errors if line.strip())


def own_write_modules(errors: Sequence[str], *, written: Sequence[str]) -> tuple[str, ...]:
    """The Modules this transaction wrote that the errors blame, in write order.

    ADR-0016's auto-revert trigger, and deliberately the narrowest question the attribution
    can answer: an empty tuple means the reload failed for reasons this write did not cause,
    and the app must not respond by writing again.
    """
    blamed = {
        error.module
        for error in attribute(errors, written=written)
        if error.ownership is Ownership.OWN_WRITE and error.module is not None
    }
    return tuple(name for name in written if name in blamed)


def _one(line: str, written: frozenset[str]) -> ConfigError:
    require = _REQUIRE.match(line.strip())
    if require is not None:
        target = require.group("module")
        # A require target is hypr-dir-relative and extensionless; giving it the suffix the
        # file has on disk lets one classifier answer both shapes.
        module = _module_for_path(f"{target.strip().removesuffix('.lua')}.lua")
        return ConfigError(
            line=line,
            path=target,
            number=None,
            module=module,
            ownership=_classify(module, target, written),
        )

    located = _FILE_LINE.match(line.strip())
    if located is None:
        return ConfigError(
            line=line, path="", number=None, module=None, ownership=Ownership.UNKNOWN
        )

    path = located.group("file")
    module = _module_for_path(path)
    return ConfigError(
        line=line,
        path=path,
        number=int(located.group("line")),
        module=module,
        ownership=_classify(module, path, written),
    )


def _classify(module: str | None, path: str, written: frozenset[str]) -> Ownership:
    if module == ENTRYPOINT_NAME:
        return Ownership.ENTRYPOINT
    if module is not None:
        return Ownership.OWN_WRITE if module in written else Ownership.APP_MODULE
    return Ownership.FOREIGN if _is_foreign(path) else Ownership.UNKNOWN


def _module_for_path(path: str) -> str | None:
    """The App-dir-relative name a path implicates, or `None` for a file the app never writes.

    Suffix matching on `<APP_DIR_NAME>/…` rather than a comparison against `paths.app_dir` --
    see the module docstring for why the absolute path Hyprland printed cannot be trusted to
    equal the one the app computed. The names it matches on are `paths`' own constants, so a
    layout change moves both this and the writer together.
    """
    cleaned = _normalise(path).rstrip("/")
    if not cleaned or _is_foreign(cleaned):
        return None

    marker = f"/{APP_DIR_NAME}/"
    index = cleaned.rfind(marker)
    if index >= 0:
        return cleaned[index + len(marker) :]
    if cleaned.startswith(f"{APP_DIR_NAME}/"):
        return cleaned[len(APP_DIR_NAME) + 1 :]

    # Outside the App dir, so the only app-owned file left is the Entrypoint beside it.
    if cleaned == ENTRYPOINT_NAME or cleaned.endswith(f"/{ENTRYPOINT_NAME}"):
        return ENTRYPOINT_NAME
    return None


def _normalise(path: str) -> str:
    """The file part with the whitespace Hyprland pads its messages with taken off."""
    return path.strip()


def _is_foreign(path: str) -> bool:
    """Whether a path names one of the files the app has promised never to rewrite.

    `user.lua` is the escape hatch, `legacy.lua` is written once by the Importer, and a
    Bridge module belongs to the tool that emits it. Two of the three live *inside* the App
    dir, so this has to be asked before the App-dir match rather than after it -- otherwise
    a `legacy.lua` error would be attributed to the app and, if the same transaction had
    written a Module, could authorise an automatic write over somebody else's file.
    """
    cleaned = _normalise(path)
    if f"/{BRIDGE_DIR}/" in cleaned or cleaned.startswith(f"{BRIDGE_DIR}/"):
        return True
    return any(
        cleaned == name or cleaned.endswith(f"/{name}")
        for name in (f"{USER_MODULE}.lua", f"{LEGACY_MODULE}.lua")
    )
