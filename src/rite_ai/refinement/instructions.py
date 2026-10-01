"""What the Owner is told about refining, every cycle (TR2, with TR3's text).

Two parts, both only for the Manager that refines (the one holding `route`,
or a lone Manager; TRQ7):

* **`instructions`**: the standing rules, in Robert's words where he gave
  them. Ask straight away when there are doubts, propose when there are
  none, never decide for him, and never work on a guess.
* **`brief`**: this cycle's refinement work, computed by rite from one read
  of each `scheduled` ticket and the round ledger: what is open and where it
  stands, what to start, what waits for him, what is queued, what needs a
  person. **So the Owner never has to read the board to refine** (the note's
  part 3.4 step 1), which is what makes this work on Jira, where a Manager
  cannot read the board at all.

The ticket text in the brief is the board's, not rite's and not the User's,
so it is normalised (SPEC §6.6.1) and quoted line by line: it cannot forge a
header or pass for an instruction.
"""

from __future__ import annotations

import time
from pathlib import Path

from rite_ai.refinement import admit, rounds
from rite_ai.refinement import status as st


def refiner(root: Path, config) -> str:
    """The Manager that refines in this project, or "" when none can be named
    (then nobody is told to refine, and the loop says so)."""
    from rite_ai.config.managers import routing_owner

    roles = list(config.coordination.manager_roles)
    if roles:
        return routing_owner(roles) or ""
    managers = list(config.coordination.managers)
    return managers[0] if len(managers) == 1 else ""


def _is_refiner(root: Path, manager: str, config) -> bool:
    roles = list(config.coordination.manager_roles)
    if not roles:
        return True  # a lone Manager refines its own tickets
    return refiner(root, config) == manager


def instructions(root: Path, manager: str, config) -> str:
    """The standing rules, for the Manager that refines; "" for any other."""
    if not _is_refiner(root, manager, config):
        return ""
    from rite_ai import own_command
    from rite_ai.managers import stdin_text

    rite = own_command()
    limits = config.refinement
    words = ", ".join(f"`{w}`" for w in limits.accept_words)
    return "\n".join(
        [
            "",
            "",
            "## Refining work with the User (you are the one who refines)",
            "",
            "Work starts only on a REFINED ticket: one with a definition of "
            "done the User agreed, which rite holds as a signed record. rite "
            "refuses to route, assign or start a Worker on anything else. "
            "Your part is to agree it with him, through rite:",
            "",
            "- **Refine straight away.** When a ticket or an instruction "
            "reaches you, send its first round in this same turn. If you have "
            "doubts, ask; if you have none, propose.",
            "- **A round** is at most three numbered questions under "
            "`Questions:`, or a proposal under `Proposal:`, or both. From "
            "round 2 every round proposes. End each proposal item with where "
            'it came from: `[ticket: "…"]` or `[answer: "…"]`, quoted '
            "exactly, or `[proposed]` for your own idea. rite checks every "
            "quote, and shows him which items are yours.",
            '- **"You decide"** means: send your recommendation as a complete '
            "proposal in the next round, and ask nothing more. It is not a yes.",
            f"- **He accepts with one word** ({words}) under the latest "
            "proposal, and rite records it. Nothing else is a yes, and you "
            "never write a definition of done yourself.",
            "- **One round per ticket at a time.** rite tells you when he "
            "answers. If he does not, do not ask again: rite puts the same "
            "question back in front of him when he is next active.",
            "- **An instruction he gives in chat** is refined straight away "
            "too, by its message id (`--message`). If he does not reply in "
            f"{limits.chore_after_minutes} minutes, rite files it as a chore "
            "with exactly his words, unrefined, so it is not lost.",
            "- **Take the rounds the topic needs.** While he is answering "
            "there is no limit on rounds. If your proposal has not changed "
            "from one round to the next, rite tells him so in the message: "
            "change it in response to what he said, or ask what is still "
            "wrong. If he does not reply to "
            f"{limits.unanswered} messages in a row about a ticket, rite stops "
            "asking and parks it. Nothing is ever worked on a guess.",
            "",
            "To send a round about a ticket:",
            stdin_text.heredoc(f"{rite} refine ask <ID> -", "<your round>"),
            "and about an instruction he gave in chat:",
            stdin_text.heredoc(
                f"{rite} refine ask --message <message-id> -", "<your round>"
            ),
            stdin_text.RULE,
            "",
        ]
    )


