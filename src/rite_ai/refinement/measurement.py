"""A host measurement of a definition-of-done item: signed, attributed, audited.

S31. Some items need a measurement a Worker cannot take inside its sandbox (a
nested `sandbox_apply` is denied there, "Operation not permitted"). The record
says so (`Record.host_measured`), agreed at refinement; the Worker is told the
item is not its to run; and the host runs it and records the result here with
`rite refine measured`. Publishing waits for that result (`holds`).

**What a result carries, and what binds it.** Who measured (the owner, by the
one attribution `refinement.attribution` gives every answer, with the channel:
always this machine's terminal), when, pass or fail, and the SHA-256 of the
output the person gives as evidence. It is bound to the ticket, the board, the
refinement record's id and the item's own text hash, so a result cannot be
moved to another item, another record or another board.

**Tamper-evident, the way the refinement record is.** It is signed with rite's
refinement key (`refinement.key`), which no Manager's or Worker's sandbox can
read, over the parsed payload. An edited result, or one written by anything
without the key, does not verify, and an unverified result counts as none.

**Written twice, read from the one rite controls.** Posted on the ticket as a
comment and read back (attributable on the board, as a record is), and
appended to an audit log, one JSON object per line, never rewritten: the same
convention as the claims ledger's force-release audit. The log lives beside
PB1's publish records, under no path any profile grants, and it is what the
publish hold reads, so the hold needs no board read and a board comment
deleted later does not un-measure anything. A line deleted from the log makes
that item unmeasured again, which holds publishing: the failure is closed.

⚠ **What it cannot prove.** Run on the host as the person, it is attested in
the TRQ10 sense: rite cannot tell the person from a session running as them,
and the record says `via: terminal`. It proves that someone holding the host's
key recorded this result for this exact item, and what output they cited; not
that the measurement was performed as described.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.refinement import record as rec

MARKER = "rite-host-measurement"
"""Its fence and kind. Deliberately not containing `rec.MARKER`
("rite-refinement"): a measurement comment must never read as a record claim,
or every measured ticket would be UNREADABLE."""

VERSION = 1
PASS = "pass"
FAIL = "fail"
RESULTS = (PASS, FAIL)
LOG = "host-measurements.jsonl"


def log_path(root: Path) -> Path:
    """Beside the publish records: outside every Manager's grant."""
    from rite_ai.publishing.record import _records_dir  # noqa: PLC2701

    return _records_dir(Path(root)) / LOG


@dataclass(frozen=True)
class Measurement:
    payload: dict

    @property
    def result(self) -> str:
        return str(self.payload.get("result", ""))

    @property
    def item(self) -> int:
        return int(self.payload.get("item", -1))


def build(
    *,
    record: rec.Record,
    item: int,
    result: str,
    output: bytes,
    by: dict,
    key: bytes,
    now: datetime | None = None,
) -> Measurement:
    """A signed result for item `item` (0-based) of `record`. Refuses one
    that could not be valid, rather than repairing it."""
    if result not in RESULTS:
        raise ValueError(f"a result is one of {RESULTS}, not {result!r}")
    if not record.is_host_measured(item):
        raise ValueError(
            f"item {item + 1} of record {record.record_id} is not one the host "
            "measures; only an item agreed as host-measured takes a host result"
        )
    body = {
        "kind": MARKER,
        "v": VERSION,
        "ticket": record.ticket,
        "board": record.board,
        "record_id": record.record_id,
        "item": item,
        "item_sha256": rec.text_sha256(record.definition_of_done[item]),
        "result": result,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "output_bytes": len(output),
        "measured_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "measured_by": by,
    }
    body["measurement_id"] = rec.record_id_of(body)
    body["mac"] = rec.mac_of(body, key)
    return Measurement(body)


def verifies(payload: dict, key: bytes | None, record: rec.Record, item: int) -> bool:
    """Whether `payload` is a genuine result for item `item` of `record`:
    signed with this key, and bound to this ticket, board, record and item
    text. Anything else is no result."""
    if key is None or not isinstance(payload, dict):
        return False
    if payload.get("kind") != MARKER or payload.get("v") != VERSION:
        return False
    if not rec.mac_verifies(payload, key):
        return False
    return (
        payload.get("ticket") == record.ticket
        and payload.get("board") == record.board
        and payload.get("record_id") == record.record_id
        and payload.get("item") == item
        and 0 <= item < len(record.definition_of_done)
        and payload.get("item_sha256")
        == rec.text_sha256(record.definition_of_done[item])
        and payload.get("result") in RESULTS
    )


def render(m: Measurement, record: rec.Record) -> str:
    """The comment: what a person reads, then the block rite can read."""
    from rite_ai.refinement import attribution

    p = m.payload
    return "\n".join(
        [
            f"**rite: host measurement of {p['ticket']}, item {p['item'] + 1}: "
            f"{p['result'].upper()}**",
            "",
            f"> {record.definition_of_done[p['item']]}",
            "",
            f"Measured by {attribution.describe(p['measured_by'])}, at "
            f"{p['measured_at']}. Output: {p['output_bytes']} bytes, sha256 "
            f"{p['output_sha256']}. For refinement record {p['record_id']}.",
            "",
            "Written by rite and signed; an edited copy does not verify.",
            "",
            f"```{MARKER}",
            json.dumps(p, sort_keys=True, ensure_ascii=False, indent=1),
            "```",
        ]
    )


def append(root: Path, m: Measurement) -> None:
    """One line, appended, never rewritten (the audit convention)."""
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(rec.canonical(m.payload) + "\n")


def logged(root: Path) -> list[dict]:
    path = log_path(root)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def latest_for(
    entries: list[dict], key: bytes | None, record: rec.Record, item: int
) -> dict | None:
    """The last genuine result for this item of this record, or None. The
    last one stands: a FAIL re-measured as a PASS is a PASS, and the other
    way round."""
    found = None
    for payload in entries:
        if verifies(payload, key, record, item):
            found = payload
    return found


def holds(root: Path, record: rec.Record, key: bytes | None) -> list[str]:
    """Why publishing `record`'s work must wait, one line per host-measured
    item without a genuine PASS; [] when none does. With no key nothing can
    be verified, so every host item holds."""
    entries = logged(root)
    reasons = []
    for item in record.host_measured:
        got = latest_for(entries, key, record, item)
        if got is None:
            reasons.append(f"item {item + 1} has no host measurement recorded")
        elif got.get("result") != PASS:
            reasons.append(
                f"item {item + 1} was measured on the host and FAILED "
                f"({got.get('measured_at')})"
            )
    return reasons
