"""Who holds the role, and who has gone quiet (§2.3, §2.4.1, §3.4).

§2.3 makes it the Owner's job to "detect stalled Managers (no heartbeat) and
surface them to the human", and nothing did. A fleet whose state can only be
read by another machine is a fleet nobody can debug at three in the morning:
the first question in any incident is who is Owner, and the second is who
stopped answering.

Three answers per Manager, never two — alive, stalled, or cannot tell — for
the same reason the state layer has three (D-58): "no heartbeat I could
read" and "no heartbeat" are different facts, and only one of them means a
machine is gone.

The lease is reported with its verdict, not just its contents. A lease that
exists but expired is not an Owner, and one whose expiry is further ahead
than any honest writer could have set it means somebody's clock is wrong —
which §2.4.1 requires be said distinctly rather than silently recovered from
(D-59).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.heartbeat import is_stalled, liveness
from rite_ai.coordination.lease import LEASE_KEY
from rite_ai.coordination.schemas import LeaseVerdict, lease_from_json
from rite_ai.coordination.state_layer import Absent, Present, StateLayer, Unavailable

ALIVE = "alive"
STALLED = "stalled"
UNKNOWN = "unknown"


@dataclass
class ManagerView:
    name: str
    state: str
    detail: str = ""
    is_this_machine: bool = False


@dataclass
class Overview:
    owner: str = ""
    """Who the lease names. Empty when nobody holds it or it cannot be read."""
    verdict: str = ""
    expires: str = ""
    managers: list[ManagerView] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """What a human has to act on: a stalled Manager, a wrong clock."""
    notes: list[str] = field(default_factory=list)
    """Facts about the LEASE a human should know but cannot act on. Per
    Manager facts belong on that Manager's line, once."""

    @property
    def has_owner(self) -> bool:
        return bool(self.owner) and self.verdict == LeaseVerdict.HELD


def read_overview(
    layer: StateLayer,
    config: CoordinationConfig,
    *,
    now: datetime,
    heartbeat: HeartbeatConfig | None = None,
    this_machine: str | None = None,
) -> Overview:
    heartbeat = heartbeat or HeartbeatConfig()
    overview = Overview()

    read = layer.read_state(LEASE_KEY)
    if isinstance(read, Unavailable):
        # Not "there is no Owner" — the difference between those two is the
        # whole reason this layer reports three outcomes.
        overview.notes.append(f"the lease could not be read: {read.reason}")
    elif isinstance(read, Absent):
        overview.notes.append("no lease has ever been written — nobody is Owner yet")
    elif isinstance(read, Present):
        lease = lease_from_json(read.value.decode("utf-8", "replace"))
        if lease is None:
            overview.problems.append(
                "the lease file is not readable JSON — no Owner can be "
                "established until it is replaced"
            )
        else:
            overview.owner = lease.owner
            overview.expires = lease.expires
            overview.verdict = lease.verdict(
                now=now,
                owner_lease_minutes=config.owner_lease_minutes,
                skew_tolerance_seconds=config.skew_tolerance_seconds,
            )
            if overview.verdict == LeaseVerdict.NOT_CREDIBLE:
                # D-59, and §2.4.1 wants it said distinctly: this is the only
                # signal anybody gets that a machine's clock is wrong.
                overview.problems.append(
                    f"{lease.owner or 'somebody'} holds a lease expiring "
                    f"{lease.expires!r}, which no honest writer could have set "
                    f"(ceiling: {config.owner_lease_minutes}m + "
                    f"{config.skew_tolerance_seconds}s) — a clock is wrong"
                )
            elif overview.verdict == LeaseVerdict.EXPIRED:
                overview.notes.append(
                    f"the lease from {lease.owner or 'somebody'} expired at "
                    f"{lease.expires} — the next Manager in priority order "
                    "takes the role"
                )

    for name in config.managers:
        live = liveness(
            layer, name, now=now, interval_minutes=heartbeat.interval_minutes
        )
        mine = this_machine is not None and name == this_machine
        if not live.known:
            # No note: the Manager's own line already says "unknown" and
            # why. Saying it twice in one report teaches the reader to skim,
            # which is how the line that mattered gets missed.
            overview.managers.append(ManagerView(name, UNKNOWN, live.detail, mine))
        elif is_stalled(live, stall_threshold=heartbeat.stall_threshold):
            overview.managers.append(
                ManagerView(name, STALLED, f"{live.missed} intervals missed", mine)
            )
            # §2.3: this is the thing the Owner owes a human.
            overview.problems.append(
                f"{name} has missed {live.missed} heartbeats "
                f"(threshold {heartbeat.stall_threshold}) — its work can be "
                "handed over"
            )
        else:
            overview.managers.append(ManagerView(name, ALIVE, live.detail, mine))
    return overview


def format_overview(overview: Overview) -> list[str]:
    """Lines for `doctor`. The role first, because it is the first question."""
    lines: list[str] = []
    if overview.has_owner:
        lines.append(
            f"coordination: Owner is {overview.owner} (until {overview.expires})"
        )
    elif overview.owner:
        lines.append(
            f"coordination: no current Owner — {overview.owner}'s lease is "
            f"{overview.verdict}"
        )
    else:
        lines.append("coordination: no current Owner")
    for manager in overview.managers:
        marker = " (this machine)" if manager.is_this_machine else ""
        detail = f" — {manager.detail}" if manager.detail else ""
        lines.append(f"coordination: {manager.name}: {manager.state}{marker}{detail}")
    return lines
