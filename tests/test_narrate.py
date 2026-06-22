"""Tests for the Ollama narration layer (M5), using a mocked HTTP transport."""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest

from yak_tracker.narrate import (
    PERSONAS,
    Narration,
    build_outline,
    build_prompt,
    narrate,
)
from yak_tracker.tree import DetourKind, Node


def _sample_forest() -> list[Node]:
    """A tiny two-node tree: an intention with one install detour."""
    ts = datetime(2026, 6, 21, 9, 30)
    root = Node(
        label="fix login bug",
        kind=DetourKind.ROOT,
        event=None,
    )
    # Give the root a timestamp via a stub event-less node by attaching children.
    child = Node(label="npm install left-pad", kind=DetourKind.INSTALL, event=None)
    root.children.append(child)
    # Patch in timestamps through a lightweight fake event.
    from yak_tracker.models import Event

    root.event = Event(cmd="commit abc fix login bug", ts=ts, cwd=None, source="git:app")
    child.event = Event(
        cmd="npm install left-pad",
        ts=ts.replace(minute=35),
        cwd=None,
        source="shell:zsh",
    )
    return [root]


def _client_returning(payload: dict, *, capture: list | None = None) -> httpx.Client:
    """Build an httpx.Client whose transport returns ``payload`` as JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- outline / prompt -------------------------------------------------------


def test_build_outline_is_deterministic_and_indented() -> None:
    forest = _sample_forest()
    outline = build_outline(forest, date_label="2026-06-21")
    assert "Coding activity for 2026-06-21:" in outline
    assert "Intention: fix login bug" in outline
    assert "install/dependency detour" in outline
    # The detour is indented beneath the intention.
    assert "  - [09:35]" in outline
    # Deterministic: same input → same output.
    assert build_outline(forest, date_label="2026-06-21") == outline


def test_build_outline_empty_forest() -> None:
    assert build_outline([]) == "(no recorded activity)"


def test_build_prompt_includes_persona() -> None:
    forest = _sample_forest()
    for fmt in PERSONAS:
        prompt = build_prompt(forest, fmt=fmt)
        assert PERSONAS[fmt][:20] in prompt
        assert "ACTIVITY OUTLINE" in prompt


def test_build_prompt_rejects_bad_format() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        build_prompt(_sample_forest(), fmt="sonnet")


# --- narrate(): success -----------------------------------------------------


def test_narrate_success_returns_text() -> None:
    captured: list = []
    client = _client_returning({"response": "  You set out to fix login.  "}, capture=captured)
    result = narrate(
        _sample_forest(),
        fmt="story",
        model="llama3",
        client=client,
    )
    assert isinstance(result, Narration)
    assert result.ok is True
    assert result.text == "You set out to fix login."  # stripped
    assert result.format == "story"
    assert result.model == "llama3"
    assert result.notice is None
    # The request actually carried our prompt + model + stream=False.
    assert len(captured) == 1
    sent = captured[0]
    assert sent["model"] == "llama3"
    assert sent["stream"] is False
    assert "fix login bug" in sent["prompt"]


def test_narrate_each_persona_sent() -> None:
    for fmt in ("standup", "story", "learning"):
        captured: list = []
        client = _client_returning({"response": f"{fmt} text"}, capture=captured)
        result = narrate(_sample_forest(), fmt=fmt, client=client)
        assert result.ok
        assert result.format == fmt
        assert PERSONAS[fmt][:15] in captured[0]["prompt"]


# --- narrate(): graceful fallbacks -----------------------------------------


def test_narrate_empty_forest_is_not_ok() -> None:
    result = narrate([], fmt="story", client=_client_returning({"response": "x"}))
    assert result.ok is False
    assert result.text is None
    assert "nothing to narrate" in (result.notice or "")


def test_narrate_bad_format_is_not_ok() -> None:
    result = narrate(_sample_forest(), fmt="limerick", client=_client_returning({"r": 1}))
    assert result.ok is False
    assert "unknown format" in (result.notice or "")


def test_narrate_connect_error_falls_back() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(boom))
    result = narrate(_sample_forest(), fmt="story", host="http://localhost:11434", client=client)
    assert result.ok is False
    assert result.text is None
    assert "could not reach Ollama" in (result.notice or "")


def test_narrate_timeout_falls_back() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    client = httpx.Client(transport=httpx.MockTransport(slow))
    result = narrate(_sample_forest(), fmt="story", timeout=5, client=client)
    assert result.ok is False
    assert "timed out" in (result.notice or "")


def test_narrate_http_error_surfaces_ollama_message() -> None:
    # Ollama's classic "model not found" 404 with a JSON error body.
    client = _client_returning_status(404, {"error": "model 'llama3' not found"})
    result = narrate(_sample_forest(), fmt="story", client=client)
    assert result.ok is False
    assert "HTTP 404" in (result.notice or "")
    assert "not found" in (result.notice or "")


def test_narrate_empty_response_falls_back() -> None:
    client = _client_returning({"response": "   "})
    result = narrate(_sample_forest(), fmt="story", client=client)
    assert result.ok is False
    assert "empty narration" in (result.notice or "")


def test_narrate_non_json_body_falls_back() -> None:
    def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    client = httpx.Client(transport=httpx.MockTransport(garbage))
    result = narrate(_sample_forest(), fmt="story", client=client)
    assert result.ok is False
    assert "could not be parsed" in (result.notice or "")


def _client_returning_status(status: int, payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))
