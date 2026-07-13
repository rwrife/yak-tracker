"""The ``yak`` command-line entry point.

M1 scaffold: wires up the Typer app with a version callback and a couple of
placeholder commands. Real collectors/sessionizer/tree logic land in later
milestones (see PLAN.md). Keeping this thin and dependency-light makes the
scaffold easy to test and fast to start.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .blame import BlameError, blame_to_dict, build_blame
from .collectors import collect_extra_shells_for_date
from .collectors import git as git_collector
from .collectors import shell as shell_collector
from .config import VALID_FORMATS, ConfigExistsError, load_config, write_starter_config
from .export import ExportError, write_export
from .narrate import narrate as narrate_forest
from .narrate import narrate_blame
from .render import (
    render_blame,
    render_events,
    render_narration,
    render_score,
    render_score_history,
    render_sessions,
    render_trees,
    render_week,
    score_footer,
)
from .sample import sample_events
from .score import DEFAULT_HISTORY_DAYS, build_history, score_day
from .serialize import forest_to_dict
from .sessionize import sessionize
from .tree import build_forest
from .week import DEFAULT_WEEK_DAYS, build_week

app = typer.Typer(
    name="yak",
    help="Reconstruct the story of your coding day — tangents and all — 100% locally. 🐃",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"yak-tracker {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    _version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show the yak-tracker version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """yak — your local-first coding-day storyteller."""


@app.command()
def hello(name: str = typer.Argument("yak", help="Who to greet.")) -> None:
    """Placeholder command — proves the CLI is wired up end to end."""
    console.print(f"🐃 Hello, {name}! yak-tracker is alive.")


@app.command()
def version() -> None:
    """Print the yak-tracker version (same as ``--version``)."""
    console.print(f"yak-tracker {__version__}")


def _parse_date(value: str | None) -> date:
    """Parse a ``YYYY-MM-DD`` string into a date, defaulting to today."""
    if not value:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter("date must be in YYYY-MM-DD format") from exc


def _date_range(end: date, since: int | None) -> list[date]:
    """Inclusive list of dates ending at ``end``, spanning ``since`` days.

    ``--since 1`` (or ``None``) yields just ``end``; ``--since 7`` yields the
    week ending on ``end`` (7 days, oldest first).
    """
    span = 1 if since is None else since
    if span < 1:
        raise typer.BadParameter("--since must be a positive number of days")
    return [end - timedelta(days=offset) for offset in range(span - 1, -1, -1)]


@app.command()
def raw(
    date_str: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Day to show (YYYY-MM-DD). Defaults to today.",
    ),
    shell: str = typer.Option(
        None,
        "--shell",
        help="Force shell grammar: bash or zsh. Defaults to auto-detect.",
    ),
    histfile: Path = typer.Option(
        None,
        "--histfile",
        help="Parse this history file instead of the auto-located one.",
        exists=False,
    ),
    include_undated: bool = typer.Option(
        False,
        "--include-undated",
        help="Also include events with no timestamp (e.g. plain bash history).",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help="Do not scrub secrets/tokens from commands (show the raw history).",
    ),
) -> None:
    """Dump a day's raw shell-history events as a table.

    This is the M2 collector surface: it parses bash/zsh history into normalized
    events and lists the ones from the chosen day. Timestamps appear where the
    history format records them (zsh extended history, or bash with
    ``HISTTIMEFORMAT`` set); otherwise the Time column shows ``—``.

    Secrets (API keys, tokens, ``KEY=value`` credentials, URL passwords) are
    redacted to ``«REDACTED:…»`` by default; pass ``--no-redact`` to see the raw
    commands.
    """
    target = _parse_date(date_str)
    events = shell_collector.collect_for_date(
        target,
        shell=shell,
        path=histfile,
        include_undated=include_undated,
        redact=not no_redact,
    )
    render_events(
        events,
        console=console,
        title=f"Shell events — {target.isoformat()}",
        empty_message=(
            f"No shell events found for {target.isoformat()}. "
            "(Try --include-undated, or check that your history file has timestamps.)"
        ),
    )


def _collect_day_events(
    target: date,
    *,
    repos: list[Path] | None,
    shell: str | None,
    histfile: Path | None,
    no_git: bool,
    no_shell: bool,
    redact: bool = True,
) -> list:
    """Collect a day's shell + git events (shared by ``sessions`` and ``today``)."""
    collected: list = []
    if not no_shell:
        collected += shell_collector.collect_for_date(
            target,
            shell=shell,
            path=histfile,
            redact=redact,
        )
        # Also pick up fish + nushell from their own auto-detected history
        # files (unless the user pinned an explicit --histfile to parse).
        if histfile is None:
            collected += collect_extra_shells_for_date(target, redact=redact)
    if not no_git:
        repo_paths = list(repos) if repos else [Path.cwd()]
        git_events = git_collector.collect(repo_paths)
        collected += [e for e in git_events if e.on_date(target)]
    return collected


