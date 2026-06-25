"""CLI tests for the secret-redaction surface (issue #8).

Verifies that ``yak raw`` scrubs secrets by default, that ``--no-redact`` is an
explicit opt-out, and that ``yak config`` surfaces the redact setting.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app

runner = CliRunner()

# The zsh epoch used elsewhere → timezone-safe day for --date filtering.
DAY1 = datetime.fromtimestamp(1750118400).date().isoformat()
# Assembled at runtime so no full ghp_ literal sits in source (secret scanning).
SECRET = "ghp" + "_" + "abcdefabcdefabcdefabcdefabcdefabcdef12"


def _secret_history(tmp_path: Path) -> Path:
    hist = tmp_path / "zsh_history"
    hist.write_text(
        f": 1750118400:0;export GITHUB_TOKEN={SECRET}\n"
        ": 1750118460:0;git status\n"
    )
    return hist


def test_raw_redacts_secret_by_default(tmp_path: Path) -> None:
    hist = _secret_history(tmp_path)
    result = runner.invoke(
        app,
        ["raw", "--shell", "zsh", "--histfile", str(hist), "--date", DAY1],
    )
    assert result.exit_code == 0, result.stdout
    # The raw token must not appear; a redaction tag should.
    assert SECRET not in result.stdout
    assert "REDACTED" in result.stdout


def test_raw_no_redact_shows_raw_token(tmp_path: Path) -> None:
    hist = _secret_history(tmp_path)
    result = runner.invoke(
        app,
        [
            "raw",
            "--shell",
            "zsh",
            "--histfile",
            str(hist),
            "--date",
            DAY1,
            "--no-redact",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "REDACTED" not in result.stdout
    # rich may wrap long cells; check a stable token prefix survives intact.
    assert "ghp" + "_" + "abcdef" in result.stdout.replace("\n", "")


def test_config_shows_redact_row(tmp_path: Path, monkeypatch) -> None:
    # Point config discovery at an empty dir so defaults are reported.
    monkeypatch.setenv("YAK_TRACKER_CONFIG", str(tmp_path / "nope.toml"))
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.stdout
    assert "redact" in result.stdout
    assert "on" in result.stdout
