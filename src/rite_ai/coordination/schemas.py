"""The two files every other Phase 2 component reads and writes (P2-0b).

`owner-lease.json` and `managers/<name>.json` live on the state branch
(§3.3.1). Their shapes are proposals: §2.4.1 names the lease's four fields,
and §3.4 describes the Manager status file only in prose.

**Unknown fields are preserved, deliberately.** Every writer must
read-merge-write the WHOLE state before pushing (§2.4.2), so a Manager
running an older rite will routinely rewrite a file a newer one authored.
If parsing dropped the fields it did not recognise, that rewrite would
silently delete them — the same shape as the delta-push hazard §2.4.2
already warns about, and just as invisible. Round-tripping unknown keys
costs one dict and removes a whole class of version-skew data loss.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

# The fields this version knows. Anything else round-trips untouched.
_LEASE_KNOWN = frozenset({"owner", "acquired", "expires", "priority"})
_STATUS_KNOWN = frozenset({"name", "last_seen", "workers"})


class LeaseVerdict:
    """Why a lease may or may not be taken. Three outcomes, not two.

    `NOT_CREDIBLE` is separate from `EXPIRED` on purpose: both mean the
    lease can be challenged, but only one of them means somebody's clock
    is wrong, and that is worth saying out loud rather than silently
    recovering from (D-54)."""

    HELD = "held"
    EXPIRED = "expired"
    NOT_CREDIBLE = "not_credible"


def _parse_ts(value: object) -> datetime | None:
    """An ISO-8601 timestamp, or None if it is not one.

    None means "unusable", never "now" — a corrupt timestamp defaulting to
    the current time would make an invalid lease look freshly renewed."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class OwnerLease:
    """§2.4.1's lease file. `expires` is what everything turns on."""

    owner: str = ""
    acquired: str = ""
    expires: str = ""
    priority: int = 0
    extra: dict = field(default_factory=dict)

    def verdict(
        self,
        *,
        now: datetime,
        owner_lease_minutes: int,
        skew_tolerance_seconds: int,
    ) -> str:
        """Whether this lease still holds the role, and if not, why.

        Two boundaries, and they guard opposite failure modes:

        **Expiry**, with the skew margin (§2.4.1): expired only once
        `now > expires + skew_tolerance`, never at the bare boundary. This
        protects an incumbent from a challenger whose clock runs fast.

        **Credibility** (D-54): nothing honest can write an `expires`
        further ahead than `owner_lease_minutes + skew_tolerance`, because
        that is the longest lease the configuration permits plus the most
        drift it tolerates. Past that ceiling the lease is invalid and
        therefore challengeable — otherwise a Manager whose clock is a day
        ahead holds the role permanently, which needs no malice, only a
        wrong clock.

        An unparseable or absent `expires` is NOT credible rather than
        expired: it cannot be shown to hold, and it is the same "somebody's
        clock or file is wrong" signal.
        """
        expires = _parse_ts(self.expires)
        if expires is None:
            return LeaseVerdict.NOT_CREDIBLE
        ceiling_seconds = owner_lease_minutes * 60 + skew_tolerance_seconds
        if (expires - now).total_seconds() > ceiling_seconds:
            return LeaseVerdict.NOT_CREDIBLE
        if (now - expires).total_seconds() > skew_tolerance_seconds:
            return LeaseVerdict.EXPIRED
        return LeaseVerdict.HELD


@dataclass
class ManagerStatus:
    """§3.4's Manager heartbeat file, whose shape is prose-only there.

    `workers` is the Manager's worker names. An in-flight count belongs
    here too once capacity routing exists, which is why unknown fields
    round-trip rather than being dropped."""

    name: str = ""
    last_seen: str = ""
    workers: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _split_known(raw: dict, known: frozenset[str]) -> dict:
    return {k: v for k, v in raw.items() if k not in known}


def lease_from_json(text: str) -> OwnerLease | None:
    """A lease, or None when the bytes are not a JSON object.

    None is "could not read", which callers must not render as "no lease" —
    §2.4.2 step 2 treats a missing lease as the first-write case, and an
    unreadable one is emphatically not that."""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    priority = raw.get("priority", 0)
    return OwnerLease(
        owner=str(raw.get("owner", "")),
        acquired=str(raw.get("acquired", "")),
        expires=str(raw.get("expires", "")),
        priority=priority if isinstance(priority, int) else 0,
        extra=_split_known(raw, _LEASE_KNOWN),
    )


def lease_to_json(lease: OwnerLease) -> str:
    data = {
        "owner": lease.owner,
        "acquired": lease.acquired,
        "expires": lease.expires,
        "priority": lease.priority,
        **lease.extra,
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def status_from_json(text: str) -> ManagerStatus | None:
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    workers = raw.get("workers", [])
    return ManagerStatus(
        name=str(raw.get("name", "")),
        last_seen=str(raw.get("last_seen", "")),
        workers=[str(w) for w in workers] if isinstance(workers, list) else [],
        extra=_split_known(raw, _STATUS_KNOWN),
    )


def status_to_json(status: ManagerStatus) -> str:
    data = {
        "name": status.name,
        "last_seen": status.last_seen,
        "workers": status.workers,
        **status.extra,
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
