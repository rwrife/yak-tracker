"""Narration: turn yak-shaving trees into prose via a local LLM (PLAN.md M5).

This is the LLM seam of the pipeline. It takes the :class:`~yak_tracker.tree.Node`
forest produced by M4, serialises it into a compact, deterministic text outline,
wraps that in a persona-specific prompt, and asks a **local Ollama** server to
narrate it. The privacy stance is the whole point: raw shell history is
summarised locally and sent only to ``localhost`` (or wherever the user pointed
``ollama_host``) — never to a cloud API.

Three personas (``--format``) reuse the same outline with different framing:

* **standup** — terse, shippable bullet points for the morning sync.
* **story** — the funny narrative of the day's rabbit holes.
* **learning** — what you learned, to fight AI-coding skill rot.

**Graceful degradation is a feature, not an afterthought.** If Ollama is not
running, errors, or returns nothing, narration never raises: it returns a
:class:`Narration` whose ``ok`` is ``False`` and ``text`` is ``None``, plus a
human ``notice`` explaining why. The CLI then falls back to the raw tree render,
so ``yak today`` always produces *something* useful even offline.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from .config import VALID_BACKENDS, VALID_FORMATS
from .tree import DetourKind, Node

__all__ = [
    "Narration",
    "BackendError",
    "NarrationBackend",
    "OllamaBackend",
    "OpenAICompatBackend",
    "make_backend",
    "build_outline",
    "build_prompt",
    "narrate",
    "narrate_blame",
    "build_blame_outline",
    "PERSONAS",
]

# Environment variable holding the API key for non-Ollama (openai_compat)
# backends. Read lazily so it is never captured in config or logged; only the
# Authorization header ever sees it.
LLM_API_KEY_ENV = "YAK_LLM_API_KEY"

# Low temperature everywhere: we want a faithful retelling of the outline, not
# creative embellishment that invents commands the user never ran.
_TEMPERATURE = 0.4


# Per-persona system framing. The shared outline (below) is appended to whichever
# of these the user picked. Kept short and directive — small local models follow
# concise instructions better than flowery ones.
PERSONAS: dict[str, str] = {
    "standup": (
        "You are writing a developer's daily standup. Using ONLY the activity "
        "outline below, produce a short bulleted list of what was actually "
        "worked on and shipped. Group related work; lead with outcomes, not "
        "commands. Keep it to 3-6 bullets. No preamble, no sign-off."
    ),
    "story": (
        "You are a witty narrator recounting a developer's coding day as a "
        "'yak-shaving' saga: one intention that spiralled into rabbit holes. "
        "Using ONLY the activity outline below, tell the story in 1-2 short "
        "paragraphs — the detours (installs, forced fixes, branch hops) are the "
        "fun part. Be concrete and a little dry; don't invent facts."
    ),
    "learning": (
        "You are a reflective engineering coach. Using ONLY the activity outline "
        "below, write a short 'what I learned today' log: 3-5 bullets on the "
        "skills, tools, and gotchas implied by the work and its detours. Focus "
        "on durable lessons, not a play-by-play. No preamble."
    ),
}

# Glyph-free kind labels for the text outline we feed the model (the rich glyphs
# in render.py are for humans; the LLM gets plain words).
_KIND_WORD: dict[str, str] = {
    DetourKind.ROOT: "intention",
    DetourKind.STEP: "step",
    DetourKind.INSTALL: "install/dependency detour",
    DetourKind.ERROR_FIX: "forced fix / cleanup",
    DetourKind.DIR_CHANGE: "context switch",
    DetourKind.BRANCH: "branch switch",
}


@dataclass(frozen=True, slots=True)
class Narration:
    """Result of a narration attempt.

    Attributes:
        ok: True if the LLM returned usable text.
        text: The narrated prose, or ``None`` when narration was unavailable.
        format: The persona used (``standup`` / ``story`` / ``learning``).
        model: The Ollama model that was asked (for display/debugging).
        notice: Human-readable explanation when ``ok`` is False (why we fell
            back), or ``None`` on success.
    """

    ok: bool
    text: str | None
    format: str
    model: str
    notice: str | None = None


class BackendError(Exception):
    """Raised inside a backend when generation fails in a recoverable way.

    Backends translate transport/HTTP/parse failures into this exception with a
    user-facing ``notice``. :func:`_generate` catches it and turns it into a
    :class:`Narration` with ``ok=False`` so the CLI falls back to the raw tree.
    Backends never leak raw stack traces to the user.
    """

    def __init__(self, notice: str) -> None:
        super().__init__(notice)
        self.notice = notice


@runtime_checkable
class NarrationBackend(Protocol):
    """The LLM seam: anything that can turn a prompt into prose.

    Implementations own their own transport and model. They must raise
    :class:`BackendError` (with a friendly ``notice``) on any recoverable
    failure rather than propagating transport exceptions, and return a
    non-empty string on success. ``name``/``model`` are used purely for display.
    """

    name: str
    model: str

    def generate(self, prompt: str) -> str:
        """Return narrated prose for ``prompt`` or raise :class:`BackendError`."""
        ...


class OllamaBackend:
    """Narrate via a local **Ollama** server (``/api/generate``).

    The historical default and the privacy-preserving happy path: talks to
    ``localhost`` (or a LAN box) and never a cloud API.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        model: str = "llama3",
        base_url: str = "http://localhost:11434",
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._client = client

    def generate(self, prompt: str) -> str:
        url = self.base_url.rstrip("/") + "/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": _TEMPERATURE},
        }
        data = _post_json(
            url,
            payload,
            headers=None,
            timeout=self.timeout,
            client=self._client,
            label=f"Ollama at {self.base_url}",
        )
        text = (data.get("response") or "").strip() if isinstance(data, dict) else ""
        if not text:
            raise BackendError(
                "Ollama returned an empty narration. Showing the raw tree."
            )
        return text


