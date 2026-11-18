"""Offline stats CLI: date windows, collection bounds, scoping and presentation."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from yak_tracker import cli
from yak_tracker.config import Config
from yak_tracker.models import Event

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_DAY = datetime.fromtimestamp(1750150000).date().isoformat()


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_config", Config)

    def forbidden(*args, **kwargs):
        raise AssertionError("stats must not construct an LLM backend")

    monkeypatch.setattr(cli, "_backend_for", forbidden)
    monkeypatch.setattr(cli, "make_backend", forbidden)


def _args(*extra: str) -> list[str]:
    return [
        "stats", "--no-git", "--histfile", str(FIXTURES / "zsh_yakshave"),
        "--shell", "zsh", "--to", FIXTURE_DAY, *extra,
    ]


def test_json_default_window_and_fixture_pipeline() -> None:
    result = runner.invoke(cli.app, _args("--json"))
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["kind"] == "stats"
    assert len(doc["days"]) == 30
    assert doc["from"] == (
        date.fromisoformat(FIXTURE_DAY) - timedelta(days=29)
    ).isoformat()
    assert doc["summary"]["sessions"] > 0
    assert doc["summary"]["detours"] > 0
    assert doc["summary"]["deepest_tangent"]["date"] == FIXTURE_DAY


def test_dashboard_and_empty_dashboard_render() -> None:
    active = runner.invoke(cli.app, _args("--since", "2w", "--rabbit-hole-threshold", "1"))
    assert active.exit_code == 0, active.output
    assert "Yak stats" in active.stdout
    assert "median" in active.stdout
    assert "Rabbit-hole days (depth > 1)" in active.stdout
    assert "Daily yak-score" in active.stdout
    assert "Most common detour trigger" in active.stdout
    empty = runner.invoke(cli.app, [
        "stats", "--from", "1999-01-01", "--to", "1999-01-02", "--no-git", "--no-shell",
    ])
    assert empty.exit_code == 0, empty.output
    assert "No activity in this window" in empty.stdout


@pytest.mark.parametrize("extra", [
    ("--since", "0d"), ("--since", "banana"), ("--since", "-1"),
    ("--from", "2027-01-01"), ("--from", "bad-date"),
    ("--from", "2020-01-01", "--since", "30d"),
    ("--rabbit-hole-threshold", "-1"),
])
def test_bad_windows_and_threshold_rejected(extra: tuple[str, ...]) -> None:
    assert runner.invoke(cli.app, _args(*extra)).exit_code != 0


def test_collects_once_and_git_window_is_not_seven_day_or_500_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git = Mock(return_value=[])
    shell = Mock(return_value=[])
    fish = Mock(return_value=[])
    nu = Mock(return_value=[])
    monkeypatch.setattr(cli.git_collector, "collect", git)
    monkeypatch.setattr(cli.shell_collector, "collect", shell)
    monkeypatch.setattr(cli.fish_collector, "collect", fish)
    monkeypatch.setattr(cli.nushell_collector, "collect", nu)
    result = runner.invoke(cli.app, [
        "stats", "--from", "2025-01-01", "--to", "2025-02-01", "--json",
    ])
    assert result.exit_code == 0, result.output
    for collector in (git, shell, fish, nu):
        collector.assert_called_once()
    assert git.call_args.kwargs == {"since": "2025-01-01 00:00:00", "max_count": None}
    assert len(json.loads(result.stdout)["days"]) == 32


def test_scopes_shell_events_and_includes_exact_date_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    repo = tmp_path / "app"
    other = tmp_path / "app-other"
    events = [
        Event("start", datetime(2026, 1, 1), str(repo), "shell:nu"),
        Event("npm install one", datetime(2026, 1, 1, 0, 1), str(repo / "src"), "shell:nu"),
        Event("npm install two", datetime(2026, 1, 1, 0, 2), str(other), "shell:nu"),
        Event("npm install three", datetime(2026, 1, 1, 0, 3), None, "shell:zsh"),
        Event("npm install four", datetime(2026, 1, 1, 0, 4), "relative", "shell:nu"),
        Event("end", datetime(2026, 1, 2, 23, 59, 59), str(repo), "shell:nu"),
        Event("too early", datetime(2025, 12, 31, 23, 59, 59), str(repo), "shell:nu"),
        Event("too late", datetime(2026, 1, 3), str(repo), "shell:nu"),
        Event("undated", None, str(repo), "shell:nu"),
    ]
    monkeypatch.setattr(cli.shell_collector, "collect", lambda **_: events)
    result = runner.invoke(cli.app, [
        "stats", "--from", "2026-01-01", "--to", "2026-01-02", "--repo", str(repo),
        "--no-git", "--histfile", "unused", "--json",
    ])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["summary"]["busiest_repos"] == {str(repo): 3}
    assert doc["summary"]["detour_triggers"] == {"install": 1}
    assert [day["sessions"] for day in doc["days"]] == [1, 1]


def test_history_secrets_are_redacted(tmp_path: Path) -> None:
    history = tmp_path / "history"
    token = "this-is-a-secret-value"
    history.write_text(
        ": 1750150000:0;npm test\n"
        f": 1750150060:0;API_KEY={token} npm install one\n",
        encoding="utf-8",
    )
    result = runner.invoke(cli.app, [
        "stats", "--no-git", "--shell", "zsh", "--since", "1",
        "--to", FIXTURE_DAY, "--json", "--histfile", str(history),
    ])
    assert result.exit_code == 0, result.output
    assert token not in result.stdout
    assert "REDACTED" in json.loads(result.stdout)["summary"]["deepest_tangent"]["label"]


def test_default_end_is_today() -> None:
    result = runner.invoke(cli.app, ["stats", "--no-git", "--no-shell", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["to"] == date.today().isoformat()


def test_terminal_does_not_interpret_history_as_rich_markup(tmp_path: Path) -> None:
    history = tmp_path / "history"
    history.write_text(
        ": 1750150000:0;npm test\n"
        ": 1750150060:0;npm install [bold]literal[/bold]\n",
        encoding="utf-8",
    )
    result = runner.invoke(cli.app, [
        "stats", "--no-git", "--shell", "zsh", "--since", "1",
        "--to", FIXTURE_DAY, "--histfile", str(history),
    ])
    assert result.exit_code == 0, result.output
    assert "[bold]literal[/bold]" in result.stdout
