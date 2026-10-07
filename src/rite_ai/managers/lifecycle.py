"""A Manager asks rite to stop, destroy, restart, report on or gate a Worker
(SCRUM-59).

**What happened** (the a8 dogfood, 2026-10-04). A Worker's sandbox died and
the Manager could do nothing about it: `rite sandbox stop|destroy|restart`
need yoloAI's own state and the credential store, which a Manager's boundary
does not grant, and the publish gate's findings behind a refused delivery
were out of its reach for the same reason. A person had to run host commands.

**So the Manager ASKS, the way it asks for a Worker (`broker`) or a delivery
(`publishing.requests`)**, and the supervisor, outside the boundary, does it
at the cycle boundary and puts the outcome in the Manager's next instruction.
The boundary is not widened: no credential store, no `~/.yoloai`.

**The request carries three values and no others:** the operation, which
declared Worker, and (optionally) which ticket. Everything else is rite's.
Unknown keys are refused, not ignored, and so, by construction, is the one
thing a Manager must never get by asking:

- `destroy` is never forced. There is no field to carry `force`, so a sandbox
  holding unpushed work or an unanswered question is refused, as it is for a
  person who does not type `--force`.
- a Worker another Manager started is refused (`_may_act`): rite records which
  Manager asked for each Worker it started (`record_owner`), outside every
  boundary, and only that Manager may act on it. A Worker rite has no record
  of (a person started it) is refused: it is the person's.

`rite request` writes the file in Python, so no `$( )` or `>` ever reaches the
engine: the a8 Manager was refused on exactly `echo … > …/$(date +%s).json`.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.managers import manager_dir, own_dir
from rite_ai.names import name_problem
from rite_ai.state import write_atomic

DIRNAME = "lifecycle"
STOP, DESTROY, RESTART, STATUS, GATE = "stop", "destroy", "restart", "status", "gate"
OPS = (STOP, DESTROY, RESTART, STATUS, GATE)
ALLOWED_KEYS = frozenset({"op", "worker", "ticket"})
MAX_REQUEST_BYTES = 4096
TICKET_MAX = 64
TAKEN = ".taken"
ABOUT = "a Worker you asked rite about"
FINDINGS_SHOWN = 10


@dataclass(frozen=True)
class Request:
    op: str
    worker: str
    ticket: str = ""


def lifecycle_dir(root: Path, manager: str) -> Path:
    """The Manager's own `lifecycle/`, beside `requests/` and `deliveries/`,
    under the one directory of rite's its boundary lets it write."""
    return manager_dir(Path(root), manager) / DIRNAME


def request_name() -> str:
    """A request's file name: oldest first by name, and unique.

    ⚠ Not `time.time_ns()` alone: two requests in one tick would share a
    name, and `write_atomic`'s replace would silently drop the first."""
    return f"{time.time_ns():020d}-{os.getpid()}-{secrets.token_hex(4)}.json"


def ticket_problem(ticket: str) -> str:
    """Why `ticket` cannot be a ticket id; "" when it can. The broker's rule:
    restricted to what an id is made of, never escaped."""
    if not ticket:
        return "the ticket is empty"
    if len(ticket) > TICKET_MAX:
        return "the ticket is longer than a ticket id"
    if not (ticket[0].isascii() and ticket[0].isalnum()) or not all(
        c.isascii() and (c.isalnum() or c in "-_#/.") for c in ticket
    ):
        # A leading letter or digit too: a ticket reaches git and argv, and
        # `-x` there reads as an option.
        return (
            f"{ticket!r} is not shaped like a ticket id — a letter or digit "
            "first, then letters, digits, '-', '_', '#', '/' and '.' only"
        )
    if ".." in ticket or ticket.endswith((".", "/")):
        return f"{ticket!r} cannot be a ticket id: it is not a branch name"
    return ""