@app.command()
def sessions(
    date_str: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Day to sessionize (YYYY-MM-DD). Defaults to today.",
    ),
    repos: list[Path] = typer.Option(
        None,
        "--repo",
        "-r",
        help="Git repo to include (repeatable). Defaults to the current directory.",
    ),
    shell: str = typer.Option(
        None,
        "--shell",
        help="Force shell grammar: bash or zsh. Defaults to auto-detect.",
    ),
    histfile: Path = typer.Option(
        None,
        "--histfile",
        help="Parse this history file instead of the auto-located one.",
        exists=False,
    ),
    idle_gap: float = typer.Option(
        25.0,
        "--idle-gap",
        "-g",
        help="Minutes of inactivity that start a new session.",
    ),
    no_git: bool = typer.Option(
        False,
        "--no-git",
        help="Skip the git collector (shell history only).",
    ),
    no_shell: bool = typer.Option(
        False,
        "--no-shell",
        help="Skip the shell collector (git activity only).",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help="Do not scrub secrets/tokens from shell commands.",
    ),
) -> None:
    """Bucket a day's shell + git activity into time-gapped work sessions.

    Merges shell-history events with git commits/reflog across the given repos
    (``--repo``, defaulting to the current directory) and splits the combined
    timeline wherever there's an idle gap longer than ``--idle-gap`` minutes.
    Only timestamped events can be placed on the timeline. Secrets in shell
    commands are redacted by default; pass ``--no-redact`` to keep them raw.
    """
    target = _parse_date(date_str)

    collected = _collect_day_events(
        target,
        repos=repos,
        shell=shell,
        histfile=histfile,
        no_git=no_git,
        no_shell=no_shell,
        redact=not no_redact,
    )

    day_sessions = sessionize(collected, idle_gap=idle_gap)
    render_sessions(
        day_sessions,
        console=console,
        title=f"Sessions — {target.isoformat()} (idle gap {idle_gap:g}m)",
        empty_message=(
            f"No sessions found for {target.isoformat()}. "
            "(No timestamped shell/git events on that day — check --repo or your "
            "history file's timestamps.)"
        ),
    )


