"""Local multi-day statistics over the existing tree and focus-score pipeline."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean, median

from .score import ScoreHistory, build_history, detour_branches, detour_depths
from .tree import DetourKind, Node

DEFAULT_STATS_DAYS = 30
DEFAULT_RABBIT_HOLE_THRESHOLD = 6


@dataclass(slots=True)
class DeepestTangent:
    day: date
    depth: int
    label: str


@dataclass(slots=True)
class StatsSummary:
    history: ScoreHistory
    threshold: int
    depths: list[int]
    deepest: DeepestTangent | None
    busiest_repos: dict[str, int]
    triggers: dict[str, int]

    def to_dict(self) -> dict:
        """Stable JSON document; quiet days have no invented focus score."""
        return {
            "kind": "stats",
            "from": self.history.start.isoformat() if self.history.start else None,
            "to": self.history.end.isoformat() if self.history.end else None,
            "summary": {
                "sessions": sum(day.session_count for day in self.history.days),
                "active_days": len(self.history.scored_days),
                "detours": len(self.depths),
                "avg_depth": mean(self.depths) if self.depths else None,
                "median_depth": median(self.depths) if self.depths else None,
                "deepest_tangent": (
                    {
                        "date": self.deepest.day.isoformat(),
                        "depth": self.deepest.depth,
                        "label": self.deepest.label,
                    }
                    if self.deepest else None
                ),
                "rabbit_hole_threshold": self.threshold,
                "rabbit_hole_days": sum(
                    day.max_depth > self.threshold for day in self.history.days
                ),
                "busiest_repos": self.busiest_repos,
                "detour_triggers": self.triggers,
                "most_common_trigger": next(iter(self.triggers), None),
            },
            "days": [
                {
                    "date": day.day.isoformat(),
                    "sessions": day.session_count,
                    "score": day.score,
                    "max_depth": day.max_depth,
                }
                for day in self.history.days
            ],
        }


def repo_for(cwd: str | None, repos: Sequence[Path]) -> Path | None:
    """Attribute known working directories to the most specific tracked repo."""
    if not cwd or not Path(cwd).is_absolute():
        return None
    path = Path(cwd).resolve()
    return next((repo for repo in repos if path.is_relative_to(repo)), None)


def build_stats(
    end: date,
    span: int,
    forest_for: Callable[[date], Sequence[Node]],
    *,
    threshold: int = DEFAULT_RABBIT_HOLE_THRESHOLD,
    repos: Sequence[Path] = (),
) -> StatsSummary:
    """Aggregate top-level detour depths and all classified detour triggers.

    Depths use the score engine's definition, including nested ordinary steps.
    No-detour sessions contribute focus scores, but not fabricated zero-depth
    tangents. Ties keep the earliest deepest tangent; count rankings sort ties
    alphabetically.
    """
    if threshold < 0:
        raise ValueError("rabbit-hole threshold must not be negative")
    tracked = sorted(
        {repo.expanduser().resolve() for repo in repos},
        key=lambda repo: (-len(repo.parts), str(repo)),
    )
    depths: list[int] = []
    deepest: DeepestTangent | None = None
    repo_counts: Counter[str] = Counter()
    triggers: Counter[str] = Counter()

    def summarize(day: date) -> Sequence[Node]:
        nonlocal deepest
        forest = forest_for(day)
        for root in forest:
            root_depths = detour_depths(root)
            depths.extend(root_depths)
            for branch, depth in zip(detour_branches(root), root_depths, strict=True):
                if deepest is None or depth > deepest.depth:
                    deepest = DeepestTangent(day, depth, " ".join(branch.label.split()))
            stack = [root]
            while stack:
                node = stack.pop()
                if node.kind not in (DetourKind.ROOT, DetourKind.STEP):
                    triggers[node.kind] += 1
                if node.event is not None:
                    repo = repo_for(node.event.cwd, tracked)
                    if repo is not None:
                        repo_counts[str(repo)] += 1
                stack.extend(reversed(node.children))
        return forest

    history = build_history(end, span, summarize)
    return StatsSummary(
        history=history,
        threshold=threshold,
        depths=depths,
        deepest=deepest,
        busiest_repos=dict(sorted(repo_counts.items(), key=lambda row: (-row[1], row[0]))),
        triggers=dict(sorted(triggers.items(), key=lambda row: (-row[1], row[0]))),
    )