class OpenAICompatBackend:
    """Narrate via any **OpenAI-compatible** ``/v1/chat/completions`` endpoint.

    Works with LM Studio, a ``llama.cpp`` server, or a local proxy. An optional
    API key is read from ``$YAK_LLM_API_KEY`` at construction and sent only as
    an ``Authorization`` header — never logged or stored in config.

    Pointing this at a *non-local* endpoint breaks yak-tracker's privacy
    guarantee; that warning lives in the README, not enforced here.
    """

    name = "openai_compat"

    def __init__(
        self,
        *,
        model: str = "llama3",
        base_url: str = "http://localhost:1234/v1",
        timeout: float = 60.0,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        # Read the key lazily from the env if not injected (tests inject).
        self._api_key = api_key if api_key is not None else os.environ.get(LLM_API_KEY_ENV)
        self._client = client

    def generate(self, prompt: str) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": _TEMPERATURE,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = _post_json(
            url,
            payload,
            headers=headers,
            timeout=self.timeout,
            client=self._client,
            label=f"OpenAI-compatible endpoint at {self.base_url}",
        )
        text = _extract_chat_text(data)
        if not text:
            raise BackendError(
                "The LLM endpoint returned an empty narration. Showing the raw tree."
            )
        return text


def _extract_chat_text(data: object) -> str:
    """Pull the assistant message out of an OpenAI chat-completions response."""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
    # Some servers echo the legacy completion shape.
    text = first.get("text")
    return text.strip() if isinstance(text, str) else ""


