"""Aggregation math and empty-window semantics for the local stats dashboard."""

from datetime import date, datetime
from pathlib import Path

import pytest

from yak_tracker.models import Event
from yak_tracker.score import score_day
from yak_tracker.stats import build_stats
from yak_tracker.tree import DetourKind, Node

END = date(2026, 6, 17)


def _forest() -> list[Node]:
    # Top-level depths [1, 3, 2], not per-session means [2, 2, 0].
    first = Node("first", DetourKind.ROOT)
    first.add(Node("npm install one", DetourKind.INSTALL))
    deep = first.add(Node("npm install two\ncontinued", DetourKind.INSTALL))
    deep.add(Node("npm install three", DetourKind.INSTALL)).add(
        Node("npm test", DetourKind.STEP)
    )
    second = Node("second", DetourKind.ROOT)
    second.add(Node("rm -rf build", DetourKind.ERROR_FIX)).add(
        Node("pytest", DetourKind.STEP)
    )
    return [first, second, Node("focused", DetourKind.ROOT)]


def test_aggregation_uses_all_detours_and_existing_daily_score() -> None:
    forest = _forest()
    stats = build_stats(
        END, 3, lambda day: forest if day == END else [], threshold=2,
    )
    doc = stats.to_dict()
    summary = doc["summary"]
    assert summary["sessions"] == 3
    assert summary["active_days"] == 1
    assert summary["detours"] == 3
    assert summary["avg_depth"] == 2
    assert summary["median_depth"] == 2
    assert summary["deepest_tangent"] == {
        "date": END.isoformat(), "depth": 3, "label": "npm install two continued",
    }
    assert summary["rabbit_hole_days"] == 1
    assert summary["detour_triggers"] == {"install": 3, "error-fix": 1}
    assert summary["most_common_trigger"] == "install"
    assert [day["score"] for day in doc["days"]] == [
        None, None, score_day(END, forest).score,
    ]


def test_unequal_session_detours_are_weighted_individually() -> None:
    forest = _forest()
    forest[0].add(Node("cd elsewhere", DetourKind.DIR_CHANGE))
    summary = build_stats(END, 1, lambda _: forest).to_dict()["summary"]
    assert summary["avg_depth"] == pytest.approx(1.75)
    assert summary["median_depth"] == 1.5


def test_rabbit_hole_threshold_is_strict_and_counts_days_not_sessions() -> None:
    forest = _forest()
    assert build_stats(
        END, 2, lambda _: forest, threshold=3,
    ).to_dict()["summary"]["rabbit_hole_days"] == 0
    assert build_stats(
        END, 2, lambda _: forest * 2, threshold=2,
    ).to_dict()["summary"]["rabbit_hole_days"] == 2


def test_empty_window_retains_dates_and_no_fabricated_scores() -> None:
    doc = build_stats(END, 3, lambda _: []).to_dict()
    assert doc["from"] == "2026-06-15"
    assert doc["to"] == "2026-06-17"
    assert len(doc["days"]) == 3
    assert all(day["score"] is None for day in doc["days"])
    summary = doc["summary"]
    assert summary["sessions"] == summary["detours"] == summary["rabbit_hole_days"] == 0
    assert summary["avg_depth"] is summary["median_depth"] is summary["deepest_tangent"] is None
    assert summary["most_common_trigger"] is None
    assert summary["busiest_repos"] == summary["detour_triggers"] == {}


def test_focused_sessions_have_scores_but_no_tangents() -> None:
    root = Node("work", DetourKind.ROOT, children=[Node("test", DetourKind.STEP)])
    doc = build_stats(END, 1, lambda _: [root]).to_dict()
    assert doc["days"][0]["score"] == 100
    assert doc["summary"]["avg_depth"] is None
    assert doc["summary"]["deepest_tangent"] is None


def test_repo_event_counts_use_specific_tracked_path_and_stable_ties(tmp_path: Path) -> None:
    parent, nested = tmp_path / "app", tmp_path / "app" / "nested"
    root = Node("work", DetourKind.ROOT, Event(
        "work", datetime(2026, 6, 17, 9), str(nested / "src"), "shell:nu",
    ))
    root.add(Node("cd elsewhere", DetourKind.DIR_CHANGE, Event(
        "cd elsewhere", datetime(2026, 6, 17, 9, 1), str(parent), "shell:nu",
    )))
    root.add(Node("unknown", DetourKind.STEP, Event(
        "unknown", datetime(2026, 6, 17, 9, 2), None, "shell:zsh",
    )))
    stats = build_stats(END, 1, lambda _: [root], repos=[parent, nested])
    assert stats.busiest_repos == {str(parent): 1, str(nested): 1}


def test_deepest_ties_keep_earliest_day() -> None:
    stats = build_stats(END, 2, lambda _: _forest())
    assert stats.deepest is not None
    assert stats.deepest.day == date(2026, 6, 16)


@pytest.mark.parametrize("span,threshold", [(0, 6), (1, -1)])
def test_invalid_aggregation_arguments(span: int, threshold: int) -> None:
    with pytest.raises(ValueError):
        build_stats(END, span, lambda _: [], threshold=threshold)
