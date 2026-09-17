"""§2.4.2's read-merge-write loop, once (P2-4a, P2-5a).

Every state-branch writer follows the same loop — "fetch → read-merge-write the
full state → push-with-lease → on-rejection-refetch-and-retry" — and §2.4.2 is
explicit that the election is only its most visible instance. Writing it once
means a second writer cannot quietly get the conflict or the unavailable case
wrong.

Two rules the loop enforces for every caller:

- **Merge, never put.** The merge function is handed the bytes currently under
  the key and returns the bytes to write, so what it does not understand it can
  carry through (P2-1d) — or refuse, when the decision needed those bytes and
  they could not be read (D-54).
- **Unavailable is not a lost race.** It means the write may have landed, so it
  is returned as its own outcome and never retried blindly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rite_ai.coordination.state_layer import (
    Present,
    StateLayer,
    Unavailable,
    Written,
)


@dataclass
class Published:
    version: str
    conflicts: int = 0
    note: str = ""


@dataclass
class NotPublished:
    reason: str
    may_have_landed: bool = False
    """True after Unavailable: re-read before concluding anything."""


Merge = Callable[[bytes | None], "bytes | NotPublished"]


def read_merge_write(
    layer: StateLayer, key: str, merge: Merge, attempts: int = 3
) -> Published | NotPublished:
    conflicts = 0
    for _ in range(max(1, attempts)):
        read = layer.read_state(key)
        if isinstance(read, Unavailable):
            return NotPublished(f"could not read {key}: {read.reason}")
        current = read.value if isinstance(read, Present) else None
        merged = merge(current)
        if isinstance(merged, NotPublished):
            return merged
        written = layer.write_state(key, merged, read.version)
        if isinstance(written, Written):
            return Published(written.version, conflicts)
        if isinstance(written, Unavailable):
            return NotPublished(
                f"could not write {key}: {written.reason}", may_have_landed=True
            )
        conflicts += 1
    return NotPublished(f"{key}: {conflicts} conflict(s) in a row — someone is writing")
