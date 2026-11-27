"""Read-only browser-history collection and session-tree integration."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from yak_tracker.collectors.browser import collect_for_range
from yak_tracker.models import Event
from yak_tracker.sessionize import sessionize
from yak_tracker.tree import DetourKind, build_forest

CHROMIUM_EPOCH_OFFSET = 11_644_473_600_000_000
DAY = date(2026, 11, 26)
VISIT = datetime(2026, 11, 26, 9, 10)


def _firefox_db(path: Path, visits: list[tuple[str, str, datetime]]) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT);"
            "CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER, "
            "visit_date INTEGER);"
        )
        for index, (url, title, visited) in enumerate(visits, 1):
            db.execute("INSERT INTO moz_places VALUES (?, ?, ?)", (index, url, title))
            db.execute(
                "INSERT INTO moz_historyvisits VALUES (?, ?, ?)",
                (index, index, int(visited.timestamp() * 1_000_000)),
            )


def _chromium_db(path: Path, visits: list[tuple[str, str, datetime]]) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT);"
            "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);"
        )
        for index, (url, title, visited) in enumerate(visits, 1):
            db.execute("INSERT INTO urls VALUES (?, ?, ?)", (index, url, title))
            micros = int(visited.timestamp() * 1_000_000) + CHROMIUM_EPOCH_OFFSET
            db.execute("INSERT INTO visits VALUES (?, ?, ?)", (index, index, micros))


def test_firefox_reads_snapshot_filters_dates_and_defaults_to_domain(tmp_path: Path) -> None:
    database = tmp_path / "places.sqlite"
    _firefox_db(
        database,
        [
            ("https://docs.example.test/article?private=yes", "Documentation", VISIT),
            ("https://later.example.test/", "Later", datetime(2026, 11, 27, 9)),
        ],
    )
    before = database.stat().st_mtime_ns

    events = collect_for_range(DAY, DAY, browser="firefox", profile=database, detail="domain")

    assert len(events) == 1
    assert events[0].source == "browser:firefox"
    assert events[0].ts == VISIT
    assert "Documentation" in events[0].cmd
    assert "docs.example.test" in events[0].cmd
    assert "/article" not in events[0].cmd
    assert database.stat().st_mtime_ns == before


def test_chromium_full_urls_are_redacted_before_events_leave_collector(tmp_path: Path) -> None:
    profile = tmp_path / "Default"
    profile.mkdir()
    database = profile / "History"
    secret_url = "https://docs.example.test/a?access_token=secret-value-123"
    _chromium_db(database, [(secret_url, "Docs", VISIT)])

    events = collect_for_range(
        DAY, DAY, browser="chrome", profile=profile, detail="full", redact=True
    )

    assert len(events) == 1
    assert events[0].ts == VISIT
    assert "docs.example.test/a" in events[0].cmd
    assert "secret-value-123" not in events[0].cmd


def test_browser_visits_correlate_as_leaf_tree_detours(tmp_path: Path) -> None:
    database = tmp_path / "places.sqlite"
    _firefox_db(database, [("https://docs.example.test/lockfile", "Lockfile docs", VISIT)])
    browser_events = collect_for_range(DAY, DAY, browser="firefox", profile=database)
    shell = Event("pytest", datetime(2026, 11, 26, 9, 5), None, "shell:zsh")

    forest = build_forest(sessionize([shell, *browser_events]))

    assert len(forest) == 1
    visit = forest[0].children[0]
    assert visit.kind == DetourKind.BROWSER
    assert visit.children == []


def test_chromium_profile_database_and_missing_database_warning(tmp_path: Path) -> None:
    database = tmp_path / "History"
    _chromium_db(database, [("https://example.test/", "Example", VISIT)])
    events = collect_for_range(DAY, DAY, browser="chromium", profile=database)
    assert len(events) == 1
    assert events[0].source == "browser:chromium"

    with pytest.warns(RuntimeWarning, match="No firefox history database"):
        assert collect_for_range(DAY, DAY, browser="firefox", profile=tmp_path / "missing") == []


def test_incompatible_history_database_warns_and_skips(tmp_path: Path) -> None:
    database = tmp_path / "History"
    database.write_text("not a sqlite database")

    with pytest.warns(RuntimeWarning, match="Could not read chrome history database"):
        assert collect_for_range(DAY, DAY, browser="chrome", profile=database) == []
