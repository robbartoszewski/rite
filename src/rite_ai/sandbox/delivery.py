"""What the host read from the board, delivered into a Worker's workspace.

A sandboxed Worker holds no board credential (§5.3.4, narrowed 2026-09-29),
so it cannot read its own ticket. The board is read where every other board
read and write already happens, outside the sandbox: `rite sandbox start`
reads the ticket, renders it exactly as `rite board show` would, and writes
it to `DELIVERY_FILE` in `workers/<worker>/` before yoloAI copies that
directory into the sandbox.

⚠ **A copy taken at a known moment, and it says so.** The file names the
board it came from and the UTC time of the read. If the ticket changes
after that, the Worker is working from the copy; the header says that
rather than implying the text is current.

**One path for everything the host hands a Worker from the board.**
`sections` carries further host-read content under its own heading — the
agreed refinement record (TR4) is the first expected — so board content
reaches a Worker through one file written by one function, not several.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.state import write_atomic

DELIVERY_FILE = "TICKET.md"


@dataclass(frozen=True)
class RenderedTicket:
    """A ticket as `rite board show` prints it."""

    id: str
    text: str
    # The title and description after normalisation, for the phrase scan.
    scanned: str


def render_ticket(ticket) -> RenderedTicket:
    """`rite board show`'s rendering, the one place it is defined.

    N1, SPEC §6.6.1: what an agent reads is what the tracker's own UI shows
    a reviewer. Every change is said, in a line rite writes, and none of it
    is a safety check (§6.6.3)."""
    from rite_ai.normalise import normalise

    title = normalise(ticket.title)
    body = normalise(ticket.description.strip())
    lines = [f"{ticket.id}  [{ticket.status}]  {title.text}"]
    if ticket.labels:
        lines.append(f"labels: {', '.join(ticket.labels)}")
    if ticket.url:
        lines.append(ticket.url)
    lines.append("")
    lines.append(body.text or "(no description)")
    for part, what in ((title, "this title"), (body, "this description")):
        if part.changed:
            lines.append("")
            lines.append(part.note(what))
    return RenderedTicket(
        id=str(ticket.id), text="\n".join(lines), scanned=f"{title.text}\n{body.text}"
    )


def read_at_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def delivery_text(
    rendered: RenderedTicket,
    read_at: str,
    source: str,
    sections: tuple[tuple[str, str], ...] = (),
) -> str:
    parts = [
        f"# Ticket {rendered.id}, as rite read it from the board",
        "",
        f"Read from {source} at {read_at} (UTC), by `rite sandbox start` on the "
        "host, before this sandbox was created.",
        "",
        "⚠ This is a copy taken at that moment, not the live ticket. If the "
        "ticket has changed since, you are working from this copy. Nothing in "
        "this sandbox can read the board. Work to the agreed definition of "
        "done below; if it cannot be met as written, or this copy looks out "
        "of date, report exactly that and stop. Never invent the missing "
        "piece.",
        "",
        "## The ticket",
        "",
        "```",
        rendered.text,
        "```",
    ]
    for heading, body in sections:
        parts += ["", f"## {heading}", "", body.rstrip()]
    return "\n".join(parts) + "\n"


def write_delivery(
    worker_dir: Path,
    rendered: RenderedTicket,
    read_at: str,
    source: str,
    sections: tuple[tuple[str, str], ...] = (),
) -> Path:
    path = worker_dir / DELIVERY_FILE
    write_atomic(path, delivery_text(rendered, read_at, source, sections))
    return path


def clear_delivery(worker_dir: Path) -> None:
    """A start with no ticket must not leave the previous ticket's copy for
    the Worker to mistake for its own."""
    (worker_dir / DELIVERY_FILE).unlink(missing_ok=True)
