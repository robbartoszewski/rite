"""A Manager's heartbeat, and reading someone else's (P2-4a, SPEC §3.4).

"A Manager's heartbeat is its status report pushed to the coordination repo.
The Owner monitors these; a Manager that misses N consecutive heartbeats is
marked stalled." The Owner's own liveness is a lease instead (D-17): a
heartbeat says "I was alive", which is exactly what a hung-but-alive Owner
keeps saying.

**It carries an in-flight count.** Capacity is unenforceable without one, and a
field added after the fact cannot be trusted until every machine has upgraded
— so it ships with the first heartbeat rather than later (P2-4a).

**Writing is §2.4.2's loop, not a put.** The whole state shares one version, so
a heartbeat must read, merge into the state it read, write against that
version, and re-read on conflict. Merging matters as much as the CAS: this
Manager owns four fields of its own file and nothing else in it, so everything
else — keys from a newer rite, values whose shape this version does not expect
— is carried through untouched (P2-1d).

**Unavailable is not a lost race** (the state layer's fourth property): the
write may have landed. It is returned as its own outcome, never retried
blindly and never reported as "someone else won".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from rite_ai.coordination.publish import (
    NotPublished,
    Published,
    read_merge_write,
)
from rite_ai.coordination.schemas import (
    ManagerStatus,
    parse_timestamp,
    status_from_json,
    status_to_json,
)
from rite_ai.coordination.state_layer import (
    Absent,
    StateLayer,
    Unavailable,
    valid_key,
)


def status_key(name: str) -> str:
    """`managers/<name>.json` (§3.3.1), or "" when the name cannot be one."""
    key = f"managers/{name}.json"
    return key if name and valid_key(key) and "/" not in name else ""


def publish_heartbeat(
    layer: StateLayer,
    name: str,
    *,
    workers: list[str],
    in_flight: int,
    now: datetime | None = None,
    attempts: int = 3,
) -> Published | NotPublished:
    """This Manager's own four fields, merged into whatever its status file
    already holds.

    Its own unreadable status is replaced rather than refused: a Manager's
    liveness must not stop because its own file is corrupt, and the file
    belongs to it alone. The result says so — that replacement does lose
    whatever the file held. `claims.json`, which holds every machine's claims,
    is refused instead (D-54); see `claims_state`.
    """
    key = status_key(name)
    if not key:
        return NotPublished(f"{name!r} is not usable as a state key")
    stamp = (
        (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
    replaced = False

    def merge(current: bytes | None) -> bytes | NotPublished:
        nonlocal replaced
        status = None
        if current is not None:
            status = status_from_json(current.decode("utf-8", errors="replace"))
            replaced = status is None
        status = status or ManagerStatus()
        status.name = name
        status.last_seen = stamp
        status.workers = list(workers)
        status.in_flight = in_flight
        return status_to_json(status).encode()

    result = read_merge_write(layer, key, merge, attempts)
    if isinstance(result, Published) and replaced:
        result.note = f"{key} could not be parsed and was replaced"
    return result


@dataclass
class Liveness:
    """How far behind a Manager's last heartbeat is."""

    missed: int | None
    """Consecutive intervals missed, or None for "cannot tell" — no status,
    unreadable bytes, or a `last_seen` this version cannot parse. Never
    reported as stalled: refusing the decision is D-54's second half."""

    detail: str = ""

    @property
    def known(self) -> bool:
        return self.missed is not None


def liveness(
    layer: StateLayer, name: str, *, now: datetime, interval_minutes: int
) -> Liveness:
    key = status_key(name)
    if not key:
        return Liveness(None, f"{name!r} is not usable as a state key")
    read = layer.read_state(key)
    if isinstance(read, Unavailable):
        return Liveness(None, f"could not read {key}: {read.reason}")
    if isinstance(read, Absent):
        return Liveness(None, f"{name} has never published a heartbeat")
    status = status_from_json(read.value.decode("utf-8", errors="replace"))
    if status is None:
        return Liveness(None, f"{key} could not be parsed")
    last = parse_timestamp(status.last_seen)
    if last is None:
        return Liveness(None, f"{key} has no usable last_seen")
    interval = max(1, interval_minutes) * 60
    behind = (now - last).total_seconds()
    return Liveness(max(0, int(behind // interval)), f"last seen {status.last_seen}")


def is_stalled(live: Liveness, *, stall_threshold: int) -> bool:
    """§3.4: stalled once N consecutive heartbeats are missed. "Cannot tell"
    is not stalled — it is a different report, for a human to read."""
    return live.missed is not None and live.missed >= max(1, stall_threshold)
