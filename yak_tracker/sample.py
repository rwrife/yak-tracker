"""A curated, synthetic yak-shaving day for ``yak demo``.

The headline pitch of yak-tracker only lands once you *see* a real
yak-shaving tree — but a fresh install has no shell history to read and (often)
no Ollama running. ``yak demo`` bridges that gap: it feeds this hand-built day
of :class:`~yak_tracker.models.Event` objects through the **real** pipeline
(:func:`~yak_tracker.sessionize.sessionize` → :func:`~yak_tracker.tree.build_forest`
→ render / serialize), so a stranger can ``pipx install`` and immediately get
value with zero setup.

The events tell the canonical story from PLAN.md: you sat down to fix a login
bug, fell into an ``npm`` upgrade, which broke the lockfile, which earned a
rage-deleted ``node_modules``, after which you wandered into a *different* repo
to build some Rust — and only then came back and shipped. It is deliberately
shaped to exercise every detour kind (install / error-fix / dir-change /
branch-switch) and to nest a same-kind spiral a few levels deep.

Two sessions are included (a morning rabbit hole and a short, tidy afternoon)
so the multi-session render and the ``--since`` / week views have something
interesting to show too.

The day is authored against a fixed reference date and *rebased* onto whatever
day the caller asks for, so ``yak demo`` always looks like it happened today
while staying perfectly deterministic for tests.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .models import Event

__all__ = ["REFERENCE_DATE", "sample_events", "sample_events_for"]

# The synthetic day is authored relative to this date; :func:`sample_events_for`
# shifts every timestamp by the delta to the requested day.
REFERENCE_DATE = date(2026, 6, 17)


def _at(h: int, m: int, *, day: date = REFERENCE_DATE) -> datetime:
    """A naive local timestamp at ``h:m`` on ``day`` (matches collector output)."""
    return datetime.combine(day, time(hour=h, minute=m))


# The script of the day, authored on REFERENCE_DATE. Each tuple is
# (hour, minute, command, source). Times are chosen so the two sessions are
# separated by a > 25-minute idle gap (the default), and so consecutive
# same-kind detours land close enough to nest.
_SCRIPT: tuple[tuple[int, int, str, str], ...] = (
    # --- Session 1: the morning rabbit hole -------------------------------
    (9, 2, "commit a1b2c3d fix: reject empty password on login", "git:webapp"),
    (9, 4, "npm test", "shell:zsh"),
    (9, 7, "npm install jsonwebtoken@latest", "shell:zsh"),  # install detour
    (9, 9, "npm install", "shell:zsh"),  # …deeper: pulls the whole tree
    (9, 12, "npm dedupe", "shell:zsh"),  # …deeper still: lockfile churn
    (9, 18, "rm -rf node_modules", "shell:zsh"),  # error-fix: rage delete
    (9, 19, "rm package-lock.json", "shell:zsh"),  # …deeper recovery
    (9, 24, "npm install", "shell:zsh"),  # fresh install after the purge
    (9, 31, "cd ../shared-utils", "shell:zsh"),  # dir-change: wandered off
    (9, 33, "cargo add serde", "shell:zsh"),  # install in the *other* repo
    (9, 36, "cargo build", "shell:zsh"),
    (9, 41, "commit 9f8e7d6 chore(utils): add serde derive", "git:shared-utils"),
    (9, 48, "cd ../webapp", "shell:zsh"),  # dir-change: back home
    (9, 52, "npm test", "shell:zsh"),
    (9, 55, "commit c4d5e6f test: cover empty-password path", "git:webapp"),
    # --- Session 2: a short, tidy afternoon -------------------------------
    (14, 10, "reflog 1a2b3c4 checkout: moving from main to release-0.1", "git:webapp"),
    (14, 12, "npm run build", "shell:zsh"),
    (14, 19, "commit 7a8b9c0 release: cut v0.1.0", "git:webapp"),
    (14, 22, "git tag v0.1.0", "shell:zsh"),
)


def sample_events(*, day: date = REFERENCE_DATE) -> list[Event]:
    """Return the curated yak-shaving day as normalized events.

    Args:
        day: The date to stamp the events with. Defaults to
            :data:`REFERENCE_DATE`; pass ``date.today()`` (or use
            :func:`sample_events_for`) to make the demo look like today.

    Returns:
        Events in chronological order, ready to drop straight into
        :func:`~yak_tracker.sessionize.sessionize`.
    """
    offset = day - REFERENCE_DATE
    events: list[Event] = []
    for hour, minute, cmd, source in _SCRIPT:
        ts = _at(hour, minute) + offset
        events.append(Event(cmd=cmd, ts=ts, cwd=None, source=source))
    return events


def sample_events_for(end: date, days: int) -> list[Event]:
    """Spread the sample day across the ``days``-day window ending at ``end``.

    Useful for ``yak demo --since N`` and a demo-able ``yak week``: each day in
    the window gets its own copy of the curated day so multi-day views have
    activity on every row rather than a single populated day.

    Args:
        end: The most recent day in the window.
        days: How many days back from ``end`` to populate (inclusive). Values
            below 1 are treated as 1.

    Returns:
        Events for every day in the window, in chronological order.
    """
    span = max(1, days)
    events: list[Event] = []
    for offset in range(span - 1, -1, -1):
        events.extend(sample_events(day=end - timedelta(days=offset)))
    return events