@app.command()
def today(
    date_str: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Day to reconstruct (YYYY-MM-DD). Defaults to today.",
    ),
    fmt: str = typer.Option(
        None,
        "--format",
        "-f",
        help=(
            "Narration persona: standup, story, or learning. "
            "Defaults to the configured format (story). "
            "Requires a local Ollama; falls back to the raw tree if absent."
        ),
    ),
    repos: list[Path] = typer.Option(
        None,
        "--repo",
        "-r",
        help="Git repo to include (repeatable). Defaults to config or the current directory.",
    ),
    shell: str = typer.Option(
        None,
        "--shell",
        help="Force shell grammar: bash or zsh. Defaults to auto-detect.",
    ),
    histfile: Path = typer.Option(
        None,
        "--histfile",
        help="Parse this history file instead of the auto-located one.",
        exists=False,
    ),
    since: int = typer.Option(
        None,
        "--since",
        "-s",
        min=1,
        help=(
            "Reconstruct the last N days ending at --date (default today), "
            "oldest first. --since 1 is the same as a single day."
        ),
    ),
    idle_gap: float = typer.Option(
        None,
        "--idle-gap",
        "-g",
        help="Minutes of inactivity that start a new session. Overrides config.",
    ),
    model: str = typer.Option(
        None,
        "--model",
        help="Ollama model to narrate with. Overrides config.",
    ),
    ollama_host: str = typer.Option(
        None,
        "--ollama-host",
        help="Base URL of the local Ollama server. Overrides config.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help=(
            "Emit the yak-shaving forest as JSON for scripting (implies "
            "--no-llm; narration is prose, not data)."
        ),
    ),
    export: str = typer.Option(
        None,
        "--export",
        "-e",
        help=(
            "Write the day to a notes file instead of (just) printing. Only "
            "'md' is supported. Honours --format for the body; goes to --out "
            "or the configured vault_path as <date>.md."
        ),
    ),
    out_dir: Path = typer.Option(
        None,
        "--out",
        "-o",
        help=(
            "Destination directory for --export (overrides config vault_path). "
            "Created if missing."
        ),
    ),
    template: str = typer.Option(
        None,
        "--template",
        help=(
            "Filename template for --export; the only placeholder is {date} "
            "(e.g. 'daily/{date}.md'). Overrides config filename_template."
        ),
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Skip Ollama narration; print the raw yak-shaving tree only.",
    ),
    no_git: bool = typer.Option(
        False,
        "--no-git",
        help="Skip the git collector (shell history only).",
    ),
    no_shell: bool = typer.Option(
        False,
        "--no-shell",
        help="Skip the shell collector (git activity only).",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help="Do not scrub secrets/tokens from shell commands before narration/JSON.",
    ),
) -> None:
    """Reconstruct the day and narrate it with a local LLM.

    Collects the day's shell + git activity, buckets it into sessions, builds the
    yak-shaving trees, then asks a local Ollama model to narrate them in the
    chosen ``--format`` persona:

    * ``standup`` — terse, shippable bullets for the morning sync.
    * ``story``   — the funny saga of the day's rabbit holes (default).
    * ``learning``— what you learned, to fight AI-coding skill rot.

    Privacy is the point: only a summarised outline is sent, and only to your
    local Ollama. If Ollama isn't reachable (or ``--no-llm`` is set), it degrades
    gracefully to the raw tree render plus a short notice — so you always get
    *something*.

    Use ``--since N`` to reconstruct the last N days at once (oldest first), and
    ``--json`` to emit the yak-shaving forest as machine-readable JSON for
    scripting or export (``--json`` implies ``--no-llm``; with ``--since`` it
    emits a JSON array, one document per day).

    Use ``--export md`` to write each day to a dated markdown note (for Obsidian
    or any daily-notes vault) instead of printing: front-matter carries the date
    and yak score, the body is the chosen ``--format`` (narrated when Ollama is
    available, else a plain outline). It goes to ``--out`` or the configured
    ``vault_path`` as ``<date>.md`` and is rewritten in place on re-run.
    """
    if fmt is not None and fmt not in VALID_FORMATS:
        raise typer.BadParameter(f"format must be one of {', '.join(VALID_FORMATS)}")

    if export is not None and export.lower() != "md":
        raise typer.BadParameter("--export only supports 'md'")
    if as_json and export is not None:
        raise typer.BadParameter("--json and --export are mutually exclusive")

    end = _parse_date(date_str)
    days = _date_range(end, since)

    config = load_config().with_overrides(
        idle_gap=idle_gap,
        repos=list(repos) if repos else None,
        model=model,
        ollama_host=ollama_host,
        format=fmt,
        redact=False if no_redact else None,
        filename_template=template,
    )

    def _forest_for(day: date) -> list:
        collected = _collect_day_events(
            day,
            repos=list(config.repos) if config.repos else None,
            shell=shell,
            histfile=histfile,
            no_git=no_git,
            no_shell=no_shell,
            redact=config.redact,
        )
        day_sessions = sessionize(collected, idle_gap=config.idle_gap)
        return build_forest(day_sessions)

    # --json is a data surface: emit every day's forest and skip narration
    # entirely (narration is prose, not machine-readable).
    if as_json:
        documents = [forest_to_dict(_forest_for(day), day=day) for day in days]
        payload = documents[0] if len(documents) == 1 else documents
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return

    # --export writes each day to a dated markdown note (Obsidian / daily notes)
    # instead of rendering to the terminal. Narration (when available) is the
    # body; otherwise a deterministic outline is, so an offline export still has
    # content. One file per day, overwritten in place (idempotent).
    if export is not None:
        for day in days:
            forest = _forest_for(day)
            day_score = score_day(day, forest)

            narration_text: str | None = None
            if forest and not no_llm:
                narration = narrate_forest(
                    forest,
                    fmt=config.format,
                    model=config.model,
                    host=config.ollama_host,
                    timeout=config.timeout,
                    date_label=day.isoformat(),
                )
                if narration.ok:
                    narration_text = narration.text
                elif narration.notice:
                    console.print(
                        f"[yellow]\N{WARNING SIGN}  {narration.notice} "
                        f"Writing the raw outline instead.[/yellow]"
                    )

            try:
                result = write_export(
                    forest,
                    day=day,
                    out_dir=out_dir,
                    vault_path=config.vault_path,
                    filename_template=config.filename_template,
                    score=day_score,
                    fmt=config.format,
                    narration=narration_text,
                )
            except ExportError as exc:
                raise typer.BadParameter(str(exc)) from exc

            verb = "Wrote" if result.created else "Updated"
            console.print(
                f"\N{OX} {verb} [bold]{result.path}[/bold] "
                f"({result.bytes_written:,} bytes)"
            )
        return

    for day in days:
        forest = _forest_for(day)
        tree_title = f"\N{OX} Yak-shaving — {day.isoformat()}"
        empty_message = (
            f"Nothing to shave for {day.isoformat()}. "
            "(No timestamped shell/git events — check --repo or your history "
            "file's timestamps.)"
        )
        day_score = score_day(day, forest)

        # No data, or narration explicitly skipped → just the raw tree (no network).
        if not forest or no_llm:
            render_trees(
                forest,
                console=console,
                title=tree_title,
                empty_message=empty_message,
                day_score=day_score,
            )
            continue

        narration = narrate_forest(
            forest,
            fmt=config.format,
            model=config.model,
            host=config.ollama_host,
            timeout=config.timeout,
            date_label=day.isoformat(),
        )

        if narration.ok:
            render_narration(narration, console=console, title=tree_title)
            console.print(score_footer(day_score))
        else:
            # Graceful fallback: explain why narration was skipped, then raw tree.
            if narration.notice:
                console.print(f"[yellow]\N{WARNING SIGN}  {narration.notice}[/yellow]\n")
            render_trees(
                forest,
                console=console,
                title=tree_title,
                empty_message=empty_message,
                day_score=day_score,
            )


