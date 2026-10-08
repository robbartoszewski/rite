"""RL-6 as a REAL review, by a Manager of any engine (SCRUM-72 §3.3b).

🔴 **What was there, and why it was an engine gate.** SCRUM-72's root cause
was `if role.is_local`: the local tier ran only under a local Manager. The
approval path must not reintroduce one, so this is built from the principle
Robert set — **whether a Manager may approve a plan depends only on its duty
and its independence from the author, never on what kind of engine it runs.**

Approval was a **stamp**. `loop.advance_ticket` picked a reviewer with
`independent_reviewer` and immediately called `approve_plan` in that
reviewer's name: no Manager was ever asked, nothing could ever write
REJECTED, and the "review" was rite approving on a Manager's behalf. RL-6's
gate checked the rules about WHO could have reviewed, against a review that
did not happen.

**The harness never approves now.** At `decomposed` the supervisor picks the
independent reviewer, sends **one** request to that Manager's inbox — the
channel every Manager reads whatever its engine — and waits. The Manager
answers with `rite plan approve <ticket>` or `rite plan reject <ticket>
--reason-file <path>` inside its own boundary, which writes a verdict into
its own directory. The supervisor honours it at the cycle boundary. One code
path for every engine: Claude, `local:<class>`, a person answering from their
own shell.

⚠ **The reviewer's identity is the DIRECTORY the verdict was found in, never
a name in the payload.** `own_dir` opens that directory following no link at
any depth and reads nothing but a regular file, so a Manager cannot plant a
link into a peer's directory and borrow its name — and a payload carrying a
`reviewer` key is refused by name rather than ignored, because a field that
is read by nothing is a field somebody will later read.

⚠ **No approving a plan nobody read.** What was ASKED is recorded outside
every Manager's grant (beside the mailboxes, `mailbox._mail_home`'s parent,
the same place the Worker-owner records live): which plan version, asked of
whom, when. A verdict says only approve-or-reject, which ticket, and why; the
reviewer and the plan version both come from that record, never from the
verdict. If the plan has changed since it was asked about, the verdict is
refused and the review is asked again. ⚠ That is STRONGER than §3.3b's "a
verdict carries the plan version it was given": a version in the verdict is a
value the answering Manager chooses, and it could not read the real one
anyway, since the plan state is not in its grant.

**Told once, then re-asked, then escalated.** A request is sent once and
tracked. An unanswered request is re-asked on an interval, and after
`ESCALATE_AFTER_ASKS` it is reported rather than asked again — a reviewer
that never answers is a fleet fault, and asking for ever is the per-cycle
loop SCRUM-64 exists to stop.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.managers import own_dir

DIRNAME = "plan-review"
"""The Manager's own `plan-review/`, beside `lifecycle/`, `requests/` and
`deliveries/` — under the one directory of rite's its boundary lets it
write."""

APPROVE = "approve"
REJECT = "reject"
OPS = (APPROVE, REJECT)

ALLOWED_KEYS = frozenset({"op", "ticket", "reason"})
"""⚠ **A verdict says only: approve or reject, which ticket, and why.**

`reviewer` is deliberately absent: who is answering is the DIRECTORY the
verdict was found in, and a payload that names a reviewer is refused with
that reason rather than having the field ignored.

