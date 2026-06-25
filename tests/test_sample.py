"""Tests for the built-in sample day (``yak_tracker.sample``)."""

from __future__ import annotations

from datetime import date, timedelta

from yak_tracker.sample import (
    REFERENCE_DATE,
    sample_events,
    sample_events_for,
)
from yak_tracker.sessionize import sessionize
from yak_tracker.tree import DetourKind, build_forest


def _kinds(node) -> set[str]:
    """All detour kinds present in a tree (recursively), including the root."""
    found = {node.kind}
    for child in node.children:
        found |= _kinds(child)
    return found


def test_sample_events_default_land_on_reference_date() -> None:
    events = sample_events()
    assert events, "sample day should not be empty"
    assert all(e.ts is not None for e in events), "every sample event is timestamped"
    assert all(e.ts.date() == REFERENCE_DATE for e in events)
    # Chronological order is what the sessionizer assumes downstream.
    times = [e.ts for e in events]
    assert times == sorted(times)


def test_sample_events_rebase_onto_requested_day() -> None:
    target = date(2030, 1, 2)
    events = sample_events(day=target)
    assert all(e.ts.date() == target for e in events)
    # Rebasing must preserve the intra-day shape (same count, same first/last
    # clock times), just shifted to the new date.
    ref = sample_events()
    assert len(events) == len(ref)
    assert events[0].ts.time() == ref[0].ts.time()
    assert events[-1].ts.time() == ref[-1].ts.time()


def test_sample_day_builds_a_rich_multi_kind_forest() -> None:
    forest = build_forest(sessionize(sample_events(), idle_gap=25.0))
    # The day is authored to split into two sessions across the lunch gap.
    assert len(forest) == 2

    # Every detour kind should be exercised somewhere in the demo so it shows
    # off the full taxonomy (this is the whole point of the sample).
    all_kinds: set[str] = set()
    for root in forest:
        all_kinds |= _kinds(root)
    for kind in (
        DetourKind.INSTALL,
        DetourKind.ERROR_FIX,
        DetourKind.DIR_CHANGE,
        DetourKind.BRANCH,
    ):
        assert kind in all_kinds, f"sample day should include a {kind} detour"

    # And it should nest at least one same-kind spiral a couple levels deep.
    assert max(root.max_depth() for root in forest) >= 2


def test_sample_events_for_populates_every_day_in_window() -> None:
    end = date(2026, 6, 20)
    events = sample_events_for(end, 3)
    days = {e.ts.date() for e in events}
    assert days == {end - timedelta(days=offset) for offset in range(3)}
    # One full sample day per window day.
    assert len(events) == 3 * len(sample_events())


def test_sample_events_for_clamps_below_one() -> None:
    end = date(2026, 6, 20)
    assert sample_events_for(end, 0) == sample_events(day=end)
