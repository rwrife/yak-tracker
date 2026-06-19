"""M3 tests for the sessionizer (gap-bucketing logic)."""

from __future__ import annotations

from datetime import datetime, timedelta

from yak_tracker.models import Event
from yak_tracker.sessionize import (
    DEFAULT_IDLE_GAP,
    Session,
    sessionize,
    summarize,
)

BASE = datetime(2025, 6, 17, 9, 0, 0)


def _ev(minutes: float, *, source: str = "shell:zsh", cmd: str | None = None) -> Event:
    """An event at ``BASE + minutes`` from the given source."""
    return Event(
        cmd=cmd or f"cmd@{minutes}",
        ts=BASE + timedelta(minutes=minutes),
        cwd=None,
        source=source,
    )


def _undated(cmd: str = "no-ts") -> Event:
    return Event(cmd=cmd, ts=None, cwd=None, source="shell:bash")


# --- basic bucketing -----------------------------------------------------


def test_empty_input_yields_no_sessions() -> None:
    assert sessionize([]) == []


def test_single_event_one_session() -> None:
    sessions = sessionize([_ev(0)])
    assert len(sessions) == 1
    assert sessions[0].count == 1
    assert sessions[0].duration == timedelta(0)


def test_close_events_stay_in_one_session() -> None:
    # gaps of 10 and 20 min, all under the 25 min default
    sessions = sessionize([_ev(0), _ev(10), _ev(30)])
    assert len(sessions) == 1
    assert sessions[0].count == 3
    assert sessions[0].start == BASE
    assert sessions[0].end == BASE + timedelta(minutes=30)


def test_large_gap_splits_sessions() -> None:
    # 0, 5 → session A; then 40-min gap → 45, 50 → session B
    sessions = sessionize([_ev(0), _ev(5), _ev(45), _ev(50)])
    assert len(sessions) == 2
    assert [s.count for s in sessions] == [2, 2]
    assert sessions[0].end == BASE + timedelta(minutes=5)
    assert sessions[1].start == BASE + timedelta(minutes=45)


def test_gap_exactly_at_threshold_does_not_split() -> None:
    # Exactly 25 min apart: not *greater than* the gap, so same session.
    sessions = sessionize([_ev(0), _ev(25)], idle_gap=25)
    assert len(sessions) == 1


def test_gap_just_over_threshold_splits() -> None:
    sessions = sessionize([_ev(0), _ev(25.5)], idle_gap=25)
    assert len(sessions) == 2


# --- input handling ------------------------------------------------------


def test_unsorted_input_is_sorted_first() -> None:
    sessions = sessionize([_ev(50), _ev(0), _ev(5), _ev(45)])
    assert len(sessions) == 2
    assert sessions[0].start == BASE
    assert sessions[1].end == BASE + timedelta(minutes=50)


def test_undated_dropped_by_default() -> None:
    sessions = sessionize([_ev(0), _undated(), _ev(5)])
    assert len(sessions) == 1
    assert sessions[0].count == 2  # the undated event was dropped


def test_undated_attached_when_requested() -> None:
    sessions = sessionize([_ev(0), _ev(5), _undated("late")], attach_undated=True)
    assert len(sessions) == 1
    assert sessions[0].count == 3
    assert sessions[0].events[-1].cmd == "late"


def test_only_undated_with_attach_yields_nothing() -> None:
    # No timed events → nowhere to attach → no sessions.
    assert sessionize([_undated(), _undated()], attach_undated=True) == []


# --- idle_gap forms ------------------------------------------------------


def test_idle_gap_accepts_timedelta() -> None:
    sessions = sessionize([_ev(0), _ev(40)], idle_gap=timedelta(hours=1))
    assert len(sessions) == 1


def test_idle_gap_none_uses_default() -> None:
    # 30-min gap > 25-min default → split.
    sessions = sessionize([_ev(0), _ev(30)], idle_gap=None)
    assert len(sessions) == 2
    assert DEFAULT_IDLE_GAP == timedelta(minutes=25)


# --- Session helpers -----------------------------------------------------


def test_session_sources_distinct_in_order() -> None:
    s = Session(
        events=[
            _ev(0, source="shell:zsh"),
            _ev(1, source="git:proj"),
            _ev(2, source="shell:zsh"),
        ]
    )
    assert s.sources() == ["shell:zsh", "git:proj"]


def test_mixed_sources_share_a_session() -> None:
    sessions = sessionize(
        [_ev(0, source="shell:zsh"), _ev(3, source="git:proj"), _ev(6, source="shell:zsh")]
    )
    assert len(sessions) == 1
    assert set(sessions[0].sources()) == {"shell:zsh", "git:proj"}


def test_summarize_counts() -> None:
    sessions = sessionize(
        [_ev(0), _ev(5, source="git:proj"), _ev(60), _ev(65)],
        idle_gap=25,
    )
    stats = summarize(sessions)
    assert stats == {"sessions": 2, "events": 4, "sources": 2}


def test_summarize_empty() -> None:
    assert summarize([]) == {"sessions": 0, "events": 0, "sources": 0}
