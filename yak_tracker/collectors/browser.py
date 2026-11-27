"""Read browser visit history into timestamped local activity events."""

from __future__ import annotations

import configparser
import sqlite3
import tempfile
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from ..models import Event
from ..redact import redact_events

_CHROMIUM_EPOCH_OFFSET = 11_644_473_600_000_000


def _firefox_profiles() -> list[Path]:
    roots = [
        Path.home() / "AppData" / "Roaming" / "Mozilla" / "Firefox",
        Path.home() / "Library" / "Application Support" / "Firefox",
        Path.home() / ".mozilla" / "firefox",
    ]
    profiles: list[Path] = []
    for root in roots:
        ini = root / "profiles.ini"
        if ini.is_file():
            parser = configparser.ConfigParser()
            parser.read(ini, encoding="utf-8")
            sections = sorted(
                (section for section in parser.sections() if section.startswith("Profile")),
                key=lambda section: parser.getint(section, "Default", fallback=0),
                reverse=True,
            )
            for section in sections:
                rel = parser.getboolean(section, "IsRelative", fallback=True)
                raw = parser.get(section, "Path", fallback="")
                if raw:
                    path = Path(raw).expanduser()
                    profiles.append((root / path if rel else path).resolve())
        profiles.extend(sorted(root.glob("Profiles/*")))
        profiles.extend(sorted(root.glob("*.default*")))
    return list(dict.fromkeys(profiles))


def _default_profiles(browser: str) -> list[Path]:
    if browser == "firefox":
        return _firefox_profiles()
    if browser == "chrome":
        roots = [
            Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data",
            Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
            Path.home() / ".config" / "google-chrome",
        ]
    else:
        roots = [
            Path.home() / "AppData" / "Local" / "Chromium" / "User Data",
            Path.home() / "Library" / "Application Support" / "Chromium",
            Path.home() / ".config" / "chromium",
        ]
    profiles: list[Path] = []
    for root in roots:
        profiles.append(root / "Default")
        profiles.extend(sorted(root.glob("Profile *")))
    return profiles


def _history_path(browser: str, profile: Path | None) -> Path | None:
    if browser not in ("firefox", "chrome", "chromium"):
        raise ValueError("browser must be firefox, chrome, or chromium")
    database = "places.sqlite" if browser == "firefox" else "History"
    candidates = [profile.expanduser()] if profile is not None else _default_profiles(browser)
    for candidate in candidates:
        if candidate.is_file():
            if candidate.name == database:
                return candidate.resolve()
            continue
        if candidate.is_dir():
            path = candidate / database
            if path.is_file():
                return path.resolve()
    return None


@contextmanager
def _read_only_snapshot(path: Path) -> Iterator[sqlite3.Connection]:
    """Use SQLite's backup API to snapshot a live database without writing to it."""
    with tempfile.TemporaryDirectory(prefix="yak-browser-") as temp_dir:
        source_uri = f"{path.resolve().as_uri()}?mode=ro"
        source = sqlite3.connect(source_uri, uri=True, timeout=1.0)
        snapshot = sqlite3.connect(Path(temp_dir) / "history.sqlite")
        try:
            source.backup(snapshot, pages=128, sleep=0.05)
            yield snapshot
        finally:
            snapshot.close()
            source.close()


def _datetime_from_microseconds(value: int, *, chromium: bool) -> datetime:
    epoch_value = value - _CHROMIUM_EPOCH_OFFSET if chromium else value
    return datetime.fromtimestamp(epoch_value / 1_000_000)


def _event_for_visit(
    browser: str, title: str | None, url: str, visited: int, *, detail: str
) -> Event | None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    displayed_url = url if detail == "full" else parsed.hostname
    label = (title or "").strip().replace("\r", " ").replace("\n", " ")
    description = f"Visited {label} ({displayed_url})" if label else f"Visited {displayed_url}"
    return Event(
        cmd=description,
        ts=_datetime_from_microseconds(visited, chromium=browser != "firefox"),
        cwd=None,
        source=f"browser:{browser}",
    )


def collect_for_range(
    start: date,
    end: date,
    *,
    browser: str,
    profile: Path | None = None,
    detail: str = "domain",
    redact: bool = True,
) -> list[Event]:
    """Collect HTTP(S) visits in an inclusive local-date range.

    The live browser database is opened read-only and copied to a temporary
    SQLite snapshot before querying. Missing, locked, or incompatible databases
    warn and contribute no events so other collectors can continue.
    """
    if detail not in ("domain", "full"):
        raise ValueError("detail must be domain or full")
    if start > end:
        raise ValueError("start date must not be after end date")

    path = _history_path(browser, profile)
    if path is None:
        warnings.warn(
            f"No {browser} history database found; skipping browser collection",
            RuntimeWarning,
            stacklevel=2,
        )
        return []

    lower = datetime.combine(start, time.min).timestamp()
    upper = datetime.combine(end + timedelta(days=1), time.min).timestamp()
    scale = 1_000_000
    lower_us = int(lower * scale)
    upper_us = int(upper * scale)
    if browser != "firefox":
        lower_us += _CHROMIUM_EPOCH_OFFSET
        upper_us += _CHROMIUM_EPOCH_OFFSET

    try:
        with _read_only_snapshot(path) as connection:
            if browser == "firefox":
                rows = connection.execute(
                    """SELECT p.title, p.url, v.visit_date
                       FROM moz_historyvisits AS v
                       JOIN moz_places AS p ON p.id = v.place_id
                       WHERE v.visit_date >= ? AND v.visit_date < ?
                       ORDER BY v.visit_date""",
                    (lower_us, upper_us),
                )
            else:
                rows = connection.execute(
                    """SELECT u.title, u.url, v.visit_time
                       FROM visits AS v
                       JOIN urls AS u ON u.id = v.url
                       WHERE v.visit_time >= ? AND v.visit_time < ?
                       ORDER BY v.visit_time""",
                    (lower_us, upper_us),
                )
            events = [
                event
                for title, url, visited in rows
                if (event := _event_for_visit(browser, title, url, visited, detail=detail))
            ]
    except (OSError, sqlite3.Error) as exc:
        warnings.warn(
            f"Could not read {browser} history database ({exc}); skipping browser collection",
            RuntimeWarning,
            stacklevel=2,
        )
        return []

    return redact_events(events) if redact else events
