"""What a Manager session is told when it starts, and getting it there.

**A Manager session that starts with an empty prompt waits for a human to
type something** — which is the behaviour `rite start` exists to remove, and
the gap `/rite-start` was added to paper over for the Claude app (§9.14.11a,
D-90).

⚠ **Delivery is CONFIRMED, not assumed, and that is not defensive
programming — it is measured.** A `send-keys` can be swallowed by a shell
that is not yet reading: the capability probe in `session.py` treats a lost
`exit 3` as "no answer" rather than as evidence about the machine for
exactly this reason, and it reached that shape by being wrong about it
first. A prompt that vanishes leaves a Manager sitting idle, spending
nothing, while the supervisor waits for it to finish — a failure that looks
like a slow session rather than like a missing instruction.

**Composition is separate from delivery**, and the extra text arrives as an
argument rather than being imported here. The journal instructions (§9.15.6,
D-93) are composed by `managers/journal.py`, which owns their wording; this
module does not know what they say. Two modules writing a version of the
same text is the drift shape, and one of them importing the other at the
wrong layer is the coupling shape.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from rite_ai.managers.session import _tmux, session_exists

SETTLE = 0.3
CONFIRM_TRIES = 20
CONFIRM_PAUSE = 0.2


def for_manager(manager: str, *, extra: str = "") -> str:
    """The text a Manager is given at start.

    `extra` is appended verbatim and is empty when there is nothing to add,
    so the caller concatenates unconditionally rather than branching — the
    same contract `journal.instructions` and `journal.start_notice` use for
    their disabled case.
    """
    base = (
        f"You are the Manager '{manager}' for this project, started by "
        f"`rite start {manager}`.\n"
        "Read `.rite/` to orient yourself, then work the queue: "
        "`rite loop status` says what is ready and what is stuck.\n"
        "You are running in a human's foreground terminal — they can attach "
        "to this session and read along."
    )
    return base + extra


@dataclass(frozen=True)
class Delivery:
    """⚠ `ok` False is not an error to raise — it is a fact to report.

    A Manager whose prompt did not arrive is still a running session the
    human is paying for, and killing it to signal a delivery failure would
    destroy work to report a problem. The caller says so and carries on.
    """

    ok: bool
    detail: str = ""


def deliver(name: str, text: str) -> Delivery:
    """Type `text` into the session and confirm it arrived.

    ⚠ **Confirmed by reading the pane back**, because `send-keys` reports
    whether tmux ACCEPTED the keystrokes, not whether the program read
    them — the same distinction as `tmux new-session` reporting that a
    session was created rather than that its command survived, which
    `settled_alive` exists for.
    """
    if not text.strip():
        return Delivery(True, "nothing to send")
    binary = _tmux()
    if binary is None:
        return Delivery(False, "tmux not found")
    # Exactly this session. `-t` prefix-matches, so without the check a
    # prompt for `lead` can be typed into `leader`.
    if not session_exists(name):
        return Delivery(False, f"no session named {name}")

    # A settle before typing, for the same reason the capability probe
    # waits: a shell that is not yet reading drops what it is sent, and the
    # drop is silent.
    time.sleep(SETTLE)
    first = text.strip().splitlines()[0][:40]
    try:
        for line in text.splitlines():
            subprocess.run(
                [binary, "send-keys", "-t", name, line, "Enter"],
                capture_output=True,
                timeout=30,
                check=False,
            )
    except (OSError, subprocess.SubprocessError) as e:
        return Delivery(False, f"could not send the prompt: {e}")

    for _ in range(CONFIRM_TRIES):
        time.sleep(CONFIRM_PAUSE)
        try:
            seen = subprocess.run(
                [binary, "capture-pane", "-p", "-t", name],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            break
        if seen.returncode == 0 and first and first in (seen.stdout or ""):
            return Delivery(True, "prompt is on screen")
    return Delivery(
        False,
        "the prompt was sent but did not appear in the pane, so the session "
        "may be waiting for input it never received",
    )
