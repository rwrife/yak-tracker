"""Daily focus metric — the *yak score* (PLAN.md backlog → ``yak score``).

``yak today`` reconstructs a single day's yak-shaving forest and ``yak week``
rolls several days into a tangent-depth heatmap. This module distils a day down
to *one number*: how focused were you, given how far — and how often — your work
spiralled into rabbit holes.

Like :mod:`yak_tracker.week`, this is the presentation-free aggregation layer.
It walks the same :class:`~yak_tracker.tree.Node` forests the renderer and
serializer do and emits plain dataclasses, so the terminal footer / sparkline
(``render.py``) and any future ``--json`` export share one source of truth.

The scoring model
=================

For a single session we look at two things about its detours (the branches that
hang off the root intention):

* **average detour depth** — the mean ``max_depth`` of each *top-level* detour
  off the root. "On a typical tangent, how deep did I go?"
* **max detour depth** — the deepest single chain in the session
  (:meth:`yak_tracker.tree.Node.max_depth`). "How deep did the worst rabbit
  hole go?"

A session with no detours at all (a flat root) is perfect focus.

We turn depth into a **0–100 focus score where 100 = laser-focused** (no
detours) and the number falls as detours get deeper, so it *gamifies staying on
task* — bigger is better, like a credit score for not yak-shaving::

    penalty   = AVG_WEIGHT * avg_depth + MAX_WEIGHT * max_depth
    raw_score = 100 / (1 + penalty / SCALE)
    score     = round(raw_score)

The shape is a smooth decay: depth 0 → 100; shallow detours shave a few points;
deep multi-level spirals pull it down toward (but never to) 0. Using a decay
rather than a linear ``100 - k*depth`` keeps the score bounded in ``[0, 100]``
for *any* depth without a hard clamp, and makes the first level of yak-shaving
cost more than the tenth (diminishing returns on going deeper — by level 6 the
day is already "a rabbit-hole day").

A **day's** score is the session-count-weighted mean of its sessions' scores, so
a day made of mostly focused sessions still reads as focused even if one session
went deep. Empty days (no sessions) have no score (``None``) rather than a
misleading 100 — you can't focus on nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from .tree import DetourKind, Node

__all__ = [
    "DEFAULT_HISTORY_DAYS",
    "AVG_WEIGHT",
    "MAX_WEIGHT",
    "SCALE",
    "SessionScore",
    "DayScore",
    "ScoreHistory",
    "score_session",
    "score_day",
    "build_history",
    "sparkline",
    "SPARK_TICKS",
]

# How many days ``yak score --history`` looks back by default (a fortnight reads
# well as a sparkline without getting noisy).
DEFAULT_HISTORY_DAYS = 14

# Penalty weights: the *typical* tangent (average) matters more than the single
# worst one, but a deep one-off still stings. They need not sum to 1 — ``SCALE``
# sets the overall sensitivity.
AVG_WEIGHT = 2.0
MAX_WEIGHT = 1.0

# Larger SCALE → gentler decay (a given penalty costs fewer points). Tuned so a
# single one-level detour (avg=1, max=1 → penalty 3) lands around 77, and a
# genuinely gnarly day (avg≈3, max≈6 → penalty 12) lands around 45.
SCALE = 10.0

# Sparkline ramp, low → high focus. Eight ticks is the classic spark resolution.
SPARK_TICKS = "▁▂▃▄▅▆▇█"


def _detour_branches(root: Node) -> list[Node]:
    """Top-level detours hanging off a session ``root``.

    Plain ``STEP`` children are ordinary in-line work, not tangents, so they
    don't count as "a detour you went down"; everything else (install /
    error-fix / dir-change / branch-switch) is a real rabbit-hole entrance.
    """
    return [c for c in root.children if c.kind != DetourKind.STEP]


@dataclass(slots=True)
class SessionScore:
    """Focus stats for a single session's yak-shaving tree.

    Attributes:
        intention: The session root's label (what you set out to do).
        detours: Number of top-level rabbit holes off the root.
        avg_depth: Mean depth of those top-level detours — each counted as ``1``
            (entering it) plus its own nesting (``0.0`` when there were none).
        max_depth: Depth of the deepest single detour in the session (``0`` when
            the session had no detours at all).
        score: The 0–100 focus score (100 = no detours).
    """

    intention: str
    detours: int = 0
    avg_depth: float = 0.0
    max_depth: int = 0
    score: float = 100.0


@dataclass(slots=True)
class DayScore:
    """Aggregated focus stats for a single day.

    Attributes:
        day: The calendar date these stats cover.
        sessions: Per-session scores for the day (order preserved).
        score: The day's blended focus score, or ``None`` for a day with no
            sessions at all (focus is undefined when nothing happened).
    """

    day: date
    sessions: list[SessionScore] = field(default_factory=list)
    score: float | None = None

    @property
    def is_empty(self) -> bool:
        """True when the day had no reconstructed sessions."""
        return not self.sessions

    @property
    def session_count(self) -> int:
        """How many sessions contributed to the day's score."""
        return len(self.sessions)

    @property
    def avg_depth(self) -> float:
        """Mean per-session average detour depth across the day."""
        if not self.sessions:
            return 0.0
        return sum(s.avg_depth for s in self.sessions) / len(self.sessions)

    @property
    def max_depth(self) -> int:
        """Depth of the deepest single detour anywhere in the day."""
        return max((s.max_depth for s in self.sessions), default=0)


