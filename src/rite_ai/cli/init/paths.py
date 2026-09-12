"""Locate the `templates/` directory shipped alongside the rite source tree."""

from __future__ import annotations

from pathlib import Path


def templates_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "templates"
        if candidate.is_dir() and (parent / "pyproject.toml").exists():
            return candidate
    # Fallback for an installed layout where templates ship inside the package.
    fallback = here.parents[2] / "templates"
    return fallback
