"""Published claims — `claims.json` on the state branch (P2-5a, §3.3.1, D-19).

A Worker's claims are local truth in its Manager's ledger (§5.2). Published,
they let every machine see who holds what without a ticket-backend round trip:
no rate limit, and the same atomic push the lease uses.

**One file, every machine's claims, so a publisher owns only its own entry.**

```json
{"machines": {"manager-alpha": {"published": "...Z", "claims": [ ... ]}}}
```

That shape is a proposal (§3.3.1 names the file and nothing else). Two
properties it has to keep, whatever the field names settle as:

- **Another machine's entry is never rewritten**, and neither is a key this
  version does not know. Publishing replaces exactly one entry.
- **Unreadable bytes are refused, not replaced** (D-54). `managers/<name>.json`
  belongs to one Manager, so a corrupt one can be rewritten by its owner;
  `claims.json` holds everyone's, and replacing it would destroy other
  machines' claims to satisfy a write. The publisher declines and says so,
  which is also what makes a claim that might overlap an unreadable file
  refusable rather than silently granted.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from rite_ai.coordination.publish import NotPublished, Published, read_merge_write
from rite_ai.coordination.state_layer import (
    Absent,
    StateLayer,
    Unavailable,
    valid_key,
)

CLAIMS_KEY = "claims.json"


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")


def publish_claims(
    layer: StateLayer,
    machine: str,
    claims: list,
    *,
    now: datetime | None = None,
    attempts: int = 3,
) -> Published | NotPublished:
    """Replace `machine`'s published claims with `claims` (a list of
    `rite_ai.claims.ledger.Claim`)."""
    if not machine or "/" in machine or not valid_key(f"machines/{machine}"):
        return NotPublished(f"{machine!r} is not usable as a machine name")

    entry = {
        "published": _stamp(now),
        "claims": [asdict(c) for c in claims],
    }

    def merge(current: bytes | None) -> bytes | NotPublished:
        state: dict = {"machines": {}}
        if current is not None:
            try:
                parsed = json.loads(current)
            except (json.JSONDecodeError, TypeError, ValueError):
                return NotPublished(
                    f"{CLAIMS_KEY} could not be read, and replacing it would "
                    "destroy other machines' claims — publish refused; the "
                    "bytes are left exactly as they are"
                )
            if not isinstance(parsed, dict):
                return NotPublished(
                    f"{CLAIMS_KEY} is not an object — publish refused rather "
                    "than overwriting it"
                )
            state = parsed
        machines = state.get("machines")
        if not isinstance(machines, dict):
            if machines is not None:
                return NotPublished(
                    f"{CLAIMS_KEY}'s `machines` is not an object — publish "
                    "refused rather than overwriting it"
                )
            machines = {}
        state["machines"] = {**machines, machine: entry}
        return (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()

    return read_merge_write(layer, CLAIMS_KEY, merge, attempts)


def published_claims(state: dict) -> dict[str, list[dict]]:
    """Every machine's published claims, skipping entries this version cannot
    read — a malformed entry is one machine's problem, not a reason to report
    that nobody holds anything."""
    machines = state.get("machines")
    if not isinstance(machines, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for name, entry in machines.items():
        claims = entry.get("claims") if isinstance(entry, dict) else None
        if isinstance(claims, list):
            out[name] = [c for c in claims if isinstance(c, dict)]
    return out


@dataclass
class CannotTell:
    """The published claims could not be read, so no claim can be shown safe.

    D-54's second half: the bytes are left exactly as they are, and the
    operation that needed them is refused. A claim granted against claims it
    could not read is precisely the overlap the ledger exists to prevent."""

    reason: str


def published_overlaps(
    layer: StateLayer, own_machine: str, paths: list[str]
) -> list[str] | CannotTell:
    """Which of `paths` overlap a claim published by ANOTHER machine.

    This machine's own entry is skipped: its ledger is the source of truth for
    its own claims (§5.2), and a published copy of them is a projection that
    may lag by a heartbeat."""
    from rite_ai.claims.ledger import normalise_path, paths_overlap

    read = layer.read_state(CLAIMS_KEY)
    if isinstance(read, Unavailable):
        return CannotTell(f"could not read {CLAIMS_KEY}: {read.reason}")
    if isinstance(read, Absent):
        return []
    try:
        state = json.loads(read.value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return CannotTell(
            f"{CLAIMS_KEY} could not be parsed, so a claim that might overlap "
            "it cannot be shown safe"
        )
    if not isinstance(state, dict):
        return CannotTell(f"{CLAIMS_KEY} is not an object")

    found: list[str] = []
    for machine, claims in published_claims(state).items():
        if machine == own_machine:
            continue
        for claim in claims:
            holder = str(claim.get("worker", "?"))
            for cp in (
                claim.get("paths", []) if isinstance(claim.get("paths"), list) else []
            ):
                for p in paths:
                    if paths_overlap(normalise_path(p), normalise_path(str(cp))):
                        found.append(
                            f"{normalise_path(p)} overlaps {cp} "
                            f"(held by {holder} on {machine})"
                        )
    return found


@dataclass
class Expiry:
    """What an expiry pass did, and what it deliberately did not do."""

    expired: dict[str, int] = field(default_factory=dict)
    """machine -> how many published claims were expired."""

    kept: dict[str, str] = field(default_factory=dict)
    """machine -> why it was left alone (alive, or liveness unknown)."""

    refused: str = ""
    """Set when the pass did nothing at all, and why."""


def expire_offline_claims(
    layer: StateLayer,
    *,
    by: str,
    now: datetime,
    interval_minutes: int,
    stall_threshold: int,
    skip: frozenset[str] = frozenset(),
    attempts: int = 3,
) -> Expiry:
    """Expire the published claims of machines whose heartbeat says they are
    gone (P2-5c).

    Claims are held until merge (§5.2), so they do not time out on age alone —
    a long review is not an offline machine. What expires them is the
    Manager's own heartbeat lapsing (§3.4): claim timestamps say how old the
    work was, and go in the audit record rather than into the decision.

    **Attribution is not optional.** Phase 1's `force_release` takes a `by` and
    a reason and writes a durable audit trail, and doing this across machines
    must not be the cheap way around that (P2-5c). The audit here is a message
    on the log (§3.3.3 classes claim contention as a message, not state), in
    P2-0d's convention — and it is appended BEFORE the state changes, so an
    expiry cannot happen without its record. If the append fails, nothing is
    expired.

    A machine whose liveness cannot be established is never expired: that is
    "cannot tell", not "gone" (D-54).
    """
    from rite_ai.coordination.heartbeat import is_stalled, liveness
    from rite_ai.coordination.message_log import LogMessage, format_message

    read = layer.read_state(CLAIMS_KEY)
    if isinstance(read, Unavailable):
        return Expiry(refused=f"could not read {CLAIMS_KEY}: {read.reason}")
    if isinstance(read, Absent):
        return Expiry()
    try:
        state = json.loads(read.value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return Expiry(
            refused=f"{CLAIMS_KEY} could not be parsed — nothing expired, "
            "and the bytes are left exactly as they are"
        )
    if not isinstance(state, dict):
        return Expiry(refused=f"{CLAIMS_KEY} is not an object — nothing expired")

    result = Expiry()
    gone: dict[str, int] = {}
    for machine, claims in published_claims(state).items():
        if machine in skip:
            result.kept[machine] = "skipped"
            continue
        if not claims:
            continue
        live = liveness(layer, machine, now=now, interval_minutes=interval_minutes)
        if not live.known:
            result.kept[machine] = f"liveness unknown — {live.detail}"
            continue
        if not is_stalled(live, stall_threshold=stall_threshold):
            result.kept[machine] = f"alive — {live.detail}"
            continue
        gone[machine] = len(claims)
    if not gone:
        return result

    for machine, count in gone.items():
        oldest = min(
            (
                c.get("timestamp")
                for c in published_claims(state)[machine]
                if isinstance(c.get("timestamp"), int | float)
            ),
            default=None,
        )
        message = LogMessage(
            kind="claim-contention",
            subject=f"{count} claim(s) expired for {machine} (heartbeat lapsed)",
            fields={
                "Machine": machine,
                "By": by,
                "Reason": "heartbeat-lapsed",
                "Claims": str(count),
                **(
                    {"Oldest-Claim": _stamp(datetime.fromtimestamp(oldest, UTC))}
                    if oldest
                    else {}
                ),
            },
        )
        if isinstance(layer.append_message(format_message(message)), Unavailable):
            return Expiry(
                refused=(
                    f"could not record the expiry of {machine}'s claims in the "
                    "message log — nothing expired, because an expiry without "
                    "attribution is what P2-5c forbids"
                )
            )

    def merge(current: bytes | None) -> bytes | NotPublished:
        if current is None:
            return NotPublished(f"{CLAIMS_KEY} disappeared mid-expiry")
        try:
            fresh = json.loads(current)
        except (json.JSONDecodeError, TypeError, ValueError):
            return NotPublished(f"{CLAIMS_KEY} became unreadable mid-expiry")
        if not isinstance(fresh, dict) or not isinstance(fresh.get("machines"), dict):
            return NotPublished(f"{CLAIMS_KEY} changed shape mid-expiry")
        machines = dict(fresh["machines"])
        for machine in gone:
            entry = machines.get(machine)
            if not isinstance(entry, dict):
                continue
            # The entry stays, emptied: its own unknown fields survive, and the
            # record that this machine WAS here is more useful than its absence.
            machines[machine] = {
                **entry,
                "claims": [],
                "expired": _stamp(now),
                "expired_by": by,
                "expired_reason": "heartbeat-lapsed",
            }
        fresh["machines"] = machines
        return (json.dumps(fresh, indent=2, sort_keys=True) + "\n").encode()

    written = read_merge_write(layer, CLAIMS_KEY, merge, attempts)
    if isinstance(written, NotPublished):
        return Expiry(refused=f"expiry recorded but not applied: {written.reason}")
    result.expired = gone
    return result
