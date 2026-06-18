"""Event collectors — pluggable sources of coding-day activity.

Each collector turns a raw source (shell history, git logs, …) into a list of
normalized :class:`~yak_tracker.models.Event` objects.
"""

from __future__ import annotations
