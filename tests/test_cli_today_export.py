"""CLI tests for ``yak today --export md`` (markdown export to a notes vault)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from yak_tracker.cli import app

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"

# The zsh fixture's first event cluster lands on this local day (timezone-safe).
DAY1 = datetime.fromtimestamp(1750118400).date().isoformat()


def _export(tmp_path: Path, *extra: str, out: Path | None = None):
    """Run ``yak today --export md`` against the offline zsh fixture."""
    dest = out if out is not None else tmp_path
    return runner.invoke(
        app,
        [
            "today",
            "--no-git",
            "--no-llm",  # stay offline/deterministic — no Ollama in CI
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            DAY1,
            "--export",
            "md",
            "--out",
            str(dest),
            *extra,
        ],
    )


def test_export_writes_dated_markdown_file(tmp_path: Path) -> None:
    result = _export(tmp_path)
    assert result.exit_code == 0, result.output
    out_file = tmp_path / f"{DAY1}.md"
    assert out_file.is_file()
    assert "Wrote" in result.output
    assert str(out_file) in result.output


def test_export_front_matter_has_date_and_score(tmp_path: Path) -> None:
    _export(tmp_path)
    text = (tmp_path / f"{DAY1}.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert f'date: "{DAY1}"' in text
    assert "yak_score:" in text
    assert "tags: [yak-tracker]" in text
    # Offline export → outline body with the fixture's install detour.
    assert "narrated: false" in text
    assert "npm install" in text


def test_export_is_idempotent_in_place(tmp_path: Path) -> None:
    first = _export(tmp_path)
    assert "Wrote" in first.output
    before = (tmp_path / f"{DAY1}.md").read_text(encoding="utf-8")

    second = _export(tmp_path)
    assert second.exit_code == 0, second.output
    assert "Updated" in second.output  # not a second "Wrote"
    # Exactly one file for the day.
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{DAY1}.md"]

    after = (tmp_path / f"{DAY1}.md").read_text(encoding="utf-8")
    # Content is stable across re-runs except the generated_at stamp (which is
    # expected to advance) — same front-matter, same body, no churn/duplication.
    def drop_stamp(text: str) -> str:
        return "\n".join(
            ln for ln in text.splitlines() if not ln.startswith("generated_at:")
        )

    assert drop_stamp(after) == drop_stamp(before)


def test_export_respects_filename_template(tmp_path: Path) -> None:
    result = _export(tmp_path, "--template", "daily/{date}.md")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "daily" / f"{DAY1}.md").is_file()


def test_export_creates_missing_out_dir(tmp_path: Path) -> None:
    nested = tmp_path / "vault" / "notes"
    result = _export(tmp_path, out=nested)
    assert result.exit_code == 0, result.output
    assert (nested / f"{DAY1}.md").is_file()


def test_export_uses_config_vault_when_no_out(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "configured-vault"
    config = tmp_path / "config.toml"
    config.write_text(f'vault_path = "{vault}"\n', encoding="utf-8")
    monkeypatch.setenv("YAK_TRACKER_CONFIG", str(config))

    result = runner.invoke(
        app,
        [
            "today",
            "--no-git",
            "--no-llm",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            DAY1,
            "--export",
            "md",
            # no --out: should fall back to the configured vault_path
        ],
    )
    assert result.exit_code == 0, result.output
    assert (vault / f"{DAY1}.md").is_file()


def test_export_without_destination_errors(tmp_path: Path, monkeypatch) -> None:
    # Empty config (no vault_path) and no --out → a clean usage error, no file.
    config = tmp_path / "config.toml"
    config.write_text("idle_gap = 25\n", encoding="utf-8")
    monkeypatch.setenv("YAK_TRACKER_CONFIG", str(config))

    result = runner.invoke(
        app,
        [
            "today",
            "--no-git",
            "--no-llm",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            DAY1,
            "--export",
            "md",
        ],
    )
    assert result.exit_code != 0
    assert "no export destination" in result.output


def test_export_rejects_unknown_format(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "today",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            DAY1,
            "--export",
            "pdf",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "only supports 'md'" in result.output


def test_export_and_json_are_mutually_exclusive(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "today",
            "--no-git",
            "--shell",
            "zsh",
            "--histfile",
            str(FIXTURES / "zsh_yakshave"),
            "--date",
            DAY1,
            "--export",
            "md",
            "--json",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
