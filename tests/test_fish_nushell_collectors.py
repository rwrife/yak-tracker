"""Tests for the fish + nushell history collectors (issue #27)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from yak_tracker.collectors import (
    collect_extra_shells_for_date,
    fish,
    nushell,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Same epochs the shell fixtures use, so date assertions stay timezone-safe.
DAY1 = datetime.fromtimestamp(1750118400).date()
DAY2 = datetime.fromtimestamp(1750204800).date()


# --------------------------------------------------------------------------
# fish
# --------------------------------------------------------------------------


def test_fish_parses_commands_and_timestamps() -> None:
    events = fish.collect(path=FIXTURES / "fish_history")
    cmds = [e.cmd for e in events]
    assert "git status" in cmds
    assert "npm install" in cmds
    assert all(e.source == "shell:fish" for e in events)
    git = next(e for e in events if e.cmd == "git status")
    assert git.ts is not None
    assert git.ts.date() == DAY1


def test_fish_captures_cwd_from_paths() -> None:
    events = fish.collect(path=FIXTURES / "fish_history")
    cd = next(e for e in events if e.cmd == "cd ~/projects/login")
    assert cd.cwd == "~/projects/login"
    # A record without paths: has no cwd.
    git = next(e for e in events if e.cmd == "git status")
    assert git.cwd is None


def test_fish_unescapes_multiline_command() -> None:
    events = fish.collect(path=FIXTURES / "fish_history")
    multiline = next(e for e in events if e.cmd.startswith("for f in"))
    assert "\n" in multiline.cmd
    assert "end" in multiline.cmd


def test_fish_locate_honours_xdg(tmp_path: Path) -> None:
    hist = tmp_path / "fish" / "fish_history"
    hist.parent.mkdir(parents=True)
    hist.write_text("- cmd: ls\n  when: 1750118400\n")
    located = fish.locate_history_file(xdg_data_home=str(tmp_path))
    assert located == hist


def test_fish_missing_file_returns_empty(tmp_path: Path) -> None:
    assert fish.collect(home=tmp_path, xdg_data_home="") == []


def test_fish_garbage_does_not_crash() -> None:
    text = "not a record\n:::garbage:::\n- cmd: git status\n  when: nope\n"
    events = fish.parse_history(text)
    assert [e.cmd for e in events] == ["git status"]
    assert events[0].ts is None


def test_fish_collect_for_date_filters() -> None:
    day1 = fish.collect_for_date(DAY1, path=FIXTURES / "fish_history")
    assert all(e.ts is not None and e.ts.date() == DAY1 for e in day1)
    assert not any(e.cmd == 'git commit -m "next day"' for e in day1)
    day2 = fish.collect_for_date(DAY2, path=FIXTURES / "fish_history")
    assert [e.cmd for e in day2] == ['git commit -m "next day"']


# --------------------------------------------------------------------------
# nushell — plaintext
# --------------------------------------------------------------------------


def test_nu_plaintext_parses_commands() -> None:
    events = nushell.collect(path=FIXTURES / "nu_history.txt")
    assert [e.cmd for e in events][:2] == ["cd ~/projects/login", "git status"]
    assert all(e.source == "shell:nushell" for e in events)
    assert all(e.ts is None for e in events)  # plaintext has no timestamps


def test_nu_plaintext_needs_include_undated() -> None:
    assert nushell.collect_for_date(DAY1, path=FIXTURES / "nu_history.txt") == []
    got = nushell.collect_for_date(
        DAY1, path=FIXTURES / "nu_history.txt", include_undated=True
    )
    assert len(got) == 4


# --------------------------------------------------------------------------
# nushell — sqlite
# --------------------------------------------------------------------------


@pytest.fixture
def nu_sqlite(tmp_path: Path) -> Path:
    """Build a small nushell ``history.sqlite3`` mirroring the real schema."""
    db = tmp_path / "history.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "command_line TEXT, cwd TEXT, start_timestamp INTEGER)"
    )
    conn.executemany(
        "INSERT INTO history (command_line, cwd, start_timestamp) VALUES (?,?,?)",
        [
            ("cd ~/projects/login", "/home/dev", 1750118400_000),
            ("git status", "/home/dev/login", 1750118412_000),
            ("git commit -m 'next day'", "/home/dev/login", 1750204800_000),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_nu_sqlite_parses_command_cwd_timestamp(nu_sqlite: Path) -> None:
    events = nushell.collect(path=nu_sqlite)
    assert [e.cmd for e in events][:2] == ["cd ~/projects/login", "git status"]
    git = next(e for e in events if e.cmd == "git status")
    assert git.cwd == "/home/dev/login"
    assert git.ts is not None and git.ts.date() == DAY1


def test_nu_sqlite_collect_for_date(nu_sqlite: Path) -> None:
    day2 = nushell.collect_for_date(DAY2, path=nu_sqlite)
    assert [e.cmd for e in day2] == ["git commit -m 'next day'"]


def test_nu_prefers_sqlite_when_both_present(tmp_path: Path) -> None:
    base = tmp_path / ".config" / "nushell"
    base.mkdir(parents=True)
    (base / "history.txt").write_text("from txt\n")
    db = base / "history.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE history (id INTEGER PRIMARY KEY, command_line TEXT, "
        "cwd TEXT, start_timestamp INTEGER)"
    )
    conn.execute(
        "INSERT INTO history VALUES (1, 'from sqlite', NULL, NULL)"
    )
    conn.commit()
    conn.close()
    located = nushell.locate_history_file(home=tmp_path, xdg_config_home="")
    assert located == db
    events = nushell.collect(home=tmp_path, xdg_config_home="")
    assert [e.cmd for e in events] == ["from sqlite"]


def test_nu_missing_returns_empty(tmp_path: Path) -> None:
    assert nushell.collect(home=tmp_path, xdg_config_home="") == []


def test_nu_corrupt_sqlite_does_not_crash(tmp_path: Path) -> None:
    db = tmp_path / "history.sqlite3"
    db.write_text("this is not a database")
    assert nushell.parse_sqlite(db) == []


# --------------------------------------------------------------------------
# auto-detect merge helper
# --------------------------------------------------------------------------


def test_collect_extra_shells_skips_absent(monkeypatch, tmp_path: Path) -> None:
    # No fish/nu files under an empty HOME/XDG → no events, no errors.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert collect_extra_shells_for_date(DAY1) == []


def test_collect_extra_shells_merges(monkeypatch, tmp_path: Path) -> None:
    fish_dir = tmp_path / ".local" / "share" / "fish"
    fish_dir.mkdir(parents=True)
    (fish_dir / "fish_history").write_text(
        "- cmd: fish-cmd\n  when: 1750118400\n"
    )
    nu_dir = tmp_path / ".config" / "nushell"
    nu_dir.mkdir(parents=True)
    (nu_dir / "history.txt").write_text("nu-cmd\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    events = collect_extra_shells_for_date(DAY1, include_undated=True)
    cmds = {e.cmd for e in events}
    assert "fish-cmd" in cmds
    assert "nu-cmd" in cmds
