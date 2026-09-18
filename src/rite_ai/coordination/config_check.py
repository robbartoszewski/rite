"""Coordination settings that cannot possibly work (§2.4, D-42, D-56).

A misconfigured `coordination:` block does not fail — it does nothing, which
is the failure mode this project keeps deciding is the worse one. Half a
block means no election ever happens, and the machine looks healthy while it
sits there. These are the states that are ALWAYS a mistake, whatever else is
true, so they can be reported without knowing anything about the fleet.

Two halves: whether the Managers can reach each other (§2.4), and what each
one is FOR (rite local, RL-T3 — `config.managers.configuration_problems`,
called from here). They belong in one list because a reader fixing a
`coordination:` block fixes it once.

Deliberately NOT here: whether this machine is in `managers:`. A machine
cannot currently tell which Manager it is at all — a gap raised separately —
and guessing at it would turn a clear question into a wrong answer.

`rite doctor` reports these; parsing does not refuse them. A project part-way
through setup is allowed to have half a block on disk; it is only wrong once
somebody expects it to work.
"""

from __future__ import annotations

from rite_ai.config.models import CoordinationConfig


def coordination_problems(config: CoordinationConfig) -> list[str]:
    """Every reason this block cannot work, in the order a reader fixes them."""
    if not config.managers and not config.remote and not config.manager_roles:
        # Not configured at all, which is Phase 1 and entirely normal.
        return []

    problems: list[str] = []

    # What each Manager is FOR, as opposed to whether they can reach each
    # other (rite local, RL-T3). Checked here rather than in its own doctor
    # section because a reader fixing a `coordination:` block wants one list,
    # and because `configuration_problems` had no caller at all — eight rules
    # about gates that cannot work, complete and tested and reporting to
    # nobody, which is the defect shape this whole batch is about.
    #
    # `manager_roles` alone is enough to get past the early return above: a
    # rite-local project can be several Managers on ONE machine with no
    # `remote` at all, and its roles still have to make sense.
    from rite_ai.config.managers import configuration_problems

    problems.extend(
        f"coordination: {p}"
        for p in configuration_problems(config.manager_roles, names=config.managers)
    )

    if config.managers and not config.remote:
        problems.append(
            f"coordination: {len(config.managers)} manager(s) listed but no "
            "`remote` — there is nowhere to coordinate through, so no "
            "election can ever happen"
        )
    if config.remote and not config.managers:
        problems.append(
            "coordination: a `remote` but an empty `managers` list — nothing "
            "is eligible to be Owner (§2.4: the first ACTIVE manager in "
            "priority order)"
        )

    seen, duplicates = set(), []
    for name in config.managers:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    if duplicates:
        # D-56: the list IS priority. A name twice has two priorities, and
        # which one wins depends on where anyone stopped reading.
        problems.append(
            "coordination: duplicate manager(s) "
            + ", ".join(repr(d) for d in duplicates)
            + " — the list is priority order (D-56), so a repeated name has "
            "no single priority"
        )
    blank = [i for i, name in enumerate(config.managers) if not name.strip()]
    if blank:
        problems.append(
            f"coordination: blank manager name(s) at position(s) "
            f"{', '.join(str(i) for i in blank)} — a Manager with no name "
            "cannot publish a heartbeat or hold the lease"
        )

    if not config.state_branch.strip():
        problems.append(
            "coordination: `state_branch` is empty — the state layer needs a "
            "branch to force-push (§3.3.1)"
        )
    if config.owner_lease_minutes <= 0:
        problems.append(
            f"coordination: `owner_lease_minutes` is "
            f"{config.owner_lease_minutes} — a lease that is already expired "
            "when written means the Owner role can never be held"
        )
    if config.skew_tolerance_seconds < 0:
        problems.append(
            f"coordination: `skew_tolerance_seconds` is "
            f"{config.skew_tolerance_seconds} — D-42's margin cannot be "
            "negative; 0 means no tolerance, which is the documented risk"
        )
    return problems
