"""Collect project status for `rite status` (SPEC §9.8).

Answers "what's happening?" — workers, claims, module state, the handover
snapshot, and the coordination-cost counters (§2.7.2) that make a
schedule tunable from data instead of a guess. The deeper health check is
`rite doctor`.

Board state (§9.8's "ticket counts by column") IS shown, but only when
asked for: `collect_status(root, board=True)`. It is the one thing here
that leaves the machine, so it is off by default and every caller that
loops over projects — the Dispatch aggregate view — keeps it off rather
than making one network round trip per registered project.

When the query cannot be made, that is reported as the specific reason
it could not: no backend configured, credentials missing, the site
refusing them. The rule the old "not shown" line was defending still
holds — nothing invented, nothing stale — but "I did not ask" and "I
asked and was refused" are different answers and now read differently.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.budget import (
    BurnRateReport,
    compute_burn_rate,
    current_week_start,
    format_burn_rate,
    read_usage_since,
)
from rite_ai.claims.ledger import Claim, ClaimsLedger
from rite_ai.config.models import Module, WorkerManifest
from rite_ai.config.parse import load_project, parse_modules
from rite_ai.coordination_cost import CoordinationCostCounts, read_counts
from rite_ai.handover import HandoverSnapshot, read_snapshots
from rite_ai.label import project_name
from rite_ai.pool import PoolStatus
from rite_ai.pool import probe as probe_pool
from rite_ai.reporting.heartbeat import StallReport, detect_stalls, not_started
from rite_ai.state import CorruptStateError


@dataclass
class BoardState:
    """One board's ticket counts by column, or why they are missing."""

    role: str  # "board" | "workers" | "testing" — SPEC §6.2's three boards
    project_key: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    truncated: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class ProjectStatus:
    root: Path
    name: str = ""
    role: str = ""
    modules: list[Module] = field(default_factory=list)
    workers: list[WorkerManifest] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    suspect_claims: list = field(default_factory=list)
    """Claims whose HOLDER has gone quiet, not merely old ones. `STALE?`
    below flags age alone; this crosses the claim against the heartbeat, which
    is the difference between "this is old" and "nobody is coming back for
    it". Never released automatically — see `claims/suspect.py`."""
    stalled_workers: list[StallReport] = field(default_factory=list)
    not_started_workers: list[str] = field(default_factory=list)
    handovers: list[HandoverSnapshot] = field(default_factory=list)
    coordination_cost: CoordinationCostCounts = field(
        default_factory=CoordinationCostCounts
    )
    loop: str = ""
    """Whether a work-seeking loop is running for this project, and since
    when. Empty when the question could not be asked at all (no tmux).

    Here rather than only in `rite loop status`, because `rite status` is the
    command people already run — and a loop is the one thing in a project
    that keeps changing the answers below while nobody is watching. A reader
    who cannot see it is reading a report with an author they do not know
    about."""

    coordination: str = ""
    """What this machine's last coordination tick concluded, with its age —
    read from local state, never fetched. Empty when this machine does not
    coordinate, or has not ticked yet. `rite doctor` reads the fleet live;
    this is what `status` can say without a round trip (§2.5.2)."""
    burn_rate: BurnRateReport | None = None
    pool: PoolStatus | None = None
    pool_unreadable: str = ""
    """Why there is no `pool` to show. `probe` refuses an unreadable
    `pool.json` rather than reading it as an empty pool — right where it
    was written, since a pool read as empty is a pool reported as needing
    topping up, and the dead-slot records that link a crashed session to
    the claims it left behind are exactly what an unreadable file must not
    be assumed to lack. Escaping from here it took the whole command with
    it, including the machine-wide aggregate, where ONE project's corrupt
    file hid every project listed after it. A status command is the wrong
    place to refuse: it is what a human runs to find out what is wrong.

    Two claims removed from this docstring rather than corrected, because
    both were false and both were copied into the accompanying test's
    docstring. It said `fill` "would otherwise start a full set of
    sessions on top of the running ones" — `_locked_state` in
    `rite_ai.pool` retracts precisely that, measured against real tmux:
    "Three concurrent fills of a three-slot pool produced three sessions,
    not nine", because `slot_name` is deterministic and `tmux new-session`
    refuses a duplicate. And it said the aggregate hid every other
    project's "board state" — the aggregate never queries a ticket
    backend (`rite status`'s own docstring: "The aggregate view across
    registered projects never queries"), so there was no board state there
    to hide, and it hid the projects AFTER the corrupt one rather than all
    the others. Found by running this project's own review checklist
    against the commit that wrote them."""
    boards: list[BoardState] | None = None
    """None means the query was never attempted (the default). An empty
    list means it was attempted and there are no boards configured — a
    distinction the rendering has to keep, since one is "I did not look"
    and the other is "there is nothing to look at"."""
    boards_unreached: str = ""
    """Why the query was never attempted, when the reason is not the
    caller's `--no-board`. There is a THIRD case beside "I did not ask"
    and "I asked and was refused": `collect_status` returns early when
    `.rite/` is missing or the config will not parse, and never reaches
    the query at all. Left unset, that case renders as the flag's
    message — telling the user they skipped the board with a flag they
    did not pass, on a run whose actual problem is printed four lines
    higher. Measured against a live JIRA project, whose `brief.yaml`
    is missing: `rite status` with no flags reported "skipped with
    --no-board"."""
    errors: list[str] = field(default_factory=list)


