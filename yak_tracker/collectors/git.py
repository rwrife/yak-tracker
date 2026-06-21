"""Git collector: commits + reflog → normalized ``Event`` objects.

Walks one or more git repositories and turns their recent activity into
:class:`~yak_tracker.models.Event` records so the sessionizer can interleave
them with shell history.

Two sources are collected per repo:

**Commits** (``git log``). Each commit becomes an event timestamped at its
*commit* time (author time is often the same but commit time better reflects
"when it landed on this machine"). The command text is a short
``commit <abbrev> <subject>`` summary.

**Reflog** (``git reflog``). Branch switches, checkouts, resets, merges, and
rebases all show up here with their own timestamps. These capture the *navigation*
part of a coding day — jumping between branches while chasing a tangent — which
plain ``git log`` misses entirely.

Everything is best-effort: a path that is not a git repo, a repo with no
commits, or a git binary that errors out yields ``[]`` for that repo rather
than raising, so one bad repo can't sink a whole ``yak`` run.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from ..models import Event

# Field separator unlikely to appear in commit subjects. ``git log`` lets us
# pick the format, so we use a unit-separator byte to split fields robustly.
_SEP = "\x1f"

# How far back to look by default. A coding *day* rarely needs more, and it
# keeps `git log`/`reflog` cheap on large repos. Callers can widen via ``since``.
_DEFAULT_SINCE = "7.days.ago"


def _run_git(repo: Path, args: Sequence[str]) -> str | None:
    """Run ``git -C <repo> <args>`` and return stdout, or ``None`` on any error.

    Failures (not a repo, git missing, non-zero exit, timeout) are swallowed and
    surfaced as ``None`` so collection degrades gracefully.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def is_git_repo(path: Path) -> bool:
    """Return True if ``path`` is inside a git working tree."""
    out = _run_git(path, ["rev-parse", "--is-inside-work-tree"])
    return out is not None and out.strip() == "true"


def _epoch_to_dt(value: str) -> datetime | None:
    """Parse a Unix-epoch string (git ``%ct`` / reflog ``%gt``) to a datetime."""
    text = value.strip()
    if not text.lstrip("-").isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(text))
    except (OverflowError, OSError, ValueError):
        return None


def _repo_label(repo: Path) -> str:
    """A short, stable name for a repo, used in the event source/cwd."""
    toplevel = _run_git(repo, ["rev-parse", "--show-toplevel"])
    if toplevel and toplevel.strip():
        return Path(toplevel.strip()).name
    return repo.name or str(repo)


def collect_commits(repo: Path, *, since: str = _DEFAULT_SINCE) -> list[Event]:
    """Collect recent commits from ``repo`` as ``Event`` objects.

    Uses ``git log --all`` so commits on *any* branch are captured (a coding day
    often spans several branches, not just the one currently checked out).
    Merge commits are skipped. Returns ``[]`` if the path is not a repo or has
    no commits in range.
    """
    fmt = _SEP.join(["%ct", "%h", "%s"])
    out = _run_git(
        repo,
        [
            "log",
            "--all",
            f"--since={since}",
            f"--pretty=format:{fmt}",
            "--no-merges",
            "-n",
            "500",
        ],
    )
    if not out:
        return []

    label = _repo_label(repo)
    cwd = str(repo)
    events: list[Event] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(_SEP)
        if len(parts) < 3:
            continue
        ts = _epoch_to_dt(parts[0])
        abbrev, subject = parts[1].strip(), parts[2].strip()
        events.append(
            Event(
                cmd=f"commit {abbrev} {subject}".rstrip(),
                ts=ts,
                cwd=cwd,
                source=f"git:{label}",
            )
        )
    return events


def collect_reflog(repo: Path, *, since: str = _DEFAULT_SINCE) -> list[Event]:
    """Collect recent reflog entries (checkouts, resets, merges…) as events.

    The reflog records HEAD movements with their own timestamps, capturing the
    branch-hopping navigation a coding day is full of. Returns ``[]`` when the
    repo has no reflog (e.g. a brand-new repo) or on any git error.
    """
    fmt = _SEP.join(["%gt", "%h", "%gs"])
    out = _run_git(
        repo,
        ["reflog", "show", f"--since={since}", f"--pretty=format:{fmt}", "-n", "500"],
    )
    if not out:
        return []

    label = _repo_label(repo)
    cwd = str(repo)
    events: list[Event] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(_SEP)
        if len(parts) < 3:
            continue
        ts = _epoch_to_dt(parts[0])
        abbrev, subject = parts[1].strip(), parts[2].strip()
        events.append(
            Event(
                cmd=f"reflog {abbrev} {subject}".rstrip(),
                ts=ts,
                cwd=cwd,
                source=f"git:{label}",
            )
        )
    return events


def collect_repo(
    repo: Path,
    *,
    since: str = _DEFAULT_SINCE,
    include_reflog: bool = True,
) -> list[Event]:
    """Collect commits (and optionally reflog) for a single ``repo``.

    Non-repos and errors yield ``[]``. Results are not sorted here — the
    sessionizer owns ordering across all sources.
    """
    repo = repo.expanduser()
    if not is_git_repo(repo):
        return []
    events = collect_commits(repo, since=since)
    if include_reflog:
        events += collect_reflog(repo, since=since)
    return events


def collect(
    repos: Iterable[Path | str],
    *,
    since: str = _DEFAULT_SINCE,
    include_reflog: bool = True,
) -> list[Event]:
    """Collect git events across every repo in ``repos``.

    Each path is processed independently; a bad path contributes nothing rather
    than aborting the others. Returns the combined (unsorted) event list.
    """
    out: list[Event] = []
    for raw in repos:
        out += collect_repo(Path(raw), since=since, include_reflog=include_reflog)
    return out
