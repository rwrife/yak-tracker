"""Multi-day yak-shaving saga (PLAN.md backlog #12 → ``yak saga``).

``yak today``/``yak week`` are day-scoped, but a real feature ("ship OAuth")
usually spans several days, with its detours scattered across them. ``yak saga``
reconstructs the whole multi-day arc of a *single thread* into one continuous
narrative: it filters each day's sessions down to the ones that belong to the
thread (by keyword or git branch), then stitches the survivors together while
**preserving per-day boundaries** so the through-line is legible ("Day 1 you
scaffolded… Day 3 you rabbit-holed into the lockfile…").

Like ``week.py`` and ``serialize.py`` this is the presentation-free aggregation
layer: it walks the same :class:`~yak_tracker.tree.Node` forests the renderer,
serializer and narrator do, and emits plain dataclasses. Rendering lives in
``render.py``; narration reuses ``narrate.py``; ``--json`` reuses the node
serializer.

Matching is deliberately simple and honest:

* **keyword** (``--match``) — case-insensitive substring test against every
  node's label *and* raw command/cwd/source in a session's tree. A session
  matches if any of its events mention the keyword (a branch name, a commit
  subject, a touched path, or free text).
* **branch** (``--branch``) — resolves branch commit membership and bounds
  references to its first-commit through merge/last-activity window. Without
  git collection, matches explicit, case-sensitive branch tokens only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

from .collectors.git import _epoch_to_dt, _repo_label, _run_git
from .models import Event
from .tree import Node

__all__ = [
    "DEFAULT_SAGA_DAYS",
    "SinceError",
    "SagaError",
    "BranchHistory",
    "load_branch_history",
    "collect_saga_commits",
    "parse_since",
    "Matcher",
    "keyword_matcher",
    "branch_matcher",
    "session_matches",
    "SagaDay",
    "Saga",
    "build_saga",
]

# Default lookback window when neither --from/--to nor --since is given.
DEFAULT_SAGA_DAYS = 7

# A relative window like "7d", "2w", or a bare "10" (meaning days).
_SINCE_RE = re.compile(r"^\s*(\d+)\s*([dw])?\s*$", re.IGNORECASE)

# A matcher takes a session's root node and reports whether the session belongs
# to the saga thread.
Matcher = Callable[[Node], bool]


class SinceError(ValueError):
    """Raised when a ``--since`` window string can't be parsed."""


class SagaError(ValueError):
    """Raised when git history cannot establish a branch's lifetime."""


@dataclass(slots=True)
class BranchHistory:
    """Branch-specific commits and their first-commit to merge/activity window."""

    repo: Path
    commits: set[str]
    start: datetime
    end: datetime


