"""Who answered a refinement round: one attribution model (S22b).

A round can be answered in Slack (the Owner's DM, or the refinement channel
when the relay marked it the Owner's) or at this machine's terminal (`rite
refine answer`, or `rite message` with no rite header). **Every one of them is
attributed to the same identity, the project's owner (`slack.owner_user`), and
recorded in the same shape**, by `answered_by`, in the round ledger's answers,
in a pending accept, and in the signed record's provenance. There is no second
field a reader has to reconcile to learn who answered.

**The channel is kept, and it is not decoration.** A Slack answer is one the
relay saw come from the owner's own Slack account. A terminal answer is one
typed on the host, where rite cannot tell the person from a session running as
them (the note's part 3.6; TRQ10 allowed attesting there on exactly that
understanding). Recording both as the owner, with `via` saying which, is the
honest form of "the same identity": dropping `via` would make the weaker
channel read as the stronger one, which is the gap this exists to close.

**No owner configured** (`slack.owner_user` empty) is recorded as empty, and
said where it is shown. Slack cannot reach the owner then, so a terminal
answer is the only way a round is answered; rite does not refuse it, and does
not invent an identity for it.
"""

from __future__ import annotations

SLACK = "slack"
TERMINAL = "terminal"
VIAS = (SLACK, TERMINAL)


def answered_by(owner_user: str, via: str) -> dict:
    """The one attribution every answer carries: `{"owner_user", "via"}`."""
    if via not in VIAS:
        raise ValueError(f"an answer arrives via one of {VIAS}, not {via!r}")
    return {"owner_user": (owner_user or "").strip(), "via": via}


def via_of(where: str) -> str:
    """The channel of a delivered message, from `delivered.Heard.where`: the
    Owner's DM and the refinement channel are Slack; a message with no rite
    header was written on this machine."""
    from rite_ai.managers import delivered

    return TERMINAL if where == delivered.THIS_MACHINE else SLACK


def owner_user_of(root) -> str:
    """`slack.owner_user` from a project's config, or "" (unset or unreadable).
    The one place an answer's owner is read from."""
    from pathlib import Path

    from rite_ai.config.parse import ParseError, parse_config

    config = parse_config(Path(root) / ".rite" / "config.yaml")
    return "" if isinstance(config, ParseError) else config.slack.owner_user


def describe(by: dict | None) -> str:
    """How an attribution reads to a person. Unknown (a record written before
    S22b) says so rather than guessing."""
    if not isinstance(by, dict) or by.get("via") not in VIAS:
        return "the User in their channel"
    owner = by.get("owner_user") or ""
    who = (
        f"the owner (Slack user {owner})"
        if owner
        else "the owner (no slack.owner_user set)"
    )
    if by["via"] == SLACK:
        return f"{who} in Slack"
    return (
        f"{who} at this machine's terminal; rite cannot tell the person from a "
        "session running as them"
    )
