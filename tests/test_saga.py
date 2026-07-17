"""Unit tests for the multi-day saga aggregation layer (``yak saga``)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from yak_tracker.models import Event
from yak_tracker.saga import (
    DEFAULT_SAGA_DAYS,
    SinceError,
    branch_matcher,
    build_saga,
    keyword_matcher,
    parse_since,
    session_matches,
)
from yak_tracker.serialize import saga_to_dict
from yak_tracker.tree import DetourKind, Node


def _ev(cmd: str, *, source: str = "shell:zsh", cwd: str | None = None) -> Event:
    return Event(cmd=cmd, ts=datetime(2026, 1, 1, 9, 0), cwd=cwd, source=source)


def _session(label: str, *, cmds: list[str] | None = None, source: str = "shell:zsh") -> Node:
    """Build a tiny session tree: a root plus optional step children."""
    root = Node(label=label, kind=DetourKind.ROOT, event=_ev(label, source=source))
    for c in cmds or []:
        root.add(Node(label=c, kind=DetourKind.STEP, event=_ev(c, source=source)))
    return root


# --- parse_since ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_SAGA_DAYS),
        ("", DEFAULT_SAGA_DAYS),
        ("7d", 7),
        ("7D", 7),
        ("2w", 14),
        ("2W", 14),
        ("10", 10),
        (5, 5),
        ("  3d ", 3),
    ],
)
def test_parse_since_valid(value, expected) -> None:
    assert parse_since(value) == expected


@pytest.mark.parametrize("value", ["0", "0d", "-1", "nope", "3m", "d", 0, -4])
def test_parse_since_invalid(value) -> None:
    with pytest.raises(SinceError):
        parse_since(value)


# --- matchers ---------------------------------------------------------------


def test_keyword_matcher_hits_label_and_events() -> None:
    m = keyword_matcher("oauth")
    root = _session("wire up OAuth callback", cmds=["vim auth.py"])
    assert session_matches(root, m)
    # Match inside a child command even if the root label doesn't mention it.
    child_only = _session("misc", cmds=["curl https://provider/oauth/token"])
    assert session_matches(child_only, m)
    # No mention anywhere → no match.
    assert not session_matches(_session("fix css", cmds=["vim style.css"]), m)


def test_keyword_matcher_is_case_insensitive_and_rejects_empty() -> None:
    assert session_matches(_session("Ship OAUTH"), keyword_matcher("oauth"))
    with pytest.raises(ValueError):
        keyword_matcher("")


def test_branch_matcher_respects_token_boundaries() -> None:
    m = branch_matcher("main")
    # A real branch reference matches.
    assert session_matches(
        _session("commit", cmds=["reflog abc checkout: moving from x to main"]), m
    )
    # 'domain' / 'maintenance' must NOT match the 'main' branch.
    assert not session_matches(_session("edit domain model", cmds=["vim domain.py"]), m)
    assert not session_matches(_session("maintenance window notes"), m)


def test_branch_matcher_matches_slashed_branch() -> None:
    m = branch_matcher("feat/oauth")
    assert session_matches(
        _session("commit", cmds=["reflog 1a2 checkout: moving from main to feat/oauth"]),
        m,
    )
    assert not session_matches(_session("feat work"), m)
    with pytest.raises(ValueError):
        branch_matcher("   ")


# --- build_saga: cross-day grouping -----------------------------------------


def test_build_saga_groups_matches_and_preserves_day_boundaries() -> None:
    d1, d2, d3 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)

    forests = {
        d1: [_session("scaffold oauth routes"), _session("unrelated css fix")],
        d2: [_session("reading docs")],  # no match this day → dropped
        d3: [
            _session("oauth token refresh"),
            _session("oauth logout bug", cmds=["vim auth.py", "vim session.py"]),
        ],
    }

    saga = build_saga(
        [d1, d2, d3],
        lambda day: forests[day],
        keyword_matcher("oauth"),
        match_label="oauth",
    )

    # Only days with matches are kept, oldest first.
    assert [sd.day for sd in saga.days] == [d1, d3]
    # Day 1 kept only the matching session (not the css one).
    assert saga.days[0].sessions == 1
    assert saga.days[1].sessions == 2
    # Roll-ups span the whole arc even though the middle day was empty.
    assert saga.total_sessions == 3
    assert saga.span_days == 3  # d1..d3 inclusive
    assert saga.start == d1 and saga.end == d3
    assert not saga.is_empty


def test_build_saga_empty_when_nothing_matches() -> None:
    d = date(2026, 1, 1)
    saga = build_saga(
        [d],
        lambda _day: [_session("fix css")],
        keyword_matcher("oauth"),
        match_label="oauth",
    )
    assert saga.is_empty
    assert saga.days == []
    assert saga.span_days == 0
    assert saga.peak_depth == 0
    assert saga.forest() == []


def test_saga_to_dict_shape() -> None:
    d1, d2 = date(2026, 1, 1), date(2026, 1, 2)
    forests = {
        d1: [_session("oauth start", cmds=["vim a.py"])],
        d2: [_session("oauth finish")],
    }
    saga = build_saga(
        [d1, d2], lambda day: forests[day], keyword_matcher("oauth"), match_label="oauth"
    )
    doc = saga_to_dict(saga, generated_at=datetime(2026, 1, 3, 12, 0))

    assert doc["kind"] == "saga"
    assert doc["match"] == "oauth"
    assert doc["start"] == "2026-01-01"
    assert doc["end"] == "2026-01-02"
    assert doc["summary"]["active_days"] == 2
    assert doc["summary"]["sessions"] == 2
    assert doc["generated_at"] == "2026-01-03T12:00:00"
    # Per-day boundaries survive into JSON.
    assert [day["date"] for day in doc["days"]] == ["2026-01-01", "2026-01-02"]
    assert doc["days"][0]["sessions"][0]["intention"] == "oauth start"
