"""The message log's commit convention (P2-0d).

SPEC §3.3.2 puts decisions, blockers, handovers and promotion events on `main`
as "ordinary commits", with examples but no convention — so nothing could read
them back. This is the proposal.

**The subject stays human; the meaning goes in git trailers.** A subject reads
like §3.3.2's examples ("manager-alpha promoted to Owner (manager-beta lease
expired)"). What a machine needs is carried as trailers in the final paragraph:

    Rite-Event: promotion
    Rite-Manager: manager-alpha
    Rite-Previous-Owner: manager-beta
    Rite-Reason: lease-expired

Three reasons for trailers over a parseable subject:

- A person rewording or shortening the subject cannot break parsing.
- They are git's own structured-metadata format: `git interpret-trailers
  --parse` and `git log --format='%(trailers)'` read them without rite.
- `main` also carries ordinary human commits. `Rite-Event` is the marker; a
  commit without it is not a rite message and parses as `None`.

**Values are collapsed to one line before writing.** A trailer block is the
final paragraph, so a subject, body or value containing a blank line followed
by `Rite-Event: ...` could otherwise plant a forged event. Only the final
paragraph is ever read, and nothing written can add a paragraph after it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# §3.3.3's "message" rows. A kind from a newer rite is preserved, not refused:
# the log is read by every version, and an older one must not drop an event
# it does not understand.
KNOWN_KINDS = ("decision", "blocker", "handover", "promotion", "claim-contention")

EVENT_TRAILER = "Rite-Event"
_PREFIX = "Rite-"
_TRAILER_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*):\s?(.*)$")
_FIELD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


@dataclass
class LogMessage:
    kind: str
    subject: str
    body: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    """Trailer name without the `Rite-` prefix → value, e.g.
    `{"Manager": "manager-alpha"}` for `Rite-Manager: manager-alpha`."""


def format_message(message: LogMessage) -> str:
    """The full commit message: subject, optional body, trailer block."""
    kind = _one_line(message.kind)
    if not kind:
        raise ValueError("a log message needs a kind")
    subject = _one_line(message.subject)
    if not subject:
        raise ValueError("a log message needs a subject")
    trailers = [f"{EVENT_TRAILER}: {kind}"]
    for name, value in message.fields.items():
        if not _FIELD_NAME.match(name):
            raise ValueError(f"trailer name {name!r} is not a single token")
        if name.lower() == "event":
            raise ValueError("`Event` is set from `kind`, not from `fields`")
        trailers.append(f"{_PREFIX}{name}: {_one_line(value)}")
    parts = [subject]
    body = message.body.strip()
    if body:
        # Paragraphs survive; a body can never be the final paragraph, so no
        # line in it is ever read as a trailer.
        parts.append(body)
    parts.append("\n".join(trailers))
    return "\n\n".join(parts) + "\n"


def parse_message(text: str) -> LogMessage | None:
    """A rite log message, or None for any commit that is not one.

    Reads trailers only from the final paragraph, and only when every line in
    it is a trailer — the same shape git requires — so trailer-looking lines
    anywhere else in a message are never read."""
    paragraphs = [
        p.strip("\n") for p in re.split(r"\n\s*\n", text.strip()) if p.strip()
    ]
    if len(paragraphs) < 2:
        return None
    block = paragraphs[-1].splitlines()
    parsed: list[tuple[str, str]] = []
    for line in block:
        m = _TRAILER_LINE.match(line.strip())
        if not m:
            return None
        parsed.append((m.group(1), m.group(2).strip()))
    kinds = [v for k, v in parsed if k == EVENT_TRAILER]
    if len(kinds) != 1 or not kinds[0]:
        return None
    fields = {
        k[len(_PREFIX) :]: v
        for k, v in parsed
        if k.startswith(_PREFIX) and k != EVENT_TRAILER
    }
    return LogMessage(
        kind=kinds[0],
        subject=paragraphs[0].splitlines()[0].strip(),
        body="\n\n".join(paragraphs[1:-1]),
        fields=fields,
    )


def promotion_event(new_owner: str, previous_owner: str, reason: str) -> LogMessage:
    """§2.4.2 step 4's promotion event.

    `reason` is one of `lease-expired`, `lease-not-credible` (D-59 — somebody's
    clock is wrong, and this is where that gets said), `no-owner` (first
    election) or `handed-over` (graceful demotion)."""
    because = {
        "lease-expired": f"{previous_owner} lease expired",
        "lease-not-credible": f"{previous_owner} lease not credible",
        "no-owner": "no current Owner",
        "handed-over": f"handed over by {previous_owner}",
    }.get(reason, reason)
    fields = {"Manager": new_owner, "Reason": reason}
    if previous_owner:
        fields["Previous-Owner"] = previous_owner
    return LogMessage(
        kind="promotion",
        subject=f"{new_owner} promoted to Owner ({because})",
        fields=fields,
    )
