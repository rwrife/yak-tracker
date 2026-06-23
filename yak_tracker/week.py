"""Weekly tangent roll-up (PLAN.md backlog → ``yak week``).

``yak today`` reconstructs a *single* day's yak-shaving forest; this module
rolls several days up into a week-at-a-glance view of **how deep the rabbit
holes went each day**. It is the presentation-free aggregation layer — it walks
the same :class:`~yak_tracker.tree.Node` forests the renderer and serializer do
and emits plain dataclasses, so the terminal heatmap (``render.py``) and any
future ``--json`` export can share one source of truth.

The per-day metric we care about is **tangent depth**: how far a session
spiralled away from its root intention. :meth:`yak_tracker.tree.Node.max_depth`
already gives the deepest chain of a single session's tree, so a day's "depth"
is the max over its sessions, and the *deepest yak-shave of the week* is just
the single session with the largest depth across every day.

Design goals (mirroring ``serialize.py``):

* **Stable shape.** Plain dataclasses with explicit fields; no rich/typer here.
* **Lossless-enough.** Each day keeps a handle on its deepest session's root
  :class:`Node` so the renderer can name the offending intention without
  re-walking history.
* **Honest about gaps.** Days with no timestamped activity are *kept* in the
  range (as empty :class:`DaySummary` rows) so the heatmap shows quiet days too.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from .tree import Node

__all__ = [
    "DEFAULT_WEEK_DAYS",
    "DaySummary",
    "WeekSummary",
    "summarize_day",
    "build_week",
    "heat_level",
    "HEAT_LEVELS",
]

# A "week" is 7 days by default; ``--since`` overrides the span.
DEFAULT_WEEK_DAYS = 7

# Number of discrete heat buckets (0..N-1) the renderer ramps colour across.
HEAT_LEVELS = 5


@dataclass(slots=True)
class DaySummary:
    """Aggregated yak-shaving stats for a single day.

    Attributes:
        day: The calendar date these stats cover.
        sessions: How many work sessions were reconstructed that day.
        events: Total events across the day's trees (detours, *excluding* the
            per-session root anchors — a "size of the day's tangents" number).
        max_depth: Deepest single-session tangent chain that day (a day with no
            nesting is ``0``).
        deepest: The root :class:`Node` of the session that owns ``max_depth``,
            so callers can name the intention behind the deepest rabbit hole.
            ``None`` only when the day had no sessions.
    """

    day: date
    sessions: int = 0
    events: int = 0
    max_depth: int = 0
    deepest: Node | None = None

    @property
    def is_empty(self) -> bool:
        """True when the day had no reconstructed sessions at all."""
        return self.sessions == 0

    @property
    def deepest_label(self) -> str | None:
        """The intention label of the day's deepest session, if any."""
        return self.deepest.label if self.deepest is not None else None


@dataclass(slots=True)
class WeekSummary:
    """A whole span of :class:`DaySummary` rows plus week-level roll-ups.

    Attributes:
        days: One :class:`DaySummary` per calendar day in the span, oldest first
            (quiet days included as empty rows).
        peak_depth: The largest ``max_depth`` across the span (``0`` if nothing
            nested all week).
        deepest_day: The :class:`DaySummary` that owns ``peak_depth`` — i.e. the
            day with the single deepest yak-shave. ``None`` for an empty span.
    """

    days: list[DaySummary] = field(default_factory=list)
    peak_depth: int = 0
    deepest_day: DaySummary | None = None

    @property
    def start(self) -> date | None:
        """First (oldest) day in the span, if any."""
        return self.days[0].day if self.days else None

    @property
    def end(self) -> date | None:
        """Last (most recent) day in the span, if any."""
        return self.days[-1].day if self.days else None

    @property
    def total_sessions(self) -> int:
        """Sessions summed across the span."""
        return sum(d.sessions for d in self.days)

    @property
    def total_events(self) -> int:
        """Detour events summed across the span."""
        return sum(d.events for d in self.days)

    @property
    def active_days(self) -> int:
        """How many days in the span had at least one session."""
        return sum(1 for d in self.days if not d.is_empty)


def summarize_day(day: date, forest: Sequence[Node]) -> DaySummary:
    """Reduce one day's yak-shaving ``forest`` to a :class:`DaySummary`.

    ``events`` counts detours (every node *below* each session root), and the
    day's ``max_depth`` / ``deepest`` come from whichever session tree spiralled
    the furthest. A tie on depth keeps the first (earliest) session, so the
    "deepest shave" is stable for a given input.
    """
    summary = DaySummary(day=day, sessions=len(forest))
    for root in forest:
        summary.events += root.descendants()
        depth = root.max_depth()
        if summary.deepest is None or depth > summary.max_depth:
            summary.max_depth = depth
            summary.deepest = root
    return summary


def week_range(end: date, span: int) -> list[date]:
    """Inclusive list of ``span`` dates ending at ``end`` (oldest first)."""
    if span < 1:
        raise ValueError("week span must be a positive number of days")
    return [end - timedelta(days=offset) for offset in range(span - 1, -1, -1)]


def build_week(
    end: date,
    span: int,
    forest_for: Callable[[date], Sequence[Node]],
) -> WeekSummary:
    """Build a :class:`WeekSummary` for the ``span`` days ending at ``end``.

    Args:
        end: The most recent day in the span (inclusive).
        span: Number of days to roll up (e.g. ``7`` for a week). Oldest first.
        forest_for: Callback that yields a day's yak-shaving forest (one
            :class:`Node` per session). The CLI wires this to the shared
            collect → sessionize → :func:`~yak_tracker.tree.build_forest`
            pipeline so ``week`` stays decoupled from collection.

    Returns:
        A populated :class:`WeekSummary`, with the deepest day identified.
    """
    week = WeekSummary()
    for day in week_range(end, span):
        summary = summarize_day(day, forest_for(day))
        week.days.append(summary)
        if summary.deepest is not None and summary.max_depth > week.peak_depth:
            week.peak_depth = summary.max_depth
            week.deepest_day = summary
    return week


def heat_level(depth: int, peak: int, *, levels: int = HEAT_LEVELS) -> int:
    """Map a day's ``depth`` onto a ``0..levels-1`` heat bucket.

    ``0`` depth is always the coldest bucket (``0``); the busiest day (``depth ==
    peak``) is always the hottest (``levels - 1``). Everything in between is
    scaled linearly against ``peak`` so the ramp is relative to the week, not an
    absolute scale (a calm week still shows contrast).
    """
    if depth <= 0 or peak <= 0:
        return 0
    if depth >= peak:
        return levels - 1
    # Spread 1..peak across buckets 1..levels-1 (reserve bucket 0 for "no nesting").
    scaled = 1 + (depth - 1) * (levels - 2) // max(peak - 1, 1)
    return max(1, min(scaled, levels - 1))
