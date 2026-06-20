"""M4 CLI tests for the ``yak today`` command (yak-shaving tree render)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"

# Same DAY1 the sessionizer tests use: the zsh fixture's first cluster of events
# all land on this local day. Derived from the fixture epoch → timezone-safe.
DAY1 = datetime.fromtimestamp(1750118400).date().isoformat()


def test_today_renders_tree_from_shell(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "today",
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
    out = result.stdout
    assert "Yak-shaving" in out
    # The fixture's `npm install` should be detected as an install detour, and
    # the tree footer reports depth/event counts.
    assert "npm install" in out
    assert "level(s) deep" in out


def test_today_empty_day_is_graceful() -> None:
    result = runner.invoke(
        app,
        [
            "today",
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
    assert "Nothing to shave" in result.stdout


def test_today_rejects_bad_date() -> None:
    result = runner.invoke(app, ["today", "--no-git", "--date", "not-a-date"])
    assert result.exit_code != 0


def test_today_shaving_tree_fixture(tmp_path: Path) -> None:
    # A purpose-built fixture exercising the full detour taxonomy.
    result = runner.invoke(
        app,
        [
            "today",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            datetime.fromtimestamp(1750150000).date().isoformat(),
        ],
    )
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    # root intention + an install detour + a forced-fix detour all present
    assert "fix-login" in out
    assert "npm install" in out
    assert "rm -rf node_modules" in out
