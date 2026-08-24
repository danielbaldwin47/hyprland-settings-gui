"""App preferences -- how the user likes the app, never what the config says (ADR-0019).

Two stores exist and confusing them is the mistake this module is shaped to prevent. The
**config model** is the user's Hyprland configuration: versioned, journalled, exported, and
theirs. **Prefs** is which sidebar arrangement they last used and whether the Advanced switch
was on -- state that describes *this app*, is worthless in a dotfile repo, and must never
travel with the config. So Prefs lives in `$XDG_STATE_HOME/hyprtweaker/prefs.json`, beside
the Snapshots and the Journal, and nothing here ever touches `ConfigModel`.

Plain JSON, never GSettings (ADR-0019): GSettings drags in a dconf daemon, and without that
daemon the memory backend accepts every write and silently drops it -- preferences that
vanish on a minimal Hyprland box, with nothing to see in the file system either.

Read defensively, like the Manifest: a corrupt, truncated or hand-edited file is a reason to
open with defaults, never a reason to fail to start. A preference is not worth a crash, and
the recovery a user can perform on their own -- change the setting again -- is the same
recovery this would prompt them to do.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

PREFS_FILENAME = "prefs.json"
FORMAT_VERSION = 1

VIEW_KEY = "view"
SHOW_ADVANCED_KEY = "show_advanced"


@dataclass(frozen=True, slots=True)
class Prefs:
    """Every remembered app preference, as one immutable value.

    Frozen because a preference changes by being *stored*: `Prefs` objects that could be
    mutated in place would let the window drift from the file without either one being
    wrong, and "my choice was not remembered" is the whole class of bug this file exists to
    avoid. `with_` returns the next value; `PrefsStore.save` is what makes it durable.
    """

    view: str = "tasks"
    """Which sidebar arrangement to open in. Tasks is the default (#7).

    Held as `str` rather than as `ui.pages.plan.View` on purpose: the engine has no business
    importing from the UI, and an unknown string from a future version has to degrade to the
    default rather than raise. Which names are recognisable is therefore the UI's question,
    answered in one place there (`ui.shell.window._view_from`); this store only carries the
    string it was given, so a newer app's choice survives a round trip through an older one.
    """

    show_advanced: bool = False
    """The global "Show advanced settings" switch (ADR-0013 §5).

    Off by default: the audience (ADR-0004) is someone who wants their compositor to behave,
    not someone auditing `debug:` flags, and an app that opens showing everything has made
    the curated view pointless before it is seen.
    """

    def with_view(self, view: str) -> Prefs:
        return replace(self, view=view)

    def with_show_advanced(self, show_advanced: bool) -> Prefs:
        return replace(self, show_advanced=show_advanced)


class PrefsStore:
    """Reads and writes the Prefs file, and never raises at the call site.

    Every failure mode -- absent file, unreadable file, unparseable JSON, a payload that is
    not an object, a value of the wrong type, an unwritable state dir -- resolves to "use
    the default" or "the write did not happen". The window calls `save` on every toggle, so
    a store that could throw would turn a read-only `$XDG_STATE_HOME` into an app that
    crashes when you click a switch.
    """

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / PREFS_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Prefs:
        """The stored preferences, or the defaults for anything the file cannot answer.

        Field by field rather than all-or-nothing: a file whose `view` is garbage but whose
        `show_advanced` is a real boolean should lose only the broken half. Partial recovery
        costs one `isinstance` per field and saves the user re-setting preferences they
        never corrupted.
        """
        payload = self._read()
        if payload is None:
            return Prefs()

        defaults = Prefs()
        view = payload.get(VIEW_KEY)
        show_advanced = payload.get(SHOW_ADVANCED_KEY)
        return Prefs(
            view=view if isinstance(view, str) else defaults.view,
            show_advanced=(
                show_advanced if isinstance(show_advanced, bool) else defaults.show_advanced
            ),
        )

    def save(self, prefs: Prefs) -> bool:
        """Store preferences durably. Returns whether the write actually landed.

        Written to a temporary file and renamed, like the Journal: the app writes this on
        every switch flip, and a half-written `prefs.json` from a crash mid-write would be
        read back as a corrupt file and silently reset every preference at once.
        """
        payload = {
            "format_version": FORMAT_VERSION,
            VIEW_KEY: prefs.view,
            SHOW_ADVANCED_KEY: prefs.show_advanced,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(f".{PREFS_FILENAME}.tmp")
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            return False
        return True

    def _read(self) -> dict[str, Any] | None:
        """The file's payload, or None when there is nothing trustworthy to read.

        A `format_version` from a *newer* app is treated as unreadable rather than parsed
        hopefully: the keys we recognise might mean something else there, and defaults are
        the honest answer. Downgrade loses preferences; it does not corrupt them.
        """
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return None

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None
        if payload.get("format_version") != FORMAT_VERSION:
            return None
        return payload
