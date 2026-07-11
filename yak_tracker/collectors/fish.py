"""Fish-history collector: ``fish_history`` → normalized ``Event`` objects.

Fish stores its interactive history in an append-only, YAML-ish file (the fish
docs call it "a subset of YAML"). Each command is a record like::

    - cmd: git status
      when: 1718640000
    - cmd: cd ~/projects/login
      when: 1718640042
      paths:
        - ~/projects/login

Nice properties for us:

* every entry carries a first-class ``when:`` epoch — real per-command
  timestamps, unlike a bare bash history;
* ``paths:`` lists filesystem paths fish saw on the line, which we use as a
  best-effort ``cwd`` (first path wins).

Multi-line commands are stored with the embedded newlines escaped as the
literal two characters ``\\n`` (fish escapes control chars), so a single record
never spans multiple physical lines in the file — each ``- cmd:`` starts a new
record. We still parse defensively: any line we don't recognize is skipped, and
a malformed record never aborts the run.

The history file lives at ``$XDG_DATA_HOME/fish/fish_history`` when that is set,
otherwise ``~/.local/share/fish/fish_history``. (Fish also honours a
``fish_history`` *session name*; we parse whatever file we are pointed at.)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from ..models import Event
from ..redact import redact_events

SOURCE = "shell:fish"


def locate_history_file(
    *,
    home: Path | None = None,
    xdg_data_home: str | None = None,
) -> Path | None:
    """Locate the ``fish_history`` file, or ``None`` if it does not exist.

    Honours ``$XDG_DATA_HOME`` (or the ``xdg_data_home`` override) first, then
    falls back to ``~/.local/share``. Returns the path only when it is a real
    file so callers can silently skip fish when it isn't installed.
    """
    home = home or Path.home()
    xdg = xdg_data_home if xdg_data_home is not None else os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else home / ".local" / "share"
    path = base / "fish" / "fish_history"
    return path if path.is_file() else None


def _read_text(path: Path) -> str:
    """Read history, tolerating the mixed encodings a shell file may contain."""
    return path.read_text(encoding="utf-8", errors="replace")


def _unescape(value: str) -> str:
    """Undo fish's minimal escaping of ``\\n`` and ``\\\\`` inside a cmd value."""
    return value.replace("\\n", "\n").replace("\\\\", "\\")


def _epoch_to_dt(epoch: int) -> datetime | None:
    """Convert a Unix epoch to a naive local ``datetime`` (``None`` if bogus)."""
    try:
        return datetime.fromtimestamp(epoch)
    except (OverflowError, OSError, ValueError):
        return None


def _iter_records(text: str) -> Iterator[tuple[str, datetime | None, str | None]]:
    """Yield ``(cmd, ts, cwd)`` for each record in fish history ``text``.

    A record starts at a ``- cmd:`` line and continues through the indented
    ``when:`` / ``paths:`` lines until the next ``- cmd:`` (or EOF). Anything
    unrecognized is ignored, so a corrupt line can't derail the whole file.
    """
    cmd: str | None = None
    ts: datetime | None = None
    cwd: str | None = None
    in_paths = False

    def flush() -> Iterator[tuple[str, datetime | None, str | None]]:
        if cmd is not None and cmd.strip():
            yield cmd, ts, cwd

    for raw in text.splitlines():
        stripped = raw.strip()
        if raw.startswith("- cmd:"):
            # New record — emit the previous one first.
            yield from flush()
            cmd = _unescape(raw[len("- cmd:"):].strip())
            ts = None
            cwd = None
            in_paths = False
        elif cmd is None:
            # Preamble / junk before the first record.
            continue
        elif stripped.startswith("when:"):
            in_paths = False
            digits = stripped[len("when:"):].strip()
            if digits.isdigit():
                ts = _epoch_to_dt(int(digits))
        elif stripped.startswith("paths:"):
            in_paths = True
            # A ``paths:`` may carry an inline value on the same line.
            inline = stripped[len("paths:"):].strip()
            if inline and cwd is None:
                cwd = inline.lstrip("- ").strip() or None
        elif in_paths and stripped.startswith("-"):
            if cwd is None:
                cwd = stripped.lstrip("- ").strip() or None
        else:
            in_paths = False

    yield from flush()


def parse_history(text: str) -> list[Event]:
    """Parse raw fish ``fish_history`` ``text`` into ``Event`` objects."""
    return [
        Event(cmd=cmd.strip(), ts=ts, cwd=cwd, source=SOURCE)
        for cmd, ts, cwd in _iter_records(text)
        if cmd.strip()
    ]


def collect(
    *,
    home: Path | None = None,
    xdg_data_home: str | None = None,
    path: Path | None = None,
    redact: bool = True,
) -> list[Event]:
    """Collect all fish-history events, or ``[]`` when fish history is absent.

    Pass ``path`` to parse a specific file (used by tests); otherwise the file is
    located via :func:`locate_history_file`. Secrets are scrubbed by default —
    same privacy stance as the bash/zsh collector.
    """
    hist = path if path is not None else locate_history_file(
        home=home, xdg_data_home=xdg_data_home
    )
    if hist is None or not hist.is_file():
        return []
    events = parse_history(_read_text(hist))
    return redact_events(events) if redact else events


def collect_for_date(
    day: date | None = None,
    *,
    home: Path | None = None,
    xdg_data_home: str | None = None,
    path: Path | None = None,
    include_undated: bool = False,
    redact: bool = True,
) -> list[Event]:
    """Collect fish events filtered to a single ``day`` (defaults to today).

    Mirrors :func:`yak_tracker.collectors.shell.collect_for_date`: only events on
    ``day`` are returned unless ``include_undated`` is set, and results are
    sorted by timestamp (undated last).
    """
    target = day or date.today()
    events = collect(
        home=home, xdg_data_home=xdg_data_home, path=path, redact=redact
    )
    selected = [
        e for e in events if e.on_date(target) or (include_undated and e.ts is None)
    ]
    selected.sort(key=lambda e: (e.ts is None, e.ts or datetime.min))
    return selected
