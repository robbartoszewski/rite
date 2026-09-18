"""rite's ticket-label vocabulary, in one place.

"The label IS rite's assignment system; JIRA's assignee field is
informational only" (§9.10 step 3). These are the words that system uses,
gathered here because they are shared by the Owner assigning (P2-3a), the
Manager distributing (P2-4b), the Manager refusing (P2-4c) and the handover
returning work to the pool (§9.10) — four places that must agree on them.

The other labels are names: a Worker's, or a Manager's. Nothing here.
"""

from __future__ import annotations

SCHEDULED = "scheduled"
"""In the backlog and available. §9.10's orientation table routes on it."""

MODULE = "module:"
"""⚠ PROPOSAL, not settled spec. `modules.yaml` names a project's modules
and §2.3 says assignment is by label, but nothing maps a TICKET to a module
— and a Manager cannot refuse work for a module it lacks (P2-4c) without
that mapping. `module:<name>` follows the existing convention that rite's
labels are its assignment mechanism. A ticket without one carries no module
requirement, so a board that never adopts this behaves exactly as before."""


def module_required_by(ticket) -> str | None:
    """The module a ticket needs, or None. See `MODULE` for its standing."""
    for label in ticket.labels or []:
        if label.startswith(MODULE):
            name = label[len(MODULE) :].strip()
            if name:
                return name
    return None
