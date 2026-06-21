"""Configuration loading for yak-tracker (PLAN.md M5).

Resolves settings from, in order of increasing precedence:

1. **Built-in defaults** (:data:`DEFAULTS`).
2. **A TOML config file** — ``~/.config/yak-tracker/config.toml`` by default
   (overridable via ``$YAK_TRACKER_CONFIG`` or an explicit ``path=``).
3. **Per-invocation CLI overrides** — applied by the caller via
   :meth:`Config.with_overrides`, so an explicit ``--idle-gap`` always wins over
   the file.

The file is read with the stdlib :mod:`tomllib` (Python 3.11+), so there is no
third-party dependency just to parse config. A missing or malformed file never
raises: we fall back to defaults and remember *why* in :attr:`Config.source`, so
``yak config`` can tell the user what actually happened.

Recognised keys (all optional)::

    # ~/.config/yak-tracker/config.toml
    repos = ["~/code/yak-tracker", "~/code/other"]
    idle_gap = 25                       # minutes; new session after this gap
    model = "llama3"                    # Ollama model name
    ollama_host = "http://localhost:11434"
    timeout = 60                        # seconds for the Ollama request
    format = "story"                    # default persona for `yak today`
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

__all__ = [
    "Config",
    "DEFAULTS",
    "VALID_FORMATS",
    "default_config_path",
    "load_config",
]

# The personas `yak today --format` understands. Kept here (not in cli.py) so
# both config validation and narration share one source of truth.
VALID_FORMATS: tuple[str, ...] = ("standup", "story", "learning")

# Built-in defaults. Mirrors PLAN.md: local Ollama, 25-minute idle gap.
DEFAULTS: dict[str, object] = {
    "repos": [],
    "idle_gap": 25.0,
    "model": "llama3",
    "ollama_host": "http://localhost:11434",
    "timeout": 60.0,
    "format": "story",
}

# Environment variable that can point at an alternate config file (handy for
# tests and for users who keep dotfiles somewhere non-standard).
_ENV_CONFIG_PATH = "YAK_TRACKER_CONFIG"


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved yak-tracker configuration.

    Attributes:
        repos: Git repositories to scan, as expanded absolute paths.
        idle_gap: Minutes of inactivity that start a new session.
        model: Ollama model name used for narration.
        ollama_host: Base URL of the local Ollama server.
        timeout: Seconds to wait on the Ollama HTTP call before falling back.
        format: Default ``today`` persona (one of :data:`VALID_FORMATS`).
        path: The config file that was consulted (whether or not it existed).
        source: Human-readable note on where values came from — surfaced by
            ``yak config`` (e.g. "defaults (no config file)" or the file path).
        warnings: Non-fatal problems found while loading (bad keys/types). Shown
            by ``yak config`` so misconfiguration is visible, not silent.
    """

    repos: tuple[Path, ...] = ()
    idle_gap: float = 25.0
    model: str = "llama3"
    ollama_host: str = "http://localhost:11434"
    timeout: float = 60.0
    format: str = "story"
    path: Path | None = None
    source: str = "defaults"
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def with_overrides(
        self,
        *,
        idle_gap: float | None = None,
        repos: list[Path] | None = None,
        model: str | None = None,
        ollama_host: str | None = None,
        timeout: float | None = None,
        format: str | None = None,
    ) -> Config:
        """Return a copy with any non-``None`` CLI overrides applied.

        Used by the CLI so an explicit flag (``--idle-gap``, ``--model``, …)
        takes precedence over the config file without mutating the loaded
        config. ``repos`` fully replaces the configured list when provided.
        """
        changes: dict[str, object] = {}
        if idle_gap is not None:
            changes["idle_gap"] = float(idle_gap)
        if repos:
            changes["repos"] = tuple(_expand(p) for p in repos)
        if model is not None:
            changes["model"] = model
        if ollama_host is not None:
            changes["ollama_host"] = ollama_host
        if timeout is not None:
            changes["timeout"] = float(timeout)
        if format is not None:
            changes["format"] = format
        return replace(self, **changes) if changes else self


def _expand(value: str | os.PathLike[str]) -> Path:
    """Expand ``~`` and environment variables, returning an absolute path."""
    text = os.path.expandvars(os.fspath(value))
    return Path(text).expanduser().resolve()


