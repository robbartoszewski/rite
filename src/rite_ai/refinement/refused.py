"""The words rite uses when it refuses work on a ticket that is not refined.

**One place, because three paths refuse for the same reason** (TR4 and TR5):
starting a Worker (`rite sandbox start`), routing to another Manager (`rite
route --ticket`), and the Owner's assignment to Managers. Worded once, they
cannot drift into three different accounts of the same state.

⚠ **A refusal names what to do, not only the state.** A refusal that says
"NOT REFINED" and nothing else is how a person learns to look for a way
round it. And UNREADABLE is said as "rite could not check", never as "no
definition of done": an unreachable board is not an empty one.

⚠ **At most 200 characters for any ticket id rite accepts** (64). A
Manager's Worker request reaches its next instruction as the last line of
`rite sandbox start`'s stderr, cut to 200 characters (`broker.honour`), so
this line is what survives.
"""

from __future__ import annotations

from rite_ai.refinement import status as st


def remedy_line(state: str, ticket: str) -> str:
    """The state, and what a person does about it, in at most 200 characters."""
    if state == st.NOT_REFINED:
        return (
            "NOT REFINED: this ticket has no agreed definition of done. A person "
            f'agrees one on the host: `rite refine accept {ticket} --item "…"`.'
        )
    if state == st.STALE:
        return (
            "STALE: the ticket changed after its definition of done was agreed. "
            "A person re-agrees it on the host: "
            f'`rite refine accept {ticket} --item "…"`.'
        )
    if state == st.CONFLICT:
        return (
            "CONFLICT: two records each claim to be current. A person decides "
            f"which stands; `rite refine status {ticket}` on the host names them."
        )
    return (
        f"{state}: rite could not confirm a definition of done; nothing was "
        f"assumed. `rite refine status {ticket}` on the host says why."
    )
