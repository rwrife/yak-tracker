"""CLI tests for ``yak demo`` (the built-in sample day)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from yak_tracker.cli import app
from yak_tracker.sample import REFERENCE_DATE
from yak_tracker.serialize import SCHEMA_VERSION

runner = CliRunner()

DAY = REFERENCE_DATE.isoformat()


def test_demo_renders_sample_tree_without_history_or_ollama() -> None:
    # No --histfile, no repo, no Ollama: demo must stand entirely on its own.
    result = runner.invoke(app, ["demo", "--date", DAY])
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "Yak-shaving (demo)" in out
    assert DAY in out
    # The signature rabbit-hole beats from the sample script show up.
    assert "npm install" in out
    assert "rm -rf node_modules" in out
    # It announces that it's sample data, not the user's real day.
    assert "Sample data" in out


def test_demo_json_matches_the_today_json_shape() -> None:
    result = runner.invoke(app, ["demo", "--date", DAY, "--json"])
    assert result.exit_code == 0, result.stdout
    # Pure JSON: no prose, no rich header leaking into stdout.
    assert "Yak-shaving" not in result.stdout
    assert "Sample data" not in result.stdout

    doc = json.loads(result.stdout)
    assert doc["schema"] == SCHEMA_VERSION
    assert doc["date"] == DAY
    assert doc["summary"]["sessions"] == 2
    blob = json.dumps(doc)
    assert "install" in blob  # stable DetourKind string
    assert "error-fix" in blob


def test_demo_since_json_emits_one_document_per_day() -> None:
    result = runner.invoke(app, ["demo", "--date", DAY, "--since", "3", "--json"])
    assert result.exit_code == 0, result.stdout
    docs = json.loads(result.stdout)
    assert isinstance(docs, list)
    assert len(docs) == 3
    # Every day in the window has the sample activity (oldest first).
    assert all(d["summary"]["sessions"] == 2 for d in docs)
    assert docs[-1]["date"] == DAY


def test_demo_since_one_is_single_document() -> None:
    result = runner.invoke(app, ["demo", "--date", DAY, "--since", "1", "--json"])
    assert result.exit_code == 0, result.stdout
    doc = json.loads(result.stdout)
    assert isinstance(doc, dict)
    assert doc["date"] == DAY


def test_demo_defaults_to_today_when_no_date() -> None:
    from datetime import date

    result = runner.invoke(app, ["demo", "--json"])
    assert result.exit_code == 0, result.stdout
    doc = json.loads(result.stdout)
    assert doc["date"] == date.today().isoformat()
