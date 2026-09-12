"""Coordination-cost instrumentation (SPEC §2.7.2, D-45).

"Arguably more valuable than the schedule itself — the schedule is the
control, this is what makes it usable with data instead of a guess."
Three counters, each a count of discrete EVENTS, not a time measurement
(§5.2's own wording: a claim refusal is refused outright, never queued, so
there is nothing to measure the wait-time of):

- **refused_claims** — a claim attempt that was refused for overlapping
  another session's path. Incremented by `rite claim` in `rite_ai.cli.main`,
  the one caller of `ClaimsLedger.claim` there is — NOT inside the ledger,
  which is where this line used to point. Worth being exact about: a second
  caller of `.claim()` added anywhere else would not be counted, and the
  wrong attribution here is what would make that easy to miss.
- **nothing_safe_to_start** — `start`'s orientation reached "tickets exist,
  none claim-safe." Not yet wired: today's `start()` does not query the
  ticket backend as part of orientation at all (SPEC §9.10's decision
  table needs the ticket backend, which isn't threaded through `start()`
  yet) — the counter and its increment function exist and are tested, so
  wiring it in is one call once that orientation logic is built, not
  another module to write from scratch.
- **merge_conflicts** — a Worker's PR merge needed manual conflict
  resolution. Not yet wired for the same reason: there is no merge step in
  this codebase (`/ticket`'s merge is a human/Dispatch action outside any
  Python code path this project owns).

Counts are cumulative, never reset automatically — a user watching them
climb as they raise the Worker count is what finds their project's own
contention "knee" (§2.7.1), which only means anything against a rising
trend, not a per-cycle snapshot.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

_FILENAME = "coordination-cost.json"


@dataclass
class CoordinationCostCounts:
    refused_claims: int = 0
    nothing_safe_to_start: int = 0
    merge_conflicts: int = 0


def _path(root: Path) -> Path:
    return root / ".rite" / _FILENAME


def read_counts(root: Path) -> CoordinationCostCounts:
    path = _path(root)
    if not path.is_file():
        return CoordinationCostCounts()
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return CoordinationCostCounts()
    return CoordinationCostCounts(
        refused_claims=data.get("refused_claims", 0),
        nothing_safe_to_start=data.get("nothing_safe_to_start", 0),
        merge_conflicts=data.get("merge_conflicts", 0),
    )


def _increment(root: Path, field: str) -> None:
    counts = read_counts(root)
    setattr(counts, field, getattr(counts, field) + 1)
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(counts), indent=2) + "\n")


def record_refused_claim(root: Path) -> None:
    _increment(root, "refused_claims")


def record_nothing_safe_to_start(root: Path) -> None:
    _increment(root, "nothing_safe_to_start")


def record_merge_conflict(root: Path) -> None:
    _increment(root, "merge_conflicts")
