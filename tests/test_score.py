"""Tests for the daily focus metric (``yak score`` aggregation layer)."""

from __future__ import annotations

from datetime import date

import pytest

from yak_tracker.score import (
    AVG_WEIGHT,
    DEFAULT_HISTORY_DAYS,
    MAX_WEIGHT,
    SCALE,
    SPARK_TICKS,
    DayScore,
    ScoreHistory,
    build_history,
    history_range,
    score_day,
    score_session,
    sparkline,
)
from yak_tracker.tree import DetourKind, Node

# --- tree builders ----------------------------------------------------------


def _flat_root(label: str = "fix bug", *, steps: int = 0) -> Node:
    """A root intention with ``steps`` ordinary in-line steps (no detours)."""
    root = Node(label=label, kind=DetourKind.ROOT)
    cur = root
    for i in range(steps):
        cur = cur.add(Node(label=f"step{i}", kind=DetourKind.STEP))
    return root


def _detour(kind: str = DetourKind.INSTALL, *, depth: int = 0) -> Node:
    """A single detour node whose own sub-nesting is ``depth`` levels."""
    top = Node(label=f"{kind}-detour", kind=kind)
    cur = top
    for i in range(depth):
        cur = cur.add(Node(label=f"{kind}-deeper{i}", kind=kind))
    return top


def _root_with_detours(*detours: Node, label: str = "fix bug") -> Node:
    root = Node(label=label, kind=DetourKind.ROOT)
    for d in detours:
        root.children.append(d)
    return root


# --- score_session ----------------------------------------------------------


def test_flat_session_scores_100():
    # No children at all → perfect focus.
    assert score_session(_flat_root()).score == 100.0


def test_steps_only_session_still_scores_100():
    # In-line steps are not detours, so a step-only session is still 100.
    ss = score_session(_flat_root(steps=4))
    assert ss.detours == 0
    assert ss.avg_depth == 0.0
    assert ss.max_depth == 0
    assert ss.score == 100.0


def test_single_shallow_detour_depth_one():
    # One install detour with nothing nested → depth 1 (avg=1, max=1).
    root = _root_with_detours(_detour(DetourKind.INSTALL, depth=0))
    ss = score_session(root)
    assert ss.detours == 1
    assert ss.avg_depth == 1.0
    assert ss.max_depth == 1
    # 100 / (1 + (2*1 + 1*1)/10) == 100/1.3 ≈ 76.9
    assert round(ss.score, 1) == 76.9


def test_steps_do_not_change_detour_score():
    # Adding ordinary steps next to a detour must not deepen the score.
    bare = _root_with_detours(_detour(DetourKind.INSTALL, depth=0))
    with_steps = _root_with_detours(_detour(DetourKind.INSTALL, depth=0))
    with_steps.add(Node(label="ls", kind=DetourKind.STEP))
    with_steps.add(Node(label="cargo build", kind=DetourKind.STEP))
    assert score_session(bare).score == score_session(with_steps).score


def test_deeper_detour_scores_lower():
    shallow = score_session(_root_with_detours(_detour(depth=0))).score
    deep = score_session(_root_with_detours(_detour(depth=4))).score
    assert deep < shallow < 100.0


def test_more_detours_average_in():
    # Two detours: one depth-0 (→1) and one depth-2 (→3) → avg 2, max 3.
    root = _root_with_detours(
        _detour(DetourKind.INSTALL, depth=0),
        _detour(DetourKind.ERROR_FIX, depth=2),
    )
    ss = score_session(root)
    assert ss.detours == 2
    assert ss.avg_depth == 2.0
    assert ss.max_depth == 3
    # penalty = 2*2 + 1*3 = 7 → 100/1.7 ≈ 58.8
    assert round(ss.score, 1) == 58.8


def test_score_is_bounded_for_pathological_depth():
    # Even an absurdly deep spiral stays within (0, 100].
    ss = score_session(_root_with_detours(_detour(depth=50)))
    assert 0.0 < ss.score < 100.0


def test_penalty_weights_constants_are_sane():
    # avg is weighted at least as heavily as max (typical tangent matters most).
    assert AVG_WEIGHT >= MAX_WEIGHT > 0
    assert SCALE > 0


# --- score_day --------------------------------------------------------------


def test_empty_day_has_no_score():
    d = score_day(date(2025, 6, 17), [])
    assert d.is_empty
    assert d.score is None
    assert d.session_count == 0
    assert d.avg_depth == 0.0
    assert d.max_depth == 0