def collect_board_state(config, role: str, credentials=None) -> BoardState:
    """One board's counts by column, live.

    Read-only: it lists tickets and groups them. Nothing here creates,
    moves or labels anything, so it is safe to run against a board
    somebody else is writing to.

    Never raises. Every failure — no backend, no credentials, a site that
    refuses them, a network that is not there — comes back as `error` on
    the returned object, because a status command that dies because the
    board was unreachable is worse than one that says the board was
    unreachable.
    """
    from rite_ai.tickets import BackendError, create_backend_from_config

    key = (config.projects or {}).get(role, "")
    state = BoardState(role=role, project_key=key)

    backend = create_backend_from_config(
        config, board_role=role, credentials=credentials
    )
    if isinstance(backend, BackendError):
        state.error = backend.message
        return state

    try:
        result = backend.list_tickets()
    except Exception as e:  # noqa: BLE001 - a status view must not die here
        state.error = f"{type(e).__name__}: {e}"
        return state

    if isinstance(result, BackendError):
        state.error = result.message
        return state

    counts: dict[str, int] = {}
    for ticket in result:
        counts[ticket.status or "(no status)"] = (
            counts.get(ticket.status or "(no status)", 0) + 1
        )
    state.counts = counts
    state.total = len(result)
    state.truncated = getattr(result, "truncated", False)
    return state