def decide(raw: str, known_worker) -> Request | str:
    """The request, or why it is refused. Every branch refuses; only the end
    allows (the broker's rule), because this is fed by a process that may be
    compromised."""
    if len(raw.encode("utf-8", "replace")) > MAX_REQUEST_BYTES:
        return "the request is larger than a request needs to be"
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return "the request is not JSON"
    if not isinstance(parsed, dict):
        return "the request is not a JSON object"
    stray = sorted(set(parsed) - ALLOWED_KEYS)
    if stray:
        return (
            f"the request carries {', '.join(map(repr, stray))}, which a "
            "request may not set: only op, worker and ticket are a Manager's "
            "to choose (and a destroy is never forced)"
        )
    op, worker = parsed.get("op"), parsed.get("worker")
    ticket = parsed.get("ticket", "")
    if not all(isinstance(v, str) for v in (op, worker, ticket)):
        return "op, worker and ticket must be strings"
    if op not in OPS:
        return f"{op!r} is not one of {', '.join(OPS)}"
    problem = name_problem(worker, kind="worker name")
    if problem:
        return f"'worker' {problem}"
    if not known_worker(worker):
        return (
            f"there is no Worker called {worker!r} in this project; `rite "
            "status` lists the ones that exist"
        )
    ticket = ticket.strip()
    if ticket:
        problem = ticket_problem(ticket)
        if problem:
            return problem
    return Request(op=op, worker=worker, ticket=ticket)


def request(root: Path, manager: str, op: str, worker: str, ticket: str = "") -> Path:
    """Write one request, as `manager`, into its own directory. The CLI's
    half: `rite request <op> <worker>`."""
    body = {"op": op, "worker": worker}
    if ticket:
        body["ticket"] = ticket
    path = lifecycle_dir(root, manager) / request_name()
    write_atomic(path, json.dumps(body) + "\n")
    return path


def take(root: Path, manager: str) -> list[tuple[str, str]]:
    """Every pending request, oldest first, each CLAIMED by renaming it to
    `<name>.taken` before it is acted on (`chores.take`'s pattern): `(claim
    name, text)`.

    ⚠ **Never done twice.** A restart or a destroy is not idempotent, so a
    request is claimed by a rename, which only one taker can win, and a claim
    a crashed supervisor left behind is REPORTED by `interrupted`, never
    retried. Read through `own_dir`, which follows no link the Manager
    planted; a `lifecycle` that is one raises OSError for the caller to say."""
    return own_dir.take(root, manager, DIRNAME, MAX_REQUEST_BYTES, claim=TAKEN)


def interrupted(root: Path, manager: str) -> list[str]:
    """Claims a previous supervisor took and never finished."""
    return own_dir.names(root, manager, DIRNAME, ".json" + TAKEN)


def _done(root: Path, manager: str, claim: str) -> None:
    own_dir.unlink_in(root, manager, DIRNAME, claim)


ACTING = frozenset({STOP, DESTROY, RESTART})
"""The requests that change something. `status` and `gate` only look."""


def acting_requests(root: Path, manager: str) -> tuple[str, ...]:
    """The pending requests that would change a Worker, by name: what
    `progress.footprint` counts.

    🔴 Not `status` or `gate` (SCRUM-59 review): asking only to look is not
    progress, or a Manager that asks `status` every turn while it waits would
    be started again and again, without end, which is exactly what Robert's
    2026-10-02 rule ("a session that only replies is idle") forbids. Read
    without following a link; "" for anything unreadable."""
    try:
        pending = own_dir.names(root, manager, DIRNAME, ".json")
    except OSError:
        return ()
    found = []
    for name in pending:
        try:
            raw = own_dir.read(root, manager, DIRNAME, name, MAX_REQUEST_BYTES)
            if json.loads(raw).get("op") in ACTING:
                found.append(name)
        except (OSError, ValueError, AttributeError):
            continue
    return tuple(found)


_LOOKED: dict[tuple[str, str, str, str], str] = {}
"""What this process last told each Manager about a `status` or `gate`, per
(project, Manager, op, worker)."""


def _unchanged_look(root: Path, manager: str, req: Request, said: str) -> bool:
    """Whether a `status` or `gate` answer is the one already told. An
    unchanged answer is not told again: the note is mail, mail wakes a
    session, and a Manager asking every turn would otherwise wake itself
    every turn with nothing new (SCRUM-59 review)."""
    if req.op in ACTING:
        return False
    key = (str(root), manager, req.op, req.worker)
    if _LOOKED.get(key) == said:
        return True
    _LOOKED[key] = said
    return False


# --- which Manager a Worker belongs to ------------------------------------------


def _owners_dir(root: Path) -> Path:
    """Beside the publish records: under no path any profile grants, so no
    Manager can claim another's Worker by writing here."""
    from rite_ai.managers.mailbox import _checkout_key, _mail_home  # noqa: PLC2701

    return _mail_home().parent / "owners" / _checkout_key(Path(root))


