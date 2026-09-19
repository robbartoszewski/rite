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

CONTENTION_KEEP = 500
"""Rows kept in `contention.jsonl`. The tail, because the question this file
answers is "is that ticket still blocked" and never "was it blocked on
Tuesday" — and because a Worker retrying every thirty seconds writes a row
every thirty seconds. The scheduler already has the scar from an unattended
record that only ever grew."""


@dataclass
class Contention:
    """A claim that was refused, and by whom."""

    timestamp: float
    worker: str
    ticket: str
    paths: list[str]
    overlaps: list[str]

    held_by: list[str] = field(default_factory=list)
    """Who held the overlapping paths, as DATA.

    `overlaps` is a sentence for a human — and a cross-machine one reads
    "(held by alpha on mac-studio)", so parsing a name back out of it yields
    `alpha on mac-studio`, which matches no worker anywhere. Anything
    deciding anything reads this field; the parser below exists only for rows
    written before it did."""

    @property
    def holders(self) -> list[str]:
        """The workers that held the overlapping paths, deduplicated in order.

        Prefers the recorded field. Falls back to reading the message only
        for older rows, and that fallback is best-effort by construction —
        deriving identity from a display string is what this field replaced.
        """
        if self.held_by:
            return list(dict.fromkeys(self.held_by))
        found: list[str] = []
        for line in self.overlaps:
            _, _, tail = line.partition("(held by ")
            name = tail.rstrip(")").strip()
            if name and name not in found:
                found.append(name)
        return found


