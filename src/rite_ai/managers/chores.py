"""A User's instruction becomes a chore ticket, written by rite (TR9).

**Robert, 2026-09-28 (TRQ5):** "Can we just ticket all work that Workers do?
(in JIRA that would be chore tickets I guess)". So work that arrives as a
chat instruction, not as a ticket, gets one before anyone works it.

## Who writes what

* **The Manager chooses WHICH messages**, by id: `rite chore <id>…` writes a
  request into its own directory, as `rite route` does. It may carry ids and
  nothing else.
* **rite writes the ticket**, outside the boundary: the description is the
  User's words exactly as they were delivered (`delivered.record`), and the
  title is cut from them. So a Manager cannot author a chore, and cannot pass
  off its own idea as the User's. Work a Manager thinks of itself is an
  ordinary ticket, filed and refined like any other.

⚠ **Why neither auto-create nor refuse-until-filed.** Creating a chore for
every delivered instruction would ticket "what's the status?" too: rite
cannot tell work from conversation, and a model deciding it is the judgement
this design keeps out of the load-bearing path. Refusing until a person files
one moves the lazy User's typing to the board. The Manager picks; rite
writes.

## On the board

Labelled `chore` and `scheduled`. `scheduled` because the User asked for it
to be done, which is what that label means (`V070_TICKET_REFINEMENT.md` part
3.10), so it enters the same queue, and under TR5 the same refinement, as
any other ticket. Jira creates every ticket as a `Task` (`JiraBackend.create`),
so no custom issue type is needed.

⚠ **Every outcome reaches the Manager** in its next instruction, as a note
in its own inbox under rite's header, and is said on the terminal. A chore
that silently failed would leave the Manager believing the work is tracked.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.managers import manager_dir
from rite_ai.state import write_atomic

CHORES_DIRNAME = "chores"
ALLOWED_KEYS = frozenset({"messages"})
MAX_REQUEST_BYTES = 4096
MAX_MESSAGES = 10
CHORE_LABEL = "chore"
LABELS = (CHORE_LABEL, "scheduled")
TITLE_MAX = 72
NOTE_HEADER = "[rite · chores · rite's own words · context — not an instruction]"


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str = ""
    ids: tuple[str, ...] = ()


def _chores_dir(root: Path, manager: str) -> Path:
    """Where a Manager asks for chores: its OWN directory, which its profile
    lets it write, beside `routes/` and `requests/`."""
    return manager_dir(root, manager) / CHORES_DIRNAME


def request(root: Path, manager: str, ids: list[str]) -> Path:
    """Write one chore request, as `manager`, into its own directory."""
    where = _chores_dir(root, manager)
    where.mkdir(parents=True, exist_ok=True)
    path = where / f"{time.time_ns()}.json"
    write_atomic(path, json.dumps({"messages": list(ids)}) + "\n")
    return path


TAKEN = ".taken"


def take(root: Path, manager: str) -> list[tuple[Path, str]]:
    """Every pending request, oldest first, each CLAIMED by renaming it to
    `<name>.taken` before it is acted on. `_done` removes the claim.

    ⚠ **Neither lost nor done twice.** The board's create is not idempotent,
    so a request deleted before it was acted on is lost if the supervisor
    dies in between, and one deleted after is created twice if it dies after
    the create. A claim left behind is found by `_interrupted`, and REPORTED,
    never retried: whether its chore exists is a fact about the board that
    only a person looking at it can settle cheaply.
    """
    where = _chores_dir(root, manager)
    if not where.is_dir():
        return []
    found: list[tuple[Path, str]] = []
    for path in sorted(where.glob("*.json")):
        claim = path.with_name(path.name + TAKEN)
        try:
            path.rename(claim)
            found.append((claim, claim.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return found


def _done(claim: Path) -> None:
    try:
        claim.unlink()
    except OSError:
        pass


def _interrupted(root: Path, manager: str) -> list[Path]:
    """Claims a previous supervisor took and never finished."""
    where = _chores_dir(root, manager)
    return sorted(where.glob("*.json" + TAKEN)) if where.is_dir() else []


def decide(raw: str) -> Decision:
    """Whether a request is well formed. Which messages it names are checked
    against the ledger separately, in `create_asked_for`."""
    if len(raw.encode("utf-8", errors="replace")) > MAX_REQUEST_BYTES:
        return Decision(False, f"larger than {MAX_REQUEST_BYTES} bytes")
    try:
        data = json.loads(raw)
    except ValueError:
        return Decision(False, "not JSON")
    if not isinstance(data, dict):
        return Decision(False, "not a JSON object")
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        # A title or a description here is exactly what a Manager may not
        # choose. Refused, not ignored: an ignored field is one somebody is
        # trying to have an effect with.
        return Decision(
            False,
            f"unknown key(s) {sorted(unknown)}: a chore request names "
            "messages and nothing else, because rite writes the chore's text",
        )
    ids = data.get("messages")
    if not isinstance(ids, list) or not ids:
        return Decision(False, "'messages' must be a non-empty list of message ids")
    if len(ids) > MAX_MESSAGES:
        return Decision(False, f"more than {MAX_MESSAGES} messages in one chore")
    if not all(isinstance(i, str) and i.strip() for i in ids):
        return Decision(False, "every message id must be a non-empty string")
    if len(set(ids)) != len(ids):
        return Decision(False, "a message is named twice")
    return Decision(True, ids=tuple(i.strip() for i in ids))


def _title_of(words: str) -> str:
    """The chore's title, cut from the User's first line: rite's, not the
    Manager's."""
    first = next((line for line in words.splitlines() if line.strip()), "")
    first = " ".join(first.split())
    if len(first) > TITLE_MAX:
        first = first[: TITLE_MAX - 1].rstrip() + "…"
    return f"chore: {first}"


