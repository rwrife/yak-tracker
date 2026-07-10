"""``yak blame <file>`` — per-file detour reflection (PLAN.md §8 backlog).

Where ``today`` / ``week`` / ``score`` answer *what was my day*, ``blame``
answers *what was the deal with **this one file***. Point it at a path and it
reconstructs every detour that touched the file and narrates the churn.

This is a new **lens** on the existing pipeline, not a new data source:

* **Git side.** ``git log --follow --name-only`` over the target path yields the
  commits that actually modified it (following renames), each turned into a
  normalized :class:`~yak_tracker.models.Event` — the same shape the rest of the
  pipeline speaks.
* **Shell side.** The day's shell events are filtered to those whose command
  *references* the file (best-effort argument / substring match), so the
  ``vim cli.py`` / ``pytest tests/test_cli.py`` churn shows up too.

Those matched events are merged, sessionized with the normal idle-gap heuristic,
and rendered as a compact per-session timeline plus a one-paragraph "why this
file kept pulling you back" summary (local Ollama narration, with the usual
graceful no-LLM fallback so the raw timeline always prints).

Everything degrades gracefully: a path outside every tracked repo raises a
clean :class:`BlameError`; a non-repo or git error simply contributes no git
events rather than crashing the run.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .collectors.git import _repo_label, is_git_repo
from .models import Event
from .sessionize import Session, sessionize

__all__ = [
    "Blame",
    "BlameError",
    "TargetResolution",
    "resolve_target",
    "collect_git_file_events",
    "shell_events_touching",
    "build_blame",
    "blame_to_dict",
]

# Unit separator, mirroring the git collector — unlikely to appear in subjects.
_SEP = "\x1f"

# Match the git collector's default lookback so a "blame" sees the same window
# of history the rest of yak reasons about.
_DEFAULT_SINCE = "60.days.ago"

# How the git collector marks the source of blame events (so renderers/tests can
# tell a file-touch commit apart from a shell reference).
_GIT_TOUCH = "git-touch"
_SHELL_REF = "shell-ref"


class BlameError(Exception):
    """Raised when the target path can't be blamed (e.g. outside every repo)."""


@dataclass(frozen=True, slots=True)
class TargetResolution:
    """The outcome of resolving a blame target against the tracked repos.

    Attributes:
        path: The absolute, resolved file path that was requested.
        repo: The tracked repo the file lives under.
        relpath: The file's path relative to ``repo`` (what git wants).
        label: Short repo name (matches the git collector's ``git:<label>``).
    """

    path: Path
    repo: Path
    relpath: Path
    label: str


def _candidate_repos(repos: Sequence[Path] | None) -> list[Path]:
    """The repos to resolve against: the configured list, else the cwd."""
    if repos:
        return [Path(r).expanduser().resolve() for r in repos]
    return [Path.cwd().resolve()]


def resolve_target(
    target: str | Path,
    *,
    repos: Sequence[Path] | None = None,
) -> TargetResolution:
    """Resolve ``target`` to a ``(repo, relpath)`` under a tracked repo.

    The path may be relative (resolved against the cwd) or absolute. It is
    matched against each candidate repo (the configured ``repos``, or the
    current directory when unset); the first repo the file lives under wins.

    The file does **not** need to still exist on disk — a deleted file can still
    have a churn history worth blaming — but the containing repo must be a real
    git work tree.

    Raises:
        BlameError: if the path isn't under any tracked git repo, with a message
            listing where we looked.
    """
    abs_path = Path(target).expanduser()
    if not abs_path.is_absolute():
        abs_path = (Path.cwd() / abs_path).resolve()
    else:
        abs_path = abs_path.resolve()

    candidates = _candidate_repos(repos)
    for repo in candidates:
        try:
            relpath = abs_path.relative_to(repo)
        except ValueError:
            continue
        if not is_git_repo(repo):
            continue
        return TargetResolution(
            path=abs_path,
            repo=repo,
            relpath=relpath,
            label=_repo_label(repo),
        )

    where = ", ".join(str(r) for r in candidates) or "(none)"
    raise BlameError(
        f"{Path(target)} is not under any tracked git repo. "
        f"Looked in: {where}. "
        "Pass --repo to point yak at the repo that contains this file."
    )


def _run_git(repo: Path, args: Sequence[str]) -> str | None:
    """Run ``git -C <repo> <args>`` returning stdout, or ``None`` on any error."""
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


def _epoch_to_dt(value: str) -> datetime | None:
    """Parse a Unix-epoch string (git ``%ct``) to a datetime, or ``None``."""
    text = value.strip()
    if not text.lstrip("-").isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(text))
    except (OverflowError, OSError, ValueError):
        return None


def collect_git_file_events(
    resolution: TargetResolution,
    *,
    since: str = _DEFAULT_SINCE,
) -> list[Event]:
    """Collect commits that modified the target file as ``Event`` objects.

    Uses ``git log --follow --all -- <relpath>`` so renames are followed and
    commits on any branch are seen. Each commit becomes an event timestamped at
    its commit time, marked with ``source="git-touch:<label>"`` so downstream
    code can tell file-touch commits from shell references. Returns ``[]`` on any
    git error rather than raising.
    """
    fmt = _SEP.join(["%ct", "%h", "%s"])
    out = _run_git(
        resolution.repo,
        [
            "log",
            "--all",
            "--follow",
            f"--since={since}",
            f"--pretty=format:{fmt}",
            "--no-merges",
            "-n",
            "500",
            "--",
            str(resolution.relpath),
        ],
    )
    if not out:
        return []

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
                cwd=str(resolution.repo),
                source=f"{_GIT_TOUCH}:{resolution.label}",
            )
        )
    return events


