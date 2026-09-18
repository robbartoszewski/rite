"""The state-layer interface (P2-1a, SPEC §3.3.5, D-20).

Four operations, behind an interface exactly as the ticket backend is
(§6.1), so a team that outgrows git can substitute another store:

    read_state(key)                         -> Present | Absent | Unavailable
    write_state(key, value, expected)       -> Written | Conflict | Unavailable
    append_message(content)                 -> Appended | Unavailable
    read_messages(since, limit)             -> Messages | Unavailable

Four properties are load-bearing, and every backend must honour all of them.
`tests/state_layer_conformance.py` asserts each one against the CONTRACT, never
against an implementation's behaviour.

**The design test for this interface: could a Redis backend pass that suite
unchanged?** git is the default (D-19/D-20) because it needs nothing a team
does not already have, and it gives an audit trail for free. But git-as-CAS
costs a round-trip per operation and degrades under contention, and D-21's
substitutable backends are only real if the interface is not git with a coat
of paint. So: no oids, refs, branches, fetch-then-push or "stale info" appear
below. An operation is a compare-and-swap on ONE value; how that is achieved
is the backend's business.

1. **Compare-and-swap is PER KEY.** A write conflicts only if THAT key changed
   since the read. A concurrent write to another key must not disturb it.

   ⚠ This reverses P2-1a's first cut, which made the version whole-state
   because that is what `--force-with-lease` compares. The justification given
   was that per-key CAS "is not implementable" on git — and that was simply
   wrong. A git backend reads the key's own value, merges, pushes with the
   lease, and when the ref moved because a DIFFERENT key changed it re-merges
   and retries rather than reporting a conflict. The ref-level race is git's
   problem to absorb, not a semantic to export: a key-value store would have
   to serialise every write through one global version to satisfy the old
   contract, which is slower than git and absurd on its own terms.

   It is also better for git. §2.4.2 warns that "a Manager whose heartbeat
   repeatedly loses this ref-level race against other Managers' renewals could
   be marked falsely stalled"; under per-key CAS that race is retried inside
   the backend and never reaches the caller.

2. **A version is an opaque fingerprint OF THE VALUE.** Equal versions mean
   equal bytes. Callers must treat it as opaque — never parse, compare for
   order, or infer a count of writes from it.

   Two consequences, both deliberate. A backend needs no durable counter, only
   the ability to fingerprint bytes it already holds, which every store can do
   (git's blob id is one; a hash is another). And a value rewritten with
   IDENTICAL bytes leaves the version unchanged, so an A→B→A sequence cannot
   make a stale writer win a race it should lose: its decision was based on
   content, and the content is what it read. What this cannot express is "did
   anybody write, even the same bytes" — nothing here needs that, and a
   caller that does needs its own sequence number in the value.

3. **A write preserves every other key.** Values are opaque bytes and the
   layer never parses them, so D-58's first half — pass unreadable content
   through unchanged — holds by construction. D-58's second half, failing
   closed on the decision that needed the content, belongs to the consumer
   that tries to parse it. (For git this means read-merge-write of the whole
   tree, because a force-push replaces it. That is a git implementation
   detail; a key-value backend simply writes one key.)

4. **Absent, present and unavailable are three answers, not two.** Absent is a
   real state ("nothing written yet"), and its version is ABSENT: a caller
   about to create a key expects exactly that. Unavailable is "could not
   check" and must never be read as absent. Neither has a backend-specific
   cause: "unreadable" is not "the blob is corrupt", it is "this value cannot
   be produced".

5. **Conflict and Unavailable are different failures.** Conflict means
   "someone else wrote this key first; re-read and decide". Unavailable means
   "the write may or may not have landed" — §2.4.2 step 5's lost
   acknowledgement. After Unavailable a caller MUST re-read before concluding
   anything; treating it as a lost race is exactly how a Manager stands down
   from a lease it actually holds.

**No batching, no caching, no read-your-writes promise.** Each call is one
operation. A backend whose round-trip is cheap must not inherit machinery that
exists to hide one that is not.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# The version of a key that holds nothing. A write expecting ABSENT succeeds
# only if the key is still empty, which is how a first writer wins a race
# against another first writer (§2.4.2 step 1).
ABSENT = "absent"

# Key syntax is the intersection of what the stores can hold, not any one
# store's rule: `owner-lease.json`, `managers/<name>.json`, `claims.json`
# (§3.3.1). Relative, forward-slashed, no traversal, no empty segment — safe
# as a path, as a tree entry, and as a flat string key.
_KEY = re.compile(
    r"^(?!/)"  # relative
    r"(?!.*(?:^|/)\.\.?(?:/|$))"  # no `.` or `..` segment
    r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"  # no empty segment
)


def valid_key(key: str) -> bool:
    return bool(_KEY.match(key))


def fingerprint(value: bytes) -> str:
    """A version for these bytes: equal iff the bytes are equal.

    Offered rather than mandated. A backend that already has a content
    address for a value (git's blob id) uses that instead; one that does not
    can compute this from bytes it is holding anyway, with no durable
    counter and nothing to keep in step across processes.
    """
    return hashlib.sha256(value).hexdigest()


# --- read_state ---


@dataclass(frozen=True)
class Present:
    value: bytes
    version: str


@dataclass(frozen=True)
class Absent:
    """Nothing is stored under this key. `version` is ABSENT — the version a
    caller about to create this key must expect."""

    version: str = ABSENT


@dataclass(frozen=True)
class Unavailable:
    """Could not check. Never "absent"."""

    reason: str


# --- write_state ---


@dataclass(frozen=True)
class Written:
    version: str


@dataclass(frozen=True)
class Conflict:
    """Someone else wrote THIS key first. `current` is the version now in
    force, when the backend can tell."""

    current: str | None = None


# --- messages ---


@dataclass(frozen=True)
class Appended:
    cursor: str


@dataclass(frozen=True)
class Message:
    cursor: str
    content: str


# What a message's `content` IS: a full commit message in P2-0d's convention
# (`message_log.format_message`) — a human subject, and meaning carried in
# `Rite-` trailers in the final paragraph. The state layer transports it
# opaquely and never parses it; a consumer calls `message_log.parse_message`
# on what `read_messages` returns. Keeping the two separate means a backend
# only has to carry text faithfully, and the format can evolve without any
# backend changing.


@dataclass(frozen=True)
class Messages:
    """In append order. `cursor` of the last one is what a caller passes as
    `since` next time — an opaque position, not a timestamp, because
    timestamps are exactly what §2.4.1's clock-skew section says cannot be
    trusted across machines."""

    items: list[Message] = field(default_factory=list)


class StateLayer(ABC):
    @abstractmethod
    def read_state(self, key: str) -> Present | Absent | Unavailable: ...

    @abstractmethod
    def write_state(
        self, key: str, value: bytes, expected_version: str
    ) -> Written | Conflict | Unavailable: ...

    @abstractmethod
    def append_message(self, content: str) -> Appended | Unavailable: ...

    @abstractmethod
    def read_messages(
        self, since: str | None = None, limit: int | None = None
    ) -> Messages | Unavailable:
        """Messages after `since`, or all of them; at most `limit`, newest-end.

        `limit` is the LAST n, not the first: every caller that wants a few
        wants the recent few, and reading the whole log to drop all but the
        tail is the one cost in this system that grows for ever. The log is
        append-only by design (§3.3.2), so nothing else does — measured on
        the git backend at about 9ms per message, which is ~9 seconds of
        `rite doctor` for a fleet a year old printing five events.

        Backend-agnostic on purpose, which is the test this interface is held
        to: git does `rev-list -n`, a key-value store does a reverse range
        read. Neither has to fetch what it will not return.

        Ordering does not change: still append order, oldest first, so a
        caller can keep passing the last `cursor` back as `since`.

        `limit` below 1 is a caller's mistake, not a store condition, and
        raises rather than returning `Unavailable` — which would say the
        store could not be read when it was never asked.
        """
