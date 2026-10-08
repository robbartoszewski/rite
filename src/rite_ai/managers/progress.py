"""Did a Manager's session change anything? Read from outside the boundary (F22).

**What this is for.** The v0.6.0 dogfood (`~/AI/dogfood-v060`, F22): an Owner
on a Jira board it could not read (F11) was started eight times in eighty
seconds. The loop said `ready`, because tickets were labelled `scheduled`;
each session ran `rite status`, said "No change — stopping here", and ended
cleanly; so the supervisor started the next one. Two `--sessions 10` runs
were gone within minutes of their last useful turn. Nothing was wrong with
any one session. What was wrong is that rite started a session whose inputs
were exactly those of a session that had just done nothing with them.

**So the question is asked of the world, never of the model.** A session's
own "nothing to do" is text, and text is what a steered or confused Manager
gets wrong. What a session DID is observable here: a commit or an edit in
the project, a claim taken or released, a message written to its outbox
(`rite reply`, `rite ask`), work routed to another Manager, a Worker
requested. `Footprint` is those, taken before and after.

**What it deliberately does not count.** Anything rite itself writes on the
Manager's behalf every cycle (the check-in ledger, the prompt, Slack relay
state, the designation), because those change whether or not the session
did anything, and counting them would make every session look productive.
**And the journal, by rule and not by oversight:** SPEC §9.15.5 requires it
be inert ("a file that triggers behaviour is a control channel"), and a
journal entry deciding whether another session starts is exactly that. A
first draft read it here; `test_nothing_in_rite_reads_the_journal` caught
it. A session that only wrote a journal entry is, correctly, idle.
And anything on the board: the loop's own reading of the board is the other
half of the question (`basis` in `rite_ai.cli.main._loop_verdict`), and a
board write a Manager makes shows up there as a changed ready set.

**The cost of getting it wrong, in each direction.** A session judged
unproductive when it was not makes the supervisor WAIT, spending nothing,
until mail, a board change or a change here wakes it, and it says so on
every run. A session judged productive when it was not costs one more
session. So the footprint errs toward counting things: a false "it did
something" is a session, a false "it did nothing" is a stalled Manager.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Footprint:
    """Everything a session could have changed that rite can see. Compared
    whole; the parts are kept separate only so a difference can be named."""

    project: str = ""
    """HEAD plus `git status --porcelain` of the project root, hashed.
    `.rite/*` is ignored by the project's own `.gitignore`, so rite's
    writes there do not move it."""
    claims: str = ""
    outbox: tuple[str, ...] = ()
    routes: str = ""
    """routing's ledger of what this Manager routed (`routed.json`). Not the
    `routes/` directory: the router empties that while the session runs."""
    requests: tuple[str, ...] = ()
    """Worker requests not yet honoured. Read before the supervisor takes
    them at the cycle's end (`supervise._honour_worker_requests`)."""
    deliveries: tuple[str, ...] = ()
    """Delivery requests not yet honoured (PB1), read at the same moment and
    for the same reason: asking for a delivery is progress."""
    lifecycle: tuple[str, ...] = ()
    """Stop, destroy and restart requests not yet honoured (SCRUM-59): asking
    rite to change a Worker is coordination too. Not `status` or `gate`,
    which only look (`lifecycle.acting_requests`)."""

    def differs_from(self, other: Footprint) -> list[str]:
        """The names of the parts that changed, in a fixed order."""
        return [
            name
            for name in (
                "project",
                "claims",
                "outbox",
                "routes",
                "requests",
                "deliveries",
                "lifecycle",
            )
            if getattr(self, name) != getattr(other, name)
        ]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _git_state(root: Path) -> str:
    """HEAD and the porcelain status, or "" when this is not a repository.

    "" on any failure is safe in one direction only, and it is the right
    one: two failures compare EQUAL, so a project git cannot read looks
    unchanged, and the guard falls back on the other parts. It never makes
    an unchanged project look changed."""
    try:
        from rite_ai.githost import hardened_git_env

        env = hardened_git_env()
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            env=env,
            capture_output=True,
            timeout=10,
        )
        # 🔴 A repo-set core.fsmonitor runs a program on `git status`, and
        # this runs every cycle in a Manager-writable tree (SCRUM-75).
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
            env=env,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if status.returncode != 0:
        return ""
    return _digest(head.stdout + b"\0" + status.stdout)


def _own_names(root: Path, manager: str, dirname: str, area: str) -> tuple[str, ...]:
    """Names in one of the Manager's own directories, through `own_dir`, so
    a link the Manager planted there is never listed through."""
    from rite_ai.managers import own_dir

    try:
        if dirname:
            return tuple(own_dir.names(root, manager, dirname, "", area=area))
        with own_dir.directory(root, manager, area=area) as fd:
            return () if fd is None else tuple(sorted(os.listdir(fd)))
    except OSError:
        return ()


def _bytes(path: Path) -> str:
    try:
        return _digest(path.read_bytes())
    except OSError:
        return ""


def footprint(root: Path, manager: str) -> Footprint:
    """What `manager` could have changed, as it stands now."""
    from rite_ai.managers import own_dir
    from rite_ai.managers.broker import REQUESTS_DIRNAME
    from rite_ai.managers.lifecycle import acting_requests
    from rite_ai.managers.routing import routed_log
    from rite_ai.publishing.requests import DIRNAME as DELIVERIES_DIRNAME

    root = Path(root)
    return Footprint(
        project=_git_state(root),
        claims=_bytes(root / ".rite" / "claims.json"),
        outbox=_own_names(root, manager, "", own_dir.OUTBOX),
        routes=_bytes(routed_log(root, manager)),
        requests=_own_names(root, manager, REQUESTS_DIRNAME, own_dir.STATE),
        deliveries=_own_names(root, manager, DELIVERIES_DIRNAME, own_dir.STATE),
        lifecycle=acting_requests(root, manager),
    )