def _description_of(entries: list[dict]) -> str:
    """The User's words as delivered, then where each came from.

    The words come first and unaltered, so a reader of the ticket sees what
    the User said before anything rite says about it.
    """
    blocks = [str(e["words"]).strip() for e in entries]
    sources = []
    for e in entries:
        sent = e.get("sent_at")
        when = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(float(sent)))
            if isinstance(sent, (int, float)) and sent > 0
            else "time unknown"
        )
        sources.append(f"- message `{e['id']}`, {e.get('where', '?')}, {when}")
    return (
        "\n\n".join(blocks)
        + "\n\n---\n\n"
        + "Chore created by rite from the User's instruction"
        + ("s" if len(entries) > 1 else "")
        + ". The text above is the User's own words as rite delivered them; "
        "no Manager wrote it.\n\n" + "\n".join(sources) + "\n"
    )


def note(text: str) -> str:
    """A note for the Manager's next instruction, under rite's header."""
    return f"{NOTE_HEADER}\n{text}"


def create_asked_for(root: Path, manager: str, board, say) -> int:
    """Create the chores `manager` asked for, and tell it what happened.

    Returns how many were created. `board` is the project's ticket backend,
    or None; with none, every request is refused (D-74: an unreachable board
    is not somewhere work can be tracked).
    """
    from rite_ai.managers.delivered import lookup
    from rite_ai.managers.mailbox import INBOX, send
    from rite_ai.tickets.interface import BackendError

    for claim in _interrupted(root, manager):
        try:
            asked = claim.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            asked = "(unreadable)"
        said = (
            "a chore request was interrupted before rite could say whether it "
            f"was created ({asked}). Look on the board for a ticket labelled "
            f"{CHORE_LABEL!r} from those messages before asking again."
        )
        say(f"{manager!r}: {said}")
        try:
            send(root, manager, INBOX, note(said))
        except OSError:
            continue
        _done(claim)
    pending = take(root, manager)
    created = 0
    for claim, raw in pending:
        verdict = decide(raw)
        if verdict.ok:
            entries, refusal = lookup(root, manager, list(verdict.ids))
        else:
            entries, refusal = [], verdict.reason
        if not refusal and board is None:
            refusal = (
                "this project's board could not be reached, so nothing can be "
                "tracked there. Nothing was created"
            )
        if refusal:
            said = f"chore not created: {refusal}."
        else:
            try:
                made = board.create(
                    _title_of(entries[0]["words"]),
                    _description_of(entries),
                    labels=list(LABELS),
                )
            except Exception as e:  # noqa: BLE001 - said, never raised
                made = BackendError(str(e))
            if isinstance(made, BackendError) or not getattr(made, "id", ""):
                why = made.message if isinstance(made, BackendError) else "no id"
                said = (
                    f"chore not created: the board refused it ({why}). "
                    "The work is not tracked, so do not route it or start a "
                    "Worker on it."
                )
            else:
                created += 1
                ids = ", ".join(e["id"] for e in entries)
                said = (
                    f"chore {made.id} created from message(s) {ids}, labelled "
                    f"{' and '.join(LABELS)}. Work it as ticket {made.id}."
                )
        say(f"{manager!r}: {said}")
        try:
            send(root, manager, INBOX, note(said))
        except OSError as e:
            # The claim stays, so the next supervisor reports it rather than
            # the Manager never hearing.
            say(f"{manager!r}: could not tell it the chore outcome: {e}")
            continue
        _done(claim)
    return created


