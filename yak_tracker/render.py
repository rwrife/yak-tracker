"""Terminal rendering helpers (rich tables, panels, trees).

Kept separate from the CLI so the presentation layer can grow (M4's tree, M5's
narration panels) without bloating ``cli.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree as RichTree

from .models import Event
from .narrate import Narration
from .sessionize import Session
from .tree import DetourKind, Node

# Per-persona panel framing for narrated output (title + border colour).
_FORMAT_STYLE: dict[str, tuple[str, str]] = {
    "standup": ("\N{MEMO} Standup", "green"),
    "story": ("\N{OX} Yak-shaving story", "magenta"),
    "learning": ("\N{ELECTRIC LIGHT BULB} What I learned", "yellow"),
}


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


# --- yak-shaving tree (M4) -------------------------------------------------

# Per-detour-kind glyph + colour, so a tree reads at a glance: what was a
# rabbit hole (installs, forced fixes) vs. ordinary steps.
_KIND_STYLE: dict[str, tuple[str, str]] = {
    DetourKind.ROOT: ("\N{OX} ", "bold white"),
    DetourKind.STEP: ("\N{BULLET} ", "dim"),
    DetourKind.INSTALL: ("\N{PACKAGE} ", "yellow"),
    DetourKind.ERROR_FIX: ("\N{FIRE} ", "red"),
    DetourKind.DIR_CHANGE: ("\N{OPEN FILE FOLDER} ", "blue"),
    DetourKind.BRANCH: ("\N{TWISTED RIGHTWARDS ARROWS} ", "magenta"),
}


def _node_text(node: Node) -> str:
    """Markup string for a single tree node: glyph + styled label + time."""
    glyph, style = _KIND_STYLE.get(node.kind, ("\N{BULLET} ", "white"))
    when = f" [dim]({node.ts.strftime('%H:%M')})[/dim]" if node.ts else ""
    label = node.label.replace("[", "\\[")  # escape rich markup in raw commands
    return f"{glyph}[{style}]{label}[/{style}]{when}"


def _attach(branch: RichTree, node: Node) -> None:
    """Recursively attach ``node``'s children to a rich tree ``branch``."""
    for child in node.children:
        sub = branch.add(_node_text(child))
        _attach(sub, child)


def tree_view(node: Node, *, index: int | None = None) -> RichTree:
    """Build a rich ``Tree`` for one yak-shaving ``Node`` (a session)."""
    prefix = f"[dim]#{index}[/dim] " if index is not None else ""
    root = RichTree(prefix + _node_text(node), guide_style="grey39")
    _attach(root, node)
    return root


def render_trees(
    forest: Sequence[Node],
    *,
    console: Console | None = None,
    title: str | None = None,
    empty_message: str = "No sessions to shave.",
) -> None:
    """Print one yak-shaving tree per session, or a friendly empty note."""
    console = console or Console()
    if not forest:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return
    if title:
        console.print(f"[bold]{title}[/bold]")
    for i, node in enumerate(forest, start=1):
        console.print(tree_view(node, index=i))
        detours = node.descendants()
        depth = node.max_depth()
        console.print(
            f"  [dim]{detours} event(s), {depth} level(s) deep[/dim]\n"
        )


# --- narration (M5) --------------------------------------------------------


def render_narration(
    narration: Narration,
    *,
    console: Console | None = None,
    title: str | None = None,
) -> None:
    """Print a successful narration in a titled panel.

    Only call this when ``narration.ok`` is True; the CLI handles the fallback
    (raw tree + notice) for the unavailable case so the empty/offline path stays
    in one place.
    """
    console = console or Console()
    label, border = _FORMAT_STYLE.get(narration.format, ("Narration", "cyan"))
    subtitle = f"[dim]{narration.model}[/dim]"
    body = (narration.text or "").strip()
    panel = Panel(
        body,
        title=f"[bold]{label}[/bold]",
        subtitle=subtitle,
        border_style=border,
        padding=(1, 2),
    )
    if title:
        console.print(f"[bold]{title}[/bold]")
    console.print(panel)
