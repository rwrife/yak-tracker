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

from .blame import Blame
from .models import Event
from .narrate import Narration
from .score import DayScore, ScoreHistory, sparkline
from .sessionize import Session
from .tree import DetourKind, Node
from .week import HEAT_LEVELS, WeekSummary, heat_level

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
    day_score: DayScore | None = None,
) -> None:
    """Print one yak-shaving tree per session, or a friendly empty note.

    When ``day_score`` is supplied, a one-line **focus score** footer is printed
    under the trees (see :func:`score_footer`) so ``yak today`` closes with the
    day's number.
    """
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
    if day_score is not None and not day_score.is_empty:
        console.print(score_footer(day_score))


# --- weekly heatmap (yak week) ---------------------------------------------

# Cold → hot ramp for the per-day tangent-depth heatmap. Index with
# ``week.heat_level(...)`` (0 == calm/no-nesting, last == deepest day of week).
_HEAT_STYLE: tuple[str, ...] = (
    "grey39",  # 0 — no tangents that day
    "cyan",  # 1
    "green",  # 2
    "yellow",  # 3
    "red",  # 4 — deepest day of the week
)

# Solid block used to draw each day's heat cell; width scales with depth bucket.
_HEAT_BLOCK = "\N{FULL BLOCK}"


def _heat_cells(level: int, *, levels: int = HEAT_LEVELS, lit: bool = True) -> str:
    """A little bar of blocks (``level+1`` wide) in the bucket's colour.

    When ``lit`` is False the whole bar is drawn dark (used for quiet days with
    no sessions at all, so they read as visually empty rather than level-0 warm).
    """
    if not lit:
        return "[grey23]" + _HEAT_BLOCK * levels + "[/grey23]"
    style = _HEAT_STYLE[min(level, len(_HEAT_STYLE) - 1)]
    filled = _HEAT_BLOCK * (level + 1)
    empty = "[grey23]" + _HEAT_BLOCK * (levels - level - 1) + "[/grey23]"
    return f"[{style}]{filled}[/{style}]{empty}"


def week_table(week: WeekSummary, *, title: str | None = None) -> Table:
    """Build the per-day tangent-depth heatmap table for ``yak week``."""
    table = Table(title=title, expand=True, highlight=True)
    table.add_column("Day", style="cyan", no_wrap=True)
    table.add_column("Date", style="dim", no_wrap=True)
    table.add_column("Depth", style="white", no_wrap=True, justify="center")
    table.add_column("", no_wrap=True)  # heat bar
    table.add_column("Sessions", style="green", no_wrap=True, justify="right")
    table.add_column("Detours", style="yellow", no_wrap=True, justify="right")
    table.add_column("Deepest shave", style="white", overflow="fold")

    for d in week.days:
        level = heat_level(d.max_depth, week.peak_depth)
        is_peak = (
            week.deepest_day is not None
            and d.day == week.deepest_day.day
            and d.max_depth > 0
        )
        if d.is_empty:
            depth_cell = "[grey39]—[/grey39]"
            deepest = "[grey39](quiet day)[/grey39]"
        else:
            depth_cell = str(d.max_depth)
            label = (d.deepest_label or "").replace("[", "\\[")
            deepest = f"[bold red]◆[/bold red] {label}" if is_peak else label
        table.add_row(
            d.day.strftime("%a"),
            d.day.isoformat(),
            depth_cell,
            _heat_cells(level, lit=not d.is_empty),
            "—" if d.is_empty else str(d.sessions),
            "—" if d.is_empty else str(d.events),
            deepest,
        )
    return table