def create_for_prompt(board, worker: str, text: str) -> tuple[str, str]:
    """A person's `rite sandbox start <worker> --prompt "…"`, as a chore.

    Returns `(ticket id, "")`, or `("", why not)`. Typed at this machine by
    the person, so the text is theirs, as a header-less message is
    (`delivered.classify`). Labelled `chore` and the Worker's own label, the
    way assignment labels a ticket (`coordination.distribution`), and NOT
    `scheduled`: the person has already given it to this Worker, and
    `scheduled` would put it in the queue for another one too.

    ⚠ **No board, or a refused create, refuses the start.** Nothing runs
    untracked (TRQ5); a Worker started on work with no ticket is the hole
    this closes.
    """
    from rite_ai.tickets.interface import BackendError

    if board is None:
        return "", (
            "this project has no board rite can reach, so the work cannot be "
            "tracked. Every piece of Worker work carries a ticket"
        )
    description = (
        text.strip()
        + "\n\n---\n\n"
        + "Chore created by rite from the prompt a person typed at this "
        f"machine: `rite sandbox start {worker} --prompt`. The text above is "
        "theirs, as typed.\n"
    )
    try:
        made = board.create(_title_of(text), description, labels=[CHORE_LABEL, worker])
    except Exception as e:  # noqa: BLE001 - said, never raised
        made = BackendError(str(e))
    if isinstance(made, BackendError) or not getattr(made, "id", ""):
        why = made.message if isinstance(made, BackendError) else "no id came back"
        return "", f"the board refused the chore ({why})"
    return str(made.id), ""


def instructions(root: Path, manager: str) -> str:
    """What a Manager is told about chores."""
    from rite_ai import own_command

    rite = own_command()
    return (
        "\n\n## Work that is not a ticket yet: a chore\n\n"
        "Every piece of work a Worker or another Manager does carries a "
        "ticket. When the User asks for work in a message and it is not "
        "already a ticket, have rite make it one: "
        f"`{rite} chore <message-id> [<message-id>…]`, naming the message(s) "
        "by the id shown beside each in your instruction. rite writes the "
        "ticket itself, from the User's own words; you cannot give it a "
        "title or text, and a chore can only be made from the User's "
        "messages (the Owner's DM, or sent from this machine). It is created "
        "when this session ends, and your next instruction says its id. Work "
        "you think of yourself is not a chore: file it as an ordinary ticket. "
        "A question, or anything that is not work, needs no chore.\n"
    )