def shell_events_touching(
    events: Iterable[Event],
    resolution: TargetResolution,
) -> list[Event]:
    """Filter shell ``events`` to those whose command references the target file.

    Best-effort: an event matches if its command mentions the file's basename,
    its repo-relative path, or its absolute path (whitespace/quote-tokenized so a
    bare ``cli.py`` matches but an unrelated ``mycli.py`` does not). Non-shell
    events are ignored here (git touches come from
    :func:`collect_git_file_events`). Matched events are re-sourced to
    ``shell-ref:<origin>`` so the timeline can label them.
    """
    needles = {
        resolution.relpath.name,
        str(resolution.relpath),
        str(resolution.path),
    }
    # Also allow the POSIX-style relative path on platforms using backslashes.
    needles.add(resolution.relpath.as_posix())

    matched: list[Event] = []
    for ev in events:
        if not ev.source.startswith("shell"):
            continue
        tokens = _tokenize(ev.cmd)
        if _references(tokens, ev.cmd, needles):
            matched.append(
                Event(
                    cmd=ev.cmd,
                    ts=ev.ts,
                    cwd=ev.cwd,
                    source=f"{_SHELL_REF}:{ev.source}",
                )
            )
    return matched


def _tokenize(cmd: str) -> list[str]:
    """Split a command into whitespace-delimited tokens, stripping quotes."""
    return [tok.strip("'\"") for tok in cmd.split()]


def _references(tokens: Sequence[str], cmd: str, needles: set[str]) -> bool:
    """True if any token equals/ends-with a needle path (basename-aware)."""
    for tok in tokens:
        # Strip a leading ./ so "./cli.py" matches "cli.py".
        clean = tok[2:] if tok.startswith("./") else tok
        if clean in needles:
            return True
        # A token like "tests/test_cli.py" should match the basename needle only
        # when it IS that file, not merely contains the name — compare the tail.
        for needle in needles:
            if needle and (clean == needle or clean.endswith("/" + needle)):
                return True
    return False


@dataclass(slots=True)
class Blame:
    """The reconstructed churn story for a single file.

    Attributes:
        resolution: The resolved target (path / repo / relpath / label).
        sessions: The matched events, sessionized into time-gapped runs.
        events: The flat, time-ordered list of matched events.
    """

    resolution: TargetResolution
    sessions: list[Session] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    @property
    def touch_count(self) -> int:
        """Total number of matched events touching the file."""
        return len(self.events)

    @property
    def session_count(self) -> int:
        """Number of distinct sessions the file was touched in."""
        return len(self.sessions)

    @property
    def detour_count(self) -> int:
        """Distinct event sources involved (a proxy for 'kinds of detour')."""
        seen: dict[str, None] = {}
        for ev in self.events:
            seen.setdefault(ev.source, None)
        return len(seen)

    @property
    def headline(self) -> str:
        """A one-line 'cli.py — touched in N sessions across M detours' banner."""
        name = self.resolution.relpath.name
        s = self.session_count
        d = self.detour_count
        return (
            f"{name} — touched in {s} session{'s' if s != 1 else ''} "
            f"across {d} detour{'s' if d != 1 else ''}"
        )


def build_blame(
    target: str | Path,
    *,
    repos: Sequence[Path] | None = None,
    shell_events: Iterable[Event] | None = None,
    since: str = _DEFAULT_SINCE,
    idle_gap: float | None = None,
) -> Blame:
    """Reconstruct the per-file churn story for ``target``.

    Resolves the path against the tracked repos, gathers the commits that touched
    it plus the shell commands that referenced it, then sessionizes the merged,
    time-ordered stream. Undated events are dropped (they can't be placed on the
    timeline), matching the rest of the pipeline.

    Args:
        target: File path to blame (relative or absolute).
        repos: Tracked repos to resolve against (defaults to the cwd).
        shell_events: The day's shell events to scan for references. When
            ``None``, only git touches are considered.
        since: Git lookback window (e.g. ``"60.days.ago"``).
        idle_gap: Minutes of inactivity that start a new session.

    Raises:
        BlameError: if the path is outside every tracked repo.
    """
    resolution = resolve_target(target, repos=repos)

    matched: list[Event] = collect_git_file_events(resolution, since=since)
    if shell_events is not None:
        matched += shell_events_touching(shell_events, resolution)

    ordered = sorted(
        (e for e in matched if e.ts is not None),
        key=lambda e: e.ts,  # type: ignore[arg-type,return-value]
    )
    sessions = sessionize(ordered, idle_gap=idle_gap)
    return Blame(resolution=resolution, sessions=sessions, events=ordered)


def blame_to_dict(blame: Blame) -> dict:
    """Serialise a :class:`Blame` to a JSON-ready dict (for ``--json``).

    Shape::

        {
          "path": "<abs path>",
          "relpath": "<repo-relative>",
          "repo": "<repo label>",
          "touch_count": N,
          "session_count": M,
          "sessions": [
            {"start": "...", "end": "...", "events": [
                {"ts": "...", "source": "...", "cmd": "..."}, ...
            ]},
            ...
          ]
        }
    """
    return {
        "path": str(blame.resolution.path),
        "relpath": blame.resolution.relpath.as_posix(),
        "repo": blame.resolution.label,
        "touch_count": blame.touch_count,
        "session_count": blame.session_count,
        "sessions": [
            {
                "start": s.start.isoformat(),
                "end": s.end.isoformat(),
                "events": [
                    {
                        "ts": ev.ts.isoformat() if ev.ts else None,
                        "source": ev.source,
                        "cmd": ev.cmd,
                    }
                    for ev in s.events
                ],
            }
            for s in blame.sessions
        ],
    }
