"""CLI tests for the ``yak saga`` command (multi-day thread stitching)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app

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
