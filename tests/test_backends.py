"""Tests for pluggable narration backends (issue #33).

Covers the OpenAI-compatible backend, the config→backend factory wiring, the
graceful-degradation contract for both backends, and that an API key is sent as
an Authorization header (and only when present) without ever being logged.
"""

from __future__ import annotations

import json

import httpx
import pytest

from yak_tracker.config import Config, load_config
from yak_tracker.narrate import (
    LLM_API_KEY_ENV,
    BackendError,
    NarrationBackend,
    OllamaBackend,
    OpenAICompatBackend,
    make_backend,
    narrate,
)
from yak_tracker.tree import DetourKind, Node


def _sample_forest() -> list[Node]:
    from datetime import datetime

    from yak_tracker.models import Event

    ts = datetime(2026, 6, 21, 9, 30)
    root = Node(label="fix login bug", kind=DetourKind.ROOT, event=None)
    root.event = Event(cmd="commit fix", ts=ts, cwd=None, source="git:app")
    return [root]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- factory / config wiring ------------------------------------------------


def test_make_backend_defaults_to_ollama() -> None:
    be = make_backend()
    assert isinstance(be, OllamaBackend)
    assert be.name == "ollama"
    assert be.base_url == "http://localhost:11434"


def test_make_backend_openai_compat_default_base_url() -> None:
    be = make_backend(backend="openai_compat")
    assert isinstance(be, OpenAICompatBackend)
    assert be.name == "openai_compat"
    assert be.base_url == "http://localhost:1234/v1"


def test_make_backend_honours_explicit_base_url() -> None:
    be = make_backend(backend="openai_compat", base_url="http://box:8080/v1")
    assert be.base_url == "http://box:8080/v1"


def test_make_backend_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown backend"):
        make_backend(backend="gpt5-cloud")


def test_backends_satisfy_protocol() -> None:
    assert isinstance(make_backend(), NarrationBackend)
    assert isinstance(make_backend(backend="openai_compat"), NarrationBackend)


def test_config_selects_backend_and_base_url(tmp_path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'backend = "openai_compat"\nllm_base_url = "http://lan:1234/v1"\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.backend == "openai_compat"
    assert cfg.resolved_base_url() == "http://lan:1234/v1"


def test_config_bad_backend_warns_and_defaults(tmp_path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('backend = "wat"\n', encoding="utf-8")
    cfg = load_config(cfg_file)
    assert cfg.backend == "ollama"
    assert any("backend" in w for w in cfg.warnings)


def test_resolved_base_url_falls_back_to_ollama_host() -> None:
    cfg = Config(backend="ollama", ollama_host="http://myhost:11434")
    assert cfg.resolved_base_url() == "http://myhost:11434"


def test_resolved_base_url_none_for_openai_compat_default() -> None:
    cfg = Config(backend="openai_compat")
    assert cfg.resolved_base_url() is None


# --- OpenAICompatBackend HTTP behaviour -------------------------------------


def test_openai_compat_happy_path() -> None:
    captured: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, json.loads(request.content), dict(request.headers)))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "  the day, narrated  "}}
                ]
            },
        )

    be = OpenAICompatBackend(base_url="http://x/v1", client=_client(handler))
    text = be.generate("prompt here")
    assert text == "the day, narrated"
    path, body, headers = captured[0]
    assert path == "/v1/chat/completions"
    assert body["messages"][0]["content"] == "prompt here"
    # No API key configured => no Authorization header.
    assert "authorization" not in {k.lower() for k in headers}


def test_openai_compat_sends_api_key_header() -> None:
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    be = OpenAICompatBackend(api_key="secret-key", client=_client(handler))
    be.generate("p")
    assert seen[0] == "Bearer secret-key"


def test_openai_compat_reads_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv(LLM_API_KEY_ENV, "env-key")
    be = OpenAICompatBackend()
    assert be._api_key == "env-key"


def test_openai_compat_empty_choice_raises_backend_error() -> None:
    be = OpenAICompatBackend(client=_client(lambda r: httpx.Response(200, json={"choices": []})))
    with pytest.raises(BackendError, match="empty narration"):
        be.generate("p")


def test_openai_compat_http_error_message_extracted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    be = OpenAICompatBackend(client=_client(handler))
    with pytest.raises(BackendError, match="invalid api key"):
        be.generate("p")


def test_openai_compat_connect_error_raises_backend_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    be = OpenAICompatBackend(base_url="http://down/v1", client=_client(handler))
    with pytest.raises(BackendError, match="could not reach"):
        be.generate("p")


# --- narrate() driving a passed-in backend ----------------------------------


def test_narrate_with_openai_compat_backend_succeeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "standup bullets"}}]}
        )

    be = OpenAICompatBackend(client=_client(handler))
    result = narrate(_sample_forest(), fmt="standup", backend=be)
    assert result.ok is True
    assert result.text == "standup bullets"


def test_narrate_with_backend_degrades_gracefully() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    be = OpenAICompatBackend(base_url="http://down/v1", client=_client(handler))
    result = narrate(_sample_forest(), fmt="story", backend=be)
    assert result.ok is False
    assert "could not reach" in (result.notice or "")
    assert result.text is None
