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
_STATUS_KNOWN = frozenset({"name", "last_seen", "workers", "in_flight"})


# --- A known key whose value has a shape this version does not expect ---
#
# `extra` keeps keys this version does not know. That is not enough: a newer
# rite can change the SHAPE of a key this version does know — `workers` as a
# list of objects, `last_seen` as a structure — and coercing that into the
# old type ("" / 0 / []) and writing it back erases the newer value exactly as
# dropping an unknown key would (P2-1d). So the raw value is kept in `drifted`
# and written back — but only while this writer has not set the field. A
# heartbeat that assigns `last_seen`, or fills a placeholder `workers` list in
# place, has made its own value the truth, and stale drift must never override
# it. Nothing ever DECIDES on a drifted value: the typed field holds the
# placeholder, which every reader already treats as unusable.


class _KeepsDrift:
    """Mixin: assigning a known field discards the drifted raw value for it."""

    _TYPED: frozenset[str] = frozenset()

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._TYPED and self.__dict__.get("_ready"):
            self.__dict__.get("drifted", {}).pop(name, None)
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_ready", True)


def _text(raw: dict, key: str, drifted: dict) -> str:
    value = raw.get(key, "")
    if isinstance(value, str):
        return value
    drifted[key] = value
    return ""


def _int(raw: dict, key: str, drifted: dict) -> int:
    value = raw.get(key, 0)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    drifted[key] = value
    return 0


def _texts(raw: dict, key: str, drifted: dict) -> list[str]:
    value = raw.get(key, [])
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    drifted[key] = value
    return []


_PLACEHOLDER = {str: "", int: 0, list: []}


def _emit(typed: dict, drifted: dict, extra: dict) -> dict:
    """Known fields, with any untouched drifted value restored, then the keys
    this version does not know."""
    data = {}
    for key, value in typed.items():
        if key in drifted and value == _PLACEHOLDER[type(value)]:
            data[key] = drifted[key]
        else:
            data[key] = value
    return {**data, **extra}


class LeaseVerdict:
    """Why a lease may or may not be taken. Three outcomes, not two.

    `NOT_CREDIBLE` is separate from `EXPIRED` on purpose: both mean the
    lease can be challenged, but only one of them means somebody's clock
    is wrong, and that is worth saying out loud rather than silently
    recovering from (D-55)."""

    HELD = "held"
    EXPIRED = "expired"
    NOT_CREDIBLE = "not_credible"


def parse_timestamp(value: object) -> datetime | None:
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


@dataclass(eq=True)
class OwnerLease(_KeepsDrift):
    """§2.4.1's lease file. `expires` is what everything turns on."""

    _TYPED = frozenset({"owner", "acquired", "expires", "priority"})

    owner: str = ""
    acquired: str = ""
    expires: str = ""
    # WRITTEN FOR AUDIT, IGNORED ON READ (D-56). What the holder believed its
    # priority was at acquisition — useful for reconstructing why a
    # promotion went the way it did. Priority for any DECISION is the order
    # of `coordination.managers`, never this: a stale lease must not be able
    # to override a deliberate reorder of that list. Do not delete this as
    # dead weight, and do not start reading it.
    priority: int = 0
    extra: dict = field(default_factory=dict)
    drifted: dict = field(default_factory=dict)

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

        **Credibility** (D-55): nothing honest can write an `expires`
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
        expires = parse_timestamp(self.expires)
        if expires is None:
            return LeaseVerdict.NOT_CREDIBLE
        ceiling_seconds = owner_lease_minutes * 60 + skew_tolerance_seconds
        if (expires - now).total_seconds() > ceiling_seconds:
            return LeaseVerdict.NOT_CREDIBLE
        if (now - expires).total_seconds() > skew_tolerance_seconds:
            return LeaseVerdict.EXPIRED
        return LeaseVerdict.HELD


@dataclass(eq=True)
class ManagerStatus(_KeepsDrift):
    """§3.4's Manager heartbeat file, whose shape is prose-only there.

    `workers` is the Manager's worker names. `in_flight` is how many tasks
    it has in progress (P2-4a): capacity cannot be enforced without it, and
    a field added later could not be trusted until every machine upgraded."""

    _TYPED = frozenset({"name", "last_seen", "workers", "in_flight"})

    name: str = ""
    last_seen: str = ""
    workers: list[str] = field(default_factory=list)
    in_flight: int = 0
    extra: dict = field(default_factory=dict)
    drifted: dict = field(default_factory=dict)


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
    drifted: dict = {}
    return OwnerLease(
        owner=_text(raw, "owner", drifted),
        acquired=_text(raw, "acquired", drifted),
        expires=_text(raw, "expires", drifted),
        priority=_int(raw, "priority", drifted),
        extra=_split_known(raw, _LEASE_KNOWN),
        drifted=drifted,
    )


def lease_to_json(lease: OwnerLease) -> str:
    data = _emit(
        {
            "owner": lease.owner,
            "acquired": lease.acquired,
            "expires": lease.expires,
            "priority": lease.priority,
        },
        lease.drifted,
        lease.extra,
    )
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def status_from_json(text: str) -> ManagerStatus | None:
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    drifted: dict = {}
    return ManagerStatus(
        name=_text(raw, "name", drifted),
        last_seen=_text(raw, "last_seen", drifted),
        workers=_texts(raw, "workers", drifted),
        in_flight=_int(raw, "in_flight", drifted),
        extra=_split_known(raw, _STATUS_KNOWN),
        drifted=drifted,
    )


def status_to_json(status: ManagerStatus) -> str:
    data = _emit(
        {
            "name": status.name,
            "last_seen": status.last_seen,
            "workers": status.workers,
            "in_flight": status.in_flight,
        },
        status.drifted,
        status.extra,
    )
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
