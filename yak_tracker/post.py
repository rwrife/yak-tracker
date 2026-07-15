"""Deliver a rendered day to Slack or Discord via an incoming webhook.

This is the ``yak post`` surface: it takes a plain-text body (a narrated
standup/story/learning, or a deterministic outline fallback) and pushes it to a
Slack or Discord channel through an *incoming webhook* URL.

Local-first still holds: narration runs against your local backend and only the
final, redacted text ever leaves the box — and only when you explicitly run the
command. The webhook URL is never hardcoded; it comes from config (the
``[post]`` table) or ``--webhook``.

The two platforms want different JSON shapes for a simple message:

* **Slack** incoming webhooks accept ``{"text": "..."}``.
* **Discord** incoming webhooks accept ``{"content": "..."}`` (2000-char cap).

Both return a 2xx on success; anything else is surfaced as a :class:`PostError`
with the status and (truncated) response body so failures are visible, not
silent.
"""

from __future__ import annotations

from typing import Any

import httpx

__all__ = [
    "VALID_PLATFORMS",
    "DISCORD_CONTENT_LIMIT",
    "PostError",
    "build_payload",
    "post_to_webhook",
]

# Platforms we know how to shape a payload for.
VALID_PLATFORMS: tuple[str, ...] = ("slack", "discord")

# Discord rejects message content longer than 2000 characters. We truncate with
# a visible marker rather than letting the API 400 on us.
DISCORD_CONTENT_LIMIT: int = 2000

# Default seconds to wait on the webhook HTTP call.
DEFAULT_TIMEOUT: float = 15.0


class PostError(RuntimeError):
    """Raised when a webhook delivery fails (bad status, network, or config).

    Carries an optional ``status`` (HTTP code) and truncated ``detail`` (the
    response body) so the CLI can print an actionable message.
    """

    def __init__(
        self, message: str, *, status: int | None = None, detail: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


def _truncate_for_discord(text: str) -> str:
    """Clamp ``text`` to Discord's content limit with a visible marker."""
    if len(text) <= DISCORD_CONTENT_LIMIT:
        return text
    marker = "\n… (truncated)"
    keep = DISCORD_CONTENT_LIMIT - len(marker)
    return text[:keep].rstrip() + marker


def build_payload(platform: str, body: str) -> dict[str, Any]:
    """Build the JSON payload for ``platform`` carrying ``body``.

    Args:
        platform: ``"slack"`` or ``"discord"``.
        body: The message text to deliver.

    Returns:
        A dict ready to be sent as JSON.

    Raises:
        PostError: If ``platform`` is not a known platform.
    """
    if platform not in VALID_PLATFORMS:
        raise PostError(
            f"unknown platform {platform!r}; expected one of "
            f"{', '.join(VALID_PLATFORMS)}"
        )
    if platform == "slack":
        return {"text": body}
    # Discord: cap content length.
    return {"content": _truncate_for_discord(body)}


def post_to_webhook(
    platform: str,
    webhook_url: str,
    body: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> None:
    """POST ``body`` to ``webhook_url`` shaped for ``platform``.

    Args:
        platform: ``"slack"`` or ``"discord"`` (selects the payload shape).
        webhook_url: The incoming-webhook URL to POST to.
        body: The message text.
        timeout: Seconds to wait on the request.
        client: Optional pre-built :class:`httpx.Client` (used by tests to mock
            the transport). When ``None`` a client is created per call.

    Raises:
        PostError: On a missing URL, a non-2xx response, or a transport error.
    """
    if not webhook_url:
        raise PostError(
            f"no webhook URL for {platform}; set it in the [post] config table "
            "or pass --webhook"
        )

    payload = build_payload(platform, body)

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.post(webhook_url, json=payload)
    except httpx.HTTPError as exc:  # network/DNS/timeout
        raise PostError(f"could not reach {platform} webhook: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    if not (200 <= response.status_code < 300):
        detail = (response.text or "").strip()[:300]
        raise PostError(
            f"{platform} webhook returned HTTP {response.status_code}",
            status=response.status_code,
            detail=detail or None,
        )
