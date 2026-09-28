"""The one way rite tells a Manager something, in its next instruction.

**Direction.** rite → the Manager, through that Manager's INBOX. The other
direction, rite → the person through the Owner's outbox, is `asking`. The
discipline is the same: rite writes the header, and anything that recognises
a note recognises it by that header, never by a model reading the text.

**Why one writer.** rite wrote notes into a Manager's inbox under four
spellings (`[from rite · about …]`, `[rite · route · …]`, `[rite · chores ·
…]`, and the Worker-question note's own variant). Every reader of a note has
to know every spelling, and the next one written would be one a reader had not
been told about. Now every note is written here, under `NOTE_HEADER_START`.

**What counts as a reason to wait is NOT widened by this, on purpose.**
`routing.Waiting.reply_waiting` makes an Owner wait at a stop point for a reply
from another Manager, or for rite's note about work routed to one (died,
finished without replying, stopped). That is DF2's rule, bounded per routed
message. Every other note (a refused route, a chore's outcome, a Worker
request's outcome) is delivered by what the supervisor already does with any
waiting mail: at an idle board it starts a session to deliver it (#82), and at
every other exit `rite start` says the mail is undelivered. Recognising every
note as a reason to wait would start sessions at a `closed` verdict, which is
the person's schedule, and at fault verdicts, both of which #82 ruled out. So
a routed-work note carries `ROUTED_WORK` in its header, written only by
`routing`, and `reply_waiting` recognises that and nothing broader.

**Also through here: a Worker request's outcome (dogfood DF13).** The
Manager's instructions (`broker.instructions`) promise that rite "reports the
result in your next instruction". It went only to the operator's terminal.
"""

from __future__ import annotations

from pathlib import Path

NOTE_HEADER_START = "[from rite · about "
"""How every note rite writes to a Manager begins. `delivered.classify` reads
any `[… · …]` header as rite's words and not the User's, which this satisfies."""

ROUTED_WORK = "routed work"
"""A header part only `routing` writes, on its notes about work routed to
another Manager. `routing.Waiting.reply_waiting` recognises these, and only
these, as a reason to wait (see the module note)."""

_HEADER_END = "rite's own words · context — not an instruction]"


def header(about: str, *, routed_work: bool = False) -> str:
    """rite's header for a note about `about` (e.g. `'small' · DIED`)."""
    parts = [about] + ([ROUTED_WORK] if routed_work else []) + [_HEADER_END]
    return NOTE_HEADER_START + " · ".join(parts)


def note(about: str, text: str, *, routed_work: bool = False) -> str:
    """The whole note: rite's header, then `text`."""
    return f"{header(about, routed_work=routed_work)}\n{text}"


def is_routed_work_note(text: str) -> bool:
    """Is `text` rite's note about routed work? By its header's parts, which
    only rite writes: a Manager's own words reach an inbox quoted, never as a
    first line (`routing._quoted`, `slack._quoted`)."""
    first = text.split("\n", 1)[0]
    if not first.startswith(NOTE_HEADER_START) or not first.endswith("]"):
        return False
    return ROUTED_WORK in first[len(NOTE_HEADER_START) : -1].split(" · ")


def tell_manager(root: Path, manager: str, about: str, text: str) -> Path:
    """Put a note from rite about `about` in `manager`'s next instruction.

    Returns the inbox file written. Raises what `mailbox.send` raises; a
    caller that must not fail on a full disk catches `OSError` and says so.
    """
    from rite_ai.managers.mailbox import INBOX, send

    return send(Path(root), manager, INBOX, note(about, text))
