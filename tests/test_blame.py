"""Tests for ``yak blame`` — per-file detour reflection (issue #26).

Builds a real throwaway git repo with controlled commit times so file-touch
collection and path resolution can be asserted deterministically, and exercises
the shell-reference filtering, JSON shape, and no-Ollama fallback.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yak_tracker.blame import (
    BlameError,
    blame_to_dict,
    build_blame,
    resolve_target,
    shell_events_touching,
)
from yak_tracker.cli import app
from yak_tracker.models import Event

runner = CliRunner()

# Anchor commits a couple of days back so they land inside the default lookback.
BASE = (datetime.now() - timedelta(days=2)).replace(microsecond=0)


def _base_env() -> dict[str, str]:
    return dict(os.environ)


def _git(repo: Path, *args: str, when: datetime | None = None) -> None:
    env = {
        "GIT_AUTHOR_NAME": "Test Yak",
        "GIT_AUTHOR_EMAIL": "yak@example.com",
        "GIT_COMMITTER_NAME": "Test Yak",
        "GIT_COMMITTER_EMAIL": "yak@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    if when is not None:
        iso = when.strftime("%Y-%m-%d %H:%M:%S")
        env["GIT_AUTHOR_DATE"] = iso
        env["GIT_COMMITTER_DATE"] = iso
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**_base_env(), **env},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo where ``target.py`` is touched by 3 commits and ``other.py`` by 1."""
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")

    (r / "target.py").write_text("v1\n")
    _git(r, "add", "target.py")
    _git(r, "commit", "-q", "-m", "add target.py", when=BASE)

    (r / "other.py").write_text("x\n")
    _git(r, "add", "other.py")
    _git(r, "commit", "-q", "-m", "add other.py", when=BASE + timedelta(minutes=5))

    (r / "target.py").write_text("v2\n")
    _git(r, "add", "target.py")
    _git(r, "commit", "-q", "-m", "tweak target.py", when=BASE + timedelta(hours=2))

    (r / "target.py").write_text("v3\n")
    _git(r, "add", "target.py")
    _git(
        r,
        "commit",
        "-q",
        "-m",
        "tweak target.py again",
        when=BASE + timedelta(hours=2, minutes=3),
    )
    return r


# --- path resolution -------------------------------------------------------


def test_resolve_target_in_repo(repo: Path) -> None:
    res = resolve_target(repo / "target.py", repos=[repo])
    assert res.repo == repo.resolve()
    assert res.relpath == Path("target.py")
    assert res.label == "proj"


def test_resolve_target_relative_path(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo)
    res = resolve_target("target.py", repos=[repo])
    assert res.relpath == Path("target.py")


def test_resolve_target_not_in_repo_raises(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "loose.txt"
    outside.write_text("hi\n")
    with pytest.raises(BlameError) as exc:
        resolve_target(outside, repos=[repo])
    assert "not under any tracked git repo" in str(exc.value)


def test_resolve_target_non_git_dir_raises(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "f.py").write_text("x\n")
    with pytest.raises(BlameError):
        resolve_target(plain / "f.py", repos=[plain])


# --- git file-touch filtering ----------------------------------------------


def test_build_blame_matches_only_touching_commits(repo: Path) -> None:
    blame = build_blame(repo / "target.py", repos=[repo], since="30.days.ago")
    # target.py was touched by 3 commits; other.py's commit must be excluded.
    assert blame.touch_count == 3
    subjects = [e.cmd for e in blame.events]
    assert all("target.py" in s for s in subjects)
    assert not any("other.py" in s for s in subjects)


def test_build_blame_sessionizes_by_idle_gap(repo: Path) -> None:
    # First touch at BASE; next two ~2h later → two sessions with a 25m gap.
    blame = build_blame(repo / "target.py", repos=[repo], since="30.days.ago", idle_gap=25)
    assert blame.session_count == 2
    assert blame.sessions[0].count == 1
    assert blame.sessions[1].count == 2


def test_headline_pluralization(repo: Path) -> None:
    blame = build_blame(repo / "target.py", repos=[repo], since="30.days.ago")
    assert blame.headline.startswith("target.py — touched in 2 sessions")


# --- shell reference filtering ---------------------------------------------


def test_shell_events_touching_matches_and_excludes(repo: Path) -> None:
    res = resolve_target(repo / "target.py", repos=[repo])
    ts = BASE
    events = [
        Event(cmd="vim target.py", ts=ts, cwd=None, source="shell:zsh"),
        Event(cmd="pytest tests/test_other.py", ts=ts, cwd=None, source="shell:zsh"),
        Event(cmd="cat ./target.py", ts=ts, cwd=None, source="shell:bash"),
        Event(cmd="echo mytarget.py", ts=ts, cwd=None, source="shell:zsh"),
        Event(cmd="git commit -m x", ts=ts, cwd=None, source="git:proj"),
    ]
    matched = shell_events_touching(events, res)
    cmds = [e.cmd for e in matched]
    assert "vim target.py" in cmds
    assert "cat ./target.py" in cmds
    # A different file that merely contains the name must NOT match.
    assert "echo mytarget.py" not in cmds
    # Non-shell events are ignored here.
    assert "git commit -m x" not in cmds
    # Matched events are re-sourced with the shell-ref marker.
    assert all(e.source.startswith("shell-ref:") for e in matched)


def test_build_blame_merges_shell_and_git(repo: Path) -> None:
    shell = [
        Event(
            cmd="vim target.py",
            ts=BASE + timedelta(minutes=1),
            cwd=None,
            source="shell:zsh",
        )
    ]
    blame = build_blame(
        repo / "target.py", repos=[repo], shell_events=shell, since="30.days.ago"
    )
    sources = {e.source.split(":")[0] for e in blame.events}
    assert "git-touch" in sources
    assert "shell-ref" in sources


# --- JSON shape ------------------------------------------------------------


def test_blame_to_dict_shape(repo: Path) -> None:
    blame = build_blame(repo / "target.py", repos=[repo], since="30.days.ago")
    doc = blame_to_dict(blame)
    assert doc["relpath"] == "target.py"
    assert doc["repo"] == "proj"
    assert doc["touch_count"] == 3
    assert doc["session_count"] == len(doc["sessions"])
    first = doc["sessions"][0]["events"][0]
    assert set(first) == {"ts", "source", "cmd"}


# --- CLI (no-Ollama fallback + errors) -------------------------------------


def test_cli_blame_prints_timeline_offline(repo: Path) -> None:
    result = runner.invoke(
        app, ["blame", str(repo / "target.py"), "--repo", str(repo), "--no-llm"]
    )
    assert result.exit_code == 0, result.stdout
    assert "target.py — touched in" in result.stdout


def test_cli_blame_json(repo: Path) -> None:
    result = runner.invoke(
        app, ["blame", str(repo / "target.py"), "--repo", str(repo), "--json"]
    )
    assert result.exit_code == 0, result.stdout
    doc = json.loads(result.stdout)
    assert doc["touch_count"] == 3


def test_cli_blame_not_in_repo_errors(repo: Path, tmp_path: Path) -> None:
    loose = tmp_path / "loose.txt"
    loose.write_text("x\n")
    result = runner.invoke(
        app,
        ["blame", str(loose), "--repo", str(repo), "--no-llm"],
        catch_exceptions=True,
    )
    # Bad path → non-zero exit (typer surfaces the BlameError as a BadParameter).
    assert result.exit_code != 0
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "not under any tracked git repo" in combined
