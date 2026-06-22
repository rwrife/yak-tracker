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

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from .config import VALID_FORMATS
from .tree import DetourKind, Node

__all__ = [
    "Narration",
    "build_outline",
    "build_prompt",
    "narrate",
    "PERSONAS",
]


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
) -> Narration:
    """Narrate the tree ``forest`` with a local Ollama model.

    Sends a persona-framed outline to ``{host}/api/generate`` and returns the
    model's prose. Any failure — Ollama down, connection refused, timeout, HTTP
    error, empty/garbled response — is caught and turned into a
    :class:`Narration` with ``ok=False`` and an explanatory ``notice`` so the
    caller can fall back to the raw tree. This function never raises for an
    unreachable or misbehaving server.

    Args:
        forest: The yak-shaving trees to narrate.
        fmt: Persona/format (``standup`` / ``story`` / ``learning``).
        model: Ollama model name.
        host: Base URL of the Ollama server.
        timeout: Seconds to wait on the request.
        date_label: Optional date string woven into the outline preamble.
        client: Optional pre-built :class:`httpx.Client` (used by tests to mock
            the transport). When omitted, a short-lived client is created.
    """
    try:
        prompt = build_prompt(forest, fmt=fmt, date_label=date_label)
    except ValueError as exc:
        return Narration(ok=False, text=None, format=fmt, model=model, notice=str(exc))

    if not forest:
        return Narration(
            ok=False,
            text=None,
            format=fmt,
            model=model,
            notice="nothing to narrate (no sessions for this day)",
        )

    url = host.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Low temperature: we want a faithful retelling of the outline, not
        # creative embellishment that invents commands the user never ran.
        "options": {"temperature": 0.4},
    }

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError:
        return Narration(
            ok=False,
            text=None,
            format=fmt,
            model=model,
            notice=(
                f"could not reach Ollama at {host} — is it running? "
                "(`ollama serve`). Showing the raw tree instead."
            ),
        )
    except httpx.TimeoutException:
        return Narration(
            ok=False,
            text=None,
            format=fmt,
            model=model,
            notice=f"Ollama at {host} timed out after {timeout:g}s. Showing the raw tree.",
        )
    except httpx.HTTPStatusError as exc:
        detail = _ollama_error_detail(exc.response)
        return Narration(
            ok=False,
            text=None,
            format=fmt,
            model=model,
            notice=(
                f"Ollama returned HTTP {exc.response.status_code}"
                f"{detail}. Showing the raw tree."
            ),
        )
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers resp.json() on a non-JSON body.
        return Narration(
            ok=False,
            text=None,
            format=fmt,
            model=model,
            notice=f"Ollama response could not be parsed ({exc}). Showing the raw tree.",
        )
    finally:
        if owns_client:
            client.close()

    text = (data.get("response") or "").strip() if isinstance(data, dict) else ""
    if not text:
        return Narration(
            ok=False,
            text=None,
            format=fmt,
            model=model,
            notice="Ollama returned an empty narration. Showing the raw tree.",
        )

    return Narration(ok=True, text=text, format=fmt, model=model)


def _ollama_error_detail(response: httpx.Response) -> str:
    """Best-effort extraction of Ollama's JSON ``error`` field for a notice."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        msg = body["error"].strip()
        if msg:
            # Common, actionable case: the model isn't pulled yet.
            return f" ({msg})"
    return ""
