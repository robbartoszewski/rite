"""Whether a Cursor turn continued the chat rite meant (CU3).

⚠ **CURSOR CANNOT TELL US, SO RITE CHECKS.** `agent -p --resume <uuid>`
continues a chat it knows and CREATES one it does not, exiting 0 and
reporting success either way (measured, `spikes/CU1-cursor-cli.md` section
3). A continuation that silently started empty looks, from outside, exactly
like one that worked: `_default_resume_id`'s documented defect, arriving by a
third road.

**What rite checks is the chat's creation time.** Cursor writes
`$CURSOR_CONFIG_DIR/chats/<hash of the workspace>/<uuid>/meta.json`, holding
`createdAtMs` (measured, CU1b). rite records it after the first turn. Before
each continuation the chat must exist with that time; after each
continuation it must still have it.

⚠ **Why the check AFTER the turn exists, under the rule that no race is
tolerated.** A chat removed between the check before and Cursor opening it
would be recreated empty, because the create-if-absent is Cursor's and rite
cannot close that window. The check after makes it fail closed rather than
pass: a recreated chat carries a new `createdAtMs`, the cycle is reported as
one that did NOT continue, and no further cycle runs until the operator
starts fresh. The check before is only the early, cheap refusal.

**The chat is found by its UUID, not by recomputing the workspace hash.**
The hash is md5 of the path Cursor saw as its working directory, and whether
that is the resolved path (`/private/tmp` for `/tmp`) is not measured. A
UUID rite generated names one chat, so the lookup is `chats/*/<uuid>`, and
more than one match is refused rather than guessed between.

Pure: nothing here launches anything or writes rite's records. The
supervisor calls it and acts on the verdict.

⚠ **The residual, stated rather than hidden.** Identity is `createdAtMs`. A
chat deleted and recreated passes only if the new one was created in the same
millisecond as the old, which needs the wall clock to return to that instant.
Nothing here detects that; it is the whole of what this check leaves open.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONTINUE = "continue"
"""Before a turn: the recorded chat is there, as recorded. Launch it."""

FIRST_TURN = "first-turn"
"""Before a turn: no chat was ever confirmed for this handle, so launching
it is a first turn again, with the SAME UUID. Idempotent: nothing was ever
promised to continue."""

REFUSE = "refuse"
"""Before a turn: the recorded chat is missing or is not the one recorded.
Launching would run an empty chat reported as success."""

CONFIRMED = "confirmed"
"""After a first turn: the chat exists; record its creation time."""

NO_CHAT = "no-chat"
"""After a first turn: no chat appeared (the turn failed before Cursor made
one: a bad key, an unreachable service). Nothing is broken; the next launch
is a first turn again with the same UUID."""

CONTINUED = "continued"
"""After a continuation: the same chat, by creation time."""

REPLACED = "replaced"
"""After a continuation: the chat is gone or is a different one. The turn
ran without its history, and the designation is broken from here on."""


@dataclass(frozen=True)
class Verdict:
    outcome: str
    reason: str = ""
    created_at_ms: int | None = None
    """The chat's `createdAtMs` as observed, for the caller to record on
    `CONFIRMED`."""


@dataclass(frozen=True)
class Observed:
    """What is on disk for one handle, or why it cannot be said."""

    created_at_ms: int | None
    where: str
    """The path looked at, or the pattern searched, for the refusal to name."""
    problem: str = ""
    """Non-empty when the chat's presence could not be established cleanly:
    several matches, or a `meta.json` that exists and does not parse. Never
    treated as absence, which would read as "first turn" and relaunch."""


def observe(config_dir: Path, handle: str) -> Observed:
    """Find the chat named `handle` under a `CURSOR_CONFIG_DIR`."""
    pattern = f"{config_dir}/chats/*/{handle}"
    matches = sorted(p for p in (config_dir / "chats").glob(f"*/{handle}"))
    if not matches:
        return Observed(None, pattern)
    if len(matches) > 1:
        return Observed(
            None,
            pattern,
            f"{len(matches)} chats carry the id {handle} "
            f"({', '.join(str(m) for m in matches)}); refusing to guess",
        )
    meta = matches[0] / "meta.json"
    try:
        raw = json.loads(meta.read_text())
    except OSError:
        # A chat directory with no readable meta.json: Cursor is mid-write,
        # or the layout changed. Neither is "no chat".
        return Observed(None, str(meta), f"{meta} could not be read")
    except ValueError:
        return Observed(None, str(meta), f"{meta} is not JSON")
    created = raw.get("createdAtMs") if isinstance(raw, dict) else None
    if not isinstance(created, int) or isinstance(created, bool):
        return Observed(None, str(meta), f"{meta} has no integer createdAtMs")
    return Observed(created, str(meta))


def before_turn(recorded_ms: int | None, seen: Observed) -> Verdict:
    """May this handle be launched, and as what?

    `recorded_ms` is the creation time rite recorded after the first turn,
    or None when no first turn was ever confirmed.
    """
    if seen.problem:
        return Verdict(REFUSE, seen.problem)
    if recorded_ms is None:
        if seen.created_at_ms is None:
            return Verdict(FIRST_TURN)
        # A chat exists that rite never confirmed: the first turn ran and
        # rite stopped before recording it. Adopt it: it is this handle's,
        # since rite generated the UUID.
        return Verdict(CONTINUE, created_at_ms=seen.created_at_ms)
    if seen.created_at_ms is None:
        return Verdict(
            REFUSE,
            f"the chat this Manager continues is gone: nothing at {seen.where}. "
            "Cursor would start an empty chat and report success. Start fresh "
            "deliberately (--fresh) if that is what you want",
        )
    if seen.created_at_ms != recorded_ms:
        return Verdict(
            REFUSE,
            f"the chat at {seen.where} was created at {seen.created_at_ms}, "
            f"not {recorded_ms} as recorded: it is not the conversation this "
            "Manager was having",
        )
    return Verdict(CONTINUE, created_at_ms=recorded_ms)


def after_turn(expected_ms: int | None, seen: Observed) -> Verdict:
    """Did the turn that just ran continue the chat it was meant to?

    `expected_ms` is what `before_turn` found: None for a first turn,
    otherwise the creation time the turn had to keep.
    """
    if seen.problem:
        return Verdict(REPLACED, seen.problem)
    if expected_ms is None:
        if seen.created_at_ms is None:
            return Verdict(NO_CHAT, f"the first turn left no chat at {seen.where}")
        return Verdict(CONFIRMED, created_at_ms=seen.created_at_ms)
    if seen.created_at_ms == expected_ms:
        return Verdict(CONTINUED, created_at_ms=expected_ms)
    return Verdict(
        REPLACED,
        f"the chat at {seen.where} is not the one this turn was meant to "
        f"continue (created {seen.created_at_ms}, expected {expected_ms}): "
        "it ran WITHOUT its history, though Cursor reported success",
    )
