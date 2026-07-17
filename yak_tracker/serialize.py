"""JSON serialization of the yak-shaving forest (PLAN.md M6, ``--json``).

The terminal render in ``render.py`` is for humans; this module is the
machine-readable counterpart so ``yak today --json`` can feed scripts, cron
jobs, or a notes vault. It walks the same :class:`~yak_tracker.tree.Node`
forest that the renderer does and emits plain JSON-friendly ``dict``/``list``
structures — no rich, no typer, nothing that couples it to a presentation layer.

Design goals:

* **Stable shape.** Keys are explicit and ordered; the detour ``kind`` strings
  are exactly :class:`~yak_tracker.tree.DetourKind`'s constants (which is why
  those were kept as plain strings). Consumers can switch on them.
* **Self-describing.** The top-level object carries the ``date`` it covers, a
  ``generated_at`` UTC stamp, a ``schema`` version, and a rollup ``summary`` so
  a single document stands on its own.
* **Lossless-enough.** Each node round-trips its label, kind, timestamp, source,
  and the raw command — enough to re-render or post-process without re-reading
  shell history.

Timestamps are emitted as ISO-8601 strings (local time, as collected), or
``null`` when an event had none.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime

from .models import Event
from .tree import Node

__all__ = [
    "SCHEMA_VERSION",
    "node_to_dict",
    "forest_to_dict",
    "saga_to_dict",
    "dumps",
]

# Bump when the emitted shape changes incompatibly. Lets downstream consumers
# guard against a future reshuffle.
SCHEMA_VERSION = 1


def _iso(ts: datetime | None) -> str | None:
    """ISO-8601 string for a timestamp, or ``None``."""
    return ts.isoformat() if ts is not None else None


def _event_dict(event: Event | None) -> dict | None:
    """Serialize the source event of a node (or ``None`` if it has none)."""
    if event is None:
        return None
    return {
        "cmd": event.cmd,
        "ts": _iso(event.ts),
        "cwd": event.cwd,
        "source": event.source,
    }


def node_to_dict(node: Node) -> dict:
    """Recursively serialize a yak-shaving :class:`Node` into a plain dict.

    The shape mirrors the tree: ``label``/``kind``/``ts`` describe the node, the
    nested ``event`` preserves the raw source, and ``children`` recurses. Per-node
    ``descendants`` and ``depth`` are included so consumers don't have to re-walk
    the tree to size a rabbit hole.
    """
    return {
        "label": node.label,
        "kind": node.kind,
        "ts": _iso(node.ts),
        "event": _event_dict(node.event),
        "descendants": node.descendants(),
        "depth": node.max_depth(),
        "children": [node_to_dict(child) for child in node.children],
    }


def _session_dict(index: int, root: Node) -> dict:
    """Wrap one session's tree with its index and quick stats."""
    return {
        "index": index,
        "intention": root.label,
        "start": _iso(root.ts),
        "events": root.descendants() + 1,  # include the root/anchor itself
        "detours": root.descendants(),
        "depth": root.max_depth(),
        "tree": node_to_dict(root),
    }


def forest_to_dict(
    forest: Sequence[Node],
    *,
    day: date | None = None,
    generated_at: datetime | None = None,
) -> dict:
    """Serialize a whole forest (one tree per session) into a JSON-ready dict.

    Args:
        forest: The per-session root nodes from
            :func:`~yak_tracker.tree.build_forest`.
        day: The date this forest reconstructs, recorded under ``date``.
        generated_at: Override the generation timestamp (mainly for
            deterministic tests). Defaults to ``datetime.now(timezone.utc)``.

    Returns:
        A dict with ``schema``, ``date``, ``generated_at``, a rollup ``summary``,
        and the ``sessions`` array.
    """
    stamp = generated_at or datetime.now(UTC)
    sessions = [_session_dict(i, root) for i, root in enumerate(forest, start=1)]

    total_events = sum(s["events"] for s in sessions)
    deepest = max((s["depth"] for s in sessions), default=0)

    return {
        "schema": SCHEMA_VERSION,
        "date": day.isoformat() if day is not None else None,
        "generated_at": _iso(stamp),
        "summary": {
            "sessions": len(sessions),
            "events": total_events,
            "max_depth": deepest,
        },
        "sessions": sessions,
    }


def saga_to_dict(
    saga,
    *,
    generated_at: datetime | None = None,
) -> dict:
    """Serialize a multi-day :class:`~yak_tracker.saga.Saga` into a JSON dict.

    The shape mirrors :func:`forest_to_dict` but adds a ``days`` array so the
    per-day boundaries the saga preserves survive into machine-readable output.
    Each day carries its own matching ``sessions`` forest (same per-session shape
    as ``today --json``) plus quick per-day stats.
    """
    stamp = generated_at or datetime.now(UTC)
    days = []
    for sd in saga.days:
        sessions = [_session_dict(i, root) for i, root in enumerate(sd.forest, start=1)]
        days.append(
            {
                "date": sd.day.isoformat(),
                "sessions": sessions,
                "summary": {
                    "sessions": sd.sessions,
                    "events": sum(s["events"] for s in sessions),
                    "max_depth": sd.max_depth,
                },
            }
        )
    return {
        "schema": SCHEMA_VERSION,
        "kind": "saga",
        "match": saga.match,
        "start": saga.start.isoformat() if saga.start else None,
        "end": saga.end.isoformat() if saga.end else None,
        "generated_at": _iso(stamp),
        "summary": {
            "active_days": len(saga.days),
            "span_days": saga.span_days,
            "sessions": saga.total_sessions,
            "events": saga.total_events,
            "peak_depth": saga.peak_depth,
        },
        "days": days,
    }


def dumps(
    forest: Sequence[Node],
    *,
    day: date | None = None,
    generated_at: datetime | None = None,
    indent: int | None = 2,
) -> str:
    """Render a forest to a JSON string (pretty by default).

    A thin convenience over :func:`forest_to_dict` + :func:`json.dumps`. Pass
    ``indent=None`` for compact single-line output suited to piping.
    """
    payload = forest_to_dict(forest, day=day, generated_at=generated_at)
    return json.dumps(payload, indent=indent, ensure_ascii=False)