@dataclass(slots=True)
class ScoreHistory:
    """A span of :class:`DayScore` rows plus history-level roll-ups.

    Attributes:
        days: One :class:`DayScore` per calendar day, oldest first (quiet days
            included as empty rows so the sparkline shows gaps).
    """

    days: list[DayScore] = field(default_factory=list)

    @property
    def start(self) -> date | None:
        """First (oldest) day in the span, if any."""
        return self.days[0].day if self.days else None

    @property
    def end(self) -> date | None:
        """Last (most recent) day in the span, if any."""
        return self.days[-1].day if self.days else None

    @property
    def scored_days(self) -> list[DayScore]:
        """Only the days that actually have a score (non-empty days)."""
        return [d for d in self.days if d.score is not None]

    @property
    def average(self) -> float | None:
        """Mean focus score across scored days (``None`` if none scored)."""
        scored = self.scored_days
        if not scored:
            return None
        return sum(d.score for d in scored if d.score is not None) / len(scored)

    @property
    def best(self) -> DayScore | None:
        """The most-focused scored day (highest score; ties keep earliest)."""
        scored = self.scored_days
        if not scored:
            return None
        return max(scored, key=lambda d: d.score or 0.0)

    @property
    def worst(self) -> DayScore | None:
        """The deepest rabbit-hole scored day (lowest score; ties keep earliest)."""
        scored = self.scored_days
        if not scored:
            return None
        return min(scored, key=lambda d: d.score if d.score is not None else 100.0)


def _focus_from_penalty(avg_depth: float, max_depth: float) -> float:
    """Map (avg, max) detour depth onto a 0–100 focus score.

    Smooth decay: ``0`` depth → exactly ``100``; the score falls off as depth
    grows but is asymptotically bounded in ``[0, 100]`` for any input, so no
    clamp is needed. See the module docstring for the rationale.
    """
    penalty = AVG_WEIGHT * avg_depth + MAX_WEIGHT * max_depth
    if penalty <= 0:
        return 100.0
    return 100.0 / (1.0 + penalty / SCALE)


def score_session(root: Node) -> SessionScore:
    """Score a single session's yak-shaving tree.

    Both the average and the max are taken over the session's *detours* (the
    branches that aren't plain in-line steps), because only a tangent counts as
    "a rabbit hole you went down". A session whose root has only ordinary steps
    — or no children at all — has **no detours** and scores a clean ``100``; the
    depth penalty only kicks in once work actually spirals off-course.

    Each top-level detour's depth is ``1`` (entering it) plus how far it then
    nested, so a single ``npm install`` with nothing under it is depth ``1`` and
    an install that triggered a forced fix that triggered a branch switch is
    depth ``3``.
    """
    branches = _detour_branches(root)

    if not branches:
        # No real tangents — focus held, regardless of in-line steps.
        return SessionScore(
            intention=root.label,
            detours=0,
            avg_depth=0.0,
            max_depth=0,
            score=100.0,
        )

    depths = [1 + b.max_depth() for b in branches]
    avg_depth = sum(depths) / len(depths)
    max_depth = max(depths)

    return SessionScore(
        intention=root.label,
        detours=len(branches),
        avg_depth=avg_depth,
        max_depth=max_depth,
        score=_focus_from_penalty(avg_depth, max_depth),
    )


def score_day(day: date, forest: Sequence[Node]) -> DayScore:
    """Reduce one day's yak-shaving ``forest`` to a :class:`DayScore`.

    The day's score is the plain mean of its session scores (every session
    weighs the same), so a single deep session can't tank an otherwise focused
    day. A day with no sessions has ``score=None`` rather than a misleading 100.
    """
    sessions = [score_session(root) for root in forest]
    if not sessions:
        return DayScore(day=day, sessions=[], score=None)
    day_score = sum(s.score for s in sessions) / len(sessions)
    return DayScore(day=day, sessions=sessions, score=day_score)


def history_range(end: date, span: int) -> list[date]:
    """Inclusive list of ``span`` dates ending at ``end`` (oldest first)."""
    if span < 1:
        raise ValueError("history span must be a positive number of days")
    return [end - timedelta(days=offset) for offset in range(span - 1, -1, -1)]


def build_history(
    end: date,
    span: int,
    forest_for: Callable[[date], Sequence[Node]],
) -> ScoreHistory:
    """Build a :class:`ScoreHistory` for the ``span`` days ending at ``end``.

    Args:
        end: The most recent day in the span (inclusive).
        span: Number of days to score (oldest first).
        forest_for: Callback yielding a day's yak-shaving forest (one
            :class:`Node` per session). The CLI wires this to the shared
            collect → sessionize → :func:`~yak_tracker.tree.build_forest`
            pipeline so scoring stays decoupled from collection.

    Returns:
        A populated :class:`ScoreHistory`, oldest day first.
    """
    history = ScoreHistory()
    for day in history_range(end, span):
        history.days.append(score_day(day, forest_for(day)))
    return history


def sparkline(scores: Sequence[float | None], *, ticks: str = SPARK_TICKS) -> str:
    """Render focus ``scores`` (each 0–100 or ``None``) as a unicode sparkline.

    The 0–100 scale is mapped onto the fixed tick ramp on an **absolute** basis
    (not relative to the window), so a flat-focus fortnight stays near the top
    of the ramp and a rabbit-hole streak sits low — the spark is comparable
    across different ``yak score --history`` runs. ``None`` days (no activity)
    render as a space so gaps are visible without faking a score.
    """
    if not ticks:
        raise ValueError("ticks must be a non-empty string")
    out: list[str] = []
    last = len(ticks) - 1
    for value in scores:
        if value is None:
            out.append(" ")
            continue
        clamped = 0.0 if value < 0 else 100.0 if value > 100 else value
        idx = int(clamped / 100.0 * last + 0.5)
        out.append(ticks[min(idx, last)])
    return "".join(out)