def _post_json(
    url: str,
    payload: dict,
    *,
    headers: dict | None,
    timeout: float,
    client: httpx.Client | None,
    label: str,
) -> object:
    """POST ``payload`` as JSON and return the decoded body.

    Shared by both backends. Translates every recoverable transport/HTTP/parse
    failure into a :class:`BackendError` with a friendly, actionable notice so
    the caller can fall back to the raw tree. Never raises a raw httpx error.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise BackendError(
            f"could not reach {label} \u2014 is it running? Showing the raw tree instead."
        ) from None
    except httpx.TimeoutException:
        raise BackendError(
            f"{label} timed out after {timeout:g}s. Showing the raw tree."
        ) from None
    except httpx.HTTPStatusError as exc:
        detail = _http_error_detail(exc.response)
        raise BackendError(
            f"{label} returned HTTP {exc.response.status_code}{detail}. "
            "Showing the raw tree."
        ) from None
    except (httpx.HTTPError, ValueError) as exc:
        raise BackendError(
            f"{label} response could not be parsed ({exc}). Showing the raw tree."
        ) from None
    finally:
        if owns_client:
            client.close()


def _http_error_detail(response: httpx.Response) -> str:
    """Best-effort extraction of a JSON ``error`` field for a notice.

    Handles both Ollama's ``{\"error\": \"...\"}`` and OpenAI's nested
    ``{\"error\": {\"message\": \"...\"}}`` shapes.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    err = body.get("error")
    if isinstance(err, str) and err.strip():
        return f" ({err.strip()})"
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg.strip():
            return f" ({msg.strip()})"
    return ""


def make_backend(
    *,
    backend: str = "ollama",
    model: str = "llama3",
    base_url: str | None = None,
    timeout: float = 60.0,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> NarrationBackend:
    """Construct the narration backend named ``backend``.

    Wires config/CLI selection to a concrete :class:`NarrationBackend`. When
    ``base_url`` is ``None`` each backend uses its own sensible default
    (localhost Ollama, or ``localhost:1234/v1`` for openai_compat).

    Raises:
        ValueError: if ``backend`` is not one of :data:`VALID_BACKENDS`.
    """
    if backend == "ollama":
        return OllamaBackend(
            model=model,
            base_url=base_url or "http://localhost:11434",
            timeout=timeout,
            client=client,
        )
    if backend == "openai_compat":
        return OpenAICompatBackend(
            model=model,
            base_url=base_url or "http://localhost:1234/v1",
            timeout=timeout,
            api_key=api_key,
            client=client,
        )
    raise ValueError(
        f"unknown backend {backend!r}; expected one of {', '.join(VALID_BACKENDS)}"
    )


# System framing for `yak blame`: one paragraph on why a single file kept
# pulling the developer back. Reuses the same low-temperature, outline-only
# stance as the day personas.
_BLAME_PERSONA = (
    "You are a wry engineering narrator. Using ONLY the per-file activity "
    "outline below, write ONE short paragraph explaining why this single file "
    "kept pulling the developer back — the churn, the detours, the re-visits. "
    "Be concrete and a little dry; do not invent commands or facts not in the "
    "outline. No preamble, no bullet points."
)


def build_blame_outline(blame, *, date_label: str | None = None) -> str:
    """Serialise a :class:`~yak_tracker.blame.Blame` into a compact text outline.

    One block per session, each listing the events that touched the file with a
    short source tag (git touch vs shell reference). Deterministic so tests can
    assert on it and repeated runs stay stable.
    """
    if not blame.sessions:
        return "(no recorded activity touched this file)"

    header = f"File: {blame.resolution.relpath.as_posix()} (repo {blame.resolution.label})"
    if date_label:
        header += f" for {date_label}"
    header += f"\n{blame.headline}"

    blocks: list[str] = []
    for i, session in enumerate(blame.sessions, start=1):
        start = session.start.strftime("%H:%M")
        lines = [f"Session {i} (started {start}) \u2014 {session.count} touch(es):"]
        for ev in session.events:
            when = ev.ts.strftime("%H:%M") if ev.ts else "--:--"
            kind = "git" if ev.source.startswith("git-touch") else "shell"
            lines.append(f"  - [{when}] ({kind}) {ev.cmd}")
        blocks.append("\n".join(lines))

    return header + "\n\n" + "\n\n".join(blocks)


def narrate_blame(
    blame,
    *,
    model: str = "llama3",
    host: str = "http://localhost:11434",
    timeout: float = 60.0,
    date_label: str | None = None,
    client: httpx.Client | None = None,
    backend: NarrationBackend | None = None,
) -> Narration:
    """Narrate a per-file :class:`~yak_tracker.blame.Blame` with an LLM backend.

    Same graceful-degradation contract as :func:`narrate`: any failure (backend
    down, timeout, HTTP error, empty response) is caught and returned as a
    :class:`Narration` with ``ok=False`` plus an explanatory ``notice``, so the
    caller can fall back to the raw timeline. Never raises for a bad server.

    A concrete ``backend`` may be passed; otherwise an :class:`OllamaBackend`
    is built from ``model``/``host``/``timeout`` for backward compatibility.
    """
    backend = backend or OllamaBackend(model=model, base_url=host, timeout=timeout, client=client)
    if not blame.sessions:
        return Narration(
            ok=False,
            text=None,
            format="blame",
            model=backend.model,
            notice="nothing to narrate (no events touched this file)",
        )

    outline = build_blame_outline(blame, date_label=date_label)
    prompt = (
        f"{_BLAME_PERSONA}\n\n--- FILE ACTIVITY OUTLINE ---\n{outline}\n"
        "--- END OUTLINE ---\n"
    )
    return _generate(prompt, fmt="blame", backend=backend)


def _render_node(node: Node, depth: int, lines: list[str]) -> None:
    """Append an indented outline line for ``node`` and recurse into children."""
    indent = "  " * depth
    kind_word = _KIND_WORD.get(node.kind, node.kind)
    when = node.ts.strftime("%H:%M") if node.ts else "--:--"
    if node.kind == DetourKind.ROOT:
        lines.append(f"{indent}- [{when}] Intention: {node.label}")
    else:
        lines.append(f"{indent}- [{when}] ({kind_word}) {node.label}")
    for child in node.children:
        _render_node(child, depth + 1, lines)


def build_outline(forest: Sequence[Node], *, date_label: str | None = None) -> str:
    """Serialise a tree forest into a compact, deterministic text outline.

    This is what actually gets sent to the model (never the raw history). One
    block per session, each an indented intention→detour tree. Deterministic so
    tests can assert on it and so repeated runs are stable.
    """
    if not forest:
        return "(no recorded activity)"

    blocks: list[str] = []
    for i, tree in enumerate(forest, start=1):
        header = f"Session {i}"
        if tree.ts is not None:
            header += f" (started {tree.ts.strftime('%H:%M')})"
        detours = tree.descendants()
        depth = tree.max_depth()
        header += f" — {detours} event(s), {depth} level(s) deep"
        lines: list[str] = [header]
        _render_node(tree, 0, lines)
        blocks.append("\n".join(lines))

    preamble = "Coding activity"
    if date_label:
        preamble += f" for {date_label}"
    preamble += ":"
    return preamble + "\n\n" + "\n\n".join(blocks)


def build_prompt(
    forest: Sequence[Node],
    *,
    fmt: str,
    date_label: str | None = None,
) -> str:
    """Build the full prompt (persona + outline) for ``fmt``.

    Raises:
        ValueError: if ``fmt`` is not a recognised persona.
    """
    if fmt not in PERSONAS:
        raise ValueError(
            f"unknown format {fmt!r}; expected one of {', '.join(VALID_FORMATS)}"
        )
    persona = PERSONAS[fmt]
    outline = build_outline(forest, date_label=date_label)
    return f"{persona}\n\n--- ACTIVITY OUTLINE ---\n{outline}\n--- END OUTLINE ---\n"


def narrate(
    forest: Sequence[Node],
    *,
    fmt: str = "story",
    model: str = "llama3",
    host: str = "http://localhost:11434",
    timeout: float = 60.0,
    date_label: str | None = None,
    client: httpx.Client | None = None,
    backend: NarrationBackend | None = None,
) -> Narration:
    """Narrate the tree ``forest`` with an LLM backend.

    Sends a persona-framed outline to the selected backend and returns the
    model's prose. Any failure — backend down, connection refused, timeout, HTTP
    error, empty/garbled response — is caught and turned into a
    :class:`Narration` with ``ok=False`` and an explanatory ``notice`` so the
    caller can fall back to the raw tree. This function never raises for an
    unreachable or misbehaving server.

    Args:
        forest: The yak-shaving trees to narrate.
        fmt: Persona/format (``standup`` / ``story`` / ``learning``).
        model: Model name (used to build a default Ollama backend).
        host: Base URL (used to build a default Ollama backend).
        timeout: Seconds to wait on the request.
        date_label: Optional date string woven into the outline preamble.
        client: Optional pre-built :class:`httpx.Client` (used by tests to mock
            the transport) for the default Ollama backend.
        backend: A concrete :class:`NarrationBackend`. When given, it overrides
            ``model``/``host``/``timeout``/``client``; otherwise an
            :class:`OllamaBackend` is built from those for backward compat.
    """
    backend = backend or OllamaBackend(model=model, base_url=host, timeout=timeout, client=client)
    try:
        prompt = build_prompt(forest, fmt=fmt, date_label=date_label)
    except ValueError as exc:
        return Narration(
            ok=False, text=None, format=fmt, model=backend.model, notice=str(exc)
        )

    if not forest:
        return Narration(
            ok=False,
            text=None,
            format=fmt,
            model=backend.model,
            notice="nothing to narrate (no sessions for this day)",
        )

    return _generate(prompt, fmt=fmt, backend=backend)


def _generate(prompt: str, *, fmt: str, backend: NarrationBackend) -> Narration:
    """Run ``prompt`` through ``backend`` and wrap the result.

    Shared by :func:`narrate` and :func:`narrate_blame`. A :class:`BackendError`
    is caught and returned as a :class:`Narration` with ``ok=False`` and the
    backend's explanatory ``notice``; this never raises for an unreachable or
    misbehaving server.
    """
    try:
        text = backend.generate(prompt)
    except BackendError as exc:
        return Narration(
            ok=False, text=None, format=fmt, model=backend.model, notice=exc.notice
        )
    return Narration(ok=True, text=text, format=fmt, model=backend.model)