def collect_status(root: Path, board: bool = False) -> ProjectStatus:
    rite_dir = root / ".rite"
    status = ProjectStatus(root=root)

    if not rite_dir.is_dir():
        status.errors.append("no .rite/ directory — run `rite init`")
        status.boards_unreached = "no .rite/ directory"
        return status

    status.loop = _loop_line(root)

    status.coordination_cost = read_counts(root)

    from rite_ai.coordination import last_tick as last_tick_file

    last = last_tick_file.read(root)
    if last is not None:
        status.coordination = last_tick_file.describe(last)
    status.handovers = read_snapshots(root)

    # Claims are read regardless of whether `load_project` below succeeds
    # — `rite claim` only needs `.rite/` to exist (see `_claims_path()` in
    # the CLI), not a fully-populated `brief.yaml`. An earlier version of
    # this function returned before reaching this on a project-parse
    # error, which meant `rite status` showed no claims at all on a
    # minimally-initialised project — a real, user-visible regression
    # caught wiring this into the CLI for the first time.
    claims_path = rite_dir / "claims.json"
    if claims_path.exists():
        ledger = ClaimsLedger(claims_path)
        status.claims = ledger.list_claims()

    project = load_project(root)
    if isinstance(project, list):
        status.errors.extend(f"{e.file}: {e.message}" for e in project)
        modules = parse_modules(rite_dir / "modules.yaml")
        if isinstance(modules, list):
            status.modules = modules
        # The backend lives in the config that just failed to parse, so
        # there is nothing to query with — but say THAT, not "you passed
        # --no-board".
        status.boards_unreached = "project config did not load (see errors above)"
        return status

    status.name = project.brief.name
    status.role = project.brief.role
    status.modules = project.modules
    status.workers = project.workers

    threshold = (
        project.config.heartbeat.interval_minutes
        * 60
        * project.config.heartbeat.stall_threshold
    )
    names = [w.name for w in project.workers]
    if project.workers:
        status.stalled_workers = detect_stalls(root, names, threshold_seconds=threshold)
        status.not_started_workers = not_started(root, names)

    # Outside the `if`, deliberately: this walks the LEDGER rather than the
    # roster, and the case it exists for is a claim held by a name nobody
    # registered — which a project with zero configured Workers can still
    # have, and which `detect_stalls` above can never see.
    from rite_ai.claims.suspect import suspect_claims

    status.suspect_claims = suspect_claims(
        root, registered=names, threshold_seconds=threshold
    )

    tz_name = project.config.schedule.timezone or "UTC"
    now = datetime.now(UTC)
    week_start = current_week_start(now, tz_name, project.config.budget.week_start_day)
    if week_start is not None:
        samples = read_usage_since(week_start)
        status.burn_rate = compute_burn_rate(
            samples, week_start, now, project.config.budget.weekly_token_budget
        )

    # Read-only liveness probe (§2.5.2/§2.5.3: mechanical, zero-token, no
    # session spawning) — `rite pool fill` is the only thing that starts
    # new sessions, deliberately kept a separate, explicit command.
    try:
        status.pool = probe_pool(root, project.config.pool)
    except CorruptStateError as exc:
        status.pool_unreadable = str(exc)

    if board:
        tb = project.config.ticket_backend
        # Only the roles actually configured. Querying a role with no
        # project key would spend a round trip to be told what config.yaml
        # already says.
        configured = tb.projects or {}
        roles = [r for r in ("board", "workers", "testing") if configured.get(r)]
        if not roles and tb.type != "none":
            # A single-project setup with no `projects` mapping still has
            # one board; "workers" is the role every other caller defaults
            # to, so it is the one to ask about.
            roles = ["workers"]
        status.boards = [
            collect_board_state(tb, role, project.config.credentials) for role in roles
        ]

    return status


# A claim never expires on its own, and it should not: rite cannot tell a
# crashed session from a session thinking hard, and auto-releasing a path a
# live worker is editing is worse than leaving a stale one lying around. What
# it CAN do is stop a claim from five days ago rendering identically to one
# from five minutes ago. A file on a live repo sat claimed since 09-06 by a
# session that never committed anything, and nothing in this output said so.
_STALE_AFTER_HOURS = 12


def format_claim_age(claim: Claim) -> str:
    """Human age, flagged once it is old enough to be worth doubting."""
    if not claim.timestamp:
        return "age unknown"
    seconds = max(time.time() - claim.timestamp, 0)
    hours = seconds / 3600
    if hours < 1:
        age = f"{int(seconds // 60)}m"
    elif hours < 48:
        age = f"{hours:.0f}h"
    else:
        age = f"{hours / 24:.0f}d"
    if hours >= _STALE_AFTER_HOURS:
        return f"{age} — STALE? check the session still lives"
    return age