@app.command()
def tui(
    date_str: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Day to explore (YYYY-MM-DD). Defaults to today.",
    ),
    fmt: str = typer.Option(
        None,
        "--format",
        "-f",
        help=(
            "Narration persona shown first: standup, story, or learning. "
            "Defaults to the configured format. Press 'f' in the TUI to cycle."
        ),
    ),
    repos: list[Path] = typer.Option(
        None,
        "--repo",
        "-r",
        help="Git repo to include (repeatable). Defaults to config or cwd.",
    ),
    shell: str = typer.Option(
        None,
        "--shell",
        help="Force shell grammar: bash or zsh. Defaults to auto-detect.",
    ),
    histfile: Path = typer.Option(
        None,
        "--histfile",
        help="Parse this history file instead of the auto-located one.",
        exists=False,
    ),
    idle_gap: float = typer.Option(
        None,
        "--idle-gap",
        "-g",
        help="Minutes of inactivity that start a new session. Overrides config.",
    ),
    model: str = typer.Option(
        None,
        "--model",
        help="Ollama model to narrate with. Overrides config.",
    ),
    ollama_host: str = typer.Option(
        None,
        "--ollama-host",
        help="Base URL of the local Ollama server. Overrides config.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Skip Ollama narration; the footer shows the raw outline instead.",
    ),
    no_git: bool = typer.Option(
        False,
        "--no-git",
        help="Skip the git collector (shell history only).",
    ),
    no_shell: bool = typer.Option(
        False,
        "--no-shell",
        help="Skip the shell collector (git activity only).",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help="Do not scrub secrets/tokens from shell commands.",
    ),
) -> None:
    """Explore the day's yak-shaving tree in an interactive TUI.

    Runs the exact same pipeline as ``yak today`` (shell + git → sessions →
    trees) but hands the forest to a `textual` app instead of a static render.
    Collapse/expand any session or detour with the arrow keys / Enter, press
    ``e``/``c`` to expand/collapse everything, and ``f`` to cycle the footer
    summary between the standup/story/learning personas. ``q`` quits.

    The footer summary honours ``--format`` for the persona shown first. When a
    local Ollama is reachable it narrates that persona; the other personas (and
    everything when ``--no-llm`` or Ollama is down) fall back to a deterministic
    outline so the TUI always has something to show — offline included.

    ``textual`` is an optional extra; install it with
    ``pip install 'yak-tracker[tui]'`` if ``yak tui`` reports it missing.
    """
    from .narrate import build_outline
    from .tui import ForestView, TuiUnavailableError, run_tui

    if fmt is not None and fmt not in VALID_FORMATS:
        raise typer.BadParameter(f"format must be one of {', '.join(VALID_FORMATS)}")

    target = _parse_date(date_str)
    config = load_config().with_overrides(
        idle_gap=idle_gap,
        repos=list(repos) if repos else None,
        model=model,
        ollama_host=ollama_host,
        format=fmt,
        redact=False if no_redact else None,
    )

    collected = _collect_day_events(
        target,
        repos=list(config.repos) if config.repos else None,
        shell=shell,
        histfile=histfile,
        no_git=no_git,
        no_shell=no_shell,
        redact=config.redact,
    )
    day_sessions = sessionize(collected, idle_gap=config.idle_gap)
    forest = build_forest(day_sessions)

    # Deterministic outline is the always-available footer fallback for every
    # persona (and the whole footer when offline / --no-llm).
    outline = build_outline(forest, date_label=target.isoformat())
    summaries: dict[str, str] = {f: outline for f in VALID_FORMATS}

    # Try to narrate the selected persona so the footer opens on real prose;
    # any failure (Ollama down, empty) silently leaves the outline fallback.
    if forest and not no_llm:
        narration = narrate_forest(
            forest,
            fmt=config.format,
            model=config.model,
            host=config.ollama_host,
            timeout=config.timeout,
            date_label=target.isoformat(),
        )
        if narration.ok and narration.text:
            summaries[config.format] = narration.text

    view = ForestView(
        forest=forest,
        day=target,
        summaries=summaries,
        fmt=config.format,
    )
    try:
        run_tui(view)
    except TuiUnavailableError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def blame(
    file: str = typer.Argument(
        ...,
        help="File to blame (relative or absolute). Must live under a tracked repo.",
    ),
    date_str: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Day whose shell history to scan (YYYY-MM-DD). Defaults to today.",
    ),
    since: int = typer.Option(
        None,
        "--since",
        "-s",
        min=1,
        help=(
            "Scan shell history across the last N days ending at --date, and "
            "widen the git lookback to match. Defaults to a single day / 60 days "
            "of git history."
        ),
    ),
    repos: list[Path] = typer.Option(
        None,
        "--repo",
        "-r",
        help="Tracked repo to resolve the file against (repeatable). Defaults to config or cwd.",
    ),
    shell: str = typer.Option(
        None,
        "--shell",
        help="Force shell grammar: bash or zsh. Defaults to auto-detect.",
    ),
    histfile: Path = typer.Option(
        None,
        "--histfile",
        help="Parse this history file instead of the auto-located one.",
        exists=False,
    ),
    fmt: str = typer.Option(
        None,
        "--format",
        "-f",
        help="Accepted for symmetry with other commands; blame always narrates a single summary.",
    ),
    idle_gap: float = typer.Option(
        None,
        "--idle-gap",
        "-g",
        help="Minutes of inactivity that start a new session. Overrides config.",
    ),
    model: str = typer.Option(
        None,
        "--model",
        help="Ollama model to narrate with. Overrides config.",
    ),
    ollama_host: str = typer.Option(
        None,
        "--ollama-host",
        help="Base URL of the local Ollama server. Overrides config.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the per-file churn as JSON (implies --no-llm).",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Skip Ollama narration; print the raw timeline only.",
    ),
    no_shell: bool = typer.Option(
        False,
        "--no-shell",
        help="Skip shell references; blame git commits only.",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help="Do not scrub secrets/tokens from shell commands.",
    ),
) -> None:
    """Reconstruct why one file kept pulling you back — its per-file detour story.

    Where ``yak today`` answers *what was my day*, ``yak blame <file>`` answers
    *what was the deal with **this one file***. It walks the same normalized event
    stream — the git commits that modified the file (following renames) plus the
    shell commands that referenced it — buckets the matches into sessions, and
    renders a compact timeline headlined by how many sessions and detours touched
    it (e.g. *"cli.py — touched in 4 sessions across 3 detours"*).

    A local Ollama then narrates the churn in one paragraph ("why this file kept
    pulling you back"), degrading gracefully to the raw timeline if Ollama isn't
    reachable or ``--no-llm`` is set. Use ``--json`` for a machine-readable
    structure, ``--since N`` to widen the window, and ``--repo`` to point yak at
    the repo that owns the file.
    """
    if fmt is not None and fmt not in VALID_FORMATS:
        raise typer.BadParameter(f"format must be one of {', '.join(VALID_FORMATS)}")

    end = _parse_date(date_str)
    days = _date_range(end, since)

    config = load_config().with_overrides(
        idle_gap=idle_gap,
        repos=list(repos) if repos else None,
        model=model,
        ollama_host=ollama_host,
        redact=False if no_redact else None,
    )

    # Gather the shell events across the requested window (unless --no-shell).
    shell_events: list = []
    if not no_shell:
        for day in days:
            shell_events += shell_collector.collect_for_date(
                day,
                shell=shell,
                path=histfile,
                redact=config.redact,
            )
            if histfile is None:
                shell_events += collect_extra_shells_for_date(
                    day, redact=config.redact
                )

    # Widen the git lookback to at least cover --since days.
    git_since = f"{max(since or 1, 60)}.days.ago"

    try:
        result = build_blame(
            file,
            repos=list(config.repos) if config.repos else None,
            shell_events=None if no_shell else shell_events,
            since=git_since,
            idle_gap=config.idle_gap,
        )
    except BlameError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if as_json:
        console.print_json(json.dumps(blame_to_dict(result), ensure_ascii=False))
        return

    title = f"\N{OX} {result.headline}"
    empty_message = (
        f"Nothing touched {result.resolution.relpath.as_posix()} in range. "
        "(No commits modified it and no shell commands referenced it — try "
        "--since N to widen the window, or --repo if it lives elsewhere.)"
    )

    if not result.sessions or no_llm:
        render_blame(
            result, console=console, title=title, empty_message=empty_message
        )
        return

    narration = narrate_blame(
        result,
        model=config.model,
        host=config.ollama_host,
        timeout=config.timeout,
        date_label=end.isoformat(),
    )

    render_blame(result, console=console, title=title, empty_message=empty_message)
    if narration.ok:
        console.print()
        console.print(
            Panel(
                (narration.text or "").strip(),
                title="[bold]\N{OX} Why this file kept pulling you back[/bold]",
                subtitle=f"[dim]{narration.model}[/dim]",
                border_style="magenta",
                padding=(1, 2),
            )
        )
    elif narration.notice:
        console.print(f"\n[yellow]\N{WARNING SIGN}  {narration.notice}[/yellow]")


