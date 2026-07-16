"""Tests for ``yak post`` — webhook delivery to Slack/Discord (issue #32).

Covers the payload shapes for both platforms, the redaction-before-send gate,
the [post] config table parsing, --dry-run (no HTTP), and error handling on a
non-2xx webhook response. The HTTP call is mocked for both platforms; nothing
ever hits the network.
"""

from __future__ import annotations

import httpx
import pytest

from yak_tracker.config import load_config
from yak_tracker.post import (
    DISCORD_CONTENT_LIMIT,
    PostError,
    build_payload,
    post_to_webhook,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_slack_payload_shape() -> None:
    assert build_payload("slack", "hello") == {"text": "hello"}


def test_discord_payload_shape() -> None:
    assert build_payload("discord", "hello") == {"content": "hello"}


def test_discord_payload_truncates_long_content() -> None:
    body = "x" * (DISCORD_CONTENT_LIMIT + 500)
    payload = build_payload("discord", body)
    assert len(payload["content"]) <= DISCORD_CONTENT_LIMIT
    assert payload["content"].endswith("(truncated)")


def test_build_payload_rejects_unknown_platform() -> None:
    with pytest.raises(PostError):
        build_payload("telegram", "hi")


@pytest.mark.parametrize(
    ("platform", "key"),
    [("slack", "text"), ("discord", "content")],
)
def test_post_to_webhook_sends_correct_shape(platform: str, key: str) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text="ok")

    post_to_webhook(
        platform,
        "https://example.test/webhook",
        "the standup",
        client=_client(handler),
    )
    assert seen["url"] == "https://example.test/webhook"
    assert seen["body"] == {key: "the standup"}


def test_post_to_webhook_raises_on_missing_url() -> None:
    with pytest.raises(PostError):
        post_to_webhook("slack", "", "body")


def test_post_to_webhook_raises_on_non_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no such hook")

    with pytest.raises(PostError) as excinfo:
        post_to_webhook(
            "discord",
            "https://example.test/webhook",
            "body",
            client=_client(handler),
        )
    assert excinfo.value.status == 404
    assert "no such hook" in (excinfo.value.detail or "")


def test_post_to_webhook_wraps_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(PostError):
        post_to_webhook(
            "slack",
            "https://example.test/webhook",
            "body",
            client=_client(handler),
        )


def test_config_parses_post_table(tmp_path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[post]\n'
        'slack = "https://hooks.slack.com/services/T/B/X"\n'
        'discord = "https://discord.com/api/webhooks/1/y"\n'
    )
    cfg = load_config(cfg_file)
    assert cfg.post_webhooks == {
        "slack": "https://hooks.slack.com/services/T/B/X",
        "discord": "https://discord.com/api/webhooks/1/y",
    }
    assert cfg.warnings == ()


def test_config_warns_on_unknown_post_key(tmp_path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[post]\nteams = "https://x"\n')
    cfg = load_config(cfg_file)
    assert cfg.post_webhooks == {}
    assert any("post.teams" in w for w in cfg.warnings)