def _format_boards(boards: list[BoardState] | None, unreached: str = "") -> list[str]:
    """§9.8's "ticket counts by column"."""
    if boards is None:
        if unreached:
            return [f"\nboard state: not queried — {unreached}"]
        return ["\nboard state: not queried (skipped with --no-board)"]
    if not boards:
        return [
            "\nboard state: no ticket backend configured "
            "(.rite/config.yaml: ticket_backend)"
        ]

    lines = ["\nboard state:"]
    for b in boards:
        name = f"{b.role} ({b.project_key})" if b.project_key else b.role
        if not b.ok:
            # FIRST LINE ONLY. A backend error may run to several lines —
            # the missing-credential one names every account it consulted
            # and then lists the commands that fix it (§10.2) — and status
            # asks each configured board role separately, so one unset
            # `jira_token` rendered the same five-line remedy three times
            # and buried the rest of the report. Fifteen lines to say one
            # thing. The first line identifies the problem; the remedy
            # belongs in the command that actually failed.
            lines.append(f"  {name}: unavailable — {b.error.splitlines()[0]}")
            continue
        if not b.total:
            lines.append(f"  {name}: no tickets")
            continue
        # Ordered by size, so the column that needs attention is not
        # buried under whatever order the backend happened to return.
        columns = ", ".join(
            f"{status_name} {count}"
            for status_name, count in sorted(
                b.counts.items(), key=lambda kv: (-kv[1], kv[0])
            )
        )
        suffix = " (first page only — there are more)" if b.truncated else ""
        noun = "ticket" if b.total == 1 else "tickets"
        lines.append(f"  {name}: {b.total} {noun} — {columns}{suffix}")
    return lines


def _loop_line(root: Path) -> str:
    """Whether a loop is running, WITHOUT shelling out.

    `rite loop status` asks tmux, which is authoritative — it knows what it is
    running. This cannot: `collect_status` is the read-only path and must
    never spawn a subprocess, which a test pins directly ("no liveness probe
    subprocess call at all"). Asking tmux here put a process spawn into the
    command people run most often, and the test caught it.

    So this reads the loop's lock file and checks the pid with a signal, which
    is a syscall rather than a process. The cost is a few seconds of lag at
    startup — `start` releases its own lock before spawning, and the loop
    takes it once running — during which this says "not running" and `rite
    loop status` says the truth. That is the right way round: the cheap
    overview may be briefly behind, the authoritative command never is.
    """
    from rite_ai.loop.session import draining, running_pid

    pid = running_pid(root)
    if not pid:
        return "not running"
    drain = " (draining)" if draining(root) else ""
    return f"running (pid {pid}){drain} — `rite loop status` for detail"


