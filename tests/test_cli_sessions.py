"""M3 CLI tests for the ``yak sessions`` command."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"

# The zsh fixture's DAY1 events all fall on 2025-06-17 within a few minutes, so
# they collapse into exactly one session.
DAY1 = datetime.fromtimestamp(1750118400).date().isoformat()


def _git(repo: Path, *args: str, when: datetime | None = None) -> None:
    import os

    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="Test Yak",
        GIT_AUTHOR_EMAIL="yak@example.com",
        GIT_COMMITTER_NAME="Test Yak",
        GIT_COMMITTER_EMAIL="yak@example.com",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
    )
    if when is not None:
        iso = when.strftime("%Y-%m-%d %H:%M:%S")
        env["GIT_AUTHOR_DATE"] = iso
        env["GIT_COMMITTER_DATE"] = iso
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def test_sessions_from_shell_only(tmp_path: Path) -> None:
    # --no-git so we don't accidentally pick up the cwd repo; shell only.
    result = runner.invoke(
        app,
        [
            "sessions",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_history"),
            "--date",
            DAY1,
        ],
    )
    assert result.exit_code == 0, result.stdout
    # One session, several events; table shows the count and a shell source.
    assert "Sessions" in result.stdout
    assert "shell:zsh" in result.stdout


def test_sessions_empty_day_is_graceful() -> None:
    result = runner.invoke(
        app,
        [
            "sessions",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_history"),
            "--date",
            "1999-01-01",
        ],
    )
    assert result.exit_code == 0
    assert "No sessions found" in result.stdout


def test_sessions_rejects_bad_date() -> None:
    result = runner.invoke(app, ["sessions", "--no-git", "--date", "nope"])
    assert result.exit_code != 0


def test_sessions_includes_git_events(tmp_path: Path) -> None:
    # Build a repo with a recent commit and point --repo at it (shell off).
    # Use a date a couple days back so it falls in the collector's window.
    when = (datetime.now() - timedelta(days=2)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    day = when.date().isoformat()
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "work today", when=when)

    result = runner.invoke(
        app,
        [
            "sessions",
            "--no-shell",
            "--repo",
            str(repo),
            "--date",
            day,
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "git:proj" in result.stdout


def test_sessions_idle_gap_flag_changes_bucketing(tmp_path: Path) -> None:
    # With a tiny idle gap, the fixture's spread-out events split into more
    # sessions than with the default; just assert it runs and renders.
    result = runner.invoke(
        app,
        [
            "sessions",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_history"),
            "--date",
            DAY1,
            "--idle-gap",
            "0.5",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "idle gap 0.5m" in result.stdout
