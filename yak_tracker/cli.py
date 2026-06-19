"""The ``yak`` command-line entry point.

M1 scaffold: wires up the Typer app with a version callback and a couple of
placeholder commands. Real collectors/sessionizer/tree logic land in later
milestones (see PLAN.md). Keeping this thin and dependency-light makes the
scaffold easy to test and fast to start.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import typer
from rich.console import Console

from . import __version__
from .collectors import git as git_collector
from .collectors import shell as shell_collector
from .render import render_events, render_sessions
from .sessionize import sessionize

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


if __name__ == "__main__":  # pragma: no cover
    app()
