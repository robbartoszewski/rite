"""The state-layer interface (P2-1a, SPEC §3.3.5, D-20).

Four operations, behind an interface exactly as the ticket backend is
(§6.1), so a team that outgrows git can substitute another store:

    read_state(key)                         -> Present | Absent | Unavailable
    write_state(key, value, expected)       -> Written | Conflict | Unavailable
    append_message(content)                 -> Appended | Unavailable
    read_messages(since)                    -> Messages | Unavailable

Four properties are load-bearing, and every backend must honour all of them.
`tests/state_layer_conformance.py` asserts each one, and it is the contract
the git backend (P2-1b) has to pass unchanged.

1. **The version is the version of the WHOLE state, not of a key.**
   §3.3.5's signature reads as per-key compare-and-swap; §2.4.2 says the CAS
   is whole-ref. Git can only honour the second — `--force-with-lease`
   compares one ref, and every key lives on it — so a write to one key must
   CONFLICT if any key changed since the read. A per-key contract would let
   the local backend pass a test the git backend structurally cannot.

2. **A write preserves every other key's bytes verbatim.** A single-key write
   IS §2.4.2's read-merge-write: the new state is the snapshot at
   `expected_version` with one key replaced. Values are opaque bytes and the
   layer never parses them, so D-54's first half — pass unreadable content
   through unchanged — holds here by construction. D-54's second half,
   failing closed on the decision that needed the content, belongs to the
   consumer that tries to parse it.

3. **Absent, present and unavailable are three answers, not two.** Absent is
   a real state ("nothing written yet", and §2.4.2 step 1's first-write
   case). Unavailable is "could not check" — the store could not be reached
   or its exclusion does not work — and must never be read as absent.

4. **Conflict and Unavailable are different failures.** Conflict means
   "someone else wrote first; re-read and decide". Unavailable means "the
   write may or may not have landed" — §2.4.2 step 5's lost acknowledgement.
   After Unavailable a caller MUST re-read before concluding anything;
   treating it as a lost race is exactly how a Manager stands down from a
   lease it actually holds.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# The version of a state that has never been written. A write expecting
# ABSENT succeeds only if nothing has been written since — the local
# equivalent of `--force-with-lease=state:` (expected-absent), measured to
# reject a second first-writer against a real git remote.
ABSENT = "absent"

# Keys are the state branch's file paths (§3.3.1): `owner-lease.json`,
# `managers/<name>.json`, `claims.json`. Relative, forward-slashed, no
# traversal — a key is also a path inside a git tree.
_KEY = re.compile(
    r"^(?!/)"  # relative
    r"(?!.*(?:^|/)\.\.?(?:/|$))"  # no `.` or `..` segment
    r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"  # no empty segment
)


def valid_key(key: str) -> bool:
    return bool(_KEY.match(key))


# --- read_state ---


@dataclass(frozen=True)
class Present:
    value: bytes
    version: str


@dataclass(frozen=True)
class Absent:
    """Nothing is stored under this key. `version` is still the whole
    state's version — possibly ABSENT if no state exists at all — because a
    caller about to write this key needs it."""

    version: str


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
    """Someone else wrote first. `current` is the version now in force,
    when the backend can tell."""

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
    def read_messages(self, since: str | None = None) -> Messages | Unavailable: ...