@app.command()
def demo(
    date_str: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Pretend the sample day happened on this date (YYYY-MM-DD). Defaults to today.",
    ),
    since: int = typer.Option(
        None,
        "--since",
        "-s",
        min=1,
        help="Replay the sample day across the last N days, oldest first.",
    ),
    idle_gap: float = typer.Option(
        25.0,
        "--idle-gap",
        "-g",
        help="Minutes of inactivity that start a new session.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the sample forest as JSON instead of the rich tree.",
    ),
) -> None:
    """Show a built-in sample day — no shell history or Ollama required.

    ``yak demo`` runs a curated, synthetic coding day (the classic
    "fix one bug → npm rabbit hole → wandered into another repo → finally
    shipped" spiral) through the *real* pipeline — the same sessionizer and
    yak-shaving tree that ``yak today`` uses — so you can see what yak-tracker
    produces the moment you install it, before pointing it at your own history.

    It never reads your shell history and never touches Ollama (the tree speaks
    for itself), so it works on a totally fresh machine. ``--json`` emits the
    same machine-readable forest as ``yak today --json``, and ``--since N``
    replays the sample across N days so the multi-day and ``yak week`` views
    have something to chew on too.
    """
    end = _parse_date(date_str)
    days = _date_range(end, since)

    def _forest_for(day: date) -> list:
        events = sample_events(day=day)
        day_sessions = sessionize(events, idle_gap=idle_gap)
        return build_forest(day_sessions)

    if as_json:
        documents = [forest_to_dict(_forest_for(day), day=day) for day in days]
        payload = documents[0] if len(documents) == 1 else documents
        console.print_json(json.dumps(payload, ensure_ascii=False))
        return

    console.print(
        "[dim]Sample data — no shell history or Ollama used. "
        "Run [cyan]yak today[/cyan] against your own day for the real thing.[/dim]\n"
    )
    for day in days:
        forest = _forest_for(day)
        render_trees(
            forest,
            console=console,
            title=f"\N{OX} Yak-shaving (demo) — {day.isoformat()}",
            empty_message=f"No sample activity for {day.isoformat()}.",
        )


