"""M3 tests for the git collector.

These build a real throwaway git repo with controlled commit/checkout times so
the parsing and timestamp handling can be asserted deterministically.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from yak_tracker.collectors import git
from yak_tracker.models import Event

# Anchor commit times a few days in the past so they always fall inside the
# collector's lookback window regardless of when the suite runs. Truncated to
# the second because git stores integer epochs.
BASE = (datetime.now() - timedelta(days=2)).replace(microsecond=0)


def _git(repo: Path, *args: str, when: datetime | None = None) -> None:
    """Run a git command in ``repo``, optionally pinning commit/author dates."""
    env = {
        "GIT_AUTHOR_NAME": "Test Yak",
        "GIT_AUTHOR_EMAIL": "yak@example.com",
        "GIT_COMMITTER_NAME": "Test Yak",
        "GIT_COMMITTER_EMAIL": "yak@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    if when is not None:
        iso = when.strftime("%Y-%m-%d %H:%M:%S")
        env["GIT_AUTHOR_DATE"] = iso
        env["GIT_COMMITTER_DATE"] = iso
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**_base_env(), **env},
    )


def _base_env() -> dict[str, str]:
    import os

    return dict(os.environ)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small git repo with two commits and a branch switch, at known times."""
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("one\n")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-q", "-m", "add a.txt", when=BASE)

    _git(r, "checkout", "-q", "-b", "feature")
    (r / "b.txt").write_text("two\n")
    _git(r, "add", "b.txt")
    _git(r, "commit", "-q", "-m", "add b.txt on feature", when=BASE + timedelta(minutes=5))

    _git(r, "checkout", "-q", "main")
    return r


# --- repo detection ------------------------------------------------------


def test_is_git_repo_true(repo: Path) -> None:
    assert git.is_git_repo(repo) is True


def test_is_git_repo_false(tmp_path: Path) -> None:
    assert git.is_git_repo(tmp_path) is False


# --- commit collection ---------------------------------------------------


def test_collect_commits_returns_events(repo: Path) -> None:
    events = git.collect_commits(repo, since="1.year.ago")
    assert all(isinstance(e, Event) for e in events)
    subjects = " | ".join(e.cmd for e in events)
    assert "add a.txt" in subjects
    assert "add b.txt on feature" in subjects
    # Every commit event is sourced to the repo and carries a timestamp.
    assert all(e.source == "git:proj" for e in events)
    assert all(e.ts is not None for e in events)
    assert all(e.cmd.startswith("commit ") for e in events)


def test_collect_commits_timestamp_matches(repo: Path) -> None:
    events = git.collect_commits(repo, since="1.year.ago")
    by_subject = {e.cmd.split(" ", 2)[2]: e for e in events}
    assert by_subject["add a.txt"].ts == BASE


def test_collect_commits_non_repo_is_empty(tmp_path: Path) -> None:
    assert git.collect_commits(tmp_path) == []


def test_collect_commits_respects_since(repo: Path) -> None:
    # Nothing committed in the last second → empty, but no crash.
    assert git.collect_commits(repo, since="1.second.ago") == []


# --- reflog collection ---------------------------------------------------


def test_collect_reflog_captures_checkouts(repo: Path) -> None:
    events = git.collect_reflog(repo, since="1.year.ago")
    assert events, "expected reflog entries for the branch switches"
    blob = " | ".join(e.cmd for e in events)
    assert "checkout" in blob
    assert all(e.cmd.startswith("reflog ") for e in events)
    assert all(e.source == "git:proj" for e in events)


def test_collect_reflog_non_repo_is_empty(tmp_path: Path) -> None:
    assert git.collect_reflog(tmp_path) == []


# --- repo + multi-repo aggregation --------------------------------------


def test_collect_repo_combines_commits_and_reflog(repo: Path) -> None:
    commits = git.collect_repo(repo, since="1.year.ago", include_reflog=False)
    both = git.collect_repo(repo, since="1.year.ago", include_reflog=True)
    assert len(both) > len(commits)
    assert any(e.cmd.startswith("reflog ") for e in both)
    assert any(e.cmd.startswith("commit ") for e in both)


def test_collect_repo_non_repo_is_empty(tmp_path: Path) -> None:
    assert git.collect_repo(tmp_path) == []


def test_collect_multi_repo_skips_bad_paths(repo: Path, tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-repo"
    bogus.mkdir()
    events = git.collect([repo, bogus, tmp_path / "missing"], since="1.year.ago")
    # Still get the good repo's events; bad paths contribute nothing.
    assert events
    assert all(e.source == "git:proj" for e in events)


def test_collect_accepts_str_paths(repo: Path) -> None:
    events = git.collect([str(repo)], since="1.year.ago", include_reflog=False)
    assert events
    assert all(e.cmd.startswith("commit ") for e in events)


# --- robustness ----------------------------------------------------------


def test_epoch_helpers_reject_garbage() -> None:
    assert git._epoch_to_dt("not-a-number") is None
    assert git._epoch_to_dt("") is None
    assert git._epoch_to_dt("99999999999999999999") is None
