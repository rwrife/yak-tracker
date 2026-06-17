"""The ``yak`` command-line entry point.

M1 scaffold: wires up the Typer app with a version callback and a couple of
placeholder commands. Real collectors/sessionizer/tree logic land in later
milestones (see PLAN.md). Keeping this thin and dependency-light makes the
scaffold easy to test and fast to start.
"""

from __future__ import annotations

import typer
from rich.console import Console

from . import __version__

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


if __name__ == "__main__":  # pragma: no cover
    app()
