"""yak-tracker — reconstruct the story of your coding day, 100% locally.

Public package metadata lives here so both the CLI (`yak --version`) and any
downstream importer can read a single source of truth.
"""

from importlib import metadata

__all__ = ["__version__"]


def _resolve_version() -> str:
    """Return the installed package version, with a dev fallback.

    When running from a source checkout that has not been installed (e.g. a
    fresh clone before ``uv sync``), ``importlib.metadata`` raises. We fall
    back to the version declared in ``pyproject.toml`` so ``yak --version``
    still works in development.
    """
    try:
        return metadata.version("yak-tracker")
    except metadata.PackageNotFoundError:  # pragma: no cover - dev-only path
        return "0.1.0"


__version__ = _resolve_version()
