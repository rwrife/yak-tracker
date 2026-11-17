"""CLI tests for the ``yak saga`` command (multi-day thread stitching)."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yak_tracker.cli import app
from yak_tracker.narrate import PERSONAS, BackendError

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"

# The rich-detour fixture clusters on this local day (derived from its epoch so
# the assertion is timezone-safe). It tells a "fix-login" story with detours.
YAKSHAVE_DAY = datetime.fromtimestamp(1750150000).date().isoformat()


def _base_args(*extra: str) -> list[str]:
    return [
        "saga",
        "--no-git",
        "--no-llm",
        "--shell",
        "zsh",
        "--histfile",
        str(FIXTURES / "zsh_yakshave"),
        "--to",
        YAKSHAVE_DAY,
        *extra,
    ]


def test_saga_requires_match_or_branch() -> None:
    result = runner.invoke(app, ["saga", "--no-git", "--no-llm"])
    assert result.exit_code != 0


def test_saga_match_and_branch_are_mutually_exclusive() -> None:
    result = runner.invoke(
        app, _base_args("--match", "login", "--branch", "fix-login")
    )
    assert result.exit_code != 0


def test_saga_match_renders_matching_session() -> None:
    result = runner.invoke(app, _base_args("--match", "login"))
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "Yak saga" in out
    assert "login" in out
    # Per-day boundary + roll-up footer are shown.
    assert "active day(s)" in out


def test_saga_no_match_is_graceful() -> None:
    result = runner.invoke(app, _base_args("--match", "kubernetes"))
    assert result.exit_code == 0, result.stdout
    assert "No sessions matched" in result.stdout


def test_saga_json_emits_saga_document() -> None:
    result = runner.invoke(app, _base_args("--match", "login", "--json"))
    assert result.exit_code == 0, result.stdout
    doc = json.loads(result.stdout)
    assert doc["kind"] == "saga"
    assert doc["match"] == "login"
    assert doc["summary"]["active_days"] >= 1
    assert doc["days"][0]["date"] == YAKSHAVE_DAY


def test_saga_branch_matches_checkout() -> None:
    # The fixture's `git checkout -b fix-login` shell line mentions the branch.
    result = runner.invoke(app, _base_args("--branch", "fix-login"))
    assert result.exit_code == 0, result.stdout
    assert "branch:fix-login" in result.stdout


def test_saga_rejects_bad_since() -> None:
    result = runner.invoke(app, _base_args("--match", "login", "--since", "banana"))
    assert result.exit_code != 0


def test_saga_from_after_to_rejected() -> None:
    result = runner.invoke(
        app,
        _base_args("--match", "login", "--from", "2027-01-01"),
    )
    assert result.exit_code != 0


@pytest.mark.parametrize("selector", ["--match", "--branch"])
def test_saga_rejects_empty_selector(selector: str) -> None:
    result = runner.invoke(app, _base_args(selector, " "))
    assert result.exit_code != 0
    assert "must not be empty" in result.output


@pytest.fixture
def saga_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "saga-repo"
    repo.mkdir()

    def git(*args: str, day: int = 1) -> None:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test Yak",
            "GIT_AUTHOR_EMAIL": "yak@example.com",
            "GIT_COMMITTER_NAME": "Test Yak",
            "GIT_COMMITTER_EMAIL": "yak@example.com",
            "GIT_AUTHOR_DATE": f"2025-01-{day:02d}T12:00:00",
            "GIT_COMMITTER_DATE": f"2025-01-{day:02d}T12:00:00",
        }
        subprocess.run(["git", "-C", str(repo), *args], env=env, check=True,
                       capture_output=True)

    git("init", "-q", "-b", "main")
    git("commit", "--allow-empty", "-qm", "base")
    git("checkout", "-qb", "feat/oauth", day=2)
    for day, subject in [(2, "scaffold routes"), (4, "refresh tokens")]:
        (repo / "oauth.txt").write_text(f"day {day}\n")
        git("add", "oauth.txt", day=day)
        git("commit", "-qm", subject, day=day)
    git("checkout", "-q", "main", day=5)
    git("merge", "--no-ff", "-qm", "merge feature", "feat/oauth", day=6)
    git("commit", "--allow-empty", "-qm", "unrelated maintenance", day=8)
    # Model a cloned repo, where origin/HEAD exists but branch creation reflogs
    # are not the source of branch membership.
    git("update-ref", "refs/remotes/origin/main", "main", day=8)
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main", day=8)
    return repo


def _git_args(repo: Path, *extra: str) -> list[str]:
    return [
        "saga", "--repo", str(repo), "--no-shell", "--json",
        "--from", "2025-01-01", "--to", "2025-01-10", *extra,
    ]


def test_saga_git_path_match_in_historical_window(saga_repo: Path) -> None:
    result = runner.invoke(app, _git_args(saga_repo, "--match", "oauth.txt"))
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert [day["date"] for day in doc["days"]] == ["2025-01-02", "2025-01-04"]
    assert doc["summary"]["sessions"] == 2


def test_saga_branch_matches_commits_after_merge(saga_repo: Path) -> None:
    result = runner.invoke(app, _git_args(saga_repo, "--branch", "feat/oauth"))
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert [day["date"] for day in doc["days"]] == [
        "2025-01-02", "2025-01-04", "2025-01-05", "2025-01-06",
    ]
    assert "scaffold routes" in doc["days"][0]["sessions"][0]["intention"]
    assert "refresh tokens" in doc["days"][1]["sessions"][0]["intention"]


def test_saga_primary_branch_with_remote_head(saga_repo: Path) -> None:
    result = runner.invoke(app, _git_args(saga_repo, "--branch", "main"))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["end"] == "2025-01-08"


def test_saga_unmerged_branch_and_relative_window(saga_repo: Path) -> None:
    for ref in ("refs/heads/main", "refs/remotes/origin/main"):
        subprocess.run(
            ["git", "-C", str(saga_repo), "update-ref", ref, "feat/oauth~2"], check=True,
        )
    result = runner.invoke(app, [
        "saga", "--repo", str(saga_repo), "--branch", "feat/oauth", "--no-shell",
        "--json", "--since", "2w", "--to", "2025-01-03",
    ])
    assert result.exit_code == 0, result.output
    assert [day["date"] for day in json.loads(result.stdout)["days"]] == ["2025-01-02"]


def test_saga_reports_ambiguous_fast_forward_lifetime(saga_repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(saga_repo), "update-ref", "refs/remotes/origin/main", "feat/oauth"],
        check=True,
    )
    result = runner.invoke(app, _git_args(saga_repo, "--branch", "feat/oauth"))
    assert result.exit_code != 0
    assert "fast-forward" in result.output


def test_saga_reports_missing_branch(saga_repo: Path) -> None:
    result = runner.invoke(app, _git_args(saga_repo, "--branch", "does-not-exist"))
    assert result.exit_code != 0
    assert "was not found" in result.output


@pytest.mark.parametrize("fmt", ["standup", "story", "learning"])
def test_saga_persona_and_unavailable_backend_fallback(monkeypatch, fmt: str) -> None:
    prompts = []

    class UnavailableBackend:
        model = "test"

        def generate(self, prompt: str) -> str:
            prompts.append(prompt)
            raise BackendError("test backend unavailable")

    monkeypatch.setattr("yak_tracker.cli._backend_for", lambda _: UnavailableBackend())
    args = [arg for arg in _base_args("--match", "login", "--format", fmt)
            if arg != "--no-llm"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert PERSONAS[fmt] in prompts[0]
    assert f"Day {YAKSHAVE_DAY}" in prompts[0]
    assert "test backend unavailable" in result.stdout
    assert "active day(s)" in result.stdout
