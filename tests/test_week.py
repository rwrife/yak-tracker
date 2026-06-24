"""Tests for the weekly tangent roll-up (``yak week`` aggregation layer)."""

from __future__ import annotations

from datetime import date

from yak_tracker.tree import DetourKind, Node
from yak_tracker.week import (
    DEFAULT_WEEK_DAYS,
    HEAT_LEVELS,
    DaySummary,
    build_week,
    heat_level,
    summarize_day,
    week_range,
)


def _chain(label: str, depth: int) -> Node:
    """A session root whose deepest chain is exactly ``depth`` levels.

    ``depth == 0`` is a flat root (no children); ``depth == 3`` nests three
    detours under the root.
    """
    root = Node(label=label, kind=DetourKind.ROOT)
    cur = root
    for i in range(depth):
        cur = cur.add(Node(label=f"{label}-step{i}", kind=DetourKind.STEP))
    return root


# --- week_range -------------------------------------------------------------


def test_week_range_default_span_is_seven_days():
    end = date(2025, 6, 17)
    days = week_range(end, DEFAULT_WEEK_DAYS)
    assert len(days) == 7
    assert days[0] == date(2025, 6, 11)  # oldest first
    assert days[-1] == end


def test_week_range_single_day():
    end = date(2025, 6, 17)
    assert week_range(end, 1) == [end]


def test_week_range_rejects_non_positive():
    import pytest

    with pytest.raises(ValueError):
        week_range(date(2025, 6, 17), 0)


# --- summarize_day ----------------------------------------------------------


def test_summarize_empty_day():
    day = date(2025, 6, 17)
    summary = summarize_day(day, [])
    assert summary.is_empty
    assert summary.sessions == 0
    assert summary.events == 0
    assert summary.max_depth == 0
    assert summary.deepest is None
    assert summary.deepest_label is None


def test_summarize_day_picks_deepest_session():
    day = date(2025, 6, 17)
    shallow = _chain("shallow", 1)
    deep = _chain("deep", 4)
    summary = summarize_day(day, [shallow, deep])
    assert summary.sessions == 2
    assert summary.max_depth == 4
    assert summary.deepest is deep
    assert summary.deepest_label == "deep"
    # events == total descendants across both trees (1 + 4)
    assert summary.events == 5


def test_summarize_day_depth_tie_keeps_first():
    day = date(2025, 6, 17)
    first = _chain("first", 3)
    second = _chain("second", 3)
    summary = summarize_day(day, [first, second])
    assert summary.max_depth == 3
    assert summary.deepest is first  # earliest session wins a tie


# --- build_week -------------------------------------------------------------


def test_build_week_marks_deepest_day():
    end = date(2025, 6, 17)
    forests = {
        date(2025, 6, 15): [_chain("a", 2)],
        date(2025, 6, 16): [_chain("b", 5)],  # the deepest
        date(2025, 6, 17): [_chain("c", 1)],
    }

    def forest_for(day: date):
        return forests.get(day, [])

    week = build_week(end, 3, forest_for)
    assert [d.day for d in week.days] == [
        date(2025, 6, 15),
        date(2025, 6, 16),
        date(2025, 6, 17),
    ]
    assert week.peak_depth == 5
    assert week.deepest_day is not None
    assert week.deepest_day.day == date(2025, 6, 16)
    assert week.deepest_day.deepest_label == "b"
    assert week.start == date(2025, 6, 15)
    assert week.end == end


def test_build_week_keeps_quiet_days():
    end = date(2025, 6, 17)

    def forest_for(day: date):
        # Only the last day has activity.
        return [_chain("only", 2)] if day == end else []

    week = build_week(end, 5, forest_for)
    assert len(week.days) == 5
    assert week.active_days == 1
    assert week.total_sessions == 1
    quiet = week.days[:-1]
    assert all(d.is_empty for d in quiet)


def test_build_week_all_flat_has_no_deepest():
    end = date(2025, 6, 17)

    def forest_for(day: date):
        return [_chain("flat", 0)]  # a session with zero nesting

    week = build_week(end, 3, forest_for)
    assert week.peak_depth == 0
    assert week.deepest_day is None  # nothing actually spiralled
    assert week.active_days == 3


# --- heat_level -------------------------------------------------------------


def test_heat_level_extremes():
    # No depth (or no peak) is always the coldest bucket.
    assert heat_level(0, 0) == 0
    assert heat_level(0, 5) == 0
    # The week's peak is always the hottest bucket.
    assert heat_level(5, 5) == HEAT_LEVELS - 1
    assert heat_level(9, 5) == HEAT_LEVELS - 1  # clamps above peak


def test_heat_level_is_monotonic_and_bounded():
    peak = 8
    levels = [heat_level(d, peak) for d in range(peak + 1)]
    # Coldest at 0, hottest at peak, never decreasing in between, always in range.
    assert levels[0] == 0
    assert levels[-1] == HEAT_LEVELS - 1
    assert all(0 <= lv <= HEAT_LEVELS - 1 for lv in levels)
    assert all(b >= a for a, b in zip(levels, levels[1:], strict=False))


def test_day_summary_defaults():
    d = DaySummary(day=date(2025, 6, 17))
    assert d.is_empty
    assert d.deepest_label is None
