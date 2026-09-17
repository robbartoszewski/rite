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

from rite_ai.coordination.schemas import (
    ManagerStatus,
    status_from_json,
    status_to_json,
)
from rite_ai.coordination.state_layer import (
    Absent,
    Present,
    StateLayer,
    Unavailable,
    Written,
    valid_key,
)


def status_key(name: str) -> str:
    """`managers/<name>.json` (§3.3.1), or "" when the name cannot be one."""
    key = f"managers/{name}.json"
    return key if name and valid_key(key) and "/" not in name else ""


@dataclass
class Published:
    version: str
    replaced_unreadable: bool = False
    """The status already there could not be parsed and was replaced.

    Reported rather than silent: a Manager's own liveness must not stop
    because its own file is corrupt, but overwriting it does lose whatever it
    held, and that is worth saying. D-54's pass-through rule protects OTHER
    machines' files, which a heartbeat never rewrites."""

    conflicts: int = 0


@dataclass
class NotPublished:
    reason: str
    may_have_landed: bool = False
    """True after Unavailable: re-read before concluding anything."""


def publish_heartbeat(
    layer: StateLayer,
    name: str,
    *,
    workers: list[str],
    in_flight: int,
    now: datetime | None = None,
    attempts: int = 3,
) -> Published | NotPublished:
    key = status_key(name)
    if not key:
        return NotPublished(f"{name!r} is not usable as a state key")
    stamp = (
        (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    )

    conflicts = 0
    for _ in range(max(1, attempts)):
        read = layer.read_state(key)
        if isinstance(read, Unavailable):
            return NotPublished(f"could not read {key}: {read.reason}")
        replaced = False
        if isinstance(read, Present):
            status = status_from_json(read.value.decode("utf-8", errors="replace"))
            if status is None:
                status, replaced = ManagerStatus(), True
        else:
            status = ManagerStatus()
        status.name = name
        status.last_seen = stamp
        status.workers = list(workers)
        status.in_flight = in_flight

        written = layer.write_state(key, status_to_json(status).encode(), read.version)
        if isinstance(written, Written):
            return Published(written.version, replaced, conflicts)
        if isinstance(written, Unavailable):
            return NotPublished(
                f"could not write {key}: {written.reason}", may_have_landed=True
            )
        conflicts += 1
    return NotPublished(f"{key}: {conflicts} conflict(s) in a row — someone is writing")


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
    from rite_ai.coordination.schemas import _parse_ts

    last = _parse_ts(status.last_seen)
    if last is None:
        return Liveness(None, f"{key} has no usable last_seen")
    interval = max(1, interval_minutes) * 60
    behind = (now - last).total_seconds()
    return Liveness(max(0, int(behind // interval)), f"last seen {status.last_seen}")


def is_stalled(live: Liveness, *, stall_threshold: int) -> bool:
    """§3.4: stalled once N consecutive heartbeats are missed. "Cannot tell"
    is not stalled — it is a different report, for a human to read."""
    return live.missed is not None and live.missed >= max(1, stall_threshold)
