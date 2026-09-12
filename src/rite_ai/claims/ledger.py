"""Claims ledger — file/directory-level ownership for Workers."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rite_ai.state import (
    CorruptStateError,
    exclusion_holds,
    locked,
    read_json_state,
    write_atomic,
)


@dataclass
class Claim:
    paths: list[str]
    worker: str
    ticket: str = ""
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class ClaimResult:
    ok: bool
    message: str = ""
    overlaps: list[str] = field(default_factory=list)


def normalise_path(p: str) -> str:
    """Strip line/column decorations: file.ts:42-50 → file.ts"""
    for sep in (":", "#"):
        idx = p.find(sep)
        if idx > 0:
            p = p[:idx]
    return p.rstrip("/")


def paths_overlap(a: str, b: str) -> bool:
    """Check whether two paths overlap (nesting in either direction)."""
    na = normalise_path(a)
    nb = normalise_path(b)
    if na == nb:
        return True
    return na.startswith(nb + "/") or nb.startswith(na + "/")


# Directories already probed in this process. The answer is a property of
# the filesystem, not of the moment, so asking once per command is enough
# — and every `rite claim` is its own command.
_EXCLUSION_PROBED: dict[str, bool] = {}


def _warn_if_exclusion_is_decoration(directory: Path) -> None:
    """Say so, loudly, if `flock` does not exclude where this ledger lives.

    Everything this ledger promises rests on `flock` working. It does on a
    local disk; on a network mount or inside some VM shared folders it can
    be a silent no-op, and a no-op here is not a degraded feature — it is
    the defect the sidecar lock was written to remove, back again with no
    signal: two workers granted the same path, each told "claimed", each
    exiting 0.

    Warns rather than refuses. A refused claim blocks all work, and
    someone running a single session on such a filesystem is not in
    danger; the failure needs more than one writer. The `core.hooksPath`
    case refuses instead, and the difference is what refusing costs — an
    uninstalled hook blocks nothing.
    """
    key = str(directory)
    if key in _EXCLUSION_PROBED:
        return
    holds = exclusion_holds(directory)
    _EXCLUSION_PROBED[key] = holds
    if holds:
        return
    print(
        f"WARNING: file locking does not work under {directory}. rite's "
        "claim exclusion depends on it, so two workers can be granted the "
        "same path here and both told 'claimed'. This is what a network "
        "mount or a VM shared folder looks like — move the project to a "
        "local disk, or run one worker at a time. `rite doctor` reports "
        "this too.",
        file=sys.stderr,
    )


class ClaimsLedger:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _warn_if_exclusion_is_decoration(self._path.parent)

    def _read(self) -> list[Claim]:
        """Raises `CorruptStateError` for a file that exists but will not
        parse. This used to return `[]`, which made a half-written
        `claims.json` indistinguishable from a project with nothing
        claimed — so `rite status` said "no active claims" and the next
        `rite claim` handed a second worker a file the first still held,
        exit code 0, no message. Losing the ledger is recoverable; being
        told the ledger is empty when it is not is what breaks a live
        repo."""
        data = read_json_state(self._path, default=[])
        if not isinstance(data, list):
            raise CorruptStateError(
                self._path, f"expected a JSON list, got {type(data).__name__}"
            )
        return [
            Claim(
                paths=entry.get("paths", []),
                worker=entry.get("worker", ""),
                ticket=entry.get("ticket", ""),
                timestamp=entry.get("timestamp", 0.0),
            )
            for entry in data
            if isinstance(entry, dict)
        ]

    def _write(self, claims: list[Claim]) -> None:
        write_atomic(
            self._path, json.dumps([asdict(c) for c in claims], indent=2) + "\n"
        )

    def _locked(self):
        """Exclusion for every read-modify-write below.

        Held on a sidecar rather than on `claims.json`, because
        `write_atomic` replaces the file's inode and an `flock` on the
        data file therefore stops excluding the moment anyone writes —
        see `rite_ai.state.locked`, which carries the measurement."""
        return locked(self._path)

    def claim(self, paths: list[str], worker: str, ticket: str = "") -> ClaimResult:
        """Claim one or more paths for a worker. Fails on overlap."""
        if not paths:
            return ClaimResult(ok=False, message="no paths to claim")

        with self._locked():
            existing = self._read()
            overlaps: list[str] = []
            for p in paths:
                np = normalise_path(p)
                for claim in existing:
                    if claim.worker == worker:
                        continue
                    for cp in claim.paths:
                        if paths_overlap(np, cp):
                            overlaps.append(
                                f"{np} overlaps {cp} (held by {claim.worker})"
                            )

            if overlaps:
                return ClaimResult(
                    ok=False,
                    message="path contention",
                    overlaps=overlaps,
                )

            # Remove any existing claims by this worker for these paths
            normalised = [normalise_path(p) for p in paths]
            filtered = [
                c
                for c in existing
                if c.worker != worker
                or not all(normalise_path(cp) in normalised for cp in c.paths)
            ]
            filtered.append(Claim(paths=normalised, worker=worker, ticket=ticket))
            self._write(filtered)
            return ClaimResult(ok=True)

    def release(self, worker: str, paths: list[str] | None = None) -> int:
        """Release claims. If paths is None, release all for worker."""
        with self._locked():
            existing = self._read()
            if paths is None:
                after = [c for c in existing if c.worker != worker]
            else:
                normalised = {normalise_path(p) for p in paths}
                after = [
                    c
                    for c in existing
                    if c.worker != worker
                    or not any(normalise_path(cp) in normalised for cp in c.paths)
                ]
            released = len(existing) - len(after)
            self._write(after)
            return released

    def _audit_path(self) -> Path:
        return self._path.parent / "force-releases.jsonl"

    def force_release(self, paths: list[str], by: str, reason: str) -> int:
        """Force-release paths regardless of owner, with attribution and a
        reason persisted to a durable audit trail (SPEC §5.2 — an earlier
        version accepted `by` and silently discarded it, and had no
        `reason` parameter at all)."""
        with self._locked():
            existing = self._read()
            normalised = {normalise_path(p) for p in paths}
            released_claims = [
                c
                for c in existing
                if any(normalise_path(cp) in normalised for cp in c.paths)
            ]
            after = [c for c in existing if c not in released_claims]
            self._write(after)

            if released_claims:
                record = {
                    "by": by,
                    "reason": reason,
                    "timestamp": time.time(),
                    "released": [
                        {"worker": c.worker, "paths": c.paths, "ticket": c.ticket}
                        for c in released_claims
                    ],
                }
                with self._audit_path().open("a") as f:
                    f.write(json.dumps(record) + "\n")

            return len(released_claims)

    def force_release_audit(self) -> list[dict]:
        """Read the durable force-release audit trail — one record per
        `force_release` call that actually released something. Append-only;
        never rewritten or pruned, since this is exactly the history a
        human would want when asking "why did my claim disappear?"."""
        path = self._audit_path()
        if not path.is_file():
            return []
        records = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def list_claims(self) -> list[Claim]:
        """List all active claims."""
        with self._locked():
            return self._read()

    def claims_for(self, worker: str) -> list[Claim]:
        """List claims held by a specific worker."""
        with self._locked():
            return [c for c in self._read() if c.worker == worker]