def load_branch_history(repo: Path, branch: str) -> BranchHistory | None:
    """Resolve a local branch against the repository's primary branch.

    Prefer origin/HEAD, then main/master. For a merged branch, use the merge's
    first parent to recover the original fork point, not today's merge-base
    (which would be the branch tip itself).
    """
    repo = repo.expanduser().resolve()
    ref = f"refs/heads/{branch}"
    tip_out = _run_git(repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if not tip_out:
        return None
    tip = tip_out.strip()
    base = None
    merged_at = None
    merge_sha = None
    primary = _run_git(repo, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    candidates = [primary.strip()] if primary else []
    candidates += ["refs/heads/main", "refs/heads/master"]
    for candidate in candidates:
        primary_tip = _run_git(repo, ["rev-parse", "--verify", f"{candidate}^{{commit}}"])
        if not primary_tip:
            continue
        if candidate in (ref, f"refs/remotes/origin/{branch}"):
            break
        base_out = _run_git(repo, ["merge-base", tip, primary_tip.strip()])
        if not base_out:
            raise SagaError(f"cannot determine fork point for {branch!r} in {repo}")
        base = base_out.strip()
        if base == tip:
            merges = _run_git(repo, [
                "log", "--first-parent", "--merges", "--format=%ct%x1f%h%x1f%P", candidate,
            ])
            if merges is None:
                raise SagaError(f"cannot read merge history for {branch!r} in {repo}")
            for line in reversed(merges.splitlines()):
                stamp, sha, parents_text = line.split("\x1f", 2)
                parents = parents_text.split()
                if tip in parents[1:]:
                    fork = _run_git(repo, ["merge-base", tip, parents[0]])
                    if fork:
                        base = fork.strip()
                        merged_at = _epoch_to_dt(stamp)
                        merge_sha = sha
                    break
            if base == tip:
                raise SagaError(
                    f"cannot distinguish {branch!r}'s lifetime after a fast-forward "
                    f"merge in {repo}; use --match with an explicit date window"
                )
        break

    revisions = [ref, f"^{base}"] if base else [ref]
    out = _run_git(repo, ["log", "--format=%h%x1f%ct", *revisions, "--"])
    if out is None:
        raise SagaError(f"cannot read branch history for {branch!r} in {repo}")
    commits: set[str] = set()
    stamps: list[datetime] = []
    for line in out.splitlines():
        sha, stamp = line.split("\x1f", 1)
        ts = _epoch_to_dt(stamp)
        if ts is not None:
            commits.add(sha)
            stamps.append(ts)
    if not stamps:
        raise SagaError(f"no branch-specific commits found for {branch!r} in {repo}")
    if merge_sha:
        commits.add(merge_sha)
    return BranchHistory(repo, commits, min(stamps), merged_at or max(stamps))


def collect_saga_commits(repo: Path, start: date, end: date) -> list[Event]:
    """Collect the requested window, including paths, without a seven-day cap."""
    out = _run_git(
        repo,
        [
            "log", "--all", f"--since={datetime.combine(start, time.min).isoformat()}",
            f"--until={datetime.combine(end, time(23, 59, 59)).isoformat()}",
            "--format=%x1e%ct%x1f%h%x1f%s", "--name-only", "--",
        ],
    )
    if out is None:
        raise SagaError(f"cannot read git history in {repo}; use --no-git for shell history")
    events: list[Event] = []
    label = _repo_label(repo)
    for record in out.split("\x1e"):
        if not record.strip():
            continue
        header, *paths = record.strip().splitlines()
        stamp, sha, subject = header.split("\x1f", 2)
        events.append(Event(
            cmd=f"commit {sha} {subject}" + "".join(f"\n{path}" for path in paths if path),
            ts=_epoch_to_dt(stamp),
            cwd=str(repo),
            source=f"git:{label}",
        ))
    return events


def parse_since(value: str | int | None, *, default: int = DEFAULT_SAGA_DAYS) -> int:
    """Parse a relative window into a positive number of days.

    Accepts ``"7d"`` (days), ``"2w"`` (weeks), or a bare integer/``"10"``
    (treated as days). ``None``/empty yields ``default``. Raises
    :class:`SinceError` on anything else so the CLI can surface a clean message.
    """
    if value is None or value == "":
        return default
    if isinstance(value, int):
        days = value
    else:
        match = _SINCE_RE.match(str(value))
        if not match:
            raise SinceError(
                f"invalid window {value!r}; use e.g. '7d', '2w', or a number of days"
            )
        n = int(match.group(1))
        unit = (match.group(2) or "d").lower()
        days = n * 7 if unit == "w" else n
    if days < 1:
        raise SinceError("window must be a positive number of days")
    return days


def _node_haystack(node: Node) -> str:
    """All searchable text for a single node (label + raw event fields)."""
    parts = [node.label]
    ev = node.event
    if ev is not None:
        parts.append(ev.cmd)
        if ev.cwd:
            parts.append(ev.cwd)
        parts.append(ev.source)
    return "\n".join(parts)


def _tree_haystack(root: Node) -> str:
    """Concatenate the searchable text of a whole session tree."""
    chunks: list[str] = []
    stack: list[Node] = [root]
    while stack:
        cur = stack.pop()
        chunks.append(_node_haystack(cur))
        stack.extend(cur.children)
    return "\n".join(chunks)


def keyword_matcher(keyword: str) -> Matcher:
    """Case-insensitive substring matcher over a session tree's text.

    Matches when ``keyword`` appears anywhere in the session's node labels or
    the raw command/cwd/source of any event in the tree.
    """
    needle = keyword.casefold()
    if not needle:
        raise ValueError("match keyword must not be empty")

    def _match(root: Node) -> bool:
        return needle in _tree_haystack(root).casefold()

    return _match


def branch_matcher(
    branch: str, *, histories: Sequence[BranchHistory] = ()
) -> Matcher:
    """Matcher anchored on a git ``branch`` token.

    Git-backed matching uses branch commit membership and bounds references by
    its lifetime. Without git, only explicit branch tokens can be recognized.
    """
    token = branch.strip()
    if not token:
        raise ValueError("branch name must not be empty")
    # Boundary = anything that isn't a typical branch-name char (branch names
    # allow letters, digits, '/', '-', '_', '.'). This lets "feat/oauth" match
    # cleanly while still requiring a real boundary on both sides.
    pattern = re.compile(
        rf"(?<![\w/.\-]){re.escape(token)}(?![\w/.\-])",
    )

    def _match(root: Node) -> bool:
        stack = [root]
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if not histories:
                if pattern.search(_node_haystack(node)):
                    return True
                continue
            ev = node.event
            if ev is None or ev.ts is None:
                continue
            for history in histories:
                if not history.start <= ev.ts <= history.end:
                    continue
                if ev.cwd and Path(ev.cwd).expanduser().resolve() != history.repo:
                    continue
                fields = ev.cmd.split(maxsplit=2)
                if (
                    ev.source.startswith("git:")
                    and len(fields) >= 2
                    and fields[0] == "commit"
                    and fields[1] in history.commits
                ):
                    return True
                if pattern.search(_node_haystack(node)):
                    return True
        return False

    return _match


def session_matches(root: Node, matcher: Matcher) -> bool:
    """Return True if a session's tree belongs to the saga ``matcher``."""
    return matcher(root)


@dataclass(slots=True)
class SagaDay:
    """One day's contribution to a saga: the matching session trees.

    Attributes:
        day: The calendar date these sessions ran on.
        forest: The subset of that day's session trees that matched the saga
            thread, in the day's original session order. Never empty for a
            :class:`SagaDay` that is *kept* in a :class:`Saga` (empty days are
            dropped so the saga is dense).
    """

    day: date
    forest: list[Node] = field(default_factory=list)

    @property
    def sessions(self) -> int:
        """How many matching sessions this day contributed."""
        return len(self.forest)

    @property
    def events(self) -> int:
        """Total detour events across the day's matching trees (excl. roots)."""
        return sum(root.descendants() for root in self.forest)

    @property
    def max_depth(self) -> int:
        """Deepest matching-session tangent chain this day (``0`` if flat)."""
        return max((root.max_depth() for root in self.forest), default=0)


@dataclass(slots=True)
class Saga:
    """A multi-day thread stitched from matching sessions across days.

    Attributes:
        match: Human description of what anchored the saga (the keyword or
            ``branch:<name>``), purely for display/JSON.
        days: The active :class:`SagaDay` rows (days with at least one matching
            session), oldest first. Quiet days are dropped so the arc is dense.
    """

    match: str
    days: list[SagaDay] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when nothing across the whole window matched the thread."""
        return not self.days

    @property
    def start(self) -> date | None:
        """First (oldest) active day, if any."""
        return self.days[0].day if self.days else None

    @property
    def end(self) -> date | None:
        """Last (most recent) active day, if any."""
        return self.days[-1].day if self.days else None

    @property
    def total_sessions(self) -> int:
        """Matching sessions summed across the active days."""
        return sum(d.sessions for d in self.days)

    @property
    def total_events(self) -> int:
        """Detour events summed across the active days."""
        return sum(d.events for d in self.days)

    @property
    def peak_depth(self) -> int:
        """Deepest single-session tangent chain across the whole saga."""
        return max((d.max_depth for d in self.days), default=0)

    @property
    def span_days(self) -> int:
        """Inclusive calendar span from first to last active day (``0`` empty)."""
        if not self.days:
            return 0
        return (self.end - self.start).days + 1

    def forest(self) -> list[Node]:
        """Flatten all matching trees across days into one ordered forest."""
        out: list[Node] = []
        for d in self.days:
            out.extend(d.forest)
        return out


def build_saga(
    days: Sequence[date],
    forest_for: Callable[[date], Sequence[Node]],
    matcher: Matcher,
    *,
    match_label: str,
) -> Saga:
    """Stitch a :class:`Saga` from the sessions across ``days`` that match.

    Args:
        days: The calendar window to scan, oldest first (the CLI builds this
            from ``--from``/``--to`` or ``--since``).
        forest_for: Callback yielding a day's full yak-shaving forest (one
            :class:`~yak_tracker.tree.Node` per session). Wired by the CLI to
            the shared collect → sessionize → build_forest pipeline.
        matcher: Predicate deciding whether a session belongs to the thread
            (see :func:`keyword_matcher` / :func:`branch_matcher`).
        match_label: Human description recorded on the :class:`Saga`.

    Returns:
        A :class:`Saga` whose ``days`` contain only days with ≥1 matching
        session (quiet/non-matching days are dropped), preserving per-day
        boundaries and each day's session order.
    """
    saga = Saga(match=match_label)
    for day in days:
        matched = [root for root in forest_for(day) if session_matches(root, matcher)]
        if matched:
            saga.days.append(SagaDay(day=day, forest=matched))
    return saga
