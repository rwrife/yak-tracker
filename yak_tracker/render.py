"""Terminal rendering helpers (rich tables, panels, trees).

Kept separate from the CLI so the presentation layer can grow (M4's tree, M5's
narration panels) without bloating ``cli.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from .models import Event


def events_table(events: Sequence[Event], *, title: str | None = None) -> Table:
    """Build a rich ``Table`` of events: time, source, command."""
    table = Table(title=title, expand=True, highlight=True)
    table.add_column("Time", style="cyan", no_wrap=True)
    table.add_column("Source", style="magenta", no_wrap=True)
    table.add_column("Command", style="white", overflow="fold")

    for ev in events:
        when = ev.ts.strftime("%H:%M:%S") if ev.ts else "—"
        table.add_row(when, ev.source, ev.cmd)
    return table


def render_events(
    events: Sequence[Event],
    *,
    console: Console | None = None,
    title: str | None = None,
    empty_message: str = "No events found.",
) -> None:
    """Print events as a table, or a friendly note when there are none."""
    console = console or Console()
    if not events:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return
    console.print(events_table(events, title=title))
