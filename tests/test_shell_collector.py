"""M2 tests for the shell-history collector."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from yak_tracker.collectors import shell
from yak_tracker.models import Event

FIXTURES = Path(__file__).parent / "fixtures"

# Derive expected dates from the same epochs used in the fixtures so the tests
# are timezone-independent.
DAY1 = datetime.fromtimestamp(1750118400).date()
DAY2 = datetime.fromtimestamp(1750204800).date()


# --- detection / location ------------------------------------------------


@pytest.mark.parametrize(
    ("shell_env", "expected"),
    [
        ("/usr/bin/zsh", "zsh"),
        ("/bin/bash", "bash"),
        ("/usr/local/bin/zsh", "zsh"),
        ("/usr/bin/fish", None),
        ("", None),
    ],
)
def test_detect_shell(shell_env: str, expected: str | None) -> None:
    assert shell.detect_shell(shell_env) == expected


def test_locate_history_prefers_existing_histfile(tmp_path: Path) -> None:
    hist = tmp_path / "custom_history"
    hist.write_text("git status\n")
    located = shell.locate_history_file(shell="bash", histfile_env=str(hist))
    assert located is not None
    assert located[1] == hist


def test_locate_history_falls_back_to_home(tmp_path: Path) -> None:
    (tmp_path / ".zsh_history").write_text(": 1750118400:0;git status\n")
    located = shell.locate_history_file(shell="zsh", home=tmp_path, histfile_env="")
    assert located == ("zsh", tmp_path / ".zsh_history")


def test_locate_history_returns_none_when_missing(tmp_path: Path) -> None:
    assert shell.locate_history_file(shell="zsh", home=tmp_path, histfile_env="") is None


# --- zsh parsing ---------------------------------------------------------


def test_parse_zsh_extended_history() -> None:
    events = shell.collect(shell="zsh", path=FIXTURES / "zsh_history")
    cmds = [e.cmd for e in events]
    assert "git status" in cmds
    assert "npm install" in cmds
    # timestamps are parsed
    git = next(e for e in events if e.cmd == "git status")
    assert git.ts is not None
    assert git.ts.date() == DAY1
    assert git.source == "shell:zsh"


def test_parse_zsh_multiline_command() -> None:
    events = shell.collect(shell="zsh", path=FIXTURES / "zsh_history")
    multiline = [e for e in events if e.cmd.startswith("for f in")]
    assert len(multiline) == 1
    assert "echo" in multiline[0].cmd
    assert "done" in multiline[0].cmd


# --- bash parsing --------------------------------------------------------


def test_parse_bash_timestamped() -> None:
    events = shell.collect(shell="bash", path=FIXTURES / "bash_history_timestamped")
    assert all(e.source == "shell:bash" for e in events)
    git = next(e for e in events if e.cmd == "git status")
    assert git.ts is not None
    assert git.ts.date() == DAY1
    # comment lines must not leak in as commands
    assert not any(e.cmd.startswith("#") for e in events)


def test_parse_bash_plain_has_no_timestamps() -> None:
    events = shell.collect(shell="bash", path=FIXTURES / "bash_history_plain")
    assert len(events) == 5
    assert all(e.ts is None for e in events)
    assert events[0].cmd == "cd ~/projects/login"


# --- date filtering ------------------------------------------------------


def test_collect_for_date_filters_to_day() -> None:
    day1 = shell.collect_for_date(DAY1, shell="zsh", path=FIXTURES / "zsh_history")
    assert all(e.ts is not None and e.ts.date() == DAY1 for e in day1)
    assert any(e.cmd == "npm install" for e in day1)
    assert not any(e.cmd == 'git commit -m "next day"' for e in day1)

    day2 = shell.collect_for_date(DAY2, shell="zsh", path=FIXTURES / "zsh_history")
    assert [e.cmd for e in day2] == ['git commit -m "next day"']


def test_collect_for_date_sorts_by_timestamp() -> None:
    events = shell.collect_for_date(DAY1, shell="zsh", path=FIXTURES / "zsh_history")
    timestamps = [e.ts for e in events if e.ts]
    assert timestamps == sorted(timestamps)


def test_collect_for_date_excludes_undated_by_default() -> None:
    events = shell.collect_for_date(DAY1, shell="bash", path=FIXTURES / "bash_history_plain")
    assert events == []


def test_collect_for_date_include_undated() -> None:
    events = shell.collect_for_date(
        DAY1,
        shell="bash",
        path=FIXTURES / "bash_history_plain",
        include_undated=True,
    )
    assert len(events) == 5
    assert all(e.ts is None for e in events)


# --- robustness ----------------------------------------------------------


def test_collect_missing_history_returns_empty(tmp_path: Path) -> None:
    assert shell.collect(shell="zsh", home=tmp_path, histfile_env="") == []


def test_bad_epoch_does_not_crash() -> None:
    text = ": 999999999999999999:0;git status\n"
    events = shell.parse_history(text, "zsh")
    assert len(events) == 1
    assert events[0].cmd == "git status"
    assert events[0].ts is None


def test_event_on_date() -> None:
    ev = Event(cmd="x", ts=datetime(2026, 6, 18, 9, 0), cwd=None, source="shell:zsh")
    assert ev.on_date(date(2026, 6, 18))
    assert not ev.on_date(date(2026, 6, 17))
    undated = Event(cmd="x", ts=None, cwd=None, source="shell:bash")
    assert not undated.on_date(date(2026, 6, 18))
