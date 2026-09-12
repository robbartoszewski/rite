"""Knowledge base management (SPEC §8.7).

User-provided reference material: authored files (committed) and link
snapshots (cached, gitignored). CLI: `rite kb add/refresh/list`.
"""

from __future__ import annotations

from .manage import (
    KbEntry,
    RefreshReport,
    add_file,
    add_link,
    list_entries,
    refresh,
)

__all__ = [
    "KbEntry",
    "RefreshReport",
    "add_file",
    "add_link",
    "list_entries",
    "refresh",
]
