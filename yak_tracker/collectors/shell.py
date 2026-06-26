"""Shell-history collector: bash + zsh → normalized ``Event`` objects.

Parses the user's shell history file into :class:`~yak_tracker.models.Event`
records. Two formats are supported:

**zsh extended history** (``setopt EXTENDED_HISTORY``)::

    : 1718640000:0;git status
    : 1718640042:0;npm install

Each entry is ``: <start-epoch>:<elapsed-seconds>;<command>``. Commands may span
multiple physical lines when they contain escaped newlines (zsh writes a
trailing backslash); we stitch those back together.

**bash history**. Two sub-cases:

* With ``HISTTIMEFORMAT`` set, bash writes a timestamp comment line *before*
  each command::

      #1718640000
      git status

* Without it, the file is just one command per line and we have no timestamps.

The collector auto-detects the shell from ``$SHELL`` (falling back to probing
for known history files) but everything is overridable for testing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from ..models import Event
from ..redact import redact_events

# Recognized shells and the conventional location of their history file,
# relative to the user's home directory. Order matters for auto-detection
# fallback (first existing file wins).
_HISTORY_FILES: dict[str, str] = {
    "zsh": ".zsh_history",
    "bash": ".bash_history",
}


def detect_shell(shell_env: str | None = None) -> str | None:
    """Best-effort detect the user's shell name (``"bash"`` / ``"zsh"``).

    Looks at the ``$SHELL`` environment variable (or the supplied override).
    Returns the bare shell name if recognized, else ``None``.
    """
    shell = shell_env if shell_env is not None else os.environ.get("SHELL", "")
    if not shell:
        return None
    name = Path(shell).name.lower()
    for known in _HISTORY_FILES:
        if known in name:
            return known
    return None


def locate_history_file(
    shell: str | None = None,
    *,
    home: Path | None = None,
    histfile_env: str | None = None,
) -> tuple[str, Path] | None:
    """Locate the history file to parse.

    Resolution order:

    1. An explicit ``$HISTFILE`` (or ``histfile_env`` override), if it exists.
       The shell is inferred from the given/detected shell, defaulting to bash.
    2. The conventional file for the detected/forced ``shell``.
    3. A probe of every known shell's conventional file; first hit wins.

    Returns ``(shell_name, path)`` or ``None`` if nothing is found.
    """
    home = home or Path.home()
    histfile = histfile_env if histfile_env is not None else os.environ.get("HISTFILE")

    detected = shell or detect_shell()

    if histfile:
        path = Path(histfile).expanduser()
        if path.is_file():
            return detected or "bash", path

    if detected and detected in _HISTORY_FILES:
        path = home / _HISTORY_FILES[detected]
        if path.is_file():
            return detected, path

    # Last resort: probe every known shell.
    for name, rel in _HISTORY_FILES.items():
        path = home / rel
        if path.is_file():
            return name, path

    return None


def _read_text(path: Path) -> str:
    """Read a history file, tolerating the latin-1/mixed encodings shells emit."""
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_zsh(text: str) -> Iterator[tuple[str, datetime | None]]:
    """Yield ``(cmd, ts)`` from zsh history text.

    Handles both extended-history (``: <ts>:<elapsed>;cmd``) and plain lines, and
    re-joins multi-line commands that zsh splits with a trailing backslash.
    """
    pending: list[str] = []
    pending_ts: datetime | None = None

    def flush() -> Iterator[tuple[str, datetime | None]]:
        nonlocal pending, pending_ts
        if pending:
            yield "\n".join(pending), pending_ts
            pending = []
            pending_ts = None

    for raw in text.splitlines():
        if pending:
            # Continuation of a multi-line command.
            if raw.endswith("\\"):
                pending.append(raw[:-1])
            else:
                pending.append(raw)
                yield from flush()
            continue

        line = raw
        ts: datetime | None = None
        if line.startswith(": ") and ";" in line:
            meta, _, cmd = line.partition(";")
            # meta looks like ": 1718640000:0"
            parts = meta[2:].split(":")
            if parts and parts[0].strip().isdigit():
                ts = _epoch_to_dt(int(parts[0].strip()))
                line = cmd
            # else: not actually extended-history meta; treat whole line as cmd
        if line.endswith("\\"):
            pending = [line[:-1]]
            pending_ts = ts
            continue
        if line.strip():
            yield line, ts

    yield from flush()


def _parse_bash(text: str) -> Iterator[tuple[str, datetime | None]]:
    """Yield ``(cmd, ts)`` from bash history text.

    ``#<epoch>`` comment lines (written when ``HISTTIMEFORMAT`` is set) attach a
    timestamp to the *following* command. Plain command lines yield ``ts=None``.
    """
    pending_ts: datetime | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("#") and line[1:].strip().isdigit():
            pending_ts = _epoch_to_dt(int(line[1:].strip()))
            continue
        yield line, pending_ts
        pending_ts = None


def _epoch_to_dt(epoch: int) -> datetime | None:
    """Convert a Unix epoch (local time) to a naive ``datetime``.

    Returns ``None`` for obviously bogus values so a corrupt history line can't
    crash a whole run.
    """
    try:
        return datetime.fromtimestamp(epoch)
    except (OverflowError, OSError, ValueError):
        return None


def parse_history(text: str, shell: str, *, source: str | None = None) -> list[Event]:
    """Parse raw history ``text`` for the given ``shell`` into ``Event`` objects."""
    src = source or f"shell:{shell}"
    parser = _parse_zsh if shell == "zsh" else _parse_bash
    return [
        Event(cmd=cmd.strip(), ts=ts, cwd=None, source=src)
        for cmd, ts in parser(text)
        if cmd.strip()
    ]


def collect(
    *,
    shell: str | None = None,
    home: Path | None = None,
    histfile_env: str | None = None,
    path: Path | None = None,
    redact: bool = True,
) -> list[Event]:
    """Collect all shell-history events.

    If ``path`` is given it is parsed directly (``shell`` still selects the
    grammar, defaulting to detection/bash). Otherwise the history file is
    located via :func:`locate_history_file`. Returns ``[]`` when no history can
    be found rather than raising, so the CLI degrades gracefully.

    Secrets (API keys, tokens, ``KEY=value`` credentials, URL passwords, …) are
    scrubbed from each command by default — privacy is the whole point, and that
    includes the prompt we later hand to Ollama and the ``--json`` export. Pass
    ``redact=False`` to keep the raw commands.
    """
    if path is not None:
        resolved_shell = shell or detect_shell() or "bash"
        events = parse_history(_read_text(path), resolved_shell)
        return redact_events(events) if redact else events

    located = locate_history_file(shell=shell, home=home, histfile_env=histfile_env)
    if located is None:
        return []
    shell_name, hist_path = located
    events = parse_history(_read_text(hist_path), shell_name)
    return redact_events(events) if redact else events


def collect_for_date(
    day: date | None = None,
    *,
    shell: str | None = None,
    home: Path | None = None,
    histfile_env: str | None = None,
    path: Path | None = None,
    include_undated: bool = False,
    redact: bool = True,
) -> list[Event]:
    """Collect shell events, filtered to a single ``day`` (defaults to today).

    By default only events whose timestamp falls on ``day`` are returned. Set
    ``include_undated=True`` to also include events with no timestamp (useful for
    bash histories without ``HISTTIMEFORMAT``, where filtering is impossible).
    Results are sorted by timestamp; undated events sort last. Secrets are
    redacted by default; pass ``redact=False`` to keep raw commands.
    """
    target = day or date.today()
    events = collect(
        shell=shell, home=home, histfile_env=histfile_env, path=path, redact=redact
    )

    selected = [
        e for e in events if e.on_date(target) or (include_undated and e.ts is None)
    ]
    selected.sort(key=lambda e: (e.ts is None, e.ts or datetime.min))
    return selected
