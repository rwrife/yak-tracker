"""CLI tests for ``yak config --init`` (M6 polish — starter config)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app
from yak_tracker.config import STARTER_CONFIG

runner = CliRunner()


def test_config_init_writes_starter(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "yak" / "config.toml"
    monkeypatch.setenv("YAK_TRACKER_CONFIG", str(target))
    result = runner.invoke(app, ["config", "--init"])
    assert result.exit_code == 0, result.stdout
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == STARTER_CONFIG
    # The success message points the user at the file it wrote.
    assert str(target) in result.stdout


def test_config_init_refuses_existing(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "config.toml"
    target.write_text("idle_gap = 5\n")
    monkeypatch.setenv("YAK_TRACKER_CONFIG", str(target))
    result = runner.invoke(app, ["config", "--init"])
    assert result.exit_code == 1
    assert "already exists" in result.stdout
    assert "--force" in result.stdout
    # The user's edited file is left untouched.
    assert target.read_text() == "idle_gap = 5\n"


def test_config_init_force_overwrites(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "config.toml"
    target.write_text("idle_gap = 5\n")
    monkeypatch.setenv("YAK_TRACKER_CONFIG", str(target))
    result = runner.invoke(app, ["config", "--init", "--force"])
    assert result.exit_code == 0, result.stdout
    assert target.read_text(encoding="utf-8") == STARTER_CONFIG


def test_config_init_then_config_reads_cleanly(tmp_path: Path, monkeypatch) -> None:
    # End to end: a freshly-init'd config is reported with no warnings.
    target = tmp_path / "config.toml"
    monkeypatch.setenv("YAK_TRACKER_CONFIG", str(target))
    init = runner.invoke(app, ["config", "--init"])
    assert init.exit_code == 0, init.stdout
    show = runner.invoke(app, ["config"])
    assert show.exit_code == 0, show.stdout
    assert "Warnings" not in show.stdout
