#!/usr/bin/env python3
"""Open a `Release check: Hyprland <ver>` issue when Hyprland ships a release we lack.

The weekly watcher of ADR-0012 (§ Trigger), run by `.github/workflows/release-watch.yml`::

    tools/release_watch.py [--dry-run]

It takes the newest stable `hyprwm/Hyprland` release, and opens one issue labelled
`ready-for-agent` for it (`docs/agents/hyprland-release-check.md` is the protocol that issue
starts) unless

- a schema for that version or a newer one is already shipped in `data/schema/`, or
- an issue with that exact title exists, open or closed, so a closed check never reopens.

Two releases between runs give one issue, for the newest: the check covers the newest release
only. `--dry-run` reads GitHub and says what it would open, and opens nothing.

All GitHub traffic goes through the small `GitHub` protocol, so the decision in `check` is
tested against a fake. `Gh` is the real one: a thin wrapper over the `gh` CLI, which reads
`GH_TOKEN` (the Action passes `github.token`) and, when set, `GITHUB_REPOSITORY`.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hyprtweaker.engine.schema.resolve import available_versions, version_key  # noqa: E402

UPSTREAM = "hyprwm/Hyprland"
LABEL = "ready-for-agent"
PROTOCOL_DOC = "docs/agents/hyprland-release-check.md"
_PLAIN_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)$")


class GitHub(Protocol):
    def stable_release_tags(self) -> list[str]:
        """Tags of the published, non-draft, non-prerelease releases of `hyprwm/Hyprland`."""
        ...

    def issue_titles(self, search: str) -> list[str]:
        """Titles of this repo's issues, in every state, that `search` finds (fuzzily)."""
        ...

    def create_issue(self, title: str, body: str, label: str) -> str:
        """Open an issue on this repo and return its URL."""
        ...


def shipped_versions(schema_dir: Path = REPO_ROOT / "data" / "schema") -> tuple[str, ...]:
    return available_versions(schema_dir)


def release_title(version: str) -> str:
    return f"Release check: Hyprland {version}"


def _release_check_body(version: str, tag: str) -> str:
    repository = os.environ.get("GITHUB_REPOSITORY")
    doc = (
        f"https://github.com/{repository}/blob/main/{PROTOCOL_DOC}"
        if repository
        else PROTOCOL_DOC
    )
    return (
        f"Hyprland {version} is released "
        f"(https://github.com/{UPSTREAM}/releases/tag/{tag}) "
        "and no schema for it is shipped.\n\n"
        f"Run the release check: [`{PROTOCOL_DOC}`]({doc}). "
        "Its deliverable is one PR with the new Generated schema, the machine diff and the "
        "Overlay updates, and its body carries `Closes` for this issue.\n\n"
        "Opened by the release watcher (`.github/workflows/release-watch.yml`)."
    )


def check(github: GitHub, shipped: tuple[str, ...], *, dry_run: bool = False) -> str:
    """Decide for the newest stable release; open its issue, or say why not."""
    tags = {
        match.group(1): tag
        for tag in github.stable_release_tags()
        if (match := _PLAIN_VERSION.match(tag)) is not None
    }
    if not tags:
        return "no stable Hyprland release found"
    version = max(tags, key=version_key)

    if shipped and version_key(version) <= version_key(shipped[-1]):
        return f"Hyprland {version} is already shipped (newest schema {shipped[-1]})"

    title = release_title(version)
    if title in github.issue_titles(f"{title} in:title"):
        return f"Hyprland {version} already has a release-check issue"

    if dry_run:
        return f"dry run: would open '{title}' labelled {LABEL}"
    url = github.create_issue(title, _release_check_body(version, tags[version]), LABEL)
    return f"opened {url} for Hyprland {version}"


class Gh:
    """The real `GitHub`: the `gh` CLI, whose failures stop the run with its own message."""

    def _gh(self, *args: str) -> str:
        return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout

    def _repo(self) -> list[str]:
        repository = os.environ.get("GITHUB_REPOSITORY")
        return ["-R", repository] if repository else []

    def stable_release_tags(self) -> list[str]:
        output = self._gh(
            "api",
            f"repos/{UPSTREAM}/releases?per_page=100",
            "--jq",
            ".[] | select(.draft or .prerelease | not) | .tag_name",
        )
        return output.split()

    def issue_titles(self, search: str) -> list[str]:
        output = self._gh(
            "issue", "list", "--state", "all", "--search", search, "--limit", "100",
            "--json", "title", "--jq", ".[].title", *self._repo(),
        )  # fmt: skip
        return output.splitlines()

    def create_issue(self, title: str, body: str, label: str) -> str:
        return self._gh(
            "issue", "create", "--title", title, "--body", body, "--label", label, *self._repo()
        ).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="open nothing; say what would be"
    )
    args = parser.parse_args(argv)
    try:
        print(check(Gh(), shipped_versions(), dry_run=args.dry_run))
    except subprocess.CalledProcessError as error:
        print(f"gh {' '.join(error.cmd[1:3])} failed: {error.stderr.strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
