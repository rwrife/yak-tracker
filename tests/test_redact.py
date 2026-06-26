"""Tests for the secret-redaction pass (issue #8).

Covers the individual rules in :data:`yak_tracker.redact.PATTERNS`, the
structure-preserving behaviour, the ``RedactionResult`` accounting, and the
``Event``-level helpers. The collector- and CLI-level integration (redaction on
by default, ``--no-redact`` / ``redact = false`` opt-out) lives in the shell
collector and config tests.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from yak_tracker.models import Event
from yak_tracker.redact import (
    PATTERNS,
    RedactionResult,
    redact_event,
    redact_events,
    redact_text,
)

TAG_PREFIX = "\u00abREDACTED:"

# Synthetic tokens assembled at runtime so no complete provider-pattern literal
# sits in source (which would trip GitHub push protection / secret scanning).
# These are fake by construction — the point is only to exercise the regexes.
GHP = "ghp" + "_" + "abcdefabcdefabcdefabcdefabcdefabcdef12"
GHP_ONES = "ghp" + "_" + ("a" * 36)
AKIA = "AKIA" + "IOSFODNN7EXAMPLE"


def _is_clean(text: str) -> bool:
    """True if no redaction tag is present."""
    return TAG_PREFIX not in text


# --- per-rule positive cases ---------------------------------------------


@pytest.mark.parametrize(
    ("template", "token", "rule"),
    [
        (
            "export AWS_SECRET_ACCESS_KEY={tok}",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "env-assignment",
        ),
        (
            "export GITHUB_TOKEN={tok}",
            # assembled so no full ghp_ literal sits in source
            "ghp" + "_" + "1234567890abcdef1234567890abcdef1234",
            "github-token",
        ),
        (
            'curl -H "Authorization: Bearer {tok}"',
            "abc123XYZ.tok_value-9876543210",
            "bearer-token",
        ),
        (
            "aws s3 ls --access-key {tok}",
            "AKIA" + "IOSFODNN7EXAMPLE",
            "aws-access-key-id",
        ),
        (
            "psql postgres://admin:{tok}@db.example.com:5432/app",
            "s3cr3tPass",
            "url-credentials",
        ),
        (
            "stripe keys --key {tok}",
            "sk" + "_live_" + "4eC39HqLyjWDarjtT1zdp7dc12345",
            "stripe-key",
        ),
        (
            "deploy --client-secret {tok}",
            "hunter2hunter2hunter2",
            "flag-assignment",
        ),
        (
            "set GOOGLE_API_KEY={tok}",
            "AIza" + "SyA1234567890abcdefghijklmnopqrstuv",
            "google-api-key",
        ),
        (
            "slack post --token {tok}",
            "xox" + "b-" + "1234567890-abcdefghijklmnop",
            "slack-token",
        ),
        (
            "openai --key {tok}",
            "sk" + "-proj-" + "abcdefghij1234567890ABCDEFGHIJ",
            "openai-key",
        ),
    ],
)
def test_rule_redacts_and_does_not_leak(template: str, token: str, rule: str) -> None:
    command = template.format(tok=token)
    result = redact_text(command)
    assert result.changed, f"expected a redaction in: {command}"
    assert rule in result.hits, f"expected rule {rule!r}; got {result.hits}"
    assert token not in result.text, f"secret leaked through: {result.text}"
    assert TAG_PREFIX in result.text


def test_jwt_is_redacted() -> None:
    # Built from segments + runtime-joined dots so no full a.b.c JWT literal sits
    # in source (avoids secret-scanning false positives on the test itself).
    seg1 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    seg2 = "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    seg3 = "dozjgNryP4J3jVmNHl0w5N"
    jwt = ".".join([seg1, seg2, seg3])
    result = redact_text(f"AUTH_JWT={jwt}")
    assert result.changed
    assert jwt not in result.text


# --- structure preservation ----------------------------------------------


def test_assignment_keeps_variable_name() -> None:
    result = redact_text(f"export GITHUB_TOKEN={GHP_ONES}")
    # The variable name and the `export ... =` shape survive; only value is gone.
    assert result.text.startswith("export GITHUB_TOKEN=")
    assert result.text.endswith("\u00bb")
    assert "ghp" + "_" not in result.text


def test_bearer_keeps_scheme_word() -> None:
    result = redact_text("Authorization: Bearer s0m3S3cr3tT0k3nValue123")
    assert "Bearer " in result.text
    assert "s0m3S3cr3t" not in result.text


def test_url_credentials_keep_host() -> None:
    result = redact_text("psql postgres://admin:p@sswithat@db.internal:5432/app")
    # Host and the rest of the URL must remain intact and reachable.
    assert "@db.internal:5432/app" in result.text
    assert "://admin:" in result.text
    assert "p@sswithat" not in result.text


# --- specificity / ordering ----------------------------------------------


def test_provider_label_wins_over_generic_assignment() -> None:
    # A known GitHub token assigned to a sensitive var should be labelled by the
    # specific provider rule, not the generic env-assignment sweep.
    result = redact_text(f"export GITHUB_TOKEN={GHP}")
    assert "github-token" in result.hits
    assert "env-assignment" not in result.hits


def test_high_entropy_catch_all_for_unnamed_blob() -> None:
    # A long random token with no telltale prefix and no variable name, passed as
    # a bare argument, should still be caught by the last-resort high-entropy
    # rule. (Tokens sitting inside a / path are deliberately left alone to avoid
    # mangling real file paths.)
    blob = "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0Lm3Nb"
    result = redact_text(f"myql --value {blob}")
    assert result.changed
    assert blob not in result.text
    assert "high-entropy" in result.hits


# --- false-positive guards (clean commands stay clean) -------------------


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "npm install && npm run build",
        "cd ~/code/yak-tracker && ls -la",
        "git commit -m 'fix the login bug for real this time'",
        "ssh user@host 'tail -f /var/log/syslog'",
        "git clone https://github.com/rwrife/yak-tracker.git",
        "ls -la /usr/local/lib/python3.12/site-packages",
        "docker run -p 8080:8080 myimage:latest",
        "echo $HOME && export PATH=$PATH:/opt/bin",
        "pytest tests/ -q --maxfail=1",
    ],
)
def test_clean_commands_are_untouched(command: str) -> None:
    result = redact_text(command)
    assert not result.changed, f"unexpected redaction in: {command} -> {result.text}"
    assert result.text == command
    assert _is_clean(result.text)


def test_empty_string_is_noop() -> None:
    result = redact_text("")
    assert result.text == ""
    assert not result.changed
    assert result.total == 0


# --- RedactionResult accounting ------------------------------------------


def test_result_counts_multiple_hits() -> None:
    command = f"export GITHUB_TOKEN={GHP_ONES} && aws configure --access-key {AKIA}"
    result = redact_text(command)
    assert result.total >= 2
    assert result.changed
    assert "ghp" + "_" not in result.text
    assert AKIA not in result.text


def test_patterns_have_unique_names() -> None:
    names = [p.name for p in PATTERNS]
    assert len(names) == len(set(names)), "duplicate redaction rule names"


# --- Event-level helpers --------------------------------------------------


def _event(cmd: str) -> Event:
    return Event(cmd=cmd, ts=datetime(2026, 6, 25, 9, 30), cwd=None, source="shell:zsh")


def test_redact_event_scrubs_cmd_preserves_metadata() -> None:
    token = "sk" + "-" + "abcdefghij1234567890ABCDEFGH"
    original = _event(f"export API_KEY={token}")
    cleaned = redact_event(original)
    assert cleaned is not original
    assert token not in cleaned.cmd
    # Everything except the command is preserved.
    assert cleaned.ts == original.ts
    assert cleaned.cwd == original.cwd
    assert cleaned.source == original.source


def test_redact_event_returns_same_object_when_clean() -> None:
    original = _event("git push origin main")
    cleaned = redact_event(original)
    # No needless copy when nothing matched.
    assert cleaned is original


def test_redact_events_maps_over_iterable() -> None:
    events = [
        _event("git status"),
        _event(f"export TOKEN={GHP}"),
        _event("npm test"),
    ]
    cleaned = redact_events(events)
    assert len(cleaned) == 3
    assert cleaned[0] is events[0]  # clean → unchanged identity
    assert "ghp" + "_" not in cleaned[1].cmd
    assert cleaned[2] is events[2]


def test_result_is_frozen() -> None:
    result = RedactionResult(text="x", hits={})
    with pytest.raises(AttributeError):
        result.text = "y"  # type: ignore[misc]
