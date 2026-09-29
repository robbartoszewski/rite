"""What a Worker's sandbox is doing, observed once and said one way (dogfood S1).

**What happened.** In the v0.6.0 dogfood, at the same moment and about the
same Worker, `rite status` said "not started", `rite sandbox status` said
"idle" and `rite loop run` said "busy — a sandbox is running". Each view
asked its own question of the same facts and printed its own inference.
A Manager repeated the first to Robert as "no progress". The Worker had read
its ticket and was waiting at its prompt.

**What this module does.** It asks yoloAI once, and turns the answer into
one sentence that every view prints as it is: yoloAI's word, and what yoloAI
says that word means. A view may still decide something from it (the loop
decides whether a Worker is free), but it does not restate the state in
words of its own.

The meanings are yoloAI's, from its own `sandbox_status` tool description
(0.11.0): "active=working, idle=waiting at prompt, done=finished,
failed=error"; `stopped` is the container being stopped. A word yoloAI adds
later is printed as itself and said to be unrecognised, not mapped to the
nearest one rite knows.

**What it never says.** Whether a Worker has "started". rite starts a
sandboxed Worker; nothing tells it about a session someone opens by hand in
`workers/<name>/`, which is unsandboxed and invisible here until it beats or
claims. So no sandbox is "no sandbox", and a yoloAI that could not be asked
is "unknown", never either of those.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from rite_ai.sandbox.questions import Unknown, WorkerQuestion

MEANING = {
    "active": "its agent is working",
    "idle": "its agent is waiting at its prompt",
    "done": "its agent finished and exited",
    "failed": "its agent exited with an error",
    "stopped": "the sandbox is stopped",
}


@dataclass(frozen=True)
class SandboxActivity:
    """One observation of one Worker's sandbox."""

    status: object
    """A `SandboxStatus` (or anything with `value` and `known`)."""
    question: WorkerQuestion | Unknown | None = None
    """Asked only when a sandbox exists; None means no question pending."""

    @property
    def known(self) -> bool:
        return bool(getattr(self.status, "known", True))

    @property
    def exists(self) -> bool | None:
        """True or False when yoloAI answered; None when it could not be asked."""
        if not self.known:
            return None
        return str(self.status) not in ("not found", "")

    def describe(self) -> str:
        """The sentence every view prints about this sandbox.

        A pending question is not said here: each view leads with it in its
        own form, and all of them say "waiting on a question since HH:MM"."""
        value = str(self.status)
        if not self.known:
            reason = value.removeprefix("unknown — ")
            return f"sandbox state unknown ({reason})"
        if not self.exists:
            return "no sandbox"
        meaning = MEANING.get(value, "a yoloAI state rite does not recognise")
        said = f"sandbox {value}: {meaning}"
        if isinstance(self.question, Unknown):
            said += f" (could not check for a question: {self.question.reason})"
        return said


def observe(
    worker: str,
    root: str | os.PathLike[str] | None = None,
    sandbox_status=None,
) -> SandboxActivity:
    """Ask yoloAI about `worker`'s sandbox, and whether it holds a question.

    `sandbox_status` is injectable so the loop can plan a cycle without a
    yoloAI; it defaults to the real one."""
    if sandbox_status is None:
        from rite_ai.sandbox import worker_sandbox_status as sandbox_status
    from rite_ai.sandbox import questions

    activity = SandboxActivity(status=sandbox_status(worker, root))
    if activity.exists:
        return SandboxActivity(
            status=activity.status,
            question=questions.worker_question(worker, root),
        )
    return activity