def test_day_score_is_mean_of_sessions():
    flat = _flat_root()  # 100
    deep = _root_with_detours(_detour(depth=2))  # < 100
    d = score_day(date(2025, 6, 17), [flat, deep])
    assert d.session_count == 2
    expected = (score_session(flat).score + score_session(deep).score) / 2
    assert d.score == pytest.approx(expected)
    # One focused session lifts the blended day above the deep session alone.
    assert d.score > score_session(deep).score


def test_day_all_flat_is_100():
    d = score_day(date(2025, 6, 17), [_flat_root(), _flat_root(steps=3)])
    assert d.score == 100.0
    assert d.max_depth == 0


# --- history_range / build_history -----------------------------------------


def test_history_range_default_span():
    end = date(2025, 6, 17)
    days = history_range(end, DEFAULT_HISTORY_DAYS)
    assert len(days) == DEFAULT_HISTORY_DAYS
    assert days[-1] == end
    assert days[0] == date(2025, 6, 4)  # 13 days before end, oldest first


def test_history_range_rejects_non_positive():
    with pytest.raises(ValueError):
        history_range(date(2025, 6, 17), 0)


def test_build_history_keeps_quiet_days_and_rollups():
    end = date(2025, 6, 17)
    active = [_root_with_detours(_detour(depth=1))]  # a scored day

    def forest_for(day: date):
        return active if day == end else []

    hist = build_history(end, 5, forest_for)
    assert len(hist.days) == 5
    assert hist.start == date(2025, 6, 13)
    assert hist.end == end
    # Only one day is scored; the rest are quiet (None).
    assert len(hist.scored_days) == 1
    assert all(d.score is None for d in hist.days[:-1])
    assert hist.average == hist.scored_days[0].score
    assert hist.best is hist.scored_days[0]
    assert hist.worst is hist.scored_days[0]


def test_history_best_and_worst_distinguish_days():
    end = date(2025, 6, 17)
    forests = {
        date(2025, 6, 15): [_flat_root()],  # 100 — best
        date(2025, 6, 16): [_root_with_detours(_detour(depth=5))],  # worst
        date(2025, 6, 17): [_root_with_detours(_detour(depth=1))],  # middle
    }

    def forest_for(day: date):
        return forests.get(day, [])

    hist = build_history(end, 3, forest_for)
    assert hist.best is not None and hist.best.day == date(2025, 6, 15)
    assert hist.worst is not None and hist.worst.day == date(2025, 6, 16)
    assert hist.best.score > hist.worst.score


def test_empty_history_rollups_are_none():
    end = date(2025, 6, 17)
    hist = build_history(end, 3, lambda _day: [])
    assert hist.scored_days == []
    assert hist.average is None
    assert hist.best is None
    assert hist.worst is None


def test_score_history_defaults_are_empty():
    hist = ScoreHistory()
    assert hist.start is None
    assert hist.end is None
    assert hist.average is None


def test_day_score_defaults():
    d = DayScore(day=date(2025, 6, 17))
    assert d.is_empty
    assert d.score is None


# --- sparkline --------------------------------------------------------------


def test_sparkline_extremes_map_to_ends():
    spark = sparkline([0.0, 100.0])
    assert spark[0] == SPARK_TICKS[0]
    assert spark[-1] == SPARK_TICKS[-1]


def test_sparkline_none_is_a_gap():
    assert sparkline([None]) == " "
    assert sparkline([100.0, None, 0.0]) == f"{SPARK_TICKS[-1]} {SPARK_TICKS[0]}"


def test_sparkline_is_monotonic_in_score():
    spark = sparkline([0, 25, 50, 75, 100])
    # Higher score → same-or-higher tick (absolute mapping, non-decreasing).
    idxs = [SPARK_TICKS.index(ch) for ch in spark]
    assert idxs[0] == 0
    assert idxs[-1] == len(SPARK_TICKS) - 1
    assert all(b >= a for a, b in zip(idxs, idxs[1:], strict=False))


def test_sparkline_clamps_out_of_range():
    # Defensive: values outside 0–100 are clamped, not crashed on.
    assert sparkline([-10.0]) == SPARK_TICKS[0]
    assert sparkline([200.0]) == SPARK_TICKS[-1]


def test_sparkline_rejects_empty_ticks():
    with pytest.raises(ValueError):
        sparkline([50.0], ticks="")