@app.command()
def week(
    date_str: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Last day of the window (YYYY-MM-DD). Defaults to today.",
    ),
    since: int = typer.Option(
        None,
        "--since",
        "-s",
        min=1,
        help=(
            "Number of days to roll up, ending at --date (default today). "
            f"Defaults to {DEFAULT_WEEK_DAYS} (a week)."
        ),
    ),
    repos: list[Path] = typer.Option(
        None,
        "--repo",
        "-r",
        help="Git repo to include (repeatable). Defaults to config or the current directory.",
    ),
    shell: str = typer.Option(
        None,
        "--shell",
        help="Force shell grammar: bash or zsh. Defaults to auto-detect.",
    ),
    histfile: Path = typer.Option(
        None,
        "--histfile",
        help="Parse this history file instead of the auto-located one.",
        exists=False,
    ),
    idle_gap: float = typer.Option(
        None,
        "--idle-gap",
        "-g",
        help="Minutes of inactivity that start a new session. Overrides config.",
    ),
    no_git: bool = typer.Option(
        False,
        "--no-git",
        help="Skip the git collector (shell history only).",
    ),
    no_shell: bool = typer.Option(
        False,
        "--no-shell",
        help="Skip the shell collector (git activity only).",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help="Do not scrub secrets/tokens from shell commands.",
    ),
) -> None:
    """Roll up a week into a per-day tangent-depth heatmap.

    Reconstructs each day in the window (``--since N`` days back from ``--date``,
    defaulting to the last 7), then renders a heatmap of how deep that day's
    deepest rabbit hole went — so the rabbit-hole days jump out at a glance. The
    single deepest yak-shave of the whole week is called out underneath.

    This is the multi-day companion to ``yak today``: same local-only collection
    and sessionizing, no Ollama narration (the week view is about *shape*, not
    prose). Quiet days with no timestamped activity still appear as empty rows so
    you can see the gaps.
    """
    end = _parse_date(date_str)
    span = DEFAULT_WEEK_DAYS if since is None else since

    config = load_config().with_overrides(
        idle_gap=idle_gap,
        repos=list(repos) if repos else None,
        redact=False if no_redact else None,
    )

    def _forest_for(day: date) -> list:
        collected = _collect_day_events(
            day,
            repos=list(config.repos) if config.repos else None,
            shell=shell,
            histfile=histfile,
            no_git=no_git,
            no_shell=no_shell,
            redact=config.redact,
        )
        day_sessions = sessionize(collected, idle_gap=config.idle_gap)
        return build_forest(day_sessions)

    summary = build_week(end, span, _forest_for)
    title = (
        f"\N{OX} Weekly yak-shaving — "
        f"{summary.start.isoformat()} → {summary.end.isoformat()} "
        f"({span}d)"
    )
    render_week(summary, console=console, title=title)


