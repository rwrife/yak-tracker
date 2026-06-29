"""Unit tests for markdown export (PLAN.md backlog → Obsidian / daily notes)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from yak_tracker.export import (
    ExportError,
    ExportResult,
    render_markdown,
    resolve_export_path,
    write_export,
)
from yak_tracker.models import Event
from yak_tracker.score import score_day
from yak_tracker.sessionize import sessionize
from yak_tracker.tree import build_forest

DAY = date(2026, 6, 17)
STAMP = datetime(2026, 6, 17, 18, 0, 0, tzinfo=UTC)


def _ev(cmd: str, minute: int) -> Event:
    return Event(cmd=cmd, ts=datetime(2026, 6, 17, 9, minute, 0), cwd=None, source="shell:zsh")


def _forest():
    """A small forest: an intention with an install detour and a branch switch."""
    events = [
        _ev("git checkout -b fix-login", 0),
        _ev("vim src/login.py", 1),
        _ev("npm install left-pad", 2),
        _ev("rm -rf node_modules", 3),
    ]
    return build_forest(sessionize(events, idle_gap=25.0))


# --- front-matter --------------------------------------------------------- #


def test_render_has_yaml_front_matter_with_date_and_score() -> None:
    forest = _forest()
    md = render_markdown(
        forest, day=DAY, score=score_day(DAY, forest), generated_at=STAMP
    )
    assert md.startswith("---\n")
    # Front-matter fence closes before the body heading.
    head, _, body = md.partition("\n---\n")
    assert 'date: "2026-06-17"' in head
    assert "yak_score:" in head
    assert "yak_score: null" not in head  # this day has activity → a real score
    assert "tags: [yak-tracker]" in head
    assert f"generated_at: \"{STAMP.isoformat()}\"" in head
    assert "# " in body  # a body heading follows


def test_empty_day_score_is_null_in_front_matter() -> None:
    md = render_markdown([], day=DAY, score=score_day(DAY, []), generated_at=STAMP)
    assert "yak_score: null" in md
    assert "sessions: 0" in md
    # Empty day still produces a useful body, not a blank file.
    assert "No timestamped shell/git activity" in md


def test_score_rounded_to_whole_number() -> None:
    forest = _forest()
    md = render_markdown(
        forest, day=DAY, score=score_day(DAY, forest), generated_at=STAMP
    )
    # The score line must be an integer (no decimal point), never e.g. 61.7.
    line = next(ln for ln in md.splitlines() if ln.startswith("yak_score:"))
    value = line.split(":", 1)[1].strip()
    assert value.isdigit(), line


# --- body ----------------------------------------------------------------- #


def test_narration_is_used_as_body_when_provided() -> None:
    forest = _forest()
    md = render_markdown(
        forest,
        day=DAY,
        score=score_day(DAY, forest),
        fmt="learning",
        narration="Today I relearned that `rm -rf node_modules` fixes nothing.",
        generated_at=STAMP,
    )
    assert "narrated: true" in md
    assert "format: \"learning\"" in md
    assert "# What I learned — 2026-06-17" in md
    assert "rm -rf node_modules` fixes nothing" in md
    # Narrated bodies don't also dump the raw outline.
    assert "## Session 1" not in md


def test_outline_body_when_no_narration() -> None:
    forest = _forest()
    md = render_markdown(forest, day=DAY, score=score_day(DAY, forest), generated_at=STAMP)
    assert "narrated: false" in md
    assert "## Session 1" in md
    assert "git checkout -b fix-login" in md
    assert "npm install left-pad" in md


def test_render_is_deterministic() -> None:
    forest = _forest()
    a = render_markdown(forest, day=DAY, score=score_day(DAY, forest), generated_at=STAMP)
    b = render_markdown(forest, day=DAY, score=score_day(DAY, forest), generated_at=STAMP)
    assert a == b
    assert a.endswith("\n")  # trailing newline keeps diffs/files clean


# --- path resolution ------------------------------------------------------ #


def test_resolve_uses_out_dir_over_vault(tmp_path: Path) -> None:
    out = tmp_path / "explicit"
    vault = tmp_path / "vault"
    path = resolve_export_path(
        day=DAY, out_dir=out, vault_path=vault, filename_template="{date}.md"
    )
    assert path == out / "2026-06-17.md"


def test_resolve_falls_back_to_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = resolve_export_path(
        day=DAY, out_dir=None, vault_path=vault, filename_template="{date}.md"
    )
    assert path == vault / "2026-06-17.md"


def test_resolve_template_with_subdir(tmp_path: Path) -> None:
    path = resolve_export_path(
        day=DAY, out_dir=tmp_path, vault_path=None, filename_template="daily/{date}.md"
    )
    assert path == tmp_path / "daily" / "2026-06-17.md"


def test_resolve_requires_a_destination() -> None:
    with pytest.raises(ExportError, match="no export destination"):
        resolve_export_path(
            day=DAY, out_dir=None, vault_path=None, filename_template="{date}.md"
        )


def test_resolve_rejects_unknown_placeholder(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="placeholder"):
        resolve_export_path(
            day=DAY, out_dir=tmp_path, vault_path=None, filename_template="{nope}.md"
        )


# --- writing -------------------------------------------------------------- #


def test_write_creates_dated_file(tmp_path: Path) -> None:
    forest = _forest()
    result = write_export(
        forest,
        day=DAY,
        out_dir=tmp_path,
        score=score_day(DAY, forest),
        generated_at=STAMP,
    )
    assert isinstance(result, ExportResult)
    assert result.created is True
    assert result.path == tmp_path / "2026-06-17.md"
    assert result.path.read_text(encoding="utf-8").startswith("---\n")
    assert result.bytes_written == len(result.path.read_bytes())


def test_write_creates_missing_parent_dirs(tmp_path: Path) -> None:
    forest = _forest()
    result = write_export(
        forest,
        day=DAY,
        out_dir=tmp_path / "a" / "b",
        filename_template="notes/{date}.md",
        score=score_day(DAY, forest),
        generated_at=STAMP,
    )
    assert result.path == tmp_path / "a" / "b" / "notes" / "2026-06-17.md"
    assert result.path.is_file()


def test_write_is_idempotent_and_updates_in_place(tmp_path: Path) -> None:
    forest = _forest()
    first = write_export(
        forest, day=DAY, out_dir=tmp_path, narration="v1", generated_at=STAMP
    )
    assert first.created is True

    second = write_export(
        forest, day=DAY, out_dir=tmp_path, narration="v2 replaces v1", generated_at=STAMP
    )
    # Same file, reported as an update (not a new note).
    assert second.created is False
    assert second.path == first.path
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-06-17.md"]

    text = second.path.read_text(encoding="utf-8")
    assert "v2 replaces v1" in text
    assert "v1\n" not in text.replace("v2 replaces v1", "")  # old body gone


def test_write_without_destination_raises(tmp_path: Path) -> None:
    forest = _forest()
    with pytest.raises(ExportError):
        write_export(forest, day=DAY, out_dir=None, vault_path=None)