def record_owner(root: Path, worker: str, manager: str) -> None:
    """Record that `manager` asked for `worker`, when rite started it for that
    Manager (`supervise._honour_worker_requests`)."""
    problem = name_problem(worker, kind="worker name")
    if problem:
        raise ValueError(problem)
    write_atomic(
        _owners_dir(root) / f"{worker}.json", json.dumps({"manager": manager}) + "\n"
    )


def _owner_of(root: Path, worker: str) -> str:
    """The Manager that asked for `worker`, or "" when rite has no record.

    Raises OSError or ValueError for a record that exists and cannot be read:
    that is "cannot tell", which `_may_act` refuses on, never "nobody's"."""
    path = _owners_dir(root) / f"{worker}.json"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    data = json.loads(text)
    owner = data.get("manager") if isinstance(data, dict) else None
    if not isinstance(owner, str) or not owner:
        raise ValueError(f"{path} names no Manager")
    return owner


def forget_owner(root: Path, worker: str) -> None:
    """Forget whose `worker` was, once its sandbox is gone (a destroy, or a
    delivery's): a Worker started again later is whoever's start it is, and
    one a person starts is the person's."""
    try:
        (_owners_dir(root) / f"{worker}.json").unlink()
    except OSError:
        pass


def _may_act(root: Path, manager: str, worker: str) -> str:
    """Why `manager` may not act on `worker`; "" when it may."""
    try:
        owner = _owner_of(root, worker)
    except (OSError, ValueError) as e:
        return f"rite cannot tell which Manager {worker!r} belongs to ({e})"
    if owner:
        if owner != manager:
            return (
                f"{worker!r} was started for Manager {owner!r}, not you; only "
                "the Manager that asked for a Worker may stop, restart, "
                "destroy, inspect or gate it"
            )
        return ""
    # 🔴 No record: refused, not given to "the Manager that routes"
    # (SCRUM-59 review, measured). Who routes is in `.rite/config.yaml`,
    # which a Manager can write, so a fallback read from it was one a
    # Manager could award itself. A Worker rite did not start for a Manager
    # (a person started it, or an older rite did) is the person's to act on.
    return (
        f"rite did not start {worker!r} for you, so it is not yours to act on; "
        f"ask the User to run `rite sandbox <op> {worker}` from their shell"
    )


# --- the operations, on the host ----------------------------------------------


def _stop(root: Path, req: Request) -> str:
    from rite_ai.sandbox import stop_worker

    done = stop_worker(req.worker, root)
    return f"{'Stopped' if done.ok else 'NOT stopped'} {req.worker}: {done.message}"


def _destroy(root: Path, req: Request) -> str:
    from rite_ai.publishing.deliver import _release_claims  # noqa: PLC2701
    from rite_ai.sandbox import destroy_worker

    # ⚠ Never forced: the guard against deleting unpushed work or an
    # unanswered question is the person's `--force`, not a Manager's.
    done = destroy_worker(req.worker, root, force=False)
    if not done.ok:
        return f"NOT destroyed {req.worker}: {done.message}"
    # Its claims go with it, as after a delivery: a claim left held blocks
    # the next Worker on those paths (SCRUM-70's shape).
    forget_owner(root, req.worker)
    return (
        f"Destroyed {req.worker}: {done.message}; {_release_claims(root, req.worker)}"
    )