@app.command()
def score(
    date_str: str = typer.Option(
        None,
        "--date",
        "-d",
        help="Day to score (YYYY-MM-DD). Defaults to today.",
    ),
    history: bool = typer.Option(
        False,
        "--history",
        help="Show a sparkline of focus scores over recent days instead of one day.",
    ),
    since: int = typer.Option(
        None,
        "--since",
        "-s",
        min=1,
        help=(
            "With --history, how many days to chart, ending at --date "
            f"(default {DEFAULT_HISTORY_DAYS})."
        ),
    ),
    repos: list[Path] = typer.Option(
        None,
        "--repo",
        "-r",
        help="Git repo to include (repeatable). Defaults to config or the current directory.",
    ),
    shell: str = typer.Option(
        None,
        "--shell",
        help="Force shell grammar: bash or zsh. Defaults to auto-detect.",
    ),
    histfile: Path = typer.Option(
        None,
        "--histfile",
        help="Parse this history file instead of the auto-located one.",
        exists=False,
    ),
    idle_gap: float = typer.Option(
        None,
        "--idle-gap",
        "-g",
        help="Minutes of inactivity that start a new session. Overrides config.",
    ),
    no_git: bool = typer.Option(
        False,
        "--no-git",
        help="Skip the git collector (shell history only).",
    ),
    no_shell: bool = typer.Option(
        False,
        "--no-shell",
        help="Skip the shell collector (git activity only).",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help="Do not scrub secrets/tokens from shell commands.",
    ),
) -> None:
    """Score your focus for a day — one number for how deep your detours went.

    The **yak score** distils a day to a 0–100 focus number: ``100`` is a
    laser-focused day with no rabbit holes, and the score falls as your work
    spirals deeper and more often into tangents (installs, forced fixes, wandering
    into other repos, branch hopping). It gamifies staying on task — bigger is
    better, like a credit score for not yak-shaving.

    By default it scores a single day (``--date``, defaulting to today). With
    ``--history`` it charts the last ``--since`` days (default a fortnight) as a
    sparkline with average / best / worst callouts, so you can see your focus
    trend at a glance. Same local-only collection as ``yak today`` — no Ollama,
    no network. The exact formula is documented in the README.
    """
    end = _parse_date(date_str)

    config = load_config().with_overrides(
        idle_gap=idle_gap,
        repos=list(repos) if repos else None,
        redact=False if no_redact else None,
    )

    def _forest_for(day: date) -> list:
        collected = _collect_day_events(
            day,
            repos=list(config.repos) if config.repos else None,
            shell=shell,
            histfile=histfile,
            no_git=no_git,
            no_shell=no_shell,
            redact=config.redact,
        )
        day_sessions = sessionize(collected, idle_gap=config.idle_gap)
        return build_forest(day_sessions)

    if history:
        span = DEFAULT_HISTORY_DAYS if since is None else since
        hist = build_history(end, span, _forest_for)
        title = (
            f"\N{ELECTRIC LIGHT BULB} Yak score — "
            f"{hist.start.isoformat()} → {hist.end.isoformat()} ({span}d)"
        )
        render_score_history(hist, console=console, title=title)
        return

    day_score = score_day(end, _forest_for(end))
    render_score(
        day_score,
        console=console,
        title=f"\N{ELECTRIC LIGHT BULB} Yak score — {end.isoformat()}",
        empty_message=(
            f"No focus score for {end.isoformat()}. "
            "(No timestamped shell/git events — check --repo or your history "
            "file's timestamps. Try --history for a trend.)"
        ),
    )


