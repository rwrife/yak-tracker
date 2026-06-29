"""Markdown export of a day's yak-shaving log (PLAN.md backlog → Obsidian).

``yak today`` reconstructs and (optionally) narrates a single coding day. This
module is the *file* counterpart of that view: it turns the same day into a
dated markdown note suitable for dropping straight into an Obsidian vault or any
plain "daily notes" folder.

A note has two parts:

* **YAML front-matter** — machine-readable metadata Obsidian (and Dataview)
  understands: the ``date``, the day's **yak score**, session/detour counts, the
  body ``format``, and a ``generated_at`` stamp. This is what lets you later
  query "show me my low-focus days" without parsing prose.
* **A markdown body** — the human-readable write-up. When a local Ollama
  narrated the day, that prose is the body; otherwise (offline, ``--no-llm``, or
  an empty day) we fall back to a deterministic markdown outline of the
  yak-shaving forest, so the export *always* produces something useful — the
  same graceful-degradation contract ``yak today`` itself honours.

Writing is **idempotent**: each day maps to one file (via the configured
filename template, default ``{date}.md``) and re-running simply overwrites that
day's file in place. The function returns enough information
(:class:`ExportResult`) for the CLI to tell the user what happened (created vs
updated, and where).

Design notes:

* No ``rich``/``typer`` import here — this is presentation-free I/O over the
  shared :class:`~yak_tracker.tree.Node` forest and :class:`~yak_tracker.score.DayScore`,
  mirroring ``serialize.py``. The CLI owns flags and console output.
* Front-matter values are emitted with deliberately simple, well-defined
  quoting so the result is valid YAML without pulling in a YAML dependency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .config import FILENAME_PLACEHOLDER
from .score import DayScore
from .tree import DetourKind, Node

__all__ = [
    "ExportError",
    "ExportResult",
    "render_markdown",
    "resolve_export_path",
    "write_export",
]

# Glyph for each detour kind in the markdown outline. Plainer than render.py's
# rich tree (this lands in a notes file, not a terminal), but still scannable.
_KIND_MARK: dict[str, str] = {
    DetourKind.ROOT: "🎯",
    DetourKind.STEP: "•",
    DetourKind.INSTALL: "📦",
    DetourKind.ERROR_FIX: "🔧",
    DetourKind.DIR_CHANGE: "📂",
    DetourKind.BRANCH: "🌿",
}


class ExportError(ValueError):
    """Raised when an export can't be carried out as asked.

    Used for caller errors the CLI should surface cleanly (a missing vault path,
    or a filename template that doesn't resolve), rather than letting a raw
    ``KeyError``/``TypeError`` escape as a traceback.
    """


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Outcome of :func:`write_export`.

    Attributes:
        path: The file that was written.
        created: ``True`` if the file did not exist before (a fresh note);
            ``False`` if an existing note for that day was overwritten in place.
        bytes_written: Size of the rendered note, for a terse confirmation line.
    """

    path: Path
    created: bool
    bytes_written: int


def _yaml_scalar(value: object) -> str:
    """Render a Python value as a safe single-line YAML scalar.

    Numbers and booleans pass through bare; everything else is emitted as a
    double-quoted string with the two characters that matter inside YAML double
    quotes (`\\` and `"`) escaped. Keeps the front-matter valid without a YAML
    dependency, and avoids surprises from values like ``10:24`` or ``no``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Trim a trailing ``.0`` so an integral score reads as ``72`` not ``72.0``.
        return str(int(value)) if value.is_integer() else repr(value)
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _front_matter(
    day: date,
    score: DayScore | None,
    *,
    fmt: str,
    generated_at: datetime,
    narrated: bool,
) -> list[str]:
    """Build the YAML front-matter block (including the ``---`` fences).

    The score is rounded to a whole number (it's a 0–100 focus metric; decimals
    add noise in a note). ``yak_score`` is ``null`` for a day with no activity,
    so downstream queries can tell "focused" from "nothing happened".
    """
    day_score = None if score is None else score.score
    sessions = 0 if score is None else score.session_count
    max_depth = 0 if score is None else score.max_depth

    lines = ["---"]
    lines.append(f"date: {_yaml_scalar(day.isoformat())}")
    lines.append("title: " + _yaml_scalar(f"Yak-shaving — {day.isoformat()}"))
    if day_score is None:
        lines.append("yak_score: null")
    else:
        lines.append(f"yak_score: {_yaml_scalar(round(day_score))}")
    lines.append(f"sessions: {_yaml_scalar(sessions)}")
    lines.append(f"max_detour_depth: {_yaml_scalar(max_depth)}")
    lines.append(f"format: {_yaml_scalar(fmt)}")
    lines.append(f"narrated: {_yaml_scalar(narrated)}")
    lines.append("tags: [yak-tracker]")
    lines.append(f"generated_by: {_yaml_scalar('yak-tracker')}")
    lines.append(f"generated_at: {_yaml_scalar(generated_at.isoformat())}")
    lines.append("---")
    return lines


def _outline_lines(node: Node, depth: int, out: list[str]) -> None:
    """Append a nested markdown bullet for ``node`` and recurse into children."""
    mark = _KIND_MARK.get(node.kind, "•")
    when = node.ts.strftime("%H:%M") if node.ts else "--:--"
    indent = "  " * depth
    out.append(f"{indent}- {mark} `{when}` {node.label}")
    for child in node.children:
        _outline_lines(child, depth + 1, out)


def _forest_markdown(forest: Sequence[Node]) -> str:
    """Render the yak-shaving ``forest`` as a deterministic markdown outline.

    This is the offline body: one ``## Session`` per tree, each an intention →
    detour bullet list. Deterministic so re-exports are byte-stable (no churn in
    version-controlled vaults) and tests can assert on it.
    """
    if not forest:
        return "_No timestamped shell/git activity recorded for this day._"

    blocks: list[str] = []
    for i, root in enumerate(forest, start=1):
        started = root.ts.strftime("%H:%M") if root.ts else "--:--"
        detours = root.descendants()
        depth = root.max_depth()
        header = (
            f"## Session {i} — started {started} "
            f"({detours} event(s), {depth} level(s) deep)"
        )
        lines: list[str] = [header, ""]
        _outline_lines(root, 0, lines)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_markdown(
    forest: Sequence[Node],
    *,
    day: date,
    score: DayScore | None = None,
    fmt: str = "story",
    narration: str | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Render a full markdown note (front-matter + body) for one day.

    Args:
        forest: The day's yak-shaving forest (one :class:`Node` per session).
        day: The calendar date the note covers.
        score: The day's :class:`~yak_tracker.score.DayScore` (drives front-matter
            metadata); ``None`` is treated as "no activity / no score".
        fmt: The persona label recorded in front-matter and used to title the
            body section (``standup`` / ``story`` / ``learning``).
        narration: LLM prose to use as the body. When ``None`` (offline, no-LLM,
            or empty day) a deterministic outline of ``forest`` is used instead.
        generated_at: Override the generation timestamp (for deterministic
            tests). Defaults to ``datetime.now(UTC)``.

    Returns:
        The complete note text, ending with a trailing newline so appended-to or
        diffed files stay clean.
    """
    stamp = generated_at or datetime.now(UTC)
    narrated = bool(narration and narration.strip())

    parts: list[str] = []
    parts.append(
        "\n".join(
            _front_matter(
                day, score, fmt=fmt, generated_at=stamp, narrated=narrated
            )
        )
    )

    body_title = {
        "standup": "Standup",
        "story": "Story",
        "learning": "What I learned",
    }.get(fmt, fmt.capitalize())

    if narrated:
        body = f"# {body_title} — {day.isoformat()}\n\n{narration.strip()}"
    else:
        # Offline / no-LLM / empty: deterministic outline of the forest.
        body = (
            f"# Yak-shaving — {day.isoformat()}\n\n{_forest_markdown(forest)}"
        )

    parts.append(body)
    return "\n\n".join(parts).rstrip("\n") + "\n"


def resolve_export_path(
    *,
    day: date,
    out_dir: Path | None,
    vault_path: Path | None,
    filename_template: str,
) -> Path:
    """Resolve the on-disk path for a day's export.

    Precedence for the directory: an explicit ``out_dir`` (the CLI ``--out``)
    wins over the configured ``vault_path``. The filename comes from
    ``filename_template`` with its ``{date}`` placeholder filled in; the template
    may include subdirectories (``daily/{date}.md``).

    Raises:
        ExportError: if neither a directory is given nor a vault is configured,
            or if the template can't be rendered (bad/unknown placeholder).
    """
    base = out_dir if out_dir is not None else vault_path
    if base is None:
        raise ExportError(
            "no export destination: pass --out <dir> or set vault_path in your "
            "config (yak config --init writes a commented stub)."
        )
    try:
        rendered = filename_template.format(**{FILENAME_PLACEHOLDER: day.isoformat()})
    except (KeyError, IndexError, ValueError) as exc:
        raise ExportError(
            f"invalid filename_template {filename_template!r}: only the "
            f"{{{FILENAME_PLACEHOLDER}}} placeholder is supported"
        ) from exc
    if not rendered.strip():
        raise ExportError("filename_template rendered to an empty name")
    return (Path(base) / rendered).expanduser()


def write_export(
    forest: Sequence[Node],
    *,
    day: date,
    out_dir: Path | None = None,
    vault_path: Path | None = None,
    filename_template: str = "{date}.md",
    score: DayScore | None = None,
    fmt: str = "story",
    narration: str | None = None,
    generated_at: datetime | None = None,
) -> ExportResult:
    """Render and write a day's markdown note, idempotently.

    Resolves the destination (see :func:`resolve_export_path`), creates any
    missing parent directories, and writes the note — overwriting an existing
    note for the same day **in place** (the export is idempotent: one file per
    day). Returns an :class:`ExportResult` describing what happened so the CLI
    can report created-vs-updated and the path.

    Raises:
        ExportError: for caller errors (no destination, bad template). I/O errors
            (permissions, etc.) propagate as the usual :class:`OSError`.
    """
    path = resolve_export_path(
        day=day,
        out_dir=out_dir,
        vault_path=vault_path,
        filename_template=filename_template,
    )
    content = render_markdown(
        forest,
        day=day,
        score=score,
        fmt=fmt,
        narration=narration,
        generated_at=generated_at,
    )
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return ExportResult(
        path=path,
        created=not existed,
        bytes_written=len(content.encode("utf-8")),
    )