def format_status(status: ProjectStatus) -> str:
    # The header is the label: a status report scrolled back to, or pasted
    # into a message, has to say which project it describes.
    from rite_ai.label import ProjectLabel, colour_for

    name = status.name or project_name(status.root)
    lines = [
        f"{ProjectLabel(name=name, colour=colour_for(name)).render()} "
        f"({status.role or 'unknown'})"
    ]
    lines.append(f"root: {status.root}")

    if status.errors:
        lines.append("")
        lines.append("errors:")
        for err in status.errors:
            lines.append(f"  - {err}")

    if status.modules:
        lines.append(f"\nmodules ({len(status.modules)}):")
        for m in status.modules:
            url_part = f" ({m.url})" if m.url else " (local)"
            lines.append(f"  {m.name}: {m.path}{url_part}")
    else:
        lines.append("\nno modules registered")

    stalled_names = {s.worker for s in status.stalled_workers}
    if status.workers:
        lines.append(f"\nworkers ({len(status.workers)}):")
        for w in status.workers:
            mods = ", ".join(w.modules) if w.modules else "all"
            if w.name in stalled_names:
                marker = " — STALLED"
            elif w.name in status.not_started_workers:
                marker = " — not started (no heartbeat or claims yet)"
            else:
                marker = ""
            lines.append(f"  {w.name}: modules=[{mods}]{marker}")
    else:
        lines.append("\nno workers")

    if status.claims:
        lines.append(f"\nclaims ({len(status.claims)}):")
        for c in status.claims:
            paths_str = ", ".join(c.paths)
            ticket_str = f" [{c.ticket}]" if c.ticket else ""
            lines.append(
                f"  {c.worker}{ticket_str}: {paths_str} ({format_claim_age(c)})"
            )
    else:
        lines.append("\nno active claims")

    if status.suspect_claims:
        # Age alone is `STALE?` above. This is age AND the holder having gone
        # quiet, which is the difference between "old" and "nobody is coming
        # back for it" — the state that silently narrows a continuous run
        # until nothing can be claimed.
        from rite_ai.claims.suspect import lines as suspect_lines

        lines.append("")
        lines.extend(suspect_lines(status.suspect_claims))

    if status.handovers:
        # One per worker. Rendering only the newest reported one worker's
        # state as the project's, with no sign the others existed.
        label = (
            "handover snapshots" if len(status.handovers) > 1 else "handover snapshot"
        )
        lines.append(f"\n{label}:")
        for snap in status.handovers:
            who = snap.worker or "coordinator"
            lines.append(f"  {who}:")
            if snap.unreadable:
                lines.append(f"    UNREADABLE: {snap.unreadable}")
                lines.append(f"    last written: {snap.describe_age()}")
                continue
            lines.append(f"    ticket: {snap.ticket or '(none)'}")
            # `progress` was dropped here and nowhere else, and it is the
            # field that says where the work actually stands.
            lines.append(f"    progress: {snap.progress or '(none)'}")
            lines.append(f"    next step: {snap.next_step or '(none)'}")
            if snap.blockers:
                lines.append(f"    blockers: {', '.join(snap.blockers)}")
            # This block carried NO time at all, so a snapshot from a
            # session that died hours ago read exactly like one written a
            # moment before — in the command called "what's happening?".
            lines.append(f"    recorded: {snap.describe_age()}")
    else:
        lines.append("\nno handover snapshot recorded yet")

    if status.loop:
        lines.append(f"\nloop: {status.loop}")

    if status.coordination:
        # Above the cost counters, because "who is Owner" is the question
        # somebody has when they run this during an incident.
        lines.append(f"\ncoordination: {status.coordination}")

    cc = status.coordination_cost
    lines.append("\ncoordination cost (§2.7.2):")
    lines.append(f"  refused claim attempts: {cc.refused_claims}")
    # Only `refused_claims` has a collection point today. The other two
    # can therefore only ever read zero, and an unqualified "0" claims a
    # measurement that was never taken — the same misreading `rite
    # budget`'s scope line exists to prevent. `rite.coordination_cost`'s
    # module docstring is the source of truth for WHY each is unwired;
    # when either gets its collection point, drop the suffix here.
    lines.append(
        f'  "nothing safe to start": {cc.nothing_safe_to_start} — not '
        "instrumented yet; `start` does not query the ticket backend"
    )
    lines.append(
        f"  merge conflicts: {cc.merge_conflicts} — not instrumented yet; "
        "rite owns no merge step"
    )

    if status.pool_unreadable:
        lines.append(
            # `UNREADABLE` is this repo's marker for a state file that
            # exists and will not parse — the handover branch below, the
            # cross-project line in `cli.main`, and the watchdog's reasons
            # all use it, and `tests/test_rehearsal_round4.py` greps for
            # it. A lowercase variant here made one command print the same
            # condition two different ways, and made this one invisible to
            # anything looking for the others.
            f"\ncoordinator pool (§2.5): UNREADABLE — "
            f"{status.pool_unreadable}\n  `rite pool status` for recovery steps"
        )
    if status.pool is not None:
        p = status.pool
        lines.append(f"\ncoordinator pool (§2.5): {len(p.live)}/{p.target} live")
        if p.stale:
            lines.append(f"  stale: {', '.join(p.stale)}")
        if p.warn:
            lines.append(f"  {p.message}")

    br = status.burn_rate
    if br is not None:
        lines.append(
            f"\nburn rate — WHOLE MACHINE, all projects, not just this one "
            f"(§2.6, since {br.week_start:%Y-%m-%d %H:%M %Z}):"
        )
        lines.extend(f"  {line}" for line in format_burn_rate(br))
    else:
        lines.append(
            "\nburn rate: not shown — schedule.timezone or budget.week_start_day "
            "unrecognised"
        )

    lines.extend(_format_boards(status.boards, status.boards_unreached))

    return "\n".join(lines)
