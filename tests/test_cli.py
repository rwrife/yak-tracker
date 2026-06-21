"""M1 smoke tests for the yak CLI scaffold."""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker import __version__
from yak_tracker.cli import app

runner = CliRunner()

_SEMVER = re.compile(r"^\d+\.\d+\.\d+")

FIXTURES = Path(__file__).parent / "fixtures"


def test_version_is_semver() -> None:
    assert _SEMVER.match(__version__), f"unexpected version: {__version__!r}"


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_hello_default() -> None:
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "yak" in result.stdout.lower()
    assert "alive" in result.stdout.lower()


def test_hello_with_name() -> None:
    result = runner.invoke(app, ["hello", "Ryan"])
    assert result.exit_code == 0
    assert "Ryan" in result.stdout


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # no_args_is_help exits 0 (Typer) and prints usage
    assert "Usage" in result.stdout


def test_raw_lists_events_from_histfile() -> None:
    # 1750118400 == 2025-06-17 local; pass the matching --date so the row shows.
    result = runner.invoke(
        app,
        [
            "raw",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_history"),
            "--date",
            "2025-06-17",
        ],
    )
    assert result.exit_code == 0
    assert "git status" in result.stdout
    assert "npm install" in result.stdout


def test_raw_empty_day_is_graceful() -> None:
    result = runner.invoke(
        app,
        [
            "raw",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_history"),
            "--date",
            "1999-01-01",
        ],
    )
    assert result.exit_code == 0
    assert "No shell events" in result.stdout


def test_raw_rejects_bad_date() -> None:
    result = runner.invoke(app, ["raw", "--date", "not-a-date"])
    assert result.exit_code != 0
