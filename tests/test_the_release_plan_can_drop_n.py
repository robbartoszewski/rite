"""§ N of the v0.6.0 plan must stay droppable for free.

Robert, 2026-09-25: N (ticket-text cleanup and injection-phrase reporting) is
in v0.6.0, sequenced LAST, "so we can skip it if we run out of quota". That
only holds while nothing depends on N. Two such edges had already crept in
by the time it was checked — A3 said inbound text "goes through § N", and the
check-ins note listed N's findings as part of the standup — so the rule is a
test rather than a paragraph somebody has to remember to read.
"""

from __future__ import annotations

import re
from pathlib import Path

PLAN = Path(__file__).resolve().parent.parent / "docs/design/V060_RELEASE_PLAN.md"
_ROW = re.compile(r"^\| (?:~~)?([A-Z][0-9]+[a-z]*(?:-old)?)(?:~~)? \|")
_MENTIONS_N = re.compile(r"§ N\b|\bN[12]\b")


def _ticket_rows() -> list[tuple[str, list[str]]]:
    rows = []
    for line in PLAN.read_text().splitlines():
        m = _ROW.match(line)
        if m:
            rows.append((m.group(1), [c.strip() for c in line.split(" | ")]))
    return rows


def test_the_plan_has_n_and_other_tickets_to_check():
    """A parser that found nothing would pass everything below."""
    ids = {tid for tid, _ in _ticket_rows()}
    assert {"N1", "N2", "A3", "K4", "K5"} <= ids, sorted(ids)


def test_no_ticket_outside_n_lists_n_as_a_dependency():
    for tid, cells in _ticket_rows():
        if tid.startswith("N"):
            continue
        depends = cells[-2]
        assert not _MENTIONS_N.search(depends), (
            f"{tid} depends on N ({depends!r}), so dropping N is no longer free"
        )


def test_a_ticket_outside_n_mentions_n_only_to_say_it_does_not_depend_on_it():
    """How the first edge got in: not the Depends column, the description."""
    for tid, cells in _ticket_rows():
        if tid.startswith("N"):
            continue
        row = " | ".join(cells)
        if _MENTIONS_N.search(row):
            assert "does NOT depend on § N" in row, (
                f"{tid} mentions N without saying it does not depend on it"
            )


def test_n_is_sequenced_last():
    text = PLAN.read_text()
    order = text[text.index("**Order:**") :]
    order = order[: order.index("\n\n")]
    assert "N1, N2 LAST" in order
    tail = order[order.index("N1, N2 LAST") :]
    assert "→" not in tail, f"something is sequenced after N: {tail!r}"
