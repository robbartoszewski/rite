"""The records the fixes define, read here now that they have landed.

Each function returned None while its fix was unbuilt, and `checks` turns None
into PENDING — never into a pass. v0.7.0a10 merged SCRUM-59, 64, 71 and 72, so
each reader below now reads the record its fix writes, through the INSTALLED
rite wherever rite has the function that knows where the record lives.

⚠ **Two of `checks.STAGES` are not stages in SCRUM-72's log.** 72 persists six
transitions — defined, decomposed, approved, stepping, recomposed, delivery
requested — and the plan's `approached` and `step_reviewed` are enforced
elsewhere: a Level-2 approach is its own persisted record per subtask
(`local.level2`, and `cleared_to_run` refuses an exec step without it), and a
step review is a subtask reaching `accepted`, which happens only after rite
runs the subtask's own verify (RL-7). Both are real, persisted and
un-skippable; neither is a row in the stage log.

So `stage_log` reads all three records and merges them. What it must not do is
*invent* the two: each is included only when its own artefact is on disk, and
omitted when it is not — so a run that skipped the Level-2 approach produces a
sequence that is missing `approached`, and `pipeline_stages_in_order` FAILS.
`approached` carries `level2.Stored.at`, a real timestamp. `step_reviewed` has
none to carry (a `Subtask` records `status`, not when it changed), so it is
placed by the invariant that puts it there — after `stepping`, before
`recomposed` — and its evidence says so rather than implying a measurement.
"""

from __future__ import annotations

import json

from tools.e2e_v071.observe import Observer

# SCRUM-72's own stage names -> the plan's names in `checks.STAGES`.
_STAGE_NAMES = {
    "defined": "defined",
    "decomposed": "decomposed",
    "approved": "plan_reviewed",
    "stepping": "executed",
    "recomposed": "recomposed",
    "delivery requested": "delivery_requested",
    # Kept under its own name on purpose: a rejection is a real transition, and
    # a sequence carrying it will not match STAGES, which is correct.
    "rejected": "rejected",
}

_READ_PIPELINE = r"""
import json
from pathlib import Path
from rite_ai.local import plan_state, stage, level2
from rite_ai.local import decomposition as d

root = Path('.').resolve()
st = plan_state.layer(root)
out = {}
for tid in %(tickets)r:
    entry = {"log": [], "approaches": [], "accepted": [], "unreadable": ""}
    read = stage.read(st, tid)
    if read.unavailable or read.error:
        entry["unreadable"] = read.unavailable or read.error
    elif read.record is not None:
        entry["log"] = [
            {"from": t.frm, "to": t.to, "at": t.at} for t in read.record.log
        ]
    plan = d.read(st, tid).plan
    if plan is not None:
        for sub in plan.subtasks:
            got = level2.read_approach(st, tid, sub.id)
            if got is not None and not isinstance(got, str):
                entry["approaches"].append({"subtask": sub.id, "at": got.at})
            if sub.status == d.ACCEPTED:
                entry["accepted"].append(sub.id)
    out[tid] = entry
print(json.dumps(out))
"""


def stage_log(obs: Observer, tickets: dict) -> dict | None:
    """{ticket key: [stage, ...]} in the order the records were written.

    `tickets` is the harness's key -> board id map, and the result is keyed by
    KEY, because that is what `Evidence.pipeline_keys` holds.
    """
    ids = sorted({str(v) for v in tickets.values() if v})
    if not ids:
        return {}
    raw = obs._ask_rite(_READ_PIPELINE % {"tickets": ids})  # noqa: SLF001
    if not isinstance(raw, dict):
        return None
    out: dict[str, list[str]] = {}
    for key, board_id in tickets.items():
        entry = raw.get(str(board_id)) or {}
        out[key] = _sequence(entry)
    return out


def _sequence(entry: dict) -> list[str]:
    """One ticket's stages, in order, from the three records that hold them."""
    moments: list[tuple[float, int, str]] = []
    # The stage log itself. `order` breaks ties at an equal `at` by keeping the
    # order the log was written in, which is the order that actually happened.
    for order, row in enumerate(entry.get("log") or []):
        name = _STAGE_NAMES.get(str(row.get("to") or ""))
        if name:
            moments.append((float(row.get("at") or 0.0), order, name))
    # The Level-2 approaches, at the earliest one's own recorded time.
    approaches = entry.get("approaches") or []
    if approaches:
        first = min(float(a.get("at") or 0.0) for a in approaches)
        moments.append((first, -1, "approached"))
    moments.sort(key=lambda m: (m[0], m[1]))
    names = [name for _at, _order, name in moments]
    # The step review has no timestamp of its own, so it goes where the code
    # guarantees it is: after `executed`, before `recomposed`. Absent when no
    # subtask was accepted, which is what makes its absence visible.
    if entry.get("accepted"):
        names = _place_step_review(names)
    return names


def _place_step_review(names: list[str]) -> list[str]:
    if "executed" not in names:
        # Nothing to place it after. Appending it anywhere else would assert an
        # order no record supports.
        return names
    at = names.index("executed") + 1
    while at < len(names) and names[at] == "executed":
        at += 1
    return names[:at] + ["step_reviewed"] + names[at:]


