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
from rich.table import Table

from . import __version__
from .collectors import git as git_collector
from .collectors import shell as shell_collector
from .config import VALID_FORMATS, load_config
from .narrate import narrate as narrate_forest
from .render import (
    render_events,
    render_narration,
    render_sessions,
    render_trees,
)
from .serialize import forest_to_dict
from .sessionize import sessionize
from .tree import build_forest

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
) -> None:
    """Dump a day's raw shell-history events as a table.

    This is the M2 collector surface: it parses bash/zsh history into normalized
    events and lists the ones from the chosen day. Timestamps appear where the
    history format records them (zsh extended history, or bash with
    ``HISTTIMEFORMAT`` set); otherwise the Time column shows ``—``.
    """
    target = _parse_date(date_str)
    events = shell_collector.collect_for_date(
        target,
        shell=shell,
        path=histfile,
        include_undated=include_undated,
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
) -> list:
    """Collect a day's shell + git events (shared by ``sessions`` and ``today``)."""
    collected: list = []
    if not no_shell:
        collected += shell_collector.collect_for_date(
            target,
            shell=shell,
            path=histfile,
        )
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
) -> None:
    """Bucket a day's shell + git activity into time-gapped work sessions.

    Merges shell-history events with git commits/reflog across the given repos
    (``--repo``, defaulting to the current directory) and splits the combined
    timeline wherever there's an idle gap longer than ``--idle-gap`` minutes.
    Only timestamped events can be placed on the timeline.
    """
    target = _parse_date(date_str)

    collected = _collect_day_events(
        target,
        repos=repos,
        shell=shell,
        histfile=histfile,
        no_git=no_git,
        no_shell=no_shell,
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
        format=fmt,
    )

    def _forest_for(day: date) -> list:
        collected = _collect_day_events(
            day,
            repos=list(config.repos) if config.repos else None,
            shell=shell,
            histfile=histfile,
            no_git=no_git,
            no_shell=no_shell,
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

    for day in days:
        forest = _forest_for(day)
        tree_title = f"\N{OX} Yak-shaving — {day.isoformat()}"
        empty_message = (
            f"Nothing to shave for {day.isoformat()}. "
            "(No timestamped shell/git events — check --repo or your history "
            "file's timestamps.)"
        )

        # No data, or narration explicitly skipped → just the raw tree (no network).
        if not forest or no_llm:
            render_trees(
                forest, console=console, title=tree_title, empty_message=empty_message
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
        else:
            # Graceful fallback: explain why narration was skipped, then raw tree.
            if narration.notice:
                console.print(f"[yellow]\N{WARNING SIGN}  {narration.notice}[/yellow]\n")
            render_trees(
                forest, console=console, title=tree_title, empty_message=empty_message
            )


@app.command(name="config")
def config_cmd(
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
    """
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
    table.add_row("config file", str(cfg.path))
    console.print(table)
    console.print(f"[dim]source: {cfg.source}[/dim]")
    if cfg.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for warning in cfg.warnings:
            console.print(f"  [yellow]\N{WARNING SIGN}  {warning}[/yellow]")


if __name__ == "__main__":  # pragma: no cover
    app()
