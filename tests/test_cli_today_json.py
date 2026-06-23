"""M6 CLI tests for ``yak today`` machine surfaces: --json and --since."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app
from yak_tracker.serialize import SCHEMA_VERSION

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"

# The purpose-built yak-shave fixture's events land on this local day.
SHAVE_DAY = datetime.fromtimestamp(1750150000).date()


def _run(*args: str):
    return runner.invoke(
        app,
        [
            "today",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            *args,
        ],
    )


def test_today_json_is_valid_and_well_shaped() -> None:
    result = _run("--json", "--date", SHAVE_DAY.isoformat())
    assert result.exit_code == 0, result.stdout

    doc = json.loads(result.stdout)
    assert doc["schema"] == SCHEMA_VERSION
    assert doc["date"] == SHAVE_DAY.isoformat()
    assert doc["generated_at"]  # present
    assert doc["summary"]["sessions"] >= 1

    # The fixture's detours should appear in the serialized tree.
    blob = json.dumps(doc)
    assert "npm install" in blob
    assert "rm -rf node_modules" in blob
    # kinds are the stable DetourKind strings
    assert "install" in blob
    assert "error-fix" in blob


def test_today_json_implies_no_llm_no_panels() -> None:
    # Even without --no-llm, --json must not attempt narration or print the
    # human tree header; output is pure JSON.
    result = _run("--json", "--date", SHAVE_DAY.isoformat())
    assert result.exit_code == 0, result.stdout
    assert "Yak-shaving" not in result.stdout
    # Parses cleanly as a single JSON document (no stray prose around it).
    json.loads(result.stdout)


def test_today_json_empty_day_still_valid() -> None:
    result = _run("--json", "--date", "1999-01-01")
    assert result.exit_code == 0, result.stdout
    doc = json.loads(result.stdout)
    assert doc["sessions"] == []
    assert doc["summary"]["events"] == 0


def test_today_since_json_emits_array_per_day() -> None:
    result = _run("--json", "--since", "3", "--date", SHAVE_DAY.isoformat())
    assert result.exit_code == 0, result.stdout

    docs = json.loads(result.stdout)
    assert isinstance(docs, list)
    assert len(docs) == 3

    # Oldest first, ending on SHAVE_DAY.
    dates = [d["date"] for d in docs]
    expected = [
        (SHAVE_DAY - timedelta(days=offset)).isoformat() for offset in (2, 1, 0)
    ]
    assert dates == expected

    # The day with data is the last one and carries the fixture's sessions.
    assert docs[-1]["summary"]["sessions"] >= 1
    assert docs[0]["summary"]["sessions"] == 0


def test_today_since_one_is_single_document() -> None:
    result = _run("--json", "--since", "1", "--date", SHAVE_DAY.isoformat())
    assert result.exit_code == 0, result.stdout
    doc = json.loads(result.stdout)
    # --since 1 collapses to a single object, not a one-element array.
    assert isinstance(doc, dict)
    assert doc["date"] == SHAVE_DAY.isoformat()


def test_today_since_human_renders_each_day() -> None:
    result = _run("--no-llm", "--since", "2", "--date", SHAVE_DAY.isoformat())
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    prev_day = (SHAVE_DAY - timedelta(days=1)).isoformat()
    # The data day renders its yak-shaving tree...
    assert "Yak-shaving" in out
    assert SHAVE_DAY.isoformat() in out
    # ...and the empty prior day is still processed, with its own dated notice.
    assert "Nothing to shave" in out
    assert prev_day in out


def test_today_since_rejects_zero() -> None:
    result = _run("--since", "0", "--date", SHAVE_DAY.isoformat())
    assert result.exit_code != 0
