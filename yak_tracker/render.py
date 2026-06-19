"""Terminal rendering helpers (rich tables, panels, trees).

Kept separate from the CLI so the presentation layer can grow (M4's tree, M5's
narration panels) without bloating ``cli.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from .models import Event
from .sessionize import Session


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


def _fmt_duration(td) -> str:
    """Format a timedelta as a compact ``HhMm`` / ``Mm`` / ``Ss`` string."""
    total = int(td.total_seconds())
    if total <= 0:
        return "0s"
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def sessions_table(sessions: Sequence[Session], *, title: str | None = None) -> Table:
    """Build a rich ``Table`` summarizing sessions: span, duration, events, sources."""
    table = Table(title=title, expand=True, highlight=True)
    table.add_column("#", style="dim", no_wrap=True, justify="right")
    table.add_column("Start", style="cyan", no_wrap=True)
    table.add_column("End", style="cyan", no_wrap=True)
    table.add_column("Duration", style="green", no_wrap=True, justify="right")
    table.add_column("Events", style="yellow", no_wrap=True, justify="right")
    table.add_column("Sources", style="magenta", overflow="fold")

    for i, s in enumerate(sessions, start=1):
        same_day = s.start.date() == s.end.date()
        start = s.start.strftime("%Y-%m-%d %H:%M")
        end = s.end.strftime("%H:%M" if same_day else "%Y-%m-%d %H:%M")
        table.add_row(
            str(i),
            start,
            end,
            _fmt_duration(s.duration),
            str(s.count),
            ", ".join(s.sources()),
        )
    return table


def render_sessions(
    sessions: Sequence[Session],
    *,
    console: Console | None = None,
    title: str | None = None,
    empty_message: str = "No sessions found.",
) -> None:
    """Print a sessions summary table, or a friendly note when there are none."""
    console = console or Console()
    if not sessions:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return
    console.print(sessions_table(sessions, title=title))