@app.command(name="config")
def config_cmd(
    init: bool = typer.Option(
        False,
        "--init",
        help="Write a starter config file to the resolved path, then exit.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="With --init, overwrite an existing config file.",
    ),
    show_path: bool = typer.Option(
        False,
        "--path",
        help="Print only the resolved config file path and exit.",
    ),
) -> None:
    """Print the resolved configuration (and where it came from).

    Shows the effective settings yak-tracker would use — repos, idle gap, Ollama
    model/host, default format — merged from the config file
    (``~/.config/yak-tracker/config.toml`` by default) over the built-in
    defaults. Problems loading the file are reported as warnings rather than
    failing, so this doubles as a config linter.

    Use ``--init`` to drop a fully-commented starter config at that path (every
    key set to its default, so it changes nothing until you edit it) — the
    one-step way for a fresh install to get a config without hunting down the
    XDG directory. It refuses to clobber an existing file unless you pass
    ``--force``.
    """
    if init:
        try:
            written = write_starter_config(force=force)
        except ConfigExistsError as exc:
            console.print(
                f"[yellow]\N{WARNING SIGN}  Config already exists at "
                f"{exc.path}[/yellow]\n"
                "Edit it directly, or pass [cyan]--force[/cyan] to overwrite "
                "with a fresh starter file."
            )
            raise typer.Exit(code=1) from exc
        except OSError as exc:
            console.print(f"[red]Could not write config: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        action = "Overwrote" if force else "Wrote"
        console.print(
            f"[green]\N{HEAVY CHECK MARK} {action} starter config:[/green] {written}\n"
            "Edit it to taste, then check it with [cyan]yak config[/cyan]."
        )
        return

    cfg = load_config()

    if show_path:
        console.print(str(cfg.path))
        return

    table = Table(title="yak-tracker config", expand=True, highlight=True)
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value", style="white", overflow="fold")
    repos_display = (
        "\n".join(str(p) for p in cfg.repos) if cfg.repos else "[dim](current dir)[/dim]"
    )
    table.add_row("repos", repos_display)
    table.add_row("idle_gap", f"{cfg.idle_gap:g} min")
    table.add_row("model", cfg.model)
    table.add_row("ollama_host", cfg.ollama_host)
    table.add_row("timeout", f"{cfg.timeout:g} s")
    table.add_row("format", cfg.format)
    table.add_row("redact", "on" if cfg.redact else "[red]off[/red]")
    table.add_row("config file", str(cfg.path))
    console.print(table)
    console.print(f"[dim]source: {cfg.source}[/dim]")
    if cfg.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for warning in cfg.warnings:
            console.print(f"  [yellow]\N{WARNING SIGN}  {warning}[/yellow]")


if __name__ == "__main__":  # pragma: no cover
    app()
