"""CLI tests for the ``yak week`` command (weekly tangent heatmap)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"

# The rich-detour fixture clusters on this local day (derived from its epoch so
# the assertion is timezone-safe).
YAKSHAVE_DAY = datetime.fromtimestamp(1750150000).date().isoformat()


def test_week_renders_heatmap_for_active_day() -> None:
    result = runner.invoke(
        app,
        [
            "week",
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
    out = result.stdout
    # Title + heatmap columns are present.
    assert "Weekly yak-shaving" in out
    assert "Deepest shave" in out
    # The active day's deepest intention is surfaced and called out.
    assert "fix-login" in out
    assert "level(s) deep" in out


def test_week_default_span_is_seven_days() -> None:
    result = runner.invoke(
        app,
        [
            "week",
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
    # 7 day rows → the oldest is 6 days before the end date.
    end = datetime.strptime(YAKSHAVE_DAY, "%Y-%m-%d").date()
    from datetime import timedelta

    oldest = (end - timedelta(days=6)).isoformat()
    assert oldest in result.stdout
    assert YAKSHAVE_DAY in result.stdout


def test_week_since_overrides_span() -> None:
    result = runner.invoke(
        app,
        [
            "week",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            YAKSHAVE_DAY,
            "--since",
            "2",
        ],
    )
    assert result.exit_code == 0, result.stdout
    from datetime import timedelta

    end = datetime.strptime(YAKSHAVE_DAY, "%Y-%m-%d").date()
    # --since 2 → exactly the end day and the day before it.
    assert (end - timedelta(days=1)).isoformat() in result.stdout
    # The day 6 back should NOT appear with a 2-day window.
    assert (end - timedelta(days=6)).isoformat() not in result.stdout


def test_week_quiet_window_is_graceful() -> None:
    result = runner.invoke(
        app,
        [
            "week",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            "1999-01-07",
        ],
    )
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    # No activity in this window → quiet-day markers and the flat-week note.
    assert "quiet day" in out
    assert "stayed flat" in out


def test_week_rejects_bad_date() -> None:
    result = runner.invoke(app, ["week", "--no-git", "--date", "nope"])
    assert result.exit_code != 0


def test_week_rejects_non_positive_since() -> None:
    result = runner.invoke(
        app, ["week", "--no-git", "--date", YAKSHAVE_DAY, "--since", "0"]
    )
    assert result.exit_code != 0