def render_week(
    week: WeekSummary,
    *,
    console: Console | None = None,
    title: str | None = None,
    empty_message: str = "No activity this week.",
) -> None:
    """Print the weekly heatmap, a roll-up line, and the deepest shave callout."""
    console = console or Console()
    if not week.days:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return

    console.print(week_table(week, title=title))

    # Roll-up footer: how busy the week was overall.
    console.print(
        f"  [dim]{week.active_days}/{len(week.days)} active day(s), "
        f"{week.total_sessions} session(s), {week.total_events} detour(s).[/dim]"
    )

    # Highlight the single deepest yak-shave of the week.
    if week.deepest_day is not None and week.peak_depth > 0:
        dd = week.deepest_day
        label = (dd.deepest_label or "").replace("[", "\\[")
        console.print(
            f"  [bold red]\N{FIRE} Deepest shave:[/bold red] "
            f"[white]{label}[/white] "
            f"[dim]— {week.peak_depth} level(s) deep on "
            f"{dd.day.strftime('%a %Y-%m-%d')}.[/dim]"
        )
    else:
        console.print(
            "  [dim]No rabbit holes this week — every session stayed flat. \N{SPARKLES}[/dim]"
        )


# --- focus score (yak score) -----------------------------------------------

# Focus-score colour ramp (low → high). A high score is good (focused), so green
# is the *top* of the range and red the bottom — the inverse of the heatmap,
# which colours *depth* (where hot == bad).
_SCORE_BANDS: tuple[tuple[float, str, str], ...] = (
    (85.0, "bold green", "laser-focused"),
    (70.0, "green", "focused"),
    (55.0, "yellow", "some detours"),
    (40.0, "orange3", "rabbit-hole-y"),
    (0.0, "red", "deep in the weeds"),
)


def score_style(score: float) -> tuple[str, str]:
    """Return the ``(rich_style, blurb)`` for a 0–100 focus ``score``."""
    for threshold, style, blurb in _SCORE_BANDS:
        if score >= threshold:
            return style, blurb
    # Defensive: the last band has threshold 0.0, so this is unreachable for
    # any non-negative score, but keeps the type-checker (and a stray -0.0) happy.
    return _SCORE_BANDS[-1][1], _SCORE_BANDS[-1][2]


def _score_badge(score: float) -> str:
    """A coloured ``NN/100`` badge for a focus score."""
    style, _ = score_style(score)
    return f"[{style}]{round(score)}/100[/{style}]"


def score_footer(day: DayScore) -> str:
    """One-line focus-score summary for a day (used as the ``yak today`` footer).

    Shows the badge, a plain-language blurb, and the depth stats behind it. Safe
    to call only for non-empty days (an empty day has no score).
    """
    score = day.score if day.score is not None else 100.0
    style, blurb = score_style(score)
    sess = day.session_count
    return (
        f"  \N{ELECTRIC LIGHT BULB} [bold]Yak score:[/bold] {_score_badge(score)} "
        f"[{style}]{blurb}[/{style}] "
        f"[dim]— avg detour {day.avg_depth:.1f}, deepest {day.max_depth} "
        f"across {sess} session(s).[/dim]"
    )


def score_history_table(history: ScoreHistory, *, title: str | None = None) -> Table:
    """Per-day focus-score table for ``yak score --history``."""
    table = Table(title=title, expand=True, highlight=True)
    table.add_column("Day", style="cyan", no_wrap=True)
    table.add_column("Date", style="dim", no_wrap=True)
    table.add_column("Score", no_wrap=True, justify="right")
    table.add_column("Focus", overflow="fold")
    table.add_column("Sessions", style="green", no_wrap=True, justify="right")
    table.add_column("Avg", style="yellow", no_wrap=True, justify="right")
    table.add_column("Deepest", style="yellow", no_wrap=True, justify="right")

    for d in history.days:
        if d.is_empty or d.score is None:
            table.add_row(
                d.day.strftime("%a"),
                d.day.isoformat(),
                "[grey39]—[/grey39]",
                "[grey39](quiet day)[/grey39]",
                "—",
                "—",
                "—",
            )
            continue
        style, blurb = score_style(d.score)
        table.add_row(
            d.day.strftime("%a"),
            d.day.isoformat(),
            _score_badge(d.score),
            f"[{style}]{blurb}[/{style}]",
            str(d.session_count),
            f"{d.avg_depth:.1f}",
            str(d.max_depth),
        )
    return table


