"""The refinement record: what it carries, how it is signed, how it is found.

**A board comment, never the description** (the note's part 3.2). `update()`
replaces a ticket's body wholesale on both backends with no compare-and-swap,
so a record written into the description while a person edits it loses one of
the two edits. Comments are append-only.

**rite renders both halves from one payload.** The human-readable part and the
fenced JSON are produced here together, so they cannot disagree, and only the
JSON is ever read back.

**The MAC is over the parsed payload, not the bytes.** Jira stores a comment as
ADF and flattens it on read (`adf_to_text`); whitespace may not survive the
round trip (unmeasured, TR0). A payload that parses back to the same object
verifies; one whose characters changed does not.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, replace

MARKER = "rite-refinement"
"""The fence's info string, and the one token that says "this comment claims to
be a record". A comment carrying it that cannot be read as one is not ignored
(`status.evaluate`): ignoring it could promote an older record to the head."""

VERSION = 1

ACCEPTED = "accepted"
"""The User accepted a proposal through their channel (TR2)."""
ATTESTED = "attested"
"""Run as the person, outside every boundary. Never "confirmed by a person":
rite cannot tell the person from a model running as them (the note, part 3.6)."""
PROVENANCE_KINDS = (ACCEPTED, ATTESTED)

ATTESTED_TOKEN = "rite-attested"
"""A fixed word in every attested record's comment, so every one can be found
later (TRQ10, decided): `"rite-attested" in:comments` on GitHub,
`comment ~ "rite-attested"` in JQL. Neither search is measured yet (TR0)."""

NONE_AGREED = "none agreed"
"""`verify` when no command was agreed (TRQ9, decided): explicit, never absent."""


def board_identity(board) -> dict | None:
    """Which board a record belongs to, from the backend itself: the Jira site
    or the GitHub repository. None for a backend rite cannot identify, and a
    record can then never be checked (UNREADABLE), never assumed to match.

    Taken from the backend rather than from `config.yaml`, so the identity a
    record is checked against is the board that was actually read. Narrower
    than `board_context.record_of`, which includes Jira's project mapping:
    adding a project must not invalidate every record on the site, while
    moving to another site or repository must. A Jira id carries its project.
    """
    from rite_ai.tickets.github import GitHubBackend
    from rite_ai.tickets.jira import JiraBackend, normalise_site
    from rite_ai.tickets.own_writes import ReadsItsOwnWrites

    # ⚠ Through the DF4 wrapper to the board it wraps. A project's board is
    # built wrapped whenever a root is given (`create_backend_from_config`),
    # and a wrapped board answering None would make every record on it
    # UNREADABLE: every gate refusing, for a reason no one could see.
    while isinstance(board, ReadsItsOwnWrites):
        board = board.inner
    if isinstance(board, JiraBackend):
        return {"type": "jira", "site": normalise_site(board.config.site).lower()}
    if isinstance(board, GitHubBackend):
        return {"type": "github", "repo": board.repo.strip().lower()}
    return None


def text_sha256(text: str) -> str:
    """The hash a record binds to: of the text exactly as the read path returns
    it. Normalising first would let two different texts share a record."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def mac_of(payload: dict, key: bytes) -> str:
    unsigned = {k: v for k, v in payload.items() if k != "mac"}
    return hmac.new(
        key, canonical(unsigned).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def record_id_of(unsigned_payload: dict) -> str:
    """Deterministic over EVERYTHING the record says (all but its own id and
    MAC), so a retried write of the same record after a lost response is the
    same record rather than a second head (the note's part 4, race 10), and
    two records that say different things never share an id.

    ⚠ An earlier version hashed only the ticket, its text hashes and the
    provenance. Two different definitions of done attested in the same second
    from the same host then shared an id, and the ticket read as UNREADABLE.
    A test that accepted twice found it.
    """
    basis = {k: v for k, v in unsigned_payload.items() if k not in ("record_id", "mac")}
    return hashlib.sha256(canonical(basis).encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Record:
    ticket: str
    board: dict
    """Which board the record was written for (`board_identity`). A record
    copied onto another board's ticket of the same id is not its record."""
    title_sha256: str
    description_sha256: str
    definition_of_done: tuple[str, ...]
    verify: tuple[str, ...] | str
    provenance: dict
    supersedes: str | None = None
    scope_in: tuple[str, ...] = ()
    scope_out: tuple[str, ...] = ()
    exchange: tuple[dict, ...] = ()
    record_id: str = ""
    mac: str = ""

    def payload(self) -> dict:
        body = {
            "kind": MARKER,
            "v": VERSION,
            "ticket": self.ticket,
            "board": self.board,
            "record_id": self.record_id,
            "supersedes": self.supersedes,
            "title_sha256": self.title_sha256,
            "description_sha256": self.description_sha256,
            "definition_of_done": list(self.definition_of_done),
            "verify": self.verify
            if isinstance(self.verify, str)
            else list(self.verify),
            "scope_in": list(self.scope_in),
            "scope_out": list(self.scope_out),
            "provenance": self.provenance,
            "exchange": list(self.exchange),
        }
        if self.mac:
            body["mac"] = self.mac
        return body


def build(
    *,
    ticket: str,
    board: dict,
    title: str,
    description: str,
    definition_of_done: list[str],
    verify: list[str] | str,
    provenance: dict,
    supersedes: str | None,
    key: bytes,
    scope_in: list[str] | None = None,
    scope_out: list[str] | None = None,
    exchange: list[dict] | None = None,
) -> Record:
    """A signed record for the ticket text given. Refuses, rather than
    repairs, a record that could not be valid: a definition of done must
    exist (P2), and the provenance must say which route produced it."""
    problem = schema_problem(
        {
            "definition_of_done": definition_of_done,
            "verify": verify,
            "provenance": provenance,
        }
    )
    if problem:
        raise ValueError(problem)
    title_hash = text_sha256(title)
    description_hash = text_sha256(description)
    unsigned = Record(
        ticket=ticket,
        board=board,
        title_sha256=title_hash,
        description_sha256=description_hash,
        definition_of_done=tuple(definition_of_done),
        verify=verify if isinstance(verify, str) else tuple(verify),
        provenance=provenance,
        supersedes=supersedes,
        scope_in=tuple(scope_in or ()),
        scope_out=tuple(scope_out or ()),
        exchange=tuple(exchange or ()),
    )
    unsigned = replace(unsigned, record_id=record_id_of(unsigned.payload()))
    return replace(unsigned, mac=mac_of(unsigned.payload(), key))


def schema_problem(payload: dict) -> str:
    """Why this payload cannot be a record, or "" when its shape is sound."""
    dod = payload.get("definition_of_done")
    if not isinstance(dod, list) or not dod:
        return "it has no definition of done"
    if not all(isinstance(item, str) and item.strip() for item in dod):
        return "a definition-of-done item is empty or not text"
    verify = payload.get("verify")
    if verify != NONE_AGREED and not (
        isinstance(verify, list)
        and verify
        and all(isinstance(c, str) and c.strip() for c in verify)
    ):
        return f'its verify is neither commands nor "{NONE_AGREED}"'
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("kind") not in (
        PROVENANCE_KINDS
    ):
        return "its provenance is not one of " + ", ".join(PROVENANCE_KINDS)
    return ""


def render_for_worker(record: Record) -> str:
    """The agreed definition of done as a Worker's start prompt carries it.

    Deterministic: the same record always gives the same text, so what a
    Worker was started against can be reproduced from the record id alone.
    Items are normalised as ticket text is (SPEC §6.6.1): they came from a
    board, and a board is not vetted (§6.6.3).
    """
    from rite_ai.normalise import normalise

    def clean(text: str) -> str:
        return normalise(text).text

    lines = [
        f"Agreed definition of done for {record.ticket} "
        f"(refinement record {record.record_id}):",
        *[f"- [ ] {clean(item)}" for item in record.definition_of_done],
    ]
    if record.scope_in:
        lines += ["In scope:", *[f"- {clean(item)}" for item in record.scope_in]]
    if record.scope_out:
        lines += ["Out of scope:", *[f"- {clean(item)}" for item in record.scope_out]]
    if isinstance(record.verify, str):
        lines.append(
            f"Verify: {record.verify}. Say in your report how you checked each item."
        )
    else:
        lines += ["Verify with:", *[f"    {clean(c)}" for c in record.verify]]
    how = (
        "accepted by the User"
        if record.provenance.get("kind") == ACCEPTED
        else "attested by a session running as the person, not confirmed to be a person"
    )
    lines.append(f"This definition of done was {how}. Work to it, not to the title.")
    return "\n".join(lines)


def render(record: Record) -> str:
    """The comment body: what a person reads, then the block rite reads."""
    lines = [
        f"**rite: agreed definition of done for {record.ticket}**",
        "",
        *[f"- [ ] {item}" for item in record.definition_of_done],
        "",
    ]
    if isinstance(record.verify, str):
        lines.append(f"Verify: {record.verify}.")
    else:
        lines.append("Verify:")
        lines.extend(f"    {command}" for command in record.verify)
    kind = record.provenance.get("kind", "")
    how = (
        "accepted by the User in their channel"
        if kind == ACCEPTED
        else f"{ATTESTED_TOKEN}: attested by a session running as the person, "
        "outside any sandbox; not confirmed through the User's channel"
    )
    lines += [
        "",
        f"Provenance: {how}.",
        "",
        "Written by rite. Editing this comment makes it unreadable to rite, and "
        "deleting it leaves the ticket without an agreed definition of done.",
        "",
        f"```{MARKER}",
        json.dumps(record.payload(), sort_keys=True, ensure_ascii=False, indent=1),
        "```",
    ]
    return "\n".join(lines)


def carries_marker(text: str) -> bool:
    return f"```{MARKER}" in text or f'"kind":"{MARKER}"' in text.replace(" ", "")


def extract(text: str) -> dict | None:
    """The payload in a comment, or None when none can be read.

    Tolerant of what a board may do to the fence and its whitespace: it finds
    the marker, then decodes the first JSON object after it. It does not
    tolerate changed characters; that is what the MAC is for.
    """
    at = text.find(MARKER)
    if at < 0:
        return None
    start = text.find("{", at)
    while start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict) and obj.get("kind") == MARKER:
            return obj
        start = text.find("{", start + 1)
    return None


def from_payload(payload: dict) -> Record:
    verify = payload.get("verify")
    return Record(
        ticket=str(payload.get("ticket", "")),
        board=payload.get("board") or {},
        title_sha256=str(payload.get("title_sha256", "")),
        description_sha256=str(payload.get("description_sha256", "")),
        definition_of_done=tuple(payload.get("definition_of_done") or ()),
        verify=verify if isinstance(verify, str) else tuple(verify or ()),
        provenance=payload.get("provenance") or {},
        supersedes=payload.get("supersedes"),
        scope_in=tuple(payload.get("scope_in") or ()),
        scope_out=tuple(payload.get("scope_out") or ()),
        exchange=tuple(payload.get("exchange") or ()),
        record_id=str(payload.get("record_id", "")),
        mac=str(payload.get("mac", "")),
    )


def mac_verifies(payload: dict, key: bytes) -> bool:
    claimed = payload.get("mac")
    if not isinstance(claimed, str) or not claimed:
        return False
    return hmac.compare_digest(claimed, mac_of(payload, key))