def read_contention(root: Path, limit: int = CONTENTION_KEEP) -> list[Contention]:
    """Refused claims, oldest first. Empty when nothing has been refused — and
    empty, too, when the file cannot be read, because a missing record must
    never make a contended ticket look takeable by louder means than silence.
    """
    path = root / ".rite" / "contention.jsonl"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    found: list[Contention] = []
    for line in lines[-limit:]:
        try:
            data = json.loads(line)
            found.append(
                Contention(
                    timestamp=float(data.get("timestamp", 0.0)),
                    worker=str(data.get("worker", "")),
                    ticket=str(data.get("ticket", "")),
                    paths=list(data.get("paths", [])),
                    overlaps=list(data.get("overlaps", [])),
                    held_by=[str(h) for h in data.get("held_by", [])],
                )
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            # One unreadable row is not a reason to discard the rest; this is
            # an advisory record, not an audit trail.
            continue
    return found


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
    last_publish: object = None
    """The outcome of the last publish this ledger attempted (P2-5b), or None
    when it has never been asked to publish."""

    last_released_workers: set[str]
    """Workers whose claims the last `force_release` actually removed. Lets a
    caller tell a holder nesting with its own released claim — ordinary —
    from a different holder still standing in the way."""

    last_skipped_overlaps: list[tuple[str, str]]
    """`(worker, path)` for every claim the last `force_release` left in place
    that nonetheless OVERLAPS what it was asked to clear.

    `force_release` matches paths exactly; `claim()` refuses on nesting
    overlap. So clearing `users/src` when somebody holds `users/src/auth.ts`
    releases nothing and leaves the path blocked — "force-released 0 claim(s)"
    and no explanation, which is the trap somebody hits while tidying orphans.

    Computed inside the same lock as the release rather than by a second
    query, for two reasons found in review: a separate call reacquires the
    lock, so a claim created in the gap would be reported as "not released"
    when it was never eligible; and a separate query has no access to the
    `worker` filter, so it would name a live worker's deliberately-untouched
    claim as something to force away."""

    def __init__(self, path: Path) -> None:
        self.last_skipped_overlaps = []
        self.last_released_workers = set()
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

    def claim(
        self,
        paths: list[str],
        worker: str,
        ticket: str = "",
        *,
        layer=None,
        machine: str = "",
    ) -> ClaimResult:
        """Claim one or more paths for a worker. Fails on overlap.

        With a state layer (P2-5b), claims published by OTHER machines are
        checked too, and the grant is published so those machines see it. The
        local check runs first: it is free, and a path already held here needs
        no network round trip to refuse.

        Published claims that cannot be read refuse the claim (D-58): a claim
        that might overlap them cannot be shown safe, and granting it is the
        one thing the ledger exists to prevent.
        """
        if not paths:
            return ClaimResult(ok=False, message="no paths to claim")

        with self._locked():
            existing = self._read()
            overlaps: list[str] = []
            # Collected beside the sentence, from the claim object rather than
            # from the sentence about it.
            held_by: list[str] = []
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
                            held_by.append(claim.worker)

            if not overlaps and layer is not None:
                from rite_ai.coordination.claims_state import (
                    CannotTell,
                    published_overlaps,
                )

                published = published_overlaps(layer, machine, paths)
                if isinstance(published, CannotTell):
                    return ClaimResult(ok=False, message=published.reason)
                overlaps += published

            if overlaps:
                # `held_by` is empty for a cross-machine overlap: those come
                # back as sentences from `published_overlaps` and this ledger
                # never sees the claim objects. An unknown holder is treated
                # as "not provably gone" downstream, which is the safe way to
                # be wrong.
                self._record_contention(paths, worker, ticket, overlaps, held_by)
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
            if layer is None:
                return ClaimResult(ok=True)
            # Published immediately: a claim other machines cannot see is one
            # they will claim over. A failure here does not undo the local
            # claim — the two stores cannot be made atomic — so it is reported
            # rather than swallowed, and the caller decides.
            from rite_ai.coordination.claims_state import publish_claims
            from rite_ai.coordination.publish import Published

            self.last_publish = publish_claims(layer, machine, filtered)
            note = (
                ""
                if isinstance(self.last_publish, Published)
                else f"claimed locally, but not published: {self.last_publish.reason}"
            )
            return ClaimResult(ok=True, message=note)

    def release(
        self,
        worker: str,
        paths: list[str] | None = None,
        *,
        layer=None,
        machine: str = "",
    ) -> int:
        """Release claims. If paths is None, release all for worker.

        **With a state layer, the release is PUBLISHED** (P2-5a). Releasing
        only locally leaves the path claimed as far as every other machine
        can see, and nothing takes it back: `expire_offline_claims` expires
        the claims of a machine whose HEARTBEAT lapsed, and a healthy
        machine that simply finished its work never lapses. So each
        completed piece of work would poison its paths for the rest of the
        fleet, permanently, with no symptom except other machines being
        refused a path nobody holds.

        Publishing is part of releasing rather than something callers
        remember, because four call sites already existed and none of them
        did it.

        A publish that fails does NOT undo the local release — the two
        stores cannot be made atomic — so it is recorded in `last_publish`
        for the caller to report. The local claim is gone either way, and
        the published one is stale until something republishes.
        """
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
            if layer is not None:
                from rite_ai.coordination.claims_state import publish_claims

                self.last_publish = publish_claims(layer, machine, after)
            return released

    def _audit_path(self) -> Path:
        return self._path.parent / "force-releases.jsonl"

    def _contention_path(self) -> Path:
        return self._path.parent / "contention.jsonl"

    def _record_contention(
        self,
        paths: list[str],
        worker: str,
        ticket: str,
        overlaps: list[str],
        held_by: list[str] | None = None,
    ) -> None:
        """A refused claim, written down where something can read it back.

        **This is the only place rite learns that a TICKET is blocked on a
        path rather than on capacity.** A ticket carries a label, not a file
        list, so nothing can predict the collision — it is discovered here,
        by the Worker, after a session has already started. Without this
        record the discovery dies with that session, and the next thing
        looking at the queue sees a ticket that is ready and unblocked.

        Measured next door: a dogfood queue was not short of work, it was
        short of uncontended files — four tickets blocked by other live
        sessions, holders changing. The Owner worked that out by hand, twice.

        **Bounded, because the caller retries.** A Worker refused every thirty
        seconds appends a row every thirty seconds, and the scheduler already
        has the scar from that shape: an unattended stall grew one outbox file
        per tick until it filled the disk. The tail is kept and the head is
        dropped, because the useful question is "is this still contended
        now", never "was it contended on Tuesday".

        Never raises. A ledger must not fail a claim refusal because it could
        not write a note about it.
        """
        record = {
            "timestamp": time.time(),
            "worker": worker,
            "ticket": ticket,
            "paths": [normalise_path(p) for p in paths],
            "overlaps": overlaps,
            # Identity as data, beside the sentence. Anything that decides
            # reads this; the sentence is for a person.
            "held_by": list(dict.fromkeys(held_by or [])),
        }
        path = self._contention_path()
        try:
            with path.open("a") as f:
                f.write(json.dumps(record) + "\n")
            lines = path.read_text().splitlines()
            if len(lines) > CONTENTION_KEEP:
                write_atomic(path, "\n".join(lines[-CONTENTION_KEEP:]) + "\n")
        except OSError:
            return

    def force_release(
        self,
        paths: list[str] | None = None,
        by: str = "",
        reason: str = "",
        *,
        worker: str | None = None,
        layer=None,
        machine: str = "",
    ) -> int:
        """Force-release claims, with attribution and a reason persisted to a
        durable audit trail (SPEC §5.2 — an earlier version accepted `by` and
        silently discarded it, and had no `reason` parameter at all).

        **`worker` narrows it to one holder, and exists because without it
        this cannot express what every automatic caller needs.** By paths
        alone it releases whoever holds them, which is right for a human
        typing `rite release --force <path>` — they are saying "clear this
        path, I do not care who has it". It is wrong for anything acting on
        its own behalf: a reaper that knows a specific session died and
        releases *its* paths must not also release a live worker that claimed
        an identically-named path in the window since. That is the failure
        the ledger exists to prevent, arriving as the fix for it.

        So `pool.archive`, which knows exactly whose slot died, passes
        `worker=` — and gets the audit trail it previously skipped by calling
        plain `release()`, which is why "why did my claim disappear" used to
        depend on which subsystem removed it.

        `paths=None` with a `worker` means all of that worker's claims. One
        of the two must be given: releasing everything, by nobody's request,
        is not a thing this should be able to express by omission.

        ⚠ Path matching is EXACT, not nesting-aware — `engine/` does not
        release a claim on `engine/parser.py`, though `claim()` would have
        refused that claim as overlapping. Left as it is deliberately:
        widening it silently would make every existing `--force` call release
        more than it used to, and that is a decision rather than a fix.
        """
        # Cleared before the guards, not after the lock: both raise before the
        # lock is taken, and a caller that catches the ValueError would
        # otherwise read the PREVIOUS call's overlaps and believe them current
        # — the same trust-an-attribute-across-calls hazard the lock closed.
        self.last_skipped_overlaps = []
        if paths is not None and not paths:
            # An empty LIST is a caller that computed some paths and got none
            # of them, then asked to release "those" — true with or without a
            # worker, and in both cases it releases nothing and writes no
            # audit line, which reads as success. `paths=None` is the way to
            # say "no path filter"; `[]` is always a mistake.
            raise ValueError(
                "force_release was given an empty path list — pass paths=None "
                "to mean 'all of this worker's claims'"
            )
        if paths is None and worker is None:
            raise ValueError("force_release needs paths, a worker, or both")
        with self._locked():
            existing = self._read()
            normalised = {normalise_path(p) for p in (paths or [])}
            released_claims = [
                c
                for c in existing
                if (worker is None or c.worker == worker)
                and (
                    paths is None
                    or any(normalise_path(cp) in normalised for cp in c.paths)
                )
            ]
            after = [c for c in existing if c not in released_claims]
            self._write(after)

            # Only the paths that ACTUALLY overlap, not every path on a claim
            # that happens to contain one. A claim of
            # ["users/src/auth.ts", "unrelated/file.py"] must not report
            # `unrelated/file.py` as overlapping `users/src`.
            #
            # And with `worker` set, only THAT worker's. A worker-scoped
            # release leaves other holders alone deliberately; naming theirs
            # would advise force-releasing a live worker's claim as though it
            # were an orphan. An earlier version had `worker` in scope and
            # never referenced it — the guard existed in the docstring only.
            self.last_skipped_overlaps = [
                (claim.worker, cp)
                for claim in after
                if worker is None or claim.worker == worker
                for cp in claim.paths
                if any(paths_overlap(cp, wanted) for wanted in normalised)
            ]
            # Which of those the caller has just been releasing from. A holder
            # that nests with its OWN released claim is ordinary; a different
            # holder is the one worth acting on, and the two need different
            # advice.
            self.last_released_workers = {c.worker for c in released_claims}

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

            if layer is not None:
                # Same reason as `release`: a force-release that only
                # happens locally leaves every other machine refusing a
                # path nobody holds.
                from rite_ai.coordination.claims_state import publish_claims

                self.last_publish = publish_claims(layer, machine, after)

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

    def publish(self, layer, machine: str, now=None):
        """Publish this machine's claims to the coordination state (P2-5a).

        A projection of the ledger, not a second copy of it: local claims stay
        the source of truth (§5.2) and this makes them visible to other
        machines through the state layer (D-19) — no ticket-backend round
        trip, no rate limit, and the same atomic write the lease uses.
        """
        from rite_ai.coordination.claims_state import publish_claims

        return publish_claims(layer, machine, self.list_claims(), now=now)

    def claims_for(self, worker: str) -> list[Claim]:
        """List claims held by a specific worker."""
        with self._locked():
            return [c for c in self._read() if c.worker == worker]
