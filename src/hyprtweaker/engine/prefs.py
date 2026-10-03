"""App preferences -- how the user likes the app, never what the config says (ADR-0019).

Two stores exist and confusing them is the mistake this module is shaped to prevent. The
**config model** is the user's Hyprland configuration: versioned, journalled, exported, and
theirs. **Prefs** is which sidebar arrangement they last used, whether the Advanced switch
was on, which colour scheme they forced and which dialog answers they asked to keep -- state
that describes *this app*, is worthless in a dotfile repo, and must never travel with the
config. So Prefs lives in `$XDG_STATE_HOME/hyprtweaker/prefs.json`, beside
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
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .files import write_atomic

PREFS_FILENAME = "prefs.json"
FORMAT_VERSION = 1

VIEW_KEY = "view"
SHOW_ADVANCED_KEY = "show_advanced"
THEME_KEY = "theme"
REMEMBERED_KEY = "remembered"


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

    theme: str = "system"
    """The Theme override: `system`, `light` or `dark` (ADR-0019). System by default.

    A `str` for the reason `view` is one: which names mean something is the window's
    question (`ui.shell.window._theme_from`), and an unknown one degrades to System there.
    """

    remembered: Mapping[str, str] = field(default_factory=dict, hash=False)
    """"Remember my choice" answers, by dialog id (ADR-0014). Empty by default.

    Flat on purpose: a dialog that learns to remember adds a key, never a field, so this
    file's shape does not move each time one does (#170 is the first). Read-only, and
    replaced rather than mutated by every `with_`/`without_`, so a `Prefs` the window still
    holds cannot change under it. Read an answer with `prefs.remembered.get(dialog_id)`.
    """

    def with_view(self, view: str) -> Prefs:
        return replace(self, view=view)

    def with_show_advanced(self, show_advanced: bool) -> Prefs:
        return replace(self, show_advanced=show_advanced)

    def with_theme(self, theme: str) -> Prefs:
        return replace(self, theme=theme)

    def with_remembered(self, dialog_id: str, choice: str) -> Prefs:
        return replace(self, remembered={**self.remembered, dialog_id: choice})

    def without_remembered(self, dialog_id: str) -> Prefs:
        """Forget one dialog's answer, so it asks again. Absent already: an equal `Prefs`."""
        kept = {key: value for key, value in self.remembered.items() if key != dialog_id}
        return replace(self, remembered=kept)

    def without_any_remembered(self) -> Prefs:
        """Forget every dialog's answer: the "Forget remembered choices" menu item."""
        return replace(self, remembered={})


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
        theme = payload.get(THEME_KEY)
        return Prefs(
            view=view if isinstance(view, str) else defaults.view,
            show_advanced=(
                show_advanced if isinstance(show_advanced, bool) else defaults.show_advanced
            ),
            theme=theme if isinstance(theme, str) else defaults.theme,
            remembered=_remembered_from(payload.get(REMEMBERED_KEY)),
        )

    def save(self, prefs: Prefs) -> bool:
        """Store preferences durably. Returns whether the write actually landed.

        Through `write_atomic` (#251): the app writes this on every switch flip, and a
        half-written `prefs.json` from a crash mid-write would be read back as a corrupt
        file and silently reset every preference at once.
        """
        payload = {
            "format_version": FORMAT_VERSION,
            VIEW_KEY: prefs.view,
            SHOW_ADVANCED_KEY: prefs.show_advanced,
            THEME_KEY: prefs.theme,
            REMEMBERED_KEY: dict(prefs.remembered),
        }
        try:
            write_atomic(self._path, json.dumps(payload, indent=2) + "\n")
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


def _remembered_from(value: object) -> dict[str, str]:
    """The stored dialog answers, keeping each one that is a string.

    Anything but an object is no answers at all. Inside one, an answer that is not a string
    is dropped on its own: the dialog asks again, and the other answers the user gave stay.
    JSON object keys are always strings, so only the values need checking.
    """
    if not isinstance(value, dict):
        return {}
    return {key: choice for key, choice in value.items() if isinstance(choice, str)}
