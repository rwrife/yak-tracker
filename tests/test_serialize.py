"""Unit tests for JSON serialization of the yak-shaving forest (M6 --json)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from yak_tracker.models import Event
from yak_tracker.serialize import (
    SCHEMA_VERSION,
    dumps,
    forest_to_dict,
    node_to_dict,
)
from yak_tracker.sessionize import sessionize
from yak_tracker.tree import DetourKind, Node, build_forest


def _ev(cmd: str, minute: int, source: str = "shell:zsh") -> Event:
    return Event(
        cmd=cmd,
        ts=datetime(2026, 6, 17, 9, minute, 0),
        cwd=None,
        source=source,
    )


def test_node_to_dict_round_trips_fields_and_nesting() -> None:
    child = Node(
        label="npm install left-pad",
        kind=DetourKind.INSTALL,
        event=_ev("npm install left-pad", 5),
    )
    root = Node(
        label="fix login bug",
        kind=DetourKind.ROOT,
        event=_ev("git commit", 0),
        children=[child],
    )

    d = node_to_dict(root)

    assert d["label"] == "fix login bug"
    assert d["kind"] == DetourKind.ROOT
    assert d["ts"] == "2026-06-17T09:00:00"
    assert d["descendants"] == 1
    assert d["depth"] == 1
    assert d["event"]["cmd"] == "git commit"
    assert d["event"]["source"] == "shell:zsh"

    assert len(d["children"]) == 1
    kid = d["children"][0]
    assert kid["kind"] == DetourKind.INSTALL
    assert kid["children"] == []
    assert kid["descendants"] == 0
    assert kid["depth"] == 0


def test_node_without_event_serializes_null() -> None:
    node = Node(label="session", kind=DetourKind.ROOT, event=None)
    d = node_to_dict(node)
    assert d["event"] is None
    assert d["ts"] is None


def test_forest_to_dict_has_schema_date_and_summary() -> None:
    events = [
        _ev("git commit -m wip", 0, source="git:repo"),
        _ev("npm install", 2),
        _ev("rm -rf node_modules", 4),
    ]
    forest = build_forest(sessionize(events, idle_gap=30))
    stamp = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)

    doc = forest_to_dict(forest, day=date(2026, 6, 17), generated_at=stamp)

    assert doc["schema"] == SCHEMA_VERSION
    assert doc["date"] == "2026-06-17"
    assert doc["generated_at"] == "2026-06-17T12:00:00+00:00"

    summary = doc["summary"]
    assert summary["sessions"] == len(forest) == 1
    # events count includes the root anchor + all descendants
    assert summary["events"] == forest[0].descendants() + 1
    assert summary["max_depth"] == forest[0].max_depth()

    session = doc["sessions"][0]
    assert session["index"] == 1
    assert session["intention"]  # non-empty root label
    assert session["events"] == summary["events"]
    assert "tree" in session


def test_forest_to_dict_empty_is_valid() -> None:
    doc = forest_to_dict([], day=date(2026, 6, 17))
    assert doc["sessions"] == []
    assert doc["summary"] == {"sessions": 0, "events": 0, "max_depth": 0}
    assert doc["date"] == "2026-06-17"


def test_dumps_produces_parseable_json() -> None:
    forest = build_forest(sessionize([_ev("git commit", 0, source="git:r")], idle_gap=30))
    stamp = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)
    text = dumps(forest, day=date(2026, 6, 17), generated_at=stamp)
    parsed = json.loads(text)
    assert parsed["schema"] == SCHEMA_VERSION
    assert parsed["sessions"][0]["tree"]["kind"] == DetourKind.ROOT

    compact = dumps(forest, day=date(2026, 6, 17), generated_at=stamp, indent=None)
    assert "\n" not in compact
    assert json.loads(compact) == parsed
