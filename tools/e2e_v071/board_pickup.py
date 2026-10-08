"""Does the real board hand this Manager the tickets rite assigned it? (SCRUM-79)

**Why this is not a unit test.** SCRUM-79 was a driver that asked the board
`TicketFilter(assignee=<manager>)` while rite assigns by LABEL. On a real
GitHub board that returns nothing, every cycle, silently — and the fleet looks
healthy while idle. It survived from `eb32d89` (L-6) to v0.7.0a10 because every
test of the driver used a board whose `list_tickets(self, filters=None)` ignored
`filters` and returned everything. A board that answers every question the same
way cannot tell a right question from a wrong one.

So the guard that must exist is one no fake can satisfy: ask the REAL board,
before a fleet is started, and refuse the run if the Manager picks up nothing.

⚠ **It captures the filter rather than asserting one.** Hard-coding `label=`
here would be a second definition of rite's assignment, and the next time the
driver changes its question this file would go on checking the old one — which
is the failure mode, not the fix. Instead `_local_tier_tickets` is called with
the real board wrapped in a recorder, the filters it actually sent are kept,
and each is replayed against the real board. If the question the driver asks
returns nothing, that is SCRUM-79 again and the run is refused.

⚠ **Why the driver's own return value is not the assertion.**
`_local_tier_tickets` also requires each ticket to be REFINED and held by a
LOCAL Worker of that Manager. At preflight time no Worker has been started yet,
so an empty return is correct and expected. The board read underneath it is the
thing that must not be empty, and it is what this isolates.
"""

from __future__ import annotations

import json

_PROBE = r"""
import json
from pathlib import Path
from rite_ai.cli.main import _local_tier_tickets
from rite_ai.config.parse import parse_config
from rite_ai.tickets import create_backend_from_config
from rite_ai.tickets.interface import BackendError

root = Path('.').resolve()
cfg = parse_config(root / '.rite' / 'config.yaml')
real = create_backend_from_config(cfg.ticket_backend)
out = {"backend": type(real).__name__, "asked": [], "control": None, "error": ""}

if isinstance(real, BackendError):
    out["error"] = "no usable ticket backend: %s" % real.message
    print(json.dumps(out)); raise SystemExit(0)


def ids_of(page):
    if isinstance(page, BackendError):
        return {"error": page.message}
    found = getattr(page, "tickets", page) or []
    return {"ids": [str(getattr(t, "id", "")) for t in found]}


# CONTROL: unfiltered. If this is empty the board itself is empty or unreadable,
# and nothing about a filter can be concluded.
out["control"] = ids_of(real.list_tickets(None))


class Recorder:
    # The real board, with every filter it is asked kept.
    def __init__(self, inner):
        self.inner = inner
        self.filters = []

    def list_tickets(self, filters=None):
        self.filters.append(filters)
        return self.inner.list_tickets(filters)

    def __getattr__(self, name):
        return getattr(self.inner, name)


rec = Recorder(real)
_tickets, _why = _local_tier_tickets(root, rec, __MANAGER__)
for f in rec.filters:
    out["asked"].append({
        "filter": {k: getattr(f, k, None) for k in
                   ("status", "assignee", "label", "labels")},
        "result": ids_of(real.list_tickets(f)),
    })
print(json.dumps(out))
"""


def problem(obs, manager: str, expect: list[str]) -> str:
    """Empty when the real board hands `manager` its tickets, else why not.

    `expect` is the run's own ticket ids. The assertion is an INTERSECTION, not
    equality: a board may legitimately carry tickets this run did not create.
    """
    try:
        probe = _PROBE.replace("__MANAGER__", repr(manager))
        raw = obs._ask_rite(probe)  # noqa: SLF001
    except RuntimeError as e:
        return f"the board could not be read through the installed rite: {e}"
    if not isinstance(raw, dict):
        return f"the board probe returned {type(raw).__name__}, not a record"
    if raw.get("error"):
        return str(raw["error"])

    wanted = {str(t) for t in expect if t}
    control = raw.get("control") or {}
    if control.get("error"):
        return f"the board could not be listed at all: {control['error']}"
    seen = set(control.get("ids") or [])
    if not wanted & seen:
        # The control failed, so nothing is known about any filter. Said as its
        # own sentence because "the filter found nothing" would be a wrong
        # diagnosis of an empty or lagging board.
        return (
            f"CONTROL FAILED: an unfiltered read of the board returned "
            f"{sorted(seen) or 'nothing'}, which does not include any of this "
            f"run's tickets {sorted(wanted)}. The board is empty, unreadable, "
            "or still lagging its own writes — no conclusion about pickup."
        )

    asked = raw.get("asked") or []
    if not asked:
        return (
            "the driver asked the board nothing at all, so no ticket of this "
            "Manager's could ever be picked up"
        )
    for entry in asked:
        result = entry.get("result") or {}
        if result.get("error"):
            return (
                f"the board refused the driver's own question "
                f"{_render(entry.get('filter'))}: {result['error']}"
            )
        got = set(result.get("ids") or [])
        if not wanted & got:
            return (
                f"SCRUM-79: the driver asks the board "
                f"{_render(entry.get('filter'))} and gets "
                f"{sorted(got) or 'nothing'}, which includes none of this run's "
                f"tickets {sorted(wanted)} — while an unfiltered read returns "
                f"{sorted(seen)}. The fleet would advance nothing and say nothing."
            )
    return ""


def _render(flt: dict | None) -> str:
    """The filter as a question, with the fields it did not set left out."""
    if not flt:
        return "an empty filter"
    set_fields = {k: v for k, v in flt.items() if v}
    return json.dumps(set_fields, sort_keys=True) if set_fields else "an empty filter"
