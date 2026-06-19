"""Sessionizer: bucket a mixed event stream into time-gapped work sessions.

Collectors (shell, git, …) each produce a flat list of
:class:`~yak_tracker.models.Event` objects. The sessionizer is the next stage in
the pipeline: it merges those lists into one chronological stream and splits it
into **sessions** wherever there's a long idle gap.

The heuristic is deliberately simple and matches PLAN.md: any gap larger than
``idle_gap`` (default 25 minutes) between consecutive events starts a new
session. This approximates "I stepped away / context-switched" without needing
any live shell instrumentation.

Events without a timestamp can't be placed on the timeline, so by default they
are dropped from sessionization (``yak raw`` is the place to see undated
events). Callers that want them attached to the *last* session can opt in via
``attach_undated=True``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import Event

# Default idle threshold: a gap longer than this between events ends a session.
DEFAULT_IDLE_GAP = timedelta(minutes=25)


@dataclass(slots=True)
class Session:
    """A contiguous run of activity with no gap longer than the idle threshold.

    Attributes:
        events: The session's events, in chronological order. Always non-empty.
    """

    events: list[Event] = field(default_factory=list)

    @property
    def start(self) -> datetime:
        """Timestamp of the first event in the session."""
        return self.events[0].ts  # type: ignore[return-value]

    @property
    def end(self) -> datetime:
        """Timestamp of the last event in the session."""
        return self.events[-1].ts  # type: ignore[return-value]

    @property
    def duration(self) -> timedelta:
        """Wall-clock span from first to last event (``0`` for a single event)."""
        return self.end - self.start

    @property
    def count(self) -> int:
        """Number of events in the session."""
        return len(self.events)

    def sources(self) -> list[str]:
        """Distinct event sources present, in first-seen order (e.g. shell/git)."""
        seen: dict[str, None] = {}
        for ev in self.events:
            seen.setdefault(ev.source, None)
        return list(seen)


def _idle_gap(value: timedelta | float | int | None) -> timedelta:
    """Normalize an idle-gap argument (minutes as number, or a timedelta)."""
    if value is None:
        return DEFAULT_IDLE_GAP
    if isinstance(value, timedelta):
        return value
    return timedelta(minutes=float(value))


def sessionize(
    events: Iterable[Event],
    *,
    idle_gap: timedelta | float | int | None = DEFAULT_IDLE_GAP,
    attach_undated: bool = False,
) -> list[Session]:
    """Split ``events`` into sessions on idle gaps larger than ``idle_gap``.

    Args:
        events: Events from any number of collectors (need not be pre-sorted).
        idle_gap: Maximum allowed gap *within* a session. May be a
            :class:`~datetime.timedelta` or a number of minutes. A gap strictly
            greater than this starts a new session.
        attach_undated: If True, events with ``ts is None`` are appended to the
            most recent session (or, if none yet, ignored). Defaults to dropping
            them, since they have no place on the timeline.

    Returns:
        Sessions in chronological order. Each session is non-empty and its
        events are time-ordered.
    """
    gap = _idle_gap(idle_gap)

    timed = sorted((e for e in events if e.ts is not None), key=lambda e: e.ts)  # type: ignore[arg-type,return-value]
    undated = [e for e in events if e.ts is None] if attach_undated else []

    sessions: list[Session] = []
    current: Session | None = None
    prev_ts: datetime | None = None

    for ev in timed:
        ts = ev.ts
        assert ts is not None  # filtered above; satisfies type-checkers
        if current is None or (prev_ts is not None and ts - prev_ts > gap):
            current = Session(events=[ev])
            sessions.append(current)
        else:
            current.events.append(ev)
        prev_ts = ts

    if attach_undated and undated and sessions:
        sessions[-1].events.extend(undated)

    return sessions


def summarize(sessions: Sequence[Session]) -> dict[str, int]:
    """Return small rollup stats over ``sessions`` (sessions, events, sources)."""
    total_events = sum(s.count for s in sessions)
    all_sources: dict[str, None] = {}
    for s in sessions:
        for src in s.sources():
            all_sources.setdefault(src, None)
    return {
        "sessions": len(sessions),
        "events": total_events,
        "sources": len(all_sources),
    }
