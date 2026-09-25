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

from rite_ai import own_command
from rite_ai.managers.broker import REQUESTS_DIRNAME
from rite_ai.managers.session import _tmux, session_exists

SETTLE = 0.3
CONFIRM_TRIES = 20
CONFIRM_PAUSE = 0.2


def for_manager(manager: str, *, extra: str = "") -> str:
    """The text a Manager is given at start.

    ⚠ **The Worker instructions changed shape in B9**: a Manager no longer
    runs `rite sandbox start`, it writes a request. It runs inside a sandbox
    now, and a sandbox cannot create another one — so the old instruction
    would fail every time, and the Manager would have no way to know the
    command was not the problem.

    `extra` is appended verbatim and is empty when there is nothing to add,
    so the caller concatenates unconditionally rather than branching — the
    same contract `journal.instructions` and `journal.start_notice` use for
    their disabled case.
    """
    # Relative to the project root, which is the pane's working directory —
    # this function has the Manager's name and nothing else, and the request
    # directory is a function of exactly that.
    requests = f".rite/managers/{manager}/{REQUESTS_DIRNAME}"
    # ⚠ **Absolute, for the reason `mailbox.how_to_reply` gives**: a bare
    # `rite` names whatever is first on the Manager's PATH, which was
    # measured to be an older release than the one writing this text.
    rite = own_command()
    base = (
        f"You are the Manager '{manager}' for this project, started by "
        f"`rite start {manager}`.\n"
        "Read `.rite/` to orient yourself, then work the queue: "
        f"`{rite} loop run` prints one planned cycle — what is ready, which "
        "Workers are free, and what is blocking. (`{rite} loop status` "
        "answers a different question: whether a loop process is "
        "running.)\n"
        "You are running in a human's foreground terminal — they can attach "
        "to this session and read along.\n"
        "\n"
        "## Starting Workers — you ASK, rite starts it\n"
        "\n"
        "⚠ You are running inside a sandbox, and a sandbox cannot create "
        "another one. `rite sandbox start` WILL FAIL if you run it — not "
        "because you got the command wrong, but because the operating "
        "system refuses a second sandbox from inside the first. So you ask "
        "instead, by writing one small file:\n"
        "\n"
        "    mkdir -p " + requests + "\n"
        '    echo \'{"worker":"<name>","ticket":"<ID>"}\' > '
        + requests
        + "/$(date +%s).json\n"
        "\n"
        "rite picks it up when this session ends, starts the Worker "
        "outside the sandbox, and tells you what happened in your next "
        "instruction. So do not wait for the Worker to appear during this "
        "session — it will not, and that is not a failure.\n"
        "\n"
        "Those two fields are the only ones a request may carry. Anything "
        "else in the file — an environment, a prompt, a directory — is "
        "REFUSED, because rite chooses the rest of a Worker's launch and "
        "will not take it from a request.\n"
        "\n"
        f"`{rite} add worker <name>` first if the Worker does not exist yet; "
        f"`{rite} status` lists the ones that do. A request naming a Worker "
        "that does not exist, or a ticket that is not on the board, is "
        "refused and reported.\n"
        "\n"
        "⚠ NEVER start a Worker by running `claude`, `goose` or any engine "
        "yourself, however convenient it looks. The request path is what "
        "delivers this project's credentials to the Worker; an engine you "
        "launch directly gets none and the Worker fails on launch with an "
        "authentication error, every time. If a request is refused, report "
        "the refusal and stop — do not work around it.\n"
    )
    return base + extra


@dataclass(frozen=True)
class Delivery:
    """Whether the prompt REACHED THE TERMINAL. Not whether it was read.

    ⚠ **The field is named for what it can see, because the earlier name
    lied.** It was `ok`, and callers read that as "the Manager got its
    prompt". It does not mean that. A tty echoes keystrokes whether or not
    the foreground process ever calls `read()`, and this confirms delivery
    by reading the pane back — so a session running `sleep 300`, which
    never touches stdin, returns True:

        deliver(name, "...") -> reached_terminal=True, 'the prompt is on
                                the terminal'

    **A process that never reads stdin is indistinguishable from one that
    did.** That is precisely the case §9.14.11a warns about — "a `send-keys`
    can be swallowed by a shell that is not yet reading" — so the guarantee
    is weaker than the warning it was written to answer.

    ⚠ **Do not spend an afternoon looking for a proxy; there isn't one.**
    Checked and rejected:

    * `#{pane_in_mode}` is about tmux's OWN copy mode, not the pane's
      process.
    * `#{pane_pid}` and the foreground pgid say WHAT is running, never
      whether it is blocked in `read()`.
    * `/proc/<pid>/wchan` or `syscall` would answer it on Linux and does
      not exist on macOS, which is where this runs.

    So the honest position is a narrow claim rather than a strong-sounding
    one: **this proves the characters arrived at the terminal.** What the
    program did with them is not observable from outside it.

    `reached_terminal` False with nothing sent (an empty prompt) is still
    True, because nothing was owed.
    """

    reached_terminal: bool
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
            return Delivery(
                True,
                "the prompt is on the terminal; whether the process read it "
                "is not observable",
            )
    return Delivery(
        False,
        "the prompt was sent but never appeared on the terminal, so the "
        "session did not even receive the characters",
    )