_READ_ASKED = r"""
import json
from pathlib import Path
from rite_ai.local import plan_review

root = Path('.').resolve()
out = []
where = plan_review.asked_dir(root)
if where.is_dir():
    for path in sorted(where.glob("*.json")):
        asked = plan_review.read_asked(root, path.stem)
        if asked is None:
            continue
        out.append({
            "ticket": asked.ticket,
            "to": asked.reviewer,
            "at": asked.at,
            "request": asked.request,
            "author": asked.author,
            "asks": asked.asks,
        })
print(json.dumps(out))
"""


def plan_review_requests(obs: Observer, owner: str) -> list | None:
    """[{ticket, to, at, ...}] for every plan review asked of a reviewer.

    SCRUM-72 §3.3b puts the request in `plan_review.asked_dir`, which is
    outside every Manager's grant precisely so no Manager can invent a request
    addressed to itself — so it is read through the installed rite's own
    locator rather than by re-spelling the path here.

    ⚠ A request is REMOVED when its verdict is honoured (`forget_asked`), so a
    completed review leaves nothing behind. The harness samples every poll and
    keeps the union, which is why this returns what is on disk now and the
    driver accumulates.
    """
    del owner  # every request carries its own reviewer; the check reads that
    raw = obs._ask_rite(_READ_ASKED)  # noqa: SLF001
    return raw if isinstance(raw, list) else None


_READ_REQUESTS = r"""
import json
from pathlib import Path
from rite_ai.managers import broker

root = Path('.').resolve()
out = []
for manager in %(managers)r:
    where = broker.requests_dir(root, manager)
    if not where.is_dir():
        continue
    for path in sorted(where.iterdir()):
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({"by": manager, "at": path.stat().st_mtime, "raw": raw.strip()})
print(json.dumps(out))
"""


def lifecycle_requests(obs: Observer, managers: list[str]) -> list | None:
    """[{op, worker, at, by}] for every `rite request` a Manager filed.

    SCRUM-59's requests are files in the Manager's own `requests/` directory
    (`broker.requests_dir`), taken and honoured by the supervisor outside the
    sandbox boundary.

    ⚠ **The supervisor DELETES a request as it takes it** (`take_requests`), so
    a request that was honoured is gone from disk. That is why the driver
    samples this every poll and keeps the union — a single read at judging time
    would see only what is still queued, and report an honoured request as one
    that never happened.
    """
    raw = obs._ask_rite(_READ_REQUESTS % {"managers": list(managers)})  # noqa: SLF001
    if not isinstance(raw, list):
        return None
    out = []
    for row in raw:
        op, worker = _parse_request(str(row.get("raw") or ""))
        out.append(
            {
                "op": op,
                "worker": worker,
                "at": row.get("at") or 0.0,
                "by": row.get("by") or "",
            }
        )
    return out


def _parse_request(raw: str) -> tuple[str, str]:
    """(op, worker) from a request file, whatever shape it is written in.

    Tried as JSON first and as whitespace-separated words second, because the
    file is rite's to format and this must not become a second definition of
    it: an unparsed request is reported with an empty op rather than dropped,
    so a change in shape shows up as a check failing and not as silence.
    """
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if isinstance(body, dict):
        return str(body.get("op") or ""), str(body.get("worker") or "")
    words = raw.split()
    return (words[0] if words else ""), (words[1] if len(words) > 1 else "")


# SCRUM-71's stable event kinds, rather than matching on prose.
_RECONCILED = "reconciled"
_IDLE_SLOT_HELD = "idle-slot-held"
_ESCALATION_KINDS = (_IDLE_SLOT_HELD,)


def reconcile_reports(obs: Observer, managers: list[str]) -> list | None:
    """[{at, released, told, escalated}] for each reconciliation pass.

    SCRUM-64 records a pass through SCRUM-71's recorder, which writes a journal
    entry whose kind is one of `recording.EVENTS`. The journal is the only place
    it lands, and reading it here is sound where reading it from `src/` would
    not be: `test_nothing_in_rite_reads_the_journal` forbids any module in
    `src/` from even locating the directory, and this is an instrument outside
    it that the Owner could read by hand.

    `escalated` is True for an entry that raised a held slot to the Owner —
    the thing reconciliation should have settled itself.
    """
    rows = []
    for manager in managers:
        try:
            where = obs.manager_dir(manager)
        except RuntimeError:
            continue
        for entry in obs.journal(where):
            text = str(entry.get("text") or "")
            if _RECONCILED not in text and not any(
                kind in text for kind in _ESCALATION_KINDS
            ):
                continue
            rows.append(
                {
                    "at": entry.get("mtime") or 0.0,
                    "manager": manager,
                    "released": _RECONCILED in text,
                    "told": True,  # a journal entry IS the Owner being told
                    "escalated": any(kind in text for kind in _ESCALATION_KINDS),
                    "entry": entry.get("name") or "",
                }
            )
    rows.sort(key=lambda r: r["at"])
    return rows