🔴 **And so is the plan version, deliberately — this is STRONGER than the
design asked for.** §3.3b says "a verdict carries the plan version it was
given, and is refused if the plan has changed since". A version the verdict
carries is a value the answering Manager chooses, and the Manager cannot read
the plan's version from inside its boundary anyway (the plan state is not in
its grant). So what was ASKED is recorded where no Manager can write it
(`asked_dir`), and the version is compared against THAT. The property the
design wanted — no approving a plan nobody read — holds without trusting the
answer about what was read."""

MAX_VERDICT_BYTES = 64 * 1024
"""A verdict carries a rejection's reasons, so it is larger than a lifecycle
request — and still bounded, because it is read outside every boundary."""

MAX_REASON_CHARS = 4000

REASK_SECONDS = 1800.0
"""How long an unanswered review request stands before it is asked again."""

ESCALATE_AFTER_ASKS = 3
"""After this many asks with no verdict, it is reported rather than asked
again."""


# --- what was asked, kept where no Manager can write it ---------------------------


@dataclass(frozen=True)
class Asked:
    request: str
    ticket: str
    reviewer: str
    author: str
    plan_version: str
    at: float = 0.0
    asks: int = 1

    def line(self) -> str:
        return (
            f"{self.ticket}: plan review asked of {self.reviewer} "
            f"(request {self.request}, ask {self.asks})"
        )


def asked_dir(root: Path) -> Path:
    """Beside the mailboxes and the Worker-owner records: under no path any
    Manager's profile grants, so no Manager can invent a request addressed to
    itself, or rewrite which plan version a request was about."""
    from rite_ai.managers.mailbox import _checkout_key, _mail_home  # noqa: PLC2701

    return _mail_home().parent / "plan-review" / _checkout_key(Path(root))


def _asked_path(root: Path, ticket: str) -> Path:
    from rite_ai.names import require_safe_name

    return asked_dir(root) / f"{require_safe_name(ticket, kind='ticket')}.json"


def read_asked(root: Path, ticket: str) -> Asked | None:
    """What was asked about `ticket`, or None. A record that cannot be read is
    None: the request is then asked again, which is the safe direction — the
    unsafe one is honouring a verdict against a request nobody can read."""
    try:
        raw = _asked_path(root, ticket).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    try:
        return Asked(
            request=str(body["request"]),
            ticket=str(body["ticket"]),
            reviewer=str(body["reviewer"]),
            author=str(body.get("author", "")),
            plan_version=str(body["plan_version"]),
            at=float(body.get("at", 0.0)),
            asks=int(body.get("asks", 1)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_asked(root: Path, asked: Asked) -> None:
    from rite_ai.state import write_atomic

    path = _asked_path(root, asked.ticket)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(
        path,
        json.dumps(
            {
                "request": asked.request,
                "ticket": asked.ticket,
                "reviewer": asked.reviewer,
                "author": asked.author,
                "plan_version": asked.plan_version,
                "at": asked.at,
                "asks": asked.asks,
            },
            sort_keys=True,
        )
        + "\n",
    )


def forget_asked(root: Path, ticket: str) -> None:
    """Once a verdict has been honoured, or the plan has moved on."""
    try:
        _asked_path(root, ticket).unlink()
    except (OSError, ValueError):
        pass


# --- asking: one request, to the reviewer's inbox ---------------------------------


@dataclass(frozen=True)
class AskOutcome:
    asked: Asked | None = None
    sent: bool = False
    note: str = ""
    escalated: bool = False


def ask(
    root: Path,
    manager: str,
    ticket: str,
    reviewer: str,
    author: str,
    plan_version: str,
    *,
    now: float | None = None,
    tell=None,
) -> AskOutcome:
    """Ask `reviewer` to review `ticket`'s plan, at most once per interval.

    `manager` is the Manager DRIVING the pipeline — whose Worker holds the
    ticket, and so whose publish record carries the agreed definition of done
    the request quotes. It is not the author and not the reviewer: after §3.3
    the driver keys off local WORKERS rather than the Manager's engine, so a
    Claude `lead` can be driving a ticket a local `planner` authored.

    Returns what happened, for the caller to report. Never raises: a request
    that could not be sent is said and asked again next cycle, because a
    pipeline that stopped because mail failed would be a pipeline a full disk
    ends.
    """
    import secrets

    now = time.time() if now is None else now
    standing = read_asked(root, ticket)

    # A plan that changed since the standing request is a different plan: the
    # old request is void, and asking about the new one is a new ask.
    if standing is not None and (
        standing.plan_version != plan_version or standing.reviewer != reviewer
    ):
        forget_asked(root, ticket)
        standing = None

    if standing is not None:
        if standing.asks >= ESCALATE_AFTER_ASKS:
            return AskOutcome(
                asked=standing,
                note=(
                    f"{reviewer} has been asked {standing.asks} times to review "
                    f"{ticket}'s plan and has not answered. Nothing else can "
                    f"move it: `rite plan approve {ticket}` or `rite plan reject "
                    f"{ticket} --reason-file <path>`, as {reviewer} or as the "
                    "Owner"
                ),
                escalated=True,
            )
        if now - standing.at < REASK_SECONDS:
            return AskOutcome(
                asked=standing,
                note=f"{ticket}: waiting for {reviewer}'s verdict on its plan",
            )
        asked = Asked(
            request=standing.request,
            ticket=ticket,
            reviewer=reviewer,
            author=author,
            plan_version=plan_version,
            at=now,
            asks=standing.asks + 1,
        )
    else:
        asked = Asked(
            request=secrets.token_hex(8),
            ticket=ticket,
            reviewer=reviewer,
            author=author,
            plan_version=plan_version,
            at=now,
            asks=1,
        )

    if tell is None:
        from rite_ai.managers.telling import tell_manager

        def tell(text: str) -> None:
            tell_manager(root, reviewer, "a plan waiting for your review", text)

    try:
        tell(_request_text(root, manager, ticket, asked))
    except OSError as e:
        return AskOutcome(
            asked=standing,
            note=f"{ticket}: its plan review could not be asked for ({e})",
        )
    # ⚠ Recorded only AFTER the note went out. The other order would record a
    # request nobody was sent, and then wait `REASK_SECONDS` for an answer to
    # a question nobody was asked.
    _write_asked(root, asked)
    return AskOutcome(asked=asked, sent=True, note=asked.line())


def _request_text(root: Path, manager: str, ticket: str, asked: Asked) -> str:
    """What the reviewing Manager is told. The PLAN, and the agreed definition
    of done it is to be judged against — evidence, never instruction (RL-T26):
    nothing the decomposer wrote may tell the reviewer what to check."""
    from rite_ai.coordination.local_backend import LocalStateLayer
    from rite_ai.local import decomposition as dec

    lines = [
        f"{ticket}: its decomposition is waiting for YOUR review. You hold "
        "plan-review, you did not author it, and you run a different model "
        f"from {asked.author or 'its author'} — so RL-6, DD-3.5 and RL-67 are "
        "satisfied by you and by nobody else in this fleet right now.",
        "",
        "Answer with ONE of these, from your own shell:",
        f"  rite plan approve {ticket}",
        f"  rite plan reject {ticket} --reason-file <a file you wrote>",
        "",
        "rite honours your answer at its next cycle boundary. Nothing else "
        "can move this ticket: the harness does not approve plans.",
        "",
    ]
    state = LocalStateLayer(Path(root) / ".rite")
    read = dec.read(state, ticket)
    if read.plan is None:
        lines.append(
            f"(rite could not read the plan to quote it: "
            f"{read.unavailable or read.error or 'it is not there'})"
        )
        return "\n".join(lines)
    plan = read.plan
    lines.append(f"The plan, by {plan.decomposed_by or '(nobody)'}:")
    for sub in plan.subtasks:
        lines.append(f"  {sub.id}: {sub.intent}")
        lines.append(f"      scope:  {', '.join(sub.scope) or '(none)'}")
        lines.append(f"      verify: {sub.verify or '(none)'}")
        lines.append(f"      cites:  {', '.join(sub.cites) or '(none)'}")
    if plan.returns:
        lines.append("")
        lines.append("It has been returned before, for:")
        lines.extend(f"  - {r}" for r in plan.returns)
    lines.append("")
    lines.extend(_definition_lines(root, manager, ticket))
    return "\n".join(lines)


def _definition_lines(root: Path, manager: str, ticket: str) -> list[str]:
    """The refinement record's agreed definition of done, which is what the
    plan has to be a plan FOR (§3.3b: the request carries it)."""
    from rite_ai.local.gates import definition_snapshot

    payload = definition_snapshot(Path(root), manager, ticket)
    if isinstance(payload, str):
        # The DEFINED stage cannot have been passed without one, so this is
        # said rather than guessed around.
        return [f"(rite could not read the agreed definition of done: {payload})"]
    lines = ["The agreed definition of done:"]
    for item in payload.get("definition_of_done", ()):
        lines.append(f"  - {item}")
    verify = payload.get("verify")
    lines.append(
        f"  verify: {verify if isinstance(verify, str) else ', '.join(verify or ())}"
    )
    scope_in = payload.get("scope_in") or ()
    if scope_in:
        lines.append(f"  in scope: {', '.join(scope_in)}")
    scope_out = payload.get("scope_out") or ()
    if scope_out:
        lines.append(f"  out of scope: {', '.join(scope_out)}")
    return lines


# --- answering: a verdict in the Manager's own directory --------------------------


@dataclass(frozen=True)
class Verdict:
    op: str
    ticket: str
    reason: str = ""


def decide(raw: str) -> Verdict | str:
    """The verdict, or why it is refused. Every branch refuses; only the end
    allows (the broker's rule), because this is fed by a process that may be
    compromised."""
    if len(raw.encode("utf-8", "replace")) > MAX_VERDICT_BYTES:
        return "the verdict is larger than a verdict needs to be"
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return "the verdict is not JSON"
    if not isinstance(parsed, dict):
        return "the verdict is not a JSON object"
    if "reviewer" in parsed:
        return (
            "the verdict names a reviewer, which is not a Manager's to choose: "
            "who is answering is the directory the verdict was found in, and a "
            "name in the payload would be a name anyone could write"
        )
    stray = sorted(set(parsed) - ALLOWED_KEYS)
    if stray:
        return (
            f"the verdict carries {', '.join(map(repr, stray))}, which a verdict "
            f"may not set: only {', '.join(sorted(ALLOWED_KEYS))} are"
        )
    op = parsed.get("op")
    ticket = parsed.get("ticket")
    reason = parsed.get("reason", "")
    if not all(isinstance(v, str) for v in (op, ticket, reason)):
        return "op, ticket and reason must be strings"
    if op not in OPS:
        return f"{op!r} is not one of {', '.join(OPS)}"
    from rite_ai.managers.lifecycle import ticket_problem

    problem = ticket_problem(ticket)
    if problem:
        return problem
    if op == REJECT and not reason.strip():
        return (
            "a rejection with no reasons cannot be re-authored against (RL-10): "
            "give `--reason-file <path>`"
        )
    if len(reason) > MAX_REASON_CHARS:
        return f"the reasons are longer than {MAX_REASON_CHARS} characters"
    return Verdict(op=op, ticket=ticket, reason=reason)


def verdict_dir(root: Path, manager: str) -> Path:
    """The Manager's own `plan-review/`, where it writes its answers."""
    from rite_ai.managers import manager_dir

    return manager_dir(Path(root), manager) / DIRNAME


def write_verdict(
    root: Path, manager: str, op: str, ticket: str, reason: str = ""
) -> Path:
    """A Manager's answer, written into its OWN directory — the CLI's half
    (`rite plan approve|reject`). Written in Python, so no `$()` or `>` ever
    reaches the engine (SCRUM-69's rule).

    ⚠ A plain write, `lifecycle.request`'s pattern: this runs AS the Manager,
    inside its own boundary, writing the one directory it may write. The
    link-safety is on the READING side, where rite opens this outside every
    boundary — `honour` goes through `own_dir`, which follows no link at any
    depth and reads nothing but a regular file.
    """
    from rite_ai.managers.lifecycle import request_name
    from rite_ai.state import write_atomic

    body = {"op": op, "ticket": ticket}
    if reason:
        body["reason"] = reason
    path = verdict_dir(root, manager) / request_name()
    write_atomic(path, json.dumps(body) + "\n")
    return path


def honour_verdicts(
    root: Path,
    manager: str,
    say,
    *,
    approve=None,
    reject=None,
    state=None,
) -> list[str]:
    """Honour every verdict `manager` has written, at the cycle boundary.

    ⚠ Named `honour_verdicts` and not `honour`, which is what
    `lifecycle.honour_requests` and `broker.honour` would have made the
    obvious choice. `tests/test_no_dead_wiring` matches a call by FUNCTION
    NAME across the whole package, so a second `honour` makes the broker's
    standing exemption read as stale and turns that guard red — the same
    name-collision `loop.advance_ticket`'s `author_plan` parameter is named
    around. Fixing the matcher to be scope-aware is right and is not done
    here; it uncovers four unrelated functions the loose match was masking.

    ⚠ **`manager` is the identity.** It is the directory being read, so a
    verdict here can only answer a request addressed to this Manager, and the
    payload is never consulted for who is answering.
    """
    root = Path(root)
    if state is None:
        from rite_ai.coordination.local_backend import LocalStateLayer

        state = LocalStateLayer(root / ".rite")
    if approve is None:
        from rite_ai.local.approve import approve_plan

        approve = approve_plan
    if reject is None:
        from rite_ai.local.approve import reject_plan

        reject = reject_plan

    try:
        found = own_dir.take(root, manager, DIRNAME, MAX_VERDICT_BYTES)
    except OSError as e:
        say(f"{manager!r}: its plan-review verdicts could not be read ({e})")
        return []

    said: list[str] = []
    for name, raw in found:
        if not raw:
            # `own_dir.take` set aside something that was not a regular file.
            said.append(
                f"{manager!r}: {name} was not a regular file and was set aside, "
                "so it was not read as a verdict"
            )
            say(said[-1])
            continue
        verdict = decide(raw)
        if isinstance(verdict, str):
            said.append(f"{manager!r}: a plan-review verdict was refused — {verdict}")
            say(said[-1])
            continue
        said.append(_honour_one(root, manager, verdict, state, approve, reject))
        say(said[-1])
    return said


def _honour_one(
    root: Path, manager: str, verdict: Verdict, state, approve, reject
) -> str:
    from rite_ai.local import decomposition as dec

    asked = read_asked(root, verdict.ticket)
    if asked is None:
        return (
            f"{manager!r}: its verdict on {verdict.ticket} was refused — rite "
            "has no record of asking anyone to review that plan"
        )
    if asked.reviewer != manager:
        # ⚠ Cannot normally happen: the verdict was found in THIS Manager's
        # directory. It is checked anyway, because "the identity is the
        # directory" must be a property something asserts rather than a
        # property of how the caller happens to be wired.
        return (
            f"{manager!r}: its verdict on {verdict.ticket} was refused — that "
            f"plan review was asked of {asked.reviewer}, not of this Manager"
        )

    read = dec.read(state, verdict.ticket)
    if read.unavailable or read.error or read.plan is None:
        unreadable = read.unavailable or read.error or "it is not there"
        return (
            f"{manager!r}: its verdict on {verdict.ticket} was refused — its "
            f"plan could not be read ({unreadable})"
        )
    if read.version != asked.plan_version:
        forget_asked(root, verdict.ticket)
        return (
            f"{manager!r}: its verdict on {verdict.ticket} was refused — the "
            "plan has changed since it was asked about, so the verdict would "
            "have been given to a plan nobody read. The review is asked again"
        )

    if verdict.op == APPROVE:
        result = approve(root, verdict.ticket, manager, state=state)
        why = getattr(result, "why", "")
        if why:
            return f"{manager!r}: {verdict.ticket} was NOT approved — {why}"
        forget_asked(root, verdict.ticket)
        return f"{manager!r}: {verdict.ticket}'s plan is approved by {manager}"

    result = reject(root, verdict.ticket, manager, verdict.reason, state=state)
    why = getattr(result, "why", "")
    if why:
        return f"{manager!r}: {verdict.ticket} was NOT rejected — {why}"
    forget_asked(root, verdict.ticket)
    return (
        f"{manager!r}: {verdict.ticket}'s plan is rejected by {manager}; it goes "
        f"back to {asked.author or 'its planner'} with the reasons"
    )