def render_score(
    day: DayScore,
    *,
    console: Console | None = None,
    title: str | None = None,
    empty_message: str = "No focus score — nothing happened that day.",
) -> None:
    """Print a single day's focus score (the bare ``yak score`` surface)."""
    console = console or Console()
    if day.is_empty or day.score is None:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return
    if title:
        console.print(f"[bold]{title}[/bold]")
    console.print(score_footer(day))


def render_score_history(
    history: ScoreHistory,
    *,
    console: Console | None = None,
    title: str | None = None,
    empty_message: str = "No activity in this window — no scores to chart.",
) -> None:
    """Print the focus-score table, a sparkline, and average/best/worst callouts."""
    console = console or Console()
    if not history.days:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return

    console.print(score_history_table(history, title=title))

    if not history.scored_days:
        console.print(
            "  [dim]No timestamped activity in this window — "
            "every day was quiet. \N{SPARKLES}[/dim]"
        )
        return

    # Absolute-scale sparkline of the whole window (quiet days show as gaps).
    spark = sparkline([d.score for d in history.days])
    console.print(f"  [bold]Focus:[/bold] [cyan]{spark}[/cyan]  [dim](low → high)[/dim]")

    avg = history.average
    if avg is not None:
        style, blurb = score_style(avg)
        console.print(
            f"  [bold]Average:[/bold] {_score_badge(avg)} "
            f"[{style}]{blurb}[/{style}] "
            f"[dim]over {len(history.scored_days)} active day(s).[/dim]"
        )

    best, worst = history.best, history.worst
    if best is not None and best.score is not None:
        bstyle, _ = score_style(best.score)
        console.print(
            f"  [bold green]\N{SPARKLES} Most focused:[/bold green] "
            f"[{bstyle}]{round(best.score)}/100[/{bstyle}] "
            f"[dim]on {best.day.strftime('%a %Y-%m-%d')}.[/dim]"
        )
    if worst is not None and worst.score is not None and worst is not best:
        wstyle, _ = score_style(worst.score)
        console.print(
            f"  [bold red]\N{FIRE} Deepest rabbit hole:[/bold red] "
            f"[{wstyle}]{round(worst.score)}/100[/{wstyle}] "
            f"[dim]on {worst.day.strftime('%a %Y-%m-%d')}.[/dim]"
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


# --- yak blame (per-file detour reflection) --------------------------------


def blame_timeline(blame: Blame, *, title: str | None = None) -> RichTree:
    """Build a rich tree: one branch per session, each listing file touches.

    Git touches and shell references get distinct glyphs so the churn reads at a
    glance (was it commits, or editor/test churn?).
    """
    root_label = title or f"\N{OX} {blame.headline}"
    tree = RichTree(f"[bold]{root_label}[/bold]")
    for i, session in enumerate(blame.sessions, start=1):
        same_day = session.start.date() == session.end.date()
        start = session.start.strftime("%Y-%m-%d %H:%M")
        end = session.end.strftime("%H:%M" if same_day else "%Y-%m-%d %H:%M")
        branch = tree.add(
            f"[cyan]Session {i}[/cyan] "
            f"[dim]{start} → {end} · {session.count} touch"
            f"{'es' if session.count != 1 else ''}[/dim]"
        )
        for ev in session.events:
            when = ev.ts.strftime("%H:%M") if ev.ts else "—"
            if ev.source.startswith("git-touch"):
                glyph, style = "\N{PACKAGE} ", "yellow"
            else:
                glyph, style = "\N{GREATER-THAN SIGN} ", "white"
            branch.add(f"[dim]{when}[/dim] [{style}]{glyph}{ev.cmd}[/{style}]")
    return tree


def render_blame(
    blame: Blame,
    *,
    console: Console | None = None,
    title: str | None = None,
    empty_message: str = "No activity touched this file.",
) -> None:
    """Print the per-file churn timeline, or a friendly note when it's empty."""
    console = console or Console()
    if not blame.sessions:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return
    console.print(blame_timeline(blame, title=title))
