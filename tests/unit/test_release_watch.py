"""The release watcher: when a Hyprland release gets a `Release check` issue, and when not.

`tools/release_watch.py` takes its GitHub as an argument, so every case here runs the real
decision against a fake that records what the script asked for. Nothing here reaches GitHub.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from _support import ROOT

sys.path.insert(0, str(ROOT / "tools"))

import release_watch

LABEL = "ready-for-agent"


class FakeGitHub:
    """A recording stand-in for the `gh` wrapper: `issues` holds titles in every state."""

    def __init__(self, tags: list[str], issues: list[str] | None = None) -> None:
        self.tags = tags
        self.issues = issues or []
        self.created: list[tuple[str, str, str]] = []
        self.searches: list[str] = []

    def stable_release_tags(self) -> list[str]:
        return self.tags

    def issue_titles(self, search: str) -> list[str]:
        self.searches.append(search)
        return self.issues

    def create_issue(self, title: str, body: str, label: str) -> str:
        self.created.append((title, body, label))
        return "https://github.com/example/repo/issues/1"


def test_a_newer_release_opens_one_labelled_issue_linking_the_protocol() -> None:
    github = FakeGitHub(["v0.57.0", "v0.56.2"])

    message = release_watch.check(github, ("0.56.2",))

    assert len(github.created) == 1
    title, body, label = github.created[0]
    assert title == "Release check: Hyprland 0.57.0"
    assert label == LABEL
    assert "docs/agents/hyprland-release-check.md" in body
    assert "https://github.com/hyprwm/Hyprland/releases/tag/v0.57.0" in body
    assert message == "opened https://github.com/example/repo/issues/1 for Hyprland 0.57.0"


def test_an_existing_issue_stops_a_second_one_whether_it_is_open_or_closed() -> None:
    # The fake cannot tell open from closed, and that is the point: the script asks for
    # every state and trusts the answer, so a closed check is found like an open one.
    github = FakeGitHub(["v0.57.0"], ["Release check: Hyprland 0.57.0"])

    message = release_watch.check(github, ("0.56.2",))

    assert github.created == []
    assert message == "Hyprland 0.57.0 already has a release-check issue"


def test_a_similar_title_is_not_a_match() -> None:
    # `gh issue list --search` is fuzzy: 0.57.1 and a prefix issue must not hide 0.57.0.
    github = FakeGitHub(
        ["v0.57.0"],
        ["Release check: Hyprland 0.57.1", "Release check: Hyprland 0.57.0.1", "Release check"],
    )

    release_watch.check(github, ("0.56.2",))

    assert [title for title, _, _ in github.created] == ["Release check: Hyprland 0.57.0"]


@pytest.mark.parametrize(
    ("shipped", "newest"),
    [
        (("0.56.2", "0.57.0"), "0.57.0"),
        (("0.57.0",), "0.57.0"),
        (("0.56.2", "0.58.0"), "0.58.0"),
    ],
)
def test_a_version_already_shipped_or_older_than_a_shipped_one_opens_nothing(
    shipped: tuple[str, ...], newest: str
) -> None:
    github = FakeGitHub(["v0.57.0"])

    message = release_watch.check(github, shipped)

    assert github.created == []
    assert github.searches == []  # decided before any issue lookup
    assert message == f"Hyprland 0.57.0 is already shipped (newest schema {newest})"


def test_two_releases_between_runs_open_one_issue_for_the_newest() -> None:
    github = FakeGitHub(["v0.57.0", "v0.58.0", "v0.56.2"])

    release_watch.check(github, ("0.56.2",))

    assert [title for title, _, _ in github.created] == ["Release check: Hyprland 0.58.0"]


def test_versions_order_by_number_not_by_text() -> None:
    github = FakeGitHub(["v0.56.2", "v0.56.10", "v0.56.9"])

    release_watch.check(github, ("0.56.2",))

    assert [title for title, _, _ in github.created] == ["Release check: Hyprland 0.56.10"]


def test_tags_that_are_not_plain_versions_are_ignored() -> None:
    github = FakeGitHub(["nightly", "v0.58.0-rc1", "v0.57.0"])

    release_watch.check(github, ("0.56.2",))

    assert [title for title, _, _ in github.created] == ["Release check: Hyprland 0.57.0"]


def test_no_release_at_all_is_a_quiet_no_op() -> None:
    github = FakeGitHub([])

    message = release_watch.check(github, ("0.56.2",))

    assert github.created == []
    assert message == "no stable Hyprland release found"


def test_the_lookup_names_the_exact_title_in_its_search() -> None:
    github = FakeGitHub(["v0.57.0"])

    release_watch.check(github, ("0.56.2",))

    assert github.searches == ["Release check: Hyprland 0.57.0 in:title"]


def test_the_shipped_versions_are_read_from_the_schema_directory(tmp_path: Path) -> None:
    for name in ("hyprland-0.56.2.json", "hyprland-0.56.10.json", "hyprland-0.57.0.diff.json"):
        (tmp_path / name).write_text("{}")

    assert release_watch.shipped_versions(tmp_path) == ("0.56.2", "0.56.10")


def test_the_repo_ships_the_schema_the_watcher_will_compare_against() -> None:
    assert "0.56.2" in release_watch.shipped_versions(ROOT / "data" / "schema")


def test_a_dry_run_reads_but_never_creates() -> None:
    github = FakeGitHub(["v0.57.0"])

    message = release_watch.check(github, ("0.56.2",), dry_run=True)

    assert github.created == []
    assert github.searches == ["Release check: Hyprland 0.57.0 in:title"]
    assert (
        message
        == "dry run: would open 'Release check: Hyprland 0.57.0' labelled ready-for-agent"
    )


def test_the_command_line_dry_run_runs_end_to_end_with_recorded_data(tmp_path: Path) -> None:
    # The script as the Action runs it, with `gh` replaced by a recorded-answer stub on PATH
    # that fails the run if anything but a read is asked of it.
    stub = tmp_path / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        "  \"api \"*releases*) printf 'v0.58.0\\nv0.57.0\\nv0.56.2\\n' ;;\n"
        '  "issue list "*) echo "Release check: Hyprland 0.57.0" ;;\n'
        '  *) echo "unexpected gh call: $*" >&2; exit 9 ;;\n'
        "esac\n"
    )
    stub.chmod(0o755)

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "release_watch.py"), "--dry-run"],
        capture_output=True,
        text=True,
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "GITHUB_REPOSITORY": "example/repo"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith(
        "dry run: would open 'Release check: Hyprland 0.58.0'"
    )