def _restart(root: Path, req: Request, now: float) -> str:
    """Restart in place, under recovery's limit and backoff (`recovery`'s
    ledger), so a Manager and the watchdog's recovery cannot restart one
    Worker in a storm between them.

    A requested restart is counted under its own key (`recovery.REQUESTED`),
    which recovery's per-cycle prune leaves alone; it is forgotten once
    `DEFAULT_BACKOFF_CAP` has passed since the last one. A restart recovery
    made recently holds a request back too."""
    from rite_ai.managers import recovery

    ledger = recovery._read_ledger(root)
    key = recovery.REQUESTED + req.worker
    entry = ledger.get(key, recovery.LedgerEntry())
    if entry.attempts and now - entry.last_at >= recovery.DEFAULT_BACKOFF_CAP:
        entry = recovery.LedgerEntry()
    recovered = ledger.get(req.worker, recovery.LedgerEntry())
    if recovered.attempts >= recovery.DEFAULT_MAX_RESTARTS:
        return (
            f"NOT restarted {req.worker}: rite's own recovery already restarted "
            f"it {recovered.attempts} time(s) and left it for the User; say so "
            "to the User rather than asking again"
        )
    if entry.attempts >= recovery.DEFAULT_MAX_RESTARTS:
        return (
            f"NOT restarted {req.worker}: {entry.attempts} restart(s) asked for "
            f"within {int(recovery.DEFAULT_BACKOFF_CAP)}s, the most rite does; "
            "say so to the User rather than asking again"
        )
    last = max(entry.last_at, recovered.last_at)
    wait = recovery._backoff_seconds(max(entry.attempts, 1))
    if last and now - last < wait:
        left = int(wait - (now - last))
        return (
            f"NOT restarted {req.worker}: it was restarted {int(now - last)}s "
            f"ago; rite waits {int(wait)}s between restarts. Ask again in {left}s"
        )
    ok, message = recovery._restart(root, req.worker)
    ledger[key] = recovery.LedgerEntry(
        attempts=entry.attempts + 1, first_at=entry.first_at or now, last_at=now
    )
    recovery._write_ledger(root, ledger)
    return f"{'Restarted' if ok else 'NOT restarted'} {req.worker}: {message}"


def _status(root: Path, req: Request) -> str:
    from rite_ai.publishing import record as publish_record
    from rite_ai.sandbox import (
        _work_only_in_sandbox,  # noqa: PLC2701
        existing_sandbox_name,
        worker_sandbox_status,
    )

    status = worker_sandbox_status(req.worker, root)
    lines = [f"{req.worker}: sandbox {status}"]
    started = publish_record.read(root, req.worker)
    if isinstance(started, publish_record.Record):
        lines.append(f"started on {started.ticket} at {started.started_at}")
    elif isinstance(started, publish_record.Unreadable):
        lines.append(f"its start record cannot be read ({started.reason})")
    if status.known and status.value != "not found":
        from rite_ai.sandbox.questions import Unknown, WorkerQuestion, pending_question

        name = existing_sandbox_name(req.worker, root)
        asked = pending_question(name)
        if isinstance(asked, WorkerQuestion):
            # The Worker's words, not rite's: quoted and said so, since this
            # note goes out under rite's own header.
            lines.append(
                f"waiting on a question since {asked.since()}, in the "
                f"Worker's words: {asked.headline()!r}"
            )
        elif isinstance(asked, Unknown):
            lines.append(f"could not check for a question ({asked.reason})")
        at_risk = _work_only_in_sandbox(name, req.worker, root)
        if at_risk:
            lines.append(f"work only in the sandbox: {at_risk}")
    return "; ".join(lines)


def _gate(root: Path, req: Request) -> str:
    """The publish gate on the ticket branch a delivery collected, with its
    findings: what `rite publish check` would say, said to the Manager."""
    from rite_ai.config.parse import ParseError, module_dir_problem, parse_modules
    from rite_ai.gate.gate import brief, run_gate
    from rite_ai.publishing import record as publish_record
    from rite_ai.publishing.deliver import _sha, _worker_modules  # noqa: PLC2701

    ticket = req.ticket
    if not ticket:
        started = publish_record.read(root, req.worker)
        if not isinstance(started, publish_record.Record):
            return (
                f"NOT gated {req.worker}: rite has no record of its ticket; name "
                "it with --ticket"
            )
        ticket = started.ticket
    modules = parse_modules(Path(root) / ".rite" / "modules.yaml")
    if isinstance(modules, ParseError):
        return f"NOT gated {req.worker}: modules.yaml cannot be read: {modules.message}"
    try:
        mine = _worker_modules(root, req.worker, modules)
    except ValueError as e:
        return f"NOT gated {req.worker}: {e}"
    said = []
    for module in mine:
        project = Path(root) / module.path
        problem = module_dir_problem(Path(root), module.path)
        if problem:
            said.append(f"{module.name}: {problem}")
            continue
        if not (project / ".git").exists():
            said.append(f"{module.name}: {module.path} is not a git checkout here")
            continue
        if _sha(project, f"refs/heads/{ticket}") is None:
            said.append(
                f"{module.name}: no branch {ticket} in {module.path} yet, so "
                "nothing to gate; a delivery collects it and gates it"
            )
            continue
        # Full ref names: a tag or another ref named like the ticket would
        # otherwise be the one gated. Both halves are checked names
        # (`parse.branch_problem`, `ticket_problem`), so neither is an option.
        rng = f"refs/heads/{module.branch}..refs/heads/{ticket}"
        report = run_gate(project, rev_range=rng, config_root=root)
        said.append(
            f"{module.name} ({rng}): gate {report.outcome}"
            + "".join(f"\n  {line}" for line in brief(report, FINDINGS_SHOWN))
        )
    return f"Gate for {req.worker}/{ticket}:\n" + "\n".join(said or ["no module"])


