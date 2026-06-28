"""CLI tests for the ``yak score`` command (daily focus metric)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"

# The rich-detour fixture clusters on this local day (derived from its epoch so
# the assertion is timezone-safe).
YAKSHAVE_DAY = datetime.fromtimestamp(1750150000).date().isoformat()


def _score_args(*extra: str) -> list[str]:
    return [
        "score",
        "--no-git",
        "--shell",
        "zsh",
        "--histfile",
        str(FIXTURES / "zsh_yakshave"),
        *extra,
    ]


def test_score_single_day_reports_a_number() -> None:
    result = runner.invoke(app, _score_args("--date", YAKSHAVE_DAY))
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "Yak score" in out
    # A 0–100 badge and the depth stats footer are present.
    assert "/100" in out
    assert "avg detour" in out
    assert "session(s)" in out


def test_score_empty_day_is_graceful() -> None:
    result = runner.invoke(app, _score_args("--date", "1999-01-01"))
    assert result.exit_code == 0, result.stdout
    assert "No focus score" in result.stdout


def test_score_history_renders_sparkline_and_rollups() -> None:
    result = runner.invoke(app, _score_args("--history", "--since", "5", "--date", YAKSHAVE_DAY))
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "Yak score" in out
    # Table header + sparkline label + average callout.
    assert "Focus" in out
    assert "Average" in out
    # The active day is scored; the quiet ones show as quiet days.
    assert "/100" in out
    assert "quiet day" in out


def test_score_history_default_span_is_a_fortnight() -> None:
    result = runner.invoke(app, _score_args("--history", "--date", YAKSHAVE_DAY))
    assert result.exit_code == 0, result.stdout
    end = datetime.strptime(YAKSHAVE_DAY, "%Y-%m-%d").date()
    # Default 14-day window → the oldest row is 13 days before the end date.
    assert (end - timedelta(days=13)).isoformat() in result.stdout
    assert YAKSHAVE_DAY in result.stdout


def test_score_history_since_overrides_span() -> None:
    result = runner.invoke(
        app, _score_args("--history", "--since", "3", "--date", YAKSHAVE_DAY)
    )
    assert result.exit_code == 0, result.stdout
    end = datetime.strptime(YAKSHAVE_DAY, "%Y-%m-%d").date()
    assert (end - timedelta(days=2)).isoformat() in result.stdout
    # A day outside the 3-day window should not appear.
    assert (end - timedelta(days=10)).isoformat() not in result.stdout


def test_score_history_quiet_window_is_graceful() -> None:
    result = runner.invoke(
        app, _score_args("--history", "--since", "3", "--date", "1999-01-07")
    )
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "quiet day" in out
    assert "every day was quiet" in out


def test_score_rejects_bad_date() -> None:
    result = runner.invoke(app, ["score", "--no-git", "--date", "nope"])
    assert result.exit_code != 0


def test_score_rejects_non_positive_since() -> None:
    result = runner.invoke(
        app, _score_args("--history", "--date", YAKSHAVE_DAY, "--since", "0")
    )
    assert result.exit_code != 0


def test_today_footer_includes_yak_score() -> None:
    # The `today` command should close with the day's focus score.
    result = runner.invoke(
        app,
        [
            "today",
            "--no-llm",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            YAKSHAVE_DAY,
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Yak score" in result.stdout
    assert "/100" in result.stdout
