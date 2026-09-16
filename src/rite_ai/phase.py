"""Where a project is, and what to do next (SPEC §9.10, §9.13.1).

`rite start` used to print an inventory — project loaded, scheduler, kb
cache, pool, `ready` — with no direction, and the orientation table it was
meant to embody lived only in SPEC.md, which no session reads. This is that
table as code: a phase worked out from what is on disk, and the next step
named in words someone new to rite can act on. The same table is written
into every generated `CLAUDE.md` (`PHASE_GUIDE`), so a session can route on
it without having to find it.

**Disk, not config.** A spec path in `config.yaml` that points at nothing is
no spec. A phase machine that misreports the phase is worse than none,
because it confidently sends people to the wrong step.

**Local only.** Tickets live on a board, and reading one is a network call,
which `rite start` reports rather than performs. So the last phase names both
next steps and the command that tells them apart, instead of guessing whether
the board is empty.

**A step rite does not have says what to do instead.** rite has no planning
step; the phase that would otherwise send someone to one says so and names
`/refine`. `NO_PLAN_STEP` is the one string that changes if that ever does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# rite has no planning step, and saying "`/plan` is not available yet" would
# promise one. The README says the same thing in the same words: deciding what
# gets built first is yours, and the tickets you write are the plan as rite
# sees it. If that ever changes, this is the one string that changes with it.
NO_PLAN_STEP = (
    "rite has no planning step — deciding what gets built first is yours, "
    "usually by talking it through in this session. Turn one part of the spec "
    "into a ticket with `/refine <what to build>`, and repeat for each part"
)

_PHASE_ROWS = (
    (
        "Not set up",
        "No `.rite/` here, or `.rite/brief.yaml` is missing or unreadable",
        "`rite init` — or `rite doctor` to see what is broken",
    ),
    (
        "Work in progress",
        "`rite handover show` names a ticket, or `rite status` lists claims",
        "Continue it: `/ticket <id>`",
    ),
    (
        "No spec",
        "`rite start` says there is no project spec — including when the "
        "spec path it has recorded points at nothing",
        "`/spec`: it asks about the project, writes `SPEC.md` and registers "
        "it. A spec file that already exists: `rite spec add <path>`",
    ),
    (
        "Spec, no tickets yet",
        "A spec is registered, it exists, and nothing is in progress",
        f"{NO_PLAN_STEP}. Tickets need a board; `rite doctor` says whether "
        "one is configured",
    ),
    (
        "Tickets on the board",
        "`rite board list` shows them",
        "Work one end to end: `/ticket <id>`",
    ),
)

PHASE_GUIDE = "\n".join(
    [
        "| Where the project is | How you can tell | What to do next |",
        "|---|---|---|",
        *(f"| {where} | {tell} | {do} |" for where, tell, do in _PHASE_ROWS),
    ]
)


@dataclass
class Phase:
    key: str  # not-set-up | in-progress | no-spec | spec-ready
    where: str
    next: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _paths(paths: list[str]) -> str:
    return ", ".join(f"`{p}`" for p in paths)


def detect_phase(root: Path) -> Phase:
    from rite_ai.config.models import ProjectConfig
    from rite_ai.config.parse import (
        ParseError,
        parse_brief,
        parse_config,
        parse_modules,
    )
    from rite_ai.project_spec import orphaned_enrichment_notice, spec_state

    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        return Phase(
            "not-set-up",
            "This directory is not set up for rite yet.",
            [
                "Run `rite init` here. It asks a few questions about the "
                "project and writes `.rite/`."
            ],
        )

    brief = parse_brief(rite_dir / "brief.yaml")
    if isinstance(brief, ParseError):
        return Phase(
            "not-set-up",
            "rite is only partly set up here: the project description "
            f"`.rite/brief.yaml` is unusable ({brief.message}).",
            [
                "See exactly what is wrong: `rite doctor`.",
                "If the file was deleted or damaged, restore it from git.",
                "Or start again: `rite init` offers to wipe `.rite/` and ask "
                "its questions again.",
            ],
        )

    notes: list[str] = []
    config = parse_config(rite_dir / "config.yaml")
    if isinstance(config, ParseError):
        notes.append(
            f"`.rite/config.yaml` is unreadable ({config.message}) — `rite doctor`"
        )
        config = ProjectConfig()
    modules = parse_modules(rite_dir / "modules.yaml")
    module_paths = [m.path for m in modules] if isinstance(modules, list) else []
    orphan = orphaned_enrichment_notice(root)
    if orphan:
        notes.append(orphan)
    if brief.role not in ("owner", "manager"):
        notes.append(
            "this machine's role is not recorded — ask whether it is the "
            "Owner or a Manager and set `project.role` in `.rite/brief.yaml` "
            "before assigning work"
        )

    state = spec_state(root, config, module_paths)

    tickets, holders, recorded, age = _work_in_progress(root)
    if tickets or holders:
        what = (
            ", ".join(f"ticket {t}" for t in tickets)
            if tickets
            else "claims held by " + ", ".join(holders)
        )
        if age:
            what += f" (last recorded {age})"
        steps = (
            [f"Continue it in Claude Code: `/ticket {tickets[0]}`."]
            if tickets
            else [
                "See who holds what with `rite status`, and where it stopped "
                "with `rite handover show`."
            ]
        )
        if recorded:
            steps.append(f"The recorded next step was: {recorded}")
        if not state.present:
            notes.append(
                "There is no project spec yet — run `/spec` once this work is done."
            )
        return Phase("in-progress", f"Work is in progress: {what}.", steps, notes)

    if not state.present:
        steps: list[str] = []
        if state.dangling:
            where = (
                "The project is set up but has no spec: `.rite/config.yaml` "
                f"records {_paths(state.dangling)} as the spec, and nothing is there."
            )
            steps.append(
                f"Drop the broken entry: `rite spec remove {state.dangling[0]}`."
            )
        else:
            where = (
                "The project is set up, but has no spec yet — nothing written "
                "down about what it should do."
            )
        if state.unregistered_files:
            found = state.unregistered_files[0]
            steps.append(
                f"A spec file already exists, but rite is not using it: `{found}`. "
                f"Register it: `rite spec add {found}`."
            )
        else:
            steps.append(
                "In Claude Code, in this directory, run `/spec`. It asks a few "
                "questions about the project, writes `SPEC.md`, and registers it."
            )
        for rel in state.unregistered_dirs:
            notes.append(
                f"`{rel}` may hold design documents. If it does, `rite spec add {rel}` "
                "instead of writing a new spec."
            )
        return Phase("no-spec", where, steps, notes)

    where = f"The project has a spec ({_paths(state.present)}) and no work in progress."
    if config.ticket_backend.type in ("", "none"):
        steps = [
            "Tickets need a board, and none is configured: set `ticket_backend` "
            "in `.rite/config.yaml` to `jira` or `github`, then check it with "
            "`rite doctor`.",
            f"{NO_PLAN_STEP}.",
        ]
    else:
        steps = [
            "See what is already on the board: `rite board list`.",
            "Pick a ticket up in Claude Code: `/ticket <id>`.",
            f"Board empty? {NO_PLAN_STEP}.",
        ]
    if state.dangling:
        notes.append(
            f"`.rite/config.yaml` also records {_paths(state.dangling)}, and nothing "
            f"is there — `rite spec remove {state.dangling[0]}`."
        )
    return Phase("spec-ready", where, steps, notes)


def _work_in_progress(root: Path) -> tuple[list[str], list[str], str, str]:
    """Tickets, claim holders, the recorded next step, and its age — from
    the handover snapshot and the claims ledger, both on disk."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.handover import read_snapshot
    from rite_ai.state import CorruptStateError

    tickets: list[str] = []
    holders: list[str] = []
    recorded = age = ""
    snapshot = read_snapshot(root)
    if snapshot is not None and not snapshot.unreadable and snapshot.ticket:
        tickets.append(snapshot.ticket)
        recorded = snapshot.next_step or ""
        age = snapshot.describe_age()
    claims_path = root / ".rite" / "claims.json"
    if claims_path.exists():
        try:
            claims = ClaimsLedger(claims_path).list_claims()
        except (CorruptStateError, OSError):
            claims = []
        for claim in claims:
            if claim.ticket and claim.ticket not in tickets:
                tickets.append(claim.ticket)
            if claim.worker not in holders:
                holders.append(claim.worker)
    return tickets, holders, recorded, age


def render_phase(phase: Phase) -> list[str]:
    lines = [f"where: {phase.where}", "next:"]
    lines += [f"  {i}. {step}" for i, step in enumerate(phase.next, 1)]
    lines += [f"note: {note}" for note in phase.notes]
    return lines
