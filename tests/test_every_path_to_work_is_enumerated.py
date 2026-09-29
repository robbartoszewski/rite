"""TR5: every path that reads the backlog, gives a ticket to someone, or starts
a Worker is named here, with how it treats an unrefined ticket.

The release plan's TR5 row: "enumerate every reader of readiness and every
path that starts a Worker on a ticket, with a test that fails when a new one
appears" (MM1's practice). A gate on three paths is worth little if a fourth
appears next month and nobody notices it is ungated. So this test finds the
calls by AST, outside `tickets/` (the backends themselves), and fails on any
call not in the table below. Adding a path means adding a row, and the row
means saying how it treats a ticket that is not REFINED.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "rite_ai"

WATCHED = {"list_tickets", "query", "label", "assign", "assign_to_manager"}
WATCHED_NAMES = {"start_worker", "distribute", "assign_to_manager"}

KNOWN = {
    # --- readers of the backlog ------------------------------------------
    ("scheduler/__init__.py", "_assign_the_pool", "list_tickets"): (
        "GATED: Rule 0 assigns only REFINED tickets and names the rest"
    ),
    ("loop/__init__.py", "_ready", "list_tickets"): (
        "GATED (TR2): only REFINED tickets are ready; the Owner's unrefined "
        "ones are `refining` or `waiting-on-user`, never `idle`"
    ),
    ("coordination/distribution.py", "distribute", "list_tickets"): (
        "reads this Manager's own tickets; the handout below is gated"
    ),
    ("refinement/instructions.py", "brief", "list_tickets"): (
        "the Owner's refinement brief (TR2): lists unrefined tickets to "
        "REFINE, never to work; gives nothing to anyone"
    ),
    ("reporting/status.py", "collect_board_state", "list_tickets"): (
        "reporting only: gives nothing to anyone"
    ),
    ("cli/main.py", "board_list", "list_tickets"): (
        "`rite board list`: reporting only"
    ),
    ("cli/main.py", "board_query", "query"): ("`rite board query`: reporting only"),
    # --- writes that give a ticket to someone ------------------------------
    ("scheduler/__init__.py", "_assign_the_pool", "assign_to_manager"): (
        "GATED: Rule 0, before the call"
    ),
    ("coordination/assignment.py", "assign_to_manager", "label"): (
        "the Owner's label; its one caller is gated (Rule 0)"
    ),
    ("coordination/distribution.py", "distribute", "label"): (
        "GATED: a Manager hands a Worker only a REFINED ticket"
    ),
    ("coordination/monitor.py", "_distribute", "distribute"): (
        "calls the gated handout; passes its injected check through"
    ),
    ("coordination/refusal.py", "refuse_assignment", "label"): (
        "gives the ticket BACK to the pool: `scheduled` on, the Manager off"
    ),
    ("lifecycle/commands.py", "_deliver_via_backend", "label"): (
        "`rite schedule`/`unschedule`: a person's label; schedules, assigns "
        "nobody (scheduled + not refined is the Owner's to refine)"
    ),
    ("lifecycle/commands.py", "_deliver", "label"): (
        "the function `_deliver_via_backend` is nested in: the same calls"
    ),
    ("refinement/protocol.py", "_escalate", "label"): (
        "adds `blocked` to a ticket escalated as a blocker (TR2); gives it to nobody"
    ),
    ("refinement/protocol.py", "handle", "label"): (
        "removes `blocked` when his reply unblocks it (TR2); gives it to nobody"
    ),
    ("cli/main.py", "board_label", "label"): (
        "`rite board label`: a hand write; nothing starts from a label, and "
        "the Worker start refuses an unrefined ticket (TR4)"
    ),
    ("cli/main.py", "board_assign", "assign"): (
        "`rite board assign`: a hand write; nothing starts from it, and the "
        "Worker start refuses an unrefined ticket (TR4)"
    ),
    # --- the one Worker start ----------------------------------------------
    ("cli/main.py", "sandbox_start", "start_worker"): (
        "GATED (TR4): starts only on a REFINED ticket, from one read"
    ),
}


def _calls():
    found = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("tickets/"):
            continue
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in WATCHED:
                    found.add((rel, fn.name, func.attr))
                elif isinstance(func, ast.Name) and func.id in WATCHED_NAMES:
                    found.add((rel, fn.name, func.id))
    return found


def test_every_path_to_work_is_in_the_table():
    unknown = _calls() - set(KNOWN)
    assert not unknown, (
        "a new path reads the backlog, gives a ticket to someone, or starts "
        "a Worker. Add it to KNOWN with how it treats a ticket that is not "
        f"REFINED (TR5): {sorted(unknown)}"
    )


def test_the_table_names_no_path_that_is_gone():
    """A row for a call that no longer exists is a claim nobody checks."""
    gone = set(KNOWN) - _calls()
    assert not gone, f"remove these rows: {sorted(gone)}"
