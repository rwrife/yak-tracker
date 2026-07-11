"""Nushell-history collector: ``history.txt`` **or** ``history.sqlite3``.

Nushell keeps command history in one of two backends, selected by
``$env.config.history.file_format``:

* **plaintext** (``history.txt``): one command per line, no timestamps, no cwd —
  essentially like a bare bash history;
* **sqlite** (``history.sqlite3``): a real database whose ``history`` table
  records ``command_line``, ``cwd`` and ``start_timestamp`` (epoch **millis**).
  Richer than any dotfile history — we pull all three.

Both live under nushell's config dir, which is:

* ``$XDG_CONFIG_HOME/nushell`` when set, else
* ``~/.config/nushell`` on Linux/macOS.

We auto-detect whichever file exists (preferring the richer SQLite DB when both
are present) and never raise when nushell simply isn't installed. Parsing is
defensive throughout: a malformed plaintext line is skipped, and a broken/locked
database degrades to ``[]`` rather than crashing the run.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

from ..models import Event
from ..redact import redact_events

SOURCE = "shell:nushell"

_TXT_NAME = "history.txt"
_DB_NAME = "history.sqlite3"


def config_dir(
    *,
    home: Path | None = None,
    xdg_config_home: str | None = None,
) -> Path:
    """Return nushell's config directory (``$XDG_CONFIG_HOME/nushell`` or default)."""
    home = home or Path.home()
    xdg = (
        xdg_config_home
        if xdg_config_home is not None
        else os.environ.get("XDG_CONFIG_HOME")
    )
    base = Path(xdg).expanduser() if xdg else home / ".config"
    return base / "nushell"


def locate_history_file(
    *,
    home: Path | None = None,
    xdg_config_home: str | None = None,
) -> Path | None:
    """Locate a nushell history file, preferring the richer SQLite backend.

    Returns the DB path when ``history.sqlite3`` exists, else the plaintext
    ``history.txt``, else ``None`` (nushell not in use / no history yet).
    """
    base = config_dir(home=home, xdg_config_home=xdg_config_home)
    db = base / _DB_NAME
    if db.is_file():
        return db
    txt = base / _TXT_NAME
    if txt.is_file():
        return txt
    return None


def _epoch_ms_to_dt(millis: int) -> datetime | None:
    """Convert an epoch **milliseconds** value to a naive local ``datetime``."""
    try:
        return datetime.fromtimestamp(millis / 1000.0)
    except (OverflowError, OSError, ValueError):
        return None


def parse_txt(text: str) -> list[Event]:
    """Parse plaintext ``history.txt`` (one command per line, no timestamps)."""
    events: list[Event] = []
    for raw in text.splitlines():
        cmd = raw.strip()
        if cmd:
            events.append(Event(cmd=cmd, ts=None, cwd=None, source=SOURCE))
    return events


def parse_sqlite(path: Path) -> list[Event]:
    """Parse ``history.sqlite3`` into ``Event``s with timestamps and cwd.

    Reads the ``history`` table (``command_line``, ``cwd``, ``start_timestamp``
    in epoch millis). Tolerates schema drift: missing columns fall back to
    ``None``, and any :mod:`sqlite3` error yields ``[]`` so a locked or corrupt
    DB never crashes a run. Opened read-only so we never touch the user's file.
    """
    try:
        uri = f"file:{path}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT command_line, cwd, start_timestamp "
                "FROM history ORDER BY id"
            ).fetchall()
        except sqlite3.Error:
            # Column/table names differ — retry with just the command.
            try:
                rows = conn.execute("SELECT command_line FROM history").fetchall()
            except sqlite3.Error:
                return []
        events: list[Event] = []
        for row in rows:
            keys = row.keys()
            cmd = (row["command_line"] or "").strip() if "command_line" in keys else ""
            if not cmd:
                continue
            cwd = row["cwd"] if "cwd" in keys and row["cwd"] else None
            ts = None
            if "start_timestamp" in keys and row["start_timestamp"] is not None:
                try:
                    ts = _epoch_ms_to_dt(int(row["start_timestamp"]))
                except (TypeError, ValueError):
                    ts = None
            events.append(Event(cmd=cmd, ts=ts, cwd=cwd, source=SOURCE))
        return events
    finally:
        conn.close()


def _parse_path(path: Path) -> list[Event]:
    """Dispatch parsing based on the history file's backend."""
    if path.suffix == ".sqlite3" or path.name == _DB_NAME:
        return parse_sqlite(path)
    return parse_txt(path.read_text(encoding="utf-8", errors="replace"))


def collect(
    *,
    home: Path | None = None,
    xdg_config_home: str | None = None,
    path: Path | None = None,
    redact: bool = True,
) -> list[Event]:
    """Collect all nushell-history events, or ``[]`` when none exist.

    Pass ``path`` to parse a specific file (the backend is inferred from its
    name/suffix); otherwise the history file is auto-located, preferring SQLite.
    Secrets are scrubbed by default.
    """
    hist = path if path is not None else locate_history_file(
        home=home, xdg_config_home=xdg_config_home
    )
    if hist is None or not hist.is_file():
        return []
    events = _parse_path(hist)
    return redact_events(events) if redact else events


def collect_for_date(
    day: date | None = None,
    *,
    home: Path | None = None,
    xdg_config_home: str | None = None,
    path: Path | None = None,
    include_undated: bool = False,
    redact: bool = True,
) -> list[Event]:
    """Collect nushell events filtered to a single ``day`` (defaults to today).

    Mirrors the other collectors: only events on ``day`` unless
    ``include_undated`` is set; sorted by timestamp (undated last). Note the
    plaintext backend has no timestamps, so ``include_undated`` is needed to see
    those events.
    """
    target = day or date.today()
    events = collect(
        home=home, xdg_config_home=xdg_config_home, path=path, redact=redact
    )
    selected = [
        e for e in events if e.on_date(target) or (include_undated and e.ts is None)
    ]
    selected.sort(key=lambda e: (e.ts is None, e.ts or datetime.min))
    return selected
