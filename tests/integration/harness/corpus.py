"""The pinned rice corpus, as fixtures the Harness can boot.

`tests/corpus/` (issue #17) is seven real Hyprland config trees pinned to upstream commits.
This module is the part that turns a directory of somebody else's dotfiles into something a
nested compositor can be pointed at, which takes three things the raw tree does not provide:

1. **A `$HOME` the tree resolves in.** Rices write `source = ~/.config/hypr/...` and
   `$XDG_DATA_HOME/...`; the corpus records the mapping in each rice's `ROOT` file and stores
   out-of-tree files under `<rice>/_home/`. Staging rebuilds that layout inside a throwaway
   home so every absolute path resolves to the fixture rather than to the developer's own
   config.
2. **`exec` disarmed.** A rice's autostart lines launch bars, wallpaper daemons and shell
   integrations. Run unmodified in a nested compositor they reach straight out into the
   developer's session -- and several corpus rices autostart things that rewrite
   `~/.config`. Every `exec`-family line is commented out during staging. This is also what
   makes a `.conf` and a `.lua` side comparable: neither gets to run anything.
3. **Ground truth, where it exists.** end-4 and ML4W were captured mid-migration and ship
   upstream's own hand-written `hyprland.lua` beside the `.conf` at the same commit. That is
   the only human translation of these configs that exists, so it is the reference an
   importer's output can be judged against -- and prototype #9 §7 found the mechanical
   conversion beat both hand ports. `ground_truth_lua` is `None` for the other five: absence
   is a fact about the corpus, not a failure to look.

Staging always copies. The corpus is checked in, and a harness that booted a compositor
against the real tree would let a rice's own startup rewrite the fixtures.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = ROOT / "tests" / "corpus"
LOCK_FILE = CORPUS_DIR / "corpus.lock.json"

#: Anything that runs a command at config-load time. Matched case-insensitively because
#: hyprlang keywords are, and anchored so a `$var = ... exec ...` assignment is left alone.
EXEC_LINE = re.compile(
    r"^(\s*)(exec|execr|exec-once|execr-once|exec-shutdown)\s*=", re.IGNORECASE
)

ENTRYPOINT_CONF = "hyprland.conf"
ENTRYPOINT_LUA = "hyprland.lua"


@dataclass(frozen=True, slots=True)
class Rice:
    """One pinned upstream config tree."""

    name: str
    directory: Path
    repo: str | None
    commit: str | None

    @property
    def entrypoint(self) -> Path:
        """The `.conf` Hyprland would load: `hyprland.conf` at the rice root, by convention."""
        return self.directory / ENTRYPOINT_CONF

    @property
    def ground_truth_lua(self) -> Path | None:
        """Upstream's own hand-written Lua port, if this rice shipped one."""
        candidate = self.directory / ENTRYPOINT_LUA
        return candidate if candidate.is_file() else None

    @property
    def pinned(self) -> bool:
        """Whether this rice is reproducible from upstream.

        `local/` is the box the corpus was captured on and has no upstream to pin to; every
        other rice must carry a commit, or `fetch.sh` could not rebuild the tree.
        """
        return self.commit is not None


@dataclass(frozen=True, slots=True)
class StagedRice:
    """A rice copied into a throwaway home, ready to boot."""

    rice: Rice
    home: Path
    hypr_dir: Path
    entrypoint: Path
    ground_truth_lua: Path | None


def load_lock() -> dict[str, dict[str, object]]:
    """`corpus.lock.json`, minus its `_comment` prose key."""
    payload = json.loads(LOCK_FILE.read_text())
    return {name: record for name, record in payload.items() if not name.startswith("_")}


def rices() -> tuple[Rice, ...]:
    """Every rice in the lock file that is present on disk, in lock-file order."""
    found = []
    for name, record in load_lock().items():
        directory = CORPUS_DIR / name
        if not directory.is_dir():
            continue
        repo = record.get("repo")
        commit = record.get("commit")
        found.append(
            Rice(
                name=name,
                directory=directory,
                repo=repo if isinstance(repo, str) else None,
                commit=commit if isinstance(commit, str) else None,
            )
        )
    return tuple(found)


def rice(name: str) -> Rice:
    """One rice by name, with the available names in the error when it is missing."""
    for candidate in rices():
        if candidate.name == name:
            return candidate
    available = ", ".join(candidate.name for candidate in rices())
    raise KeyError(f"no rice {name!r} in the corpus; have: {available}")


def rices_with_ground_truth() -> tuple[Rice, ...]:
    """The rices that ship upstream's own Lua port -- end-4 and ML4W, at time of writing."""
    return tuple(candidate for candidate in rices() if candidate.ground_truth_lua is not None)


def stage(target: Rice, destination: Path, *, disarm_exec: bool = True) -> StagedRice:
    """Copy a rice into `destination` as a throwaway `$HOME`, ready for a nested boot."""
    home = destination
    if home.exists():
        shutil.rmtree(home)
    hypr_dir = home / ".config" / "hypr"
    hypr_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(target.directory, hypr_dir, symlinks=True)

    # `<rice>/_home/<path>` holds files the rice sources from outside ~/.config/hypr; the
    # corpus stores them under the rice so the tree stays self-contained, and staging puts
    # them back at the home-relative paths the config's `source=` lines actually name.
    extra = hypr_dir / "_home"
    if extra.is_dir():
        shutil.copytree(extra, home, dirs_exist_ok=True, symlinks=True)

    for relative in (".local/share", ".local/state", ".cache"):
        (home / relative).mkdir(parents=True, exist_ok=True)

    if disarm_exec:
        disarm_exec_lines(home)

    return StagedRice(
        rice=target,
        home=home,
        hypr_dir=hypr_dir,
        entrypoint=hypr_dir / ENTRYPOINT_CONF,
        ground_truth_lua=(
            hypr_dir / ENTRYPOINT_LUA if target.ground_truth_lua is not None else None
        ),
    )


def disarm_exec_lines(home: Path) -> int:
    """Comment out every `exec`-family line under `home`. Returns how many were disarmed."""
    disarmed = 0
    for path in sorted(home.rglob("*.conf")):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        rewritten, hits = [], 0
        for line in lines:
            if EXEC_LINE.match(line):
                rewritten.append(f"# [harness: exec disabled] {line}")
                hits += 1
            else:
                rewritten.append(line)
        if hits:
            path.write_text("\n".join(rewritten) + "\n")
            disarmed += hits
    return disarmed
