"""Event collectors — pluggable sources of coding-day activity.

Each collector turns a raw source (shell history, git logs, …) into a list of
normalized :class:`~yak_tracker.models.Event` objects.
"""

from __future__ import annotations

from datetime import date

from . import fish as fish_collector
from . import nushell as nushell_collector

__all__ = ["collect_extra_shells_for_date"]


def collect_extra_shells_for_date(
    day: date,
    *,
    include_undated: bool = False,
    redact: bool = True,
) -> list:
    """Collect fish + nushell events for ``day`` from their auto-detected files.

    A convenience seam used by the CLI so ``yak today``/``sessions``/``blame`` see
    fish and nushell history alongside bash/zsh with no extra flags. Shells that
    aren't installed contribute nothing (their collectors return ``[]``), so this
    is always safe to call. The merged list is returned unsorted; callers already
    sort the combined stream before sessionizing.
    """
    events: list = []
    events += fish_collector.collect_for_date(
        day, include_undated=include_undated, redact=redact
    )
    events += nushell_collector.collect_for_date(
        day, include_undated=include_undated, redact=redact
    )
    return events
