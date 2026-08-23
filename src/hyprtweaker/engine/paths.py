"""Where every file the app reads or writes lives (ADR-0005).

One object rather than string joins scattered through the writer, the importers and the
state layer, because the layout carries rules that are easy to violate by accident:

- the **App dir** (`~/.config/hypr/hyprtweaker/`) is app-owned and rewritten freely;
- the **Entrypoint** (`~/.config/hypr/hyprland.lua`) is app-owned and regenerated;
- **`user.lua`** and **`legacy.lua`** are never rewritten -- `user.lua` is the escape hatch
  and `legacy.lua` holds imported constructs the GUI cannot represent;
- `hyprland.conf` is never touched, moved or deleted, which is what keeps "delete
  `hyprland.lua`" a complete migration rollback;
- history (Snapshots, Journal, backups, prefs) lives in `$XDG_STATE_HOME`, **never** inside
  the hypr dir, so a user's dotfile repo does not fill up with our churn.

`protected` is the enforcement point: the writer asks before it writes, so "never rewritten"
is a check rather than a convention.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "hyprtweaker"
ENTRYPOINT_NAME = "hyprland.lua"
USER_MODULE = "user"
LEGACY_MODULE = "legacy"
VARS_MODULE = "vars"
OPTIONS_DIR = "options"
BRIDGE_DIR = "bridge"
MANIFEST_NAME = "manifest.json"
SNAPSHOT_DIR = "snapshots"
REPORTS_DIR = "reports"
JOURNAL_NAME = "journal.jsonl"


def _xdg_dir(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable)
    return Path(value) if value else Path.home() / fallback


@dataclass(frozen=True, slots=True)
class ConfigPaths:
    """Every path the app cares about, rooted at one hypr config dir."""

    hypr_dir: Path
    state_dir: Path

    @classmethod
    def default(cls) -> ConfigPaths:
        """The real locations, from XDG with the usual fallbacks."""
        return cls(
            hypr_dir=_xdg_dir("XDG_CONFIG_HOME", ".config") / "hypr",
            state_dir=_xdg_dir("XDG_STATE_HOME", ".local/state") / APP_DIR_NAME,
        )

    @classmethod
    def rooted_at(cls, root: Path) -> ConfigPaths:
        """A whole layout under one directory -- what tests and the Harness use."""
        return cls(hypr_dir=root / "hypr", state_dir=root / "state")

    @property
    def app_dir(self) -> Path:
        return self.hypr_dir / APP_DIR_NAME

    @property
    def options_dir(self) -> Path:
        return self.app_dir / OPTIONS_DIR

    @property
    def bridge_dir(self) -> Path:
        return self.app_dir / BRIDGE_DIR

    @property
    def entrypoint(self) -> Path:
        return self.hypr_dir / ENTRYPOINT_NAME

    @property
    def user_lua(self) -> Path:
        """The escape hatch, beside the Entrypoint and required last."""
        return self.hypr_dir / f"{USER_MODULE}.lua"

    @property
    def legacy_lua(self) -> Path:
        return self.app_dir / f"{LEGACY_MODULE}.lua"

    @property
    def vars_lua(self) -> Path:
        return self.app_dir / f"{VARS_MODULE}.lua"

    @property
    def manifest(self) -> Path:
        return self.app_dir / MANIFEST_NAME

    @property
    def snapshots_dir(self) -> Path:
        """The content-addressed Snapshot store: one file per distinct Module version.

        In the state dir rather than the App dir, so a user's dotfile repo never sees the
        churn of one copy per write.
        """
        return self.state_dir / SNAPSHOT_DIR

    @property
    def reports_dir(self) -> Path:
        """Persisted Loss reports, one `<timestamp>.json` + `.md` pair per import.

        In the state dir beside the Snapshots (ADR-0009): a report is a record of what an
        import did, not config, so it must not land in a dotfile repo -- and it has to
        outlive the wizard that produced it, since "view the last import" is offered long
        afterwards.
        """
        return self.state_dir / REPORTS_DIR

    @property
    def journal(self) -> Path:
        """The change log: one JSON object per Apply transaction, newest last."""
        return self.state_dir / JOURNAL_NAME

    @property
    def hyprland_conf(self) -> Path:
        """The legacy config. Read by the Importer; never written, moved or deleted."""
        return self.hypr_dir / "hyprland.conf"

    @property
    def protected(self) -> frozenset[Path]:
        """Files the app must never rewrite, whatever a caller asks for (ADR-0005)."""
        return frozenset({self.user_lua, self.legacy_lua})

    def file_for(self, name: str) -> Path:
        """Where an app-owned name lives. The Entrypoint is the one outside the App dir.

        The single answer to a question four separate places were each answering for
        themselves -- the Manifest recording a hash, the Journal reading Snapshot bytes, the
        Writer restoring them, and the Session opening a file for the user. They must agree:
        a Journal that snapshotted `hyprtweaker/hyprland.lua` while the Writer wrote
        `hyprland.lua` would restore a file nothing requires.
        """
        return self.entrypoint if name == ENTRYPOINT_NAME else self.app_dir / name

    def require_path(self, path: Path) -> str:
        """The `require("...")` argument for a module file, relative to the hypr dir.

        Slash-separated rather than dotted: Hyprland accepts both, but a dotted require of
        `options.input-capture` is ambiguous with a directory separator, and the slash form
        reads as the path it is.
        """
        relative = path.relative_to(self.hypr_dir)
        return relative.with_suffix("").as_posix()