def default_config_path() -> Path:
    """Return the path yak-tracker reads config from by default.

    Honours ``$YAK_TRACKER_CONFIG`` first, then ``$XDG_CONFIG_HOME``, falling
    back to ``~/.config/yak-tracker/config.toml``.
    """
    override = os.environ.get(_ENV_CONFIG_PATH)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "yak-tracker" / "config.toml"


def _coerce_float(raw: object, key: str, warnings: list[str]) -> float | None:
    """Coerce ``raw`` to a positive float, warning (and dropping) on bad input."""
    if isinstance(raw, bool):  # bool is an int subclass — reject it explicitly
        warnings.append(f"{key!r} should be a number, got a boolean; ignoring it")
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value <= 0:
            warnings.append(f"{key!r} must be positive, got {value:g}; ignoring it")
            return None
        return value
    warnings.append(f"{key!r} should be a number, got {type(raw).__name__}; ignoring it")
    return None


def _coerce_str(raw: object, key: str, warnings: list[str]) -> str | None:
    """Coerce ``raw`` to a non-empty string, warning (and dropping) on bad input."""
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    warnings.append(f"{key!r} should be a non-empty string; ignoring it")
    return None


def _coerce_repos(raw: object, warnings: list[str]) -> tuple[Path, ...] | None:
    """Coerce ``raw`` to a tuple of expanded paths, warning on bad input."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        warnings.append("'repos' should be a list of path strings; ignoring it")
        return None
    return tuple(_expand(p) for p in raw)


def _parse_table(
    data: dict[str, object], warnings: list[str]
) -> dict[str, object]:
    """Validate a parsed TOML table into known config values.

    Unknown keys and bad types are recorded in ``warnings`` and skipped rather
    than raising, so one typo doesn't make ``yak`` unusable.
    """
    values: dict[str, object] = {}

    if "repos" in data:
        repos = _coerce_repos(data["repos"], warnings)
        if repos is not None:
            values["repos"] = repos

    for key in ("idle_gap", "timeout"):
        if key in data:
            num = _coerce_float(data[key], key, warnings)
            if num is not None:
                values[key] = num

    for key in ("model", "ollama_host"):
        if key in data:
            text = _coerce_str(data[key], key, warnings)
            if text is not None:
                values[key] = text

    if "format" in data:
        fmt = _coerce_str(data["format"], "format", warnings)
        if fmt is not None:
            if fmt in VALID_FORMATS:
                values["format"] = fmt
            else:
                warnings.append(
                    f"'format' must be one of {', '.join(VALID_FORMATS)}; "
                    f"got {fmt!r}, ignoring it"
                )

    known = {"repos", "idle_gap", "timeout", "model", "ollama_host", "format"}
    for unknown in sorted(set(data) - known):
        warnings.append(f"unknown config key {unknown!r}; ignoring it")

    return values


def load_config(path: Path | None = None) -> Config:
    """Load and resolve configuration from ``path`` (or the default location).

    Never raises for a missing or malformed file: defaults fill the gaps and the
    returned :class:`Config` records what happened in ``source``/``warnings``.
    """
    cfg_path = (path or default_config_path()).expanduser()
    base = Config(
        repos=tuple(DEFAULTS["repos"]),  # type: ignore[arg-type]
        idle_gap=float(DEFAULTS["idle_gap"]),  # type: ignore[arg-type]
        model=str(DEFAULTS["model"]),
        ollama_host=str(DEFAULTS["ollama_host"]),
        timeout=float(DEFAULTS["timeout"]),  # type: ignore[arg-type]
        format=str(DEFAULTS["format"]),
        path=cfg_path,
    )

    if not cfg_path.is_file():
        return replace(base, source="defaults (no config file found)")

    warnings: list[str] = []
    try:
        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return replace(
            base,
            source=f"defaults (could not read {cfg_path}: {exc})",
            warnings=(f"failed to parse {cfg_path}: {exc}",),
        )

    values = _parse_table(data, warnings)
    return replace(
        base,
        source=str(cfg_path),
        warnings=tuple(warnings),
        **values,  # type: ignore[arg-type]
    )
