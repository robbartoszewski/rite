"""Project context directory management (SPEC §8.6).

Context files are project-specific knowledge — database conventions, API
patterns, architecture decisions — indexed by `.rite/context/INDEX.md`.
"""

from __future__ import annotations

from .manage import (
    ContextEntry,
    add_context,
    check_integrity,
    list_context,
    remove_context,
)

__all__ = [
    "ContextEntry",
    "add_context",
    "check_integrity",
    "list_context",
    "remove_context",
]
