"""Secret redaction: scrub tokens/keys from history before they leave the box.

Shell history is full of things you never meant to keep: an ``export
AWS_SECRET_ACCESS_KEY=…`` you ran once, a ``curl -H "Authorization: Bearer …"``,
a ``GITHUB_TOKEN=ghp_…`` pasted into a one-off command. yak-tracker's whole
pitch is *local-first privacy*, but "local" still includes the prompt we hand to
Ollama, the ``--json`` export you might drop into a notes vault, and the plain
``yak raw`` table you might paste into a bug report. So before any of that, we
run every collected command through a **redaction pass** that replaces the
secret-looking bits with a tag like ``«REDACTED:aws-secret-key»``.

Design choices:

* **On by default.** The safe thing should require no flag. ``--no-redact`` (and
  ``redact = false`` in config) exist as an escape hatch for users who *want*
  the raw text, but you have to opt out, never in.
* **Pattern-based, conservative, ordered.** Each rule is a named
  :class:`Pattern` with a regex and a replacement. Rules run in declaration
  order; the more specific *assignment* rules (``KEY=value``) run before the
  broad *high-entropy token* sweep so a known key gets a precise label instead
  of the generic one. We err toward redacting a bit too much rather than leaking
  — a false positive costs you a slightly less readable command; a false
  negative costs you a leaked credential.
* **Structure-preserving.** We keep the *shape* of the command (the variable
  name, the flag, the ``Bearer`` prefix) and only blank the value, so the
  narrated story still reads ("exported an AWS key, hit an API with a bearer
  token") without exposing the secret itself.
* **Never raises.** Like the collectors, a redaction failure must not crash a
  run; :func:`redact_text` is pure string→string and the helpers degrade to the
  original value if anything unexpected happens.

This module is intentionally dependency-light (stdlib :mod:`re` only) and has no
knowledge of the CLI, so it can be reused by any collector or exporter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .models import Event

__all__ = [
    "Pattern",
    "PATTERNS",
    "RedactionResult",
    "redact_text",
    "redact_event",
    "redact_events",
]


def _tag(name: str) -> str:
    """The placeholder a matched secret is replaced with."""
    return f"\u00abREDACTED:{name}\u00bb"


@dataclass(frozen=True, slots=True)
class Pattern:
    """A single named redaction rule.

    Attributes:
        name: Short identifier, also used in the placeholder tag
            (``«REDACTED:<name>»``) and in :attr:`RedactionResult.hits`.
        regex: Compiled pattern. If it defines a group named ``secret``, only
            that group is replaced (the surrounding structure is preserved);
            otherwise the whole match is replaced.
        description: Human-readable note for docs/tests.
    """

    name: str
    regex: re.Pattern[str]
    description: str

    def apply(self, text: str) -> tuple[str, int]:
        """Redact every match of this rule in ``text``.

        Returns the (possibly unchanged) text and the number of substitutions
        made. When the regex has a ``secret`` group, only that span is replaced
        so the command's shape survives.
        """
        count = 0

        def _sub(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            if "secret" in match.groupdict() and match.group("secret") is not None:
                start, end = match.span("secret")
                whole = match.group(0)
                offset = match.start()
                return whole[: start - offset] + _tag(self.name) + whole[end - offset :]
            return _tag(self.name)

        return self.regex.sub(_sub, text), count


# A reusable fragment: the value side of a `KEY=value` or `KEY value` pair, up to
# the next whitespace or shell separator. Quotes (optional) are consumed so the
# tag replaces the inner value but the assignment shape remains readable. The
# negative lookahead skips values we've *already* tagged, so a provider-specific
# rule (run earlier) keeps its precise label instead of being overwritten by the
# generic assignment label.
_ASSIGN_VALUE = r"""(?P<q>['"]?)(?!\u00abREDACTED:)(?P<secret>[^\s'";|&]+)(?P=q)"""

# Environment-variable / CLI-flag names that almost always carry a credential.
# Matched case-insensitively as a whole word so ``MY_API_KEY`` and ``api-key``
# both hit but ``keyboard`` does not.
_SENSITIVE_NAME = (
    r"[A-Za-z0-9_-]*"
    r"(?:SECRET|PASSWORD|PASSWD|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY"
    r"|CLIENT[_-]?SECRET|AUTH|CREDENTIAL|PASSPHRASE|SESSION[_-]?KEY)"
    r"[A-Za-z0-9_-]*"
)


# Order matters: provider-specific and assignment rules first (precise labels),
# then the generic high-entropy sweep last (catch-all). Earlier replacements tag
# the secret so later broad rules can't match the placeholder again.
PATTERNS: tuple[Pattern, ...] = (
    # --- Provider-specific high-confidence token shapes ----------------------
    Pattern(
        name="aws-access-key-id",
        regex=re.compile(r"\b(?P<secret>(?:AKIA|ASIA)[0-9A-Z]{16})\b"),
        description="AWS access key id (AKIA…/ASIA…).",
    ),
    Pattern(
        name="github-token",
        regex=re.compile(r"\b(?P<secret>gh[posu]_[A-Za-z0-9]{36,255})\b"),
        description="GitHub personal/OAuth/server/user token (ghp_/gho_/ghs_/ghu_).",
    ),
    Pattern(
        name="slack-token",
        regex=re.compile(r"\b(?P<secret>xox[baprs]-[A-Za-z0-9-]{10,})"),
        description="Slack token (xoxb-/xoxa-/xoxp-/xoxr-/xoxs-).",
    ),
    Pattern(
        name="google-api-key",
        regex=re.compile(r"\b(?P<secret>AIza[0-9A-Za-z_-]{35})\b"),
        description="Google API key (AIza…).",
    ),
    Pattern(
        name="openai-key",
        regex=re.compile(r"\b(?P<secret>sk-[A-Za-z0-9_-]{20,})"),
        description="OpenAI-style secret key (sk-…).",
    ),
    Pattern(
        name="stripe-key",
        regex=re.compile(r"\b(?P<secret>[rs]k_(?:live|test)_[A-Za-z0-9]{16,})\b"),
        description="Stripe secret/restricted key (sk_live_/rk_test_…).",
    ),
    Pattern(
        name="jwt",
        regex=re.compile(
            r"\b(?P<secret>eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b"
        ),
        description="JSON Web Token (three base64url segments).",
    ),
    # --- Header / URL credential patterns ------------------------------------
    Pattern(
        name="bearer-token",
        regex=re.compile(
            r"(?i)\b(?P<scheme>Bearer|Basic|Token)\s+(?P<secret>[A-Za-z0-9._~+/=-]{8,})"
        ),
        description="HTTP Authorization header value (Bearer/Basic/Token …).",
    ),
    Pattern(
        name="url-credentials",
        regex=re.compile(r"://[^\s/:@]+:(?P<secret>[^\s/]+?)@(?=[^\s/@]+(?:[:/]|\s|$))"),
        description="user:password@ embedded in a URL.",
    ),
    # --- Generic KEY=value / --flag value assignments ------------------------
    Pattern(
        name="env-assignment",
        regex=re.compile(
            rf"(?i)\b(?P<name>{_SENSITIVE_NAME})\s*=\s*{_ASSIGN_VALUE}"
        ),
        description="Sensitive NAME=value assignment (export FOO_TOKEN=…).",
    ),
    Pattern(
        name="flag-assignment",
        regex=re.compile(
            rf"(?i)(?P<flag>--?{_SENSITIVE_NAME})[=\s]+{_ASSIGN_VALUE}"
        ),
        description="Sensitive --flag value (--password hunter2, --token=…).",
    ),
    # --- Last-resort high-entropy sweep --------------------------------------
    # Long unbroken runs of token-ish characters are very likely keys. Kept last
    # and deliberately strict (length >=32, mixed) so prose/paths survive.
    Pattern(
        name="high-entropy",
        regex=re.compile(
            r"(?<![\w/.-])(?P<secret>(?=[A-Za-z0-9+/_-]*[A-Za-z])"
            r"(?=[A-Za-z0-9+/_-]*[0-9])[A-Za-z0-9+/_-]{32,})(?![\w/.=-])"
        ),
        description="Generic high-entropy token (>=32 chars, letters+digits).",
    ),
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Outcome of redacting a single string.

    Attributes:
        text: The redacted text (equal to the input when nothing matched).
        hits: Mapping of rule name → number of substitutions it made. Empty when
            the text was clean. Useful for "redacted N secrets" notices and tests.
    """

    text: str
    hits: dict[str, int]

    @property
    def total(self) -> int:
        """Total number of secrets redacted across all rules."""
        return sum(self.hits.values())

    @property
    def changed(self) -> bool:
        """True if any redaction was applied."""
        return bool(self.hits)


def redact_text(text: str, *, patterns: Iterable[Pattern] = PATTERNS) -> RedactionResult:
    """Redact secrets in ``text`` using ``patterns`` (defaults to :data:`PATTERNS`).

    Pure and total: returns a :class:`RedactionResult`; never raises. Rules are
    applied in order, so a provider-specific or assignment rule labels a secret
    before the broad high-entropy sweep can re-match it.
    """
    if not text:
        return RedactionResult(text=text, hits={})

    hits: dict[str, int] = {}
    redacted = text
    for pattern in patterns:
        redacted, n = pattern.apply(redacted)
        if n:
            hits[pattern.name] = hits.get(pattern.name, 0) + n
    return RedactionResult(text=redacted, hits=hits)


def redact_event(event: Event, *, patterns: Iterable[Pattern] = PATTERNS) -> Event:
    """Return a copy of ``event`` with secrets scrubbed from its ``cmd``.

    The timestamp, cwd, and source are preserved untouched; only the command
    text is rewritten. If nothing matched, the original event is returned as-is
    (no needless copy).
    """
    result = redact_text(event.cmd, patterns=patterns)
    if not result.changed:
        return event
    return replace(event, cmd=result.text)


def redact_events(
    events: Iterable[Event], *, patterns: Iterable[Pattern] = PATTERNS
) -> list[Event]:
    """Redact a whole iterable of events (see :func:`redact_event`)."""
    return [redact_event(e, patterns=patterns) for e in events]
