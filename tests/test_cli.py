"""M1 smoke tests for the yak CLI scaffold."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from yak_tracker import __version__
from yak_tracker.cli import app

runner = CliRunner()

_SEMVER = re.compile(r"^\d+\.\d+\.\d+")


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
