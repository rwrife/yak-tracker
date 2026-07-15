"""CLI tests for ``yak post`` (issue #32).

Covers the redaction-disabled gate, --dry-run payload preview (no network), an
unknown platform, and a successful send with the delivery call monkeypatched so
nothing hits the network. Uses a real zsh history file with no LLM (offline
outline body), so these are deterministic and backend-free.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

import yak_tracker.cli as cli
from yak_tracker.cli import app

runner = CliRunner()

DAY1 = datetime.fromtimestamp(1750118400).date().isoformat()


def _history(tmp_path: Path) -> Path:
    hist = tmp_path / "zsh_history"
    hist.write_text(
        ": 1750118400:0;git commit -m 'fix login'\n"
        ": 1750118460:0;git status\n"
    )
    return hist


def test_post_rejects_unknown_platform(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["post", "--to", "telegram", "--webhook", "https://x", "--no-llm"],
    )
    assert result.exit_code != 0


def test_post_refuses_no_redact_without_force(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "post",
            "--to",
            "slack",
            "--webhook",
            "https://x",
            "--no-redact",
            "--no-llm",
        ],
    )
    # BadParameter exits with code 2 before any webhook resolution; that is
    # the redaction gate firing.
    assert result.exit_code == 2


def test_post_dry_run_prints_payload_without_sending(tmp_path: Path) -> None:
    hist = _history(tmp_path)
    result = runner.invoke(
        app,
        [
            "post",
            "--to",
            "slack",
            "--shell",
            "zsh",
            "--histfile",
            str(hist),
            "--date",
            DAY1,
            "--no-git",
            "--no-llm",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "dry run" in result.stdout.lower()
    assert '"text"' in result.stdout


def test_post_sends_via_webhook(tmp_path: Path, monkeypatch) -> None:
    hist = _history(tmp_path)
    calls: dict[str, object] = {}

    def fake_post(platform, url, body, *, timeout=15.0, client=None):
        calls["platform"] = platform
        calls["url"] = url
        calls["body"] = body

    monkeypatch.setattr(cli, "post_to_webhook", fake_post)

    result = runner.invoke(
        app,
        [
            "post",
            "--to",
            "discord",
            "--webhook",
            "https://example.test/hook",
            "--shell",
            "zsh",
            "--histfile",
            str(hist),
            "--date",
            DAY1,
            "--no-git",
            "--no-llm",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls["platform"] == "discord"
    assert calls["url"] == "https://example.test/hook"
    assert "fix login" in str(calls["body"])
