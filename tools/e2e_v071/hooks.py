"""The records the fixes will define, read here once they exist.

Each function returns None until its fix lands, and `checks` turns None into
PENDING — never into a pass. When a fix merges, implement its reader here (it
reads the record the fix writes, through the installed rite where rite has the
function) and the matching check starts judging. Nothing else changes.
"""

from __future__ import annotations

from tools.e2e_v071.observe import Observer


def stage_log(obs: Observer, tickets: dict) -> dict | None:
    """STUB-PENDING SCRUM-72 (plan 3.3a): {ticket key: [stage, ...]} from the
    persisted, append-only transition log, in the order written. Stage names, as
    the plan has them: defined, decomposed, plan_reviewed, approached, executed,
    step_reviewed, recomposed, delivery_requested. Map 72's actual names onto
    these here."""
    return None


def plan_review_requests(obs: Observer, owner: str) -> list | None:
    """STUB-PENDING SCRUM-72 (plan 3.3b): [{ticket, to, at}] for every plan-review
    request the supervisor sent to a reviewer's inbox."""
    return None


def lifecycle_requests(obs: Observer, managers: list[str]) -> list | None:
    """STUB-PENDING SCRUM-59: [{op, worker, at, by}] for every `rite request` a
    Manager filed and the supervisor honoured (plan 3.1, `state/lifecycle/`)."""
    return None


def reconcile_reports(obs: Observer, managers: list[str]) -> list | None:
    """STUB-PENDING SCRUM-64: [{at, released, told, escalated}] for each
    reconciliation pass (plan 3.2). `escalated` is True when the Manager raised a
    finding to the Owner that reconciliation should have settled itself."""
    return None