def _act(root: Path, req: Request, now: float) -> str:
    if req.op == STOP:
        return _stop(root, req)
    if req.op == DESTROY:
        return _destroy(root, req)
    if req.op == RESTART:
        return _restart(root, req, now)
    if req.op == STATUS:
        return _status(root, req)
    return _gate(root, req)


def honour_requests(root: Path, manager: str, say, *, act=None, now=None) -> None:
    """Act on every request `manager` wrote, and tell it what happened.

    `act(root, request, now) -> note` is injectable so the policy can be
    tested without yoloAI. Every outcome is SAID and TOLD, a refusal
    included: a Manager that asked for something it may not have is
    confused or compromised, and both are worth seeing."""
    from rite_ai.managers.telling import tell_manager

    root = Path(root)
    act = act or _act

    def tell(text: str) -> None:
        try:
            tell_manager(root, manager, ABOUT, text)
        except OSError as e:
            say(f"could not tell {manager!r} what happened to its request: {e}")

    try:
        stale = interrupted(root, manager)
        pending = take(root, manager)
    except OSError as e:
        said = (
            f"rite did not read your requests: {lifecycle_dir(root, manager)} "
            f"cannot be opened as rite's own directory ({e}). Nothing was done"
        )
        say(f"{manager!r}: {said}")
        tell(said)
        return
    for claim in stale:
        # Taken by an earlier supervisor that never finished: whether it ran
        # is not knowable from here, so it is said, not retried.
        said = (
            f"a request rite took earlier was not finished ({claim}): it was "
            "interrupted, or could not be read. Whether it ran is not known; "
            "check with `rite request status <worker>` before asking again"
        )
        say(f"{manager!r}: {said}")
        tell(said)
        _done(root, manager, claim)

    def known(worker: str) -> bool:
        return (root / "workers" / worker / "worker.yml").is_file()

    for claim, raw in pending:
        try:
            req = decide(raw, known)
            if isinstance(req, str):
                said = f"NOT done: {req}"
            else:
                refusal = _may_act(root, manager, req.worker)
                if refusal:
                    said = f"NOT done ({req.op} {req.worker}): {refusal}"
                else:
                    said = act(root, req, time.time() if now is None else now)
                    if _unchanged_look(root, manager, req, said):
                        say(
                            f"{manager!r}: {req.op} {req.worker}: unchanged, not retold"
                        )
                        _done(root, manager, claim)
                        continue
        except Exception as e:  # noqa: BLE001 - said, never raised into the cycle
            said = f"NOT done: rite failed acting on it ({type(e).__name__}: {e})"
        say(f"{manager!r}: {said}")
        tell(said)
        _done(root, manager, claim)


def instructions(root: Path, manager: str) -> str:
    """What the Manager is told it can ask about a Worker."""
    from rite_ai import own_command

    rite = own_command()
    return (
        "\n\n## Stopping, restarting or checking a Worker — you ASK, rite does it\n\n"
        "From inside your sandbox you cannot reach a Worker's sandbox, so you "
        "ask, with one of these (each is a whole command; run it as it is):\n\n"
        f"    {rite} request status <worker>\n"
        f"    {rite} request stop <worker>\n"
        f"    {rite} request restart <worker>\n"
        f"    {rite} request destroy <worker>\n"
        f"    {rite} request gate <worker> [--ticket <ID>]\n\n"
        "rite does it when this turn ends and puts the outcome in your next "
        "instruction: `status` says whether the sandbox is running, which "
        "ticket it was started on, and whether a question or unpushed work "
        "is waiting in it; `gate` runs the publish gate on the ticket's "
        "collected branch and names what it found. `restart` keeps the "
        "Worker's work and is limited, like rite's own recovery; `destroy` "
        "deletes the sandbox, and is refused while it holds work or a "
        "question. You may act only on Workers rite started for you. An "
        "answer to `status` or `gate` that has not changed since you last "
        "asked is not repeated: no new note means nothing new.\n"
    )
