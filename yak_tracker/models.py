"""Shared data structures used across collectors and the sessionizer.

Every collector (shell, git, …) normalizes its raw source into a list of
``Event`` objects so downstream stages (sessionize → tree → narrate) only have
to understand one shape. Keep this module dependency-light: it is imported
almost everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Event:
    """A single normalized activity event.

    Attributes:
        cmd: The command line (or, for non-shell sources, a short description).
        ts: Timestamp when the event happened, if known. Shell histories without
            timestamps (e.g. bash with no ``HISTTIMEFORMAT``) yield ``None``.
        cwd: Best-effort working directory the event ran in. Shell history files
            generally do not record this, so it is usually ``None`` for M2.
        source: Where the event came from, e.g. ``"shell:zsh"`` or ``"shell:bash"``.
    """

    cmd: str
    ts: datetime | None
    cwd: str | None
    source: str

    def on_date(self, day: date) -> bool:
        """Return True if this event's timestamp falls on ``day``.

        Events without a timestamp are considered *not* on any specific date —
        callers decide whether to include undated events separately.
        """
        return self.ts is not None and self.ts.date() == day