def _quoted(text: str) -> str:
    from rite_ai.normalise import normalise

    clean = normalise(text or "").text.strip() or "(empty)"
    return "\n".join(f"    > {line}" for line in clean.splitlines())


def brief(
    root: Path, manager: str, board, config, *, now: float | None = None, say=None
) -> str:
    """This cycle's refinement work for the Manager that refines, or "".

    It also reconciles the `ready-to-work` view (TR7) first, from the same
    reads: the Manager that refines is the Owner, the one supervisor that
    writes the label. `say` gets the start line of what it corrected.
    """
    if board is None or not _is_refiner(root, manager, config):
        return ""
    from rite_ai.refinement import view
    from rite_ai.tickets import BackendError, TicketFilter

    now = time.time() if now is None else now
    labels = view.reconcile(board, view.managers_of(config))
    if callable(say) and labels.line():
        say(labels.line())
    corrected = labels.instruction()
    listed = labels.scheduled
    if listed is None:
        listed = board.list_tickets(TicketFilter(label=view.SCHEDULED))
    if isinstance(listed, BackendError):
        return corrected + (
            "\n\n## Refinement: this cycle (rite)\n\nrite could not read the "
            f"board ({listed.message}), so there is no refinement list this "
            "cycle. Do not refine from memory.\n"
        )
    attempts = rounds.all_attempts(root, manager)
    states, tickets = [], {}
    for ticket in listed:
        answer = labels.statuses.get(ticket.id) or st.checked(
            lambda t: st.status(board, t), ticket.id
        )
        shown = answer.ticket or ticket
        tickets[ticket.id] = shown
        states.append(
            (shown, rounds.state_of(answer, attempts.get(ticket.id), now=now))
        )
    work = admit.admit(
        states,
        open_max=config.refinement.open_max,
        start_per_session=config.refinement.start_per_session,
    )
    by_id = {t.id: s for t, s in states}
    lines: list[str] = []
    for ticket_id in work.open:
        lines += _open_entry(tickets[ticket_id], by_id[ticket_id], config)
    for ticket_id in work.start:
        t = tickets[ticket_id]
        lines += [
            f"- **{ticket_id}: start refining it now** (round 1). "
            f"{by_id[ticket_id].name}. Its title and description:",
            _quoted(f"{t.title}\n{t.description}"),
        ]
    for key, attempt in attempts.items():
        if key.startswith("message-") and not attempt.parked:
            lines += _message_entry(key, attempt, config)
    if work.waiting:
        lines.append(
            "- **Waiting for him** (no answer by the deadline; do not ask "
            "again, rite re-presents it when he is back): " + ", ".join(work.waiting)
        )
    if work.queued:
        lines.append(
            "- **Queued for refinement**, in order, after the ones above: "
            + ", ".join(work.queued)
        )
    for ticket_id, why in work.needs_person.items():
        lines.append(f"- **{ticket_id} needs a person**: {why}")
    if not lines:
        return corrected
    return (
        corrected + "\n\n## Refinement: this cycle (rite)\n\n" + "\n".join(lines) + "\n"
    )


def _open_entry(ticket, state, config) -> list[str]:
    attempt = state.attempt
    latest = attempt.latest if attempt else None
    k = latest.k if latest else 0
    if latest is not None and latest.answered:
        what = (
            f"he answered round {k}: send round {k + 1} now, a proposal that "
            "quotes his answer exactly and asks only what is still open"
        )
    else:
        what = f"round {k} is with him; wait for his answer"
    lines = [
        f"- **{ticket.id}** ({state.name}): {what}. Title and description:",
        _quoted(f"{ticket.title}\n{ticket.description}"),
    ]
    if attempt and attempt.answers:
        lines.append("  His answers so far, which a proposal may quote:")
        lines += [_quoted(str(a.get("words", ""))) for a in attempt.answers]
    return lines


def _message_entry(key: str, attempt, config) -> list[str]:
    latest = attempt.latest
    if latest is None:
        return []
    state = (
        f"he answered round {latest.k}: send round {latest.k + 1}, a proposal"
        if latest.answered
        else f"round {latest.k} is with him"
    )
    lines = [f"- **Your instruction `{key[len('message-') :]}`** ({state})."]
    if attempt.answers:
        lines.append("  His answers so far, which a proposal may quote:")
        lines += [_quoted(str(a.get("words", ""))) for a in attempt.answers]
    return lines
