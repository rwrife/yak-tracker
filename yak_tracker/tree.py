"""Yak-shaving tree: turn a flat session into a nested intention→detour tree.

This is the differentiating stage of the pipeline (PLAN.md M4). The
:func:`~yak_tracker.sessionize.sessionize` step already grouped raw events into
time-gapped :class:`~yak_tracker.sessionize.Session` objects; here we take a
single session and reconstruct the *shape* of the work: the thing you set out to
do, and the rabbit holes you fell into along the way.

The model is intentionally heuristic — we are not trying to perfectly recover
intent, just to surface the structure humans forget by end of day:

* **Root intention.** The first "anchoring" event of the session: a git commit
  subject if one exists early on, otherwise the first substantive shell command
  (skipping trivial navigation like bare ``cd`` / ``ls``). Falls back to a
  generic label when nothing stands out.
* **Detours.** New nodes are opened when the work *changes context*. A detour is
  triggered by any of:

  - a **directory change** (``cd`` into a different path, or a git event in a
    different repo than the current one);
  - a **package install / dependency churn** (``npm install``, ``pip install``,
    ``cargo add``, ``brew install``, lockfile regen, …);
  - an **error→fix loop** (a command that failed or looks like cleanup —
    ``rm -rf node_modules``, ``--force``, ``git reset --hard`` — kicking off a
    sub-investigation);
  - a **branch switch** (reflog ``checkout: moving from X to Y``).

Consecutive detour triggers of the *same* kind nest under the active detour
(going "deeper"), so the classic "I just wanted to fix one bug, then I upgraded
Node, which broke the lockfile, which…" spiral shows up as an actual chain of
nested nodes rather than a flat list.

The output is a plain tree of :class:`Node` objects. Rendering lives in
``render.py``; narration (M5) will walk the same tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath

from .models import Event
from .sessionize import Session

__all__ = [
    "Node",
    "DetourKind",
    "build_tree",
    "build_forest",
    "classify_event",
]


# --- detour taxonomy --------------------------------------------------------

# Stable, human-readable kind labels. Kept as plain strings (not an Enum) so the
# tree stays trivially serializable for the future `--json` flag (M6).
class DetourKind:
    """Namespace of detour-trigger kinds (string constants)."""

    DIR_CHANGE = "dir-change"
    INSTALL = "install"
    ERROR_FIX = "error-fix"
    BRANCH = "branch-switch"
    ROOT = "root"
    STEP = "step"


# Commands that are pure navigation/noise and should never *become* a root
# intention on their own (they can still nest as steps).
_TRIVIAL_RE = re.compile(
    r"""^(
        ls | ll | la | cd | pwd | clear | exit | echo | cat |
        which | history | k | l | c
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# Package-manager / dependency-churn signatures → an "install" detour.
_INSTALL_RE = re.compile(
    r"""
    \b(
        npm \s+ (install|i|ci|add) |
        pnpm \s+ (install|add|i) |
        yarn \s+ (add|install) |
        bun \s+ (install|add|i) |
        pip \s+ install |
        pip3 \s+ install |
        (uv|poetry|pipenv) \s+ (add|install|sync|lock) |
        cargo \s+ (add|install|update) |
        go \s+ (get|install) |
        (brew|apt|apt-get|dnf|yum|pacman|apk) \s+ (install|add) |
        gem \s+ install |
        bundle \s+ (install|update) |
        composer \s+ (require|install|update) |
        (npm|pnpm|yarn) \s+ update
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Cleanup / forced-recovery / failure signatures → an "error-fix" detour. These
# are the "things went sideways and I had to dig in" markers.
_ERROR_FIX_RE = re.compile(
    r"""
    (
        rm \s+ -rf? .* (node_modules|\.venv|venv|dist|build|target|\.next|__pycache__) |
        \brm \s+ -rf\b |
        --force\b | --hard\b | -f\b .* (push|reset|checkout|clean) |
        git \s+ reset \s+ --hard |
        git \s+ clean \s+ -[a-z]*f |
        git \s+ checkout \s+ -- |
        \bkill \s+ -9\b |
        \b(rmdir|unlink)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Reflog branch-switch signature, e.g. "reflog 1a2b3c checkout: moving from a to b".
_BRANCH_RE = re.compile(
    r"checkout:\s*moving\s+from\s+(?P<from>\S+)\s+to\s+(?P<to>\S+)",
    re.IGNORECASE,
)

# A bare `cd <path>` (shell) — captures the destination for dir-change detection.
_CD_RE = re.compile(r"^\s*cd\s+(?P<path>\S+)", re.IGNORECASE)


@dataclass(slots=True)
class Node:
    """A node in a yak-shaving tree.

    Attributes:
        label: Short human-readable description of this node's intention/detour.
        kind: One of :class:`DetourKind`'s constants.
        event: The source :class:`~yak_tracker.models.Event`, if this node maps
            to a single concrete event (the root and most detours do).
        children: Nested detours that branched *off* this node, in order.
    """

    label: str
    kind: str
    event: Event | None = None
    children: list[Node] = field(default_factory=list)

    @property
    def ts(self) -> datetime | None:
        """Timestamp of this node's event, if any."""
        return self.event.ts if self.event is not None else None

    def add(self, child: Node) -> Node:
        """Append ``child`` and return it (for fluent nesting)."""
        self.children.append(child)
        return child

    def descendants(self) -> int:
        """Total number of nodes beneath this one (excluding self)."""
        return sum(1 + c.descendants() for c in self.children)

    def max_depth(self) -> int:
        """Deepest chain length below this node (a leaf has depth ``0``)."""
        if not self.children:
            return 0
        return 1 + max(c.max_depth() for c in self.children)


# --- helpers ----------------------------------------------------------------


def _repo_of(event: Event) -> str | None:
    """Best-effort repo/context key for an event (for dir-change detection)."""
    if event.cwd:
        return PurePath(event.cwd).name or event.cwd
    if event.source.startswith("git:"):
        return event.source.split(":", 1)[1]
    return None


def _is_commit(event: Event) -> bool:
    return event.source.startswith("git:") and event.cmd.startswith("commit ")


def _commit_subject(event: Event) -> str:
    """Extract the subject from a ``commit <abbrev> <subject>`` event cmd."""
    # cmd == "commit <abbrev> <subject...>"
    parts = event.cmd.split(" ", 2)
    return parts[2] if len(parts) >= 3 else event.cmd


def _short(text: str, limit: int = 72) -> str:
    """Collapse whitespace and truncate ``text`` for a node label."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def classify_event(event: Event, *, current_repo: str | None) -> tuple[str, str]:
    """Classify an event into a ``(kind, label)`` pair given the active context.

    ``current_repo`` is the repo/dir of the node currently being extended; a
    change relative to it is what makes a :data:`DetourKind.DIR_CHANGE`.

    Returns the most specific applicable detour kind. Order of precedence:
    branch switch → install → error/fix → dir change → plain step.
    """
    cmd = event.cmd

    branch = _BRANCH_RE.search(cmd)
    if branch:
        frm, to = branch.group("from"), branch.group("to")
        return DetourKind.BRANCH, f"switched branch {frm} → {to}"

    if _INSTALL_RE.search(cmd):
        return DetourKind.INSTALL, _short(cmd)

    if _ERROR_FIX_RE.search(cmd):
        return DetourKind.ERROR_FIX, _short(cmd)

    cd = _CD_RE.match(cmd)
    if cd:
        return DetourKind.DIR_CHANGE, f"cd {cd.group('path')}"

    repo = _repo_of(event)
    if repo is not None and current_repo is not None and repo != current_repo:
        return DetourKind.DIR_CHANGE, _short(cmd)

    return DetourKind.STEP, _short(cmd)


def _root_label(session: Session) -> tuple[str, Event | None]:
    """Pick a root intention label (and anchoring event) for a session.

    Preference:
      1. The subject of the first commit in the session (a landed intention).
      2. The first non-trivial shell/other command.
      3. A generic time-based label if the session is all noise.
    """
    # 1. earliest commit subject
    for ev in session.events:
        if _is_commit(ev):
            return _short(_commit_subject(ev)), ev

    # 2. first substantive command (skip pure navigation/noise)
    for ev in session.events:
        if not _TRIVIAL_RE.match(ev.cmd) and not ev.cmd.startswith("reflog "):
            return _short(ev.cmd), ev

    # 3. fall back to the very first event, or a generic label
    if session.events:
        first = session.events[0]
        return _short(first.cmd), first
    return "session", None


# --- tree construction ------------------------------------------------------


def build_tree(session: Session) -> Node:
    """Build the yak-shaving tree for a single ``session``.

    The root node represents the session's inferred intention. Each subsequent
    event either:

    * **extends the current line of work** (a plain step) → attached to the
      currently-active node, or
    * **opens a detour** (install / error-fix / dir-change / branch switch) →
      becomes a new child. A detour whose kind matches the active detour nests
      *under* it (going deeper); a detour of a different kind, or a return to
      ordinary work, pops back toward the root.

    The result mirrors how a day actually branches: a spine of intended steps
    with rabbit holes hanging off (and nested within) it.
    """
    root_label, anchor = _root_label(session)
    root = Node(label=root_label, kind=DetourKind.ROOT, event=anchor)

    # The "spine" is the path from root to the node we're currently extending.
    # Index 0 is always the root. Detours push onto the spine; returning to
    # normal work (or switching detour kind) pops back to the root spine.
    spine: list[Node] = [root]
    current_repo: str | None = _repo_of(anchor) if anchor is not None else None

    for ev in session.events:
        if ev is anchor:
            # The anchor *is* the root; don't re-add it as its own child.
            continue

        kind, label = classify_event(ev, current_repo=current_repo)
        active = spine[-1]

        if kind == DetourKind.STEP:
            # Ordinary step: hang it off whatever we're currently doing. It does
            # not deepen the spine (steps are leaves of the active node).
            active.add(Node(label=label, kind=kind, event=ev))
        else:
            # A detour. If we're already inside a detour of the *same* kind,
            # nest deeper; otherwise start a fresh detour off the root spine.
            if active.kind == kind and active is not root:
                node = active.add(Node(label=label, kind=kind, event=ev))
            else:
                # pop back to root, then open the detour there
                node = root.add(Node(label=label, kind=kind, event=ev))
                spine = [root]
            spine.append(node)

        # Track context for dir-change detection.
        repo = _repo_of(ev)
        if repo is not None:
            current_repo = repo
        elif kind == DetourKind.DIR_CHANGE:
            # a bare `cd <path>` — update context to the target's basename
            cd = _CD_RE.match(ev.cmd)
            if cd:
                current_repo = PurePath(cd.group("path")).name or cd.group("path")

    return root


def build_forest(sessions: list[Session]) -> list[Node]:
    """Build one tree per session, preserving order."""
    return [build_tree(s) for s in sessions]
