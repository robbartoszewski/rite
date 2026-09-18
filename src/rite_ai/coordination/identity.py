"""Which Manager is this machine (Q8 — RAISED, NOT SETTLED).

Every cross-machine operation in Phase 2 needs this machine's Manager name,
and until now nothing could supply one, so the whole stack was complete and
called by nobody.

**It cannot live in `config.yaml` or `brief.yaml`.** Both are committed and
shared — §9.3 has a Manager machine fetch the Owner's `config.yaml` for
alignment — so neither can say which entry of `coordination.managers` is
*this* machine. `.rite/` already draws the line this needs: a short
committed set, and everything else runtime state that is "meaningful only on
the machine that wrote it".

So: `.rite/machine`, the name as it appears in `coordination.managers`.

**One line was an assumption, not a decision** (RL-60, found building RL-T32).
Q8 asked "which Manager is this machine", and the question's shape gave the
answer: one. rite local breaks that — its Q1 answer is "one machine", with a
lead, a planner and an executor sharing it, each needing its own name because
the Owner routes on heartbeats and a ticket labelled `planner` is picked up by
whatever publishes as `planner`. So the file takes one name per line.

**The FIRST line is still one name, and that is deliberate.** A machine holds
one lease, publishes one set of claims and stands for Owner once; two local
Managers competing for the Owner role on the same box is nonsense. So this is
a primary identity plus the Managers it hosts, never a list that replaces the
name — `this_manager()` keeps meaning what every caller already reads it to
mean, and `hosted_managers()` is the new question.

**A file this version cannot read fully is not enrolled at all.** Not "the
first line, and ignore the rest": a name that cannot be established must never
be guessed, and a second line in a format this version does not understand is
evidence that the file means something other than what it appears to.

**Absent means not enrolled, and that is the safe default.** A machine
nobody has named does not publish heartbeats, does not stand for Owner and
does not touch another machine's work. Every existing project is in exactly
that state and stays behaving as it does today.

⚠ **`rite init` does not write this file, deliberately.** Adding a question
to init is a change to the setup flow, and Q8 offers two other answers (a
hostname, an environment variable) that would put identity somewhere else
entirely. This is the smallest thing that makes the machinery runnable
without foreclosing that decision: if the answer is not a file, only
`this_manager` changes.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.config.models import CoordinationConfig

MACHINE_FILE = "machine"


def _machine_file(root: Path) -> Path:
    return root / ".rite" / MACHINE_FILE


def hosted_managers(root: Path) -> list[str]:
    """Every Manager this machine hosts, primary first. Empty when it is not
    enrolled, or when the file cannot be read as a whole.

    Empty for every reason: no file, an empty one, a name that is not usable
    as a state key, or a duplicate. A name that cannot be established must
    never be guessed — the whole point of the name is that other machines act
    on what it says — and half a declaration is not a smaller declaration, it
    is an unknown one.
    """
    path = _machine_file(root)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    names = [line.strip() for line in text.splitlines()]
    names = [name for name in names if name]
    if not names:
        return []
    for name in names:
        if "/" in name or name.split() != [name]:
            # A path-like value would be a state key with a directory in it,
            # which is a different file entirely; whitespace inside a name
            # means this line is not a name.
            return []
    if len(set(names)) != len(names):
        # Two of the same Manager on one machine would publish one heartbeat
        # between them and take work twice.
        return []
    return names


def this_manager(root: Path) -> str | None:
    """This machine's PRIMARY Manager name, or None when it is not enrolled.

    The one that holds the lease, publishes this machine's claims and stands
    for Owner — unchanged for every caller and for every single-line file
    that exists today. `hosted_managers` is the other question.
    """
    hosted = hosted_managers(root)
    return hosted[0] if hosted else None


def enrolment(root: Path, config: CoordinationConfig) -> str | None:
    """The problem with this machine's enrolment, or None if there is none.

    Separate from `config_check` because this one needs the machine, not
    just the config: the same `config.yaml` is correct on one machine and
    incomplete on another, which is exactly why the name is not in it.
    """
    if not config.managers and not config.remote:
        return None  # not coordinating at all, which is Phase 1
    hosted = hosted_managers(root)
    if not hosted:
        return (
            "coordination: this machine has no usable `.rite/machine`, so it "
            "is not enrolled — it will not publish a heartbeat, stand for "
            "Owner, or take over another machine's work"
        )
    name = hosted[0]
    if config.managers and name not in config.managers:
        return (
            f"coordination: this machine calls itself {name!r}, which is not "
            f"in `managers` ({', '.join(config.managers)}) — it can never be "
            "Owner, and the other machines will not defer to it"
        )
    if config.managers:
        strangers = [n for n in hosted[1:] if n not in config.managers]
        if strangers:
            # Named separately from the primary: this machine IS enrolled and
            # coordinating, and the problem is one line further down a file
            # nobody re-reads.
            return (
                f"coordination: `.rite/machine` also hosts "
                f"{', '.join(repr(s) for s in strangers)}, which "
                f"{'are' if len(strangers) > 1 else 'is'} not in `managers` "
                f"({', '.join(config.managers)}) — nothing will ever be routed "
                f"to {'them' if len(strangers) > 1 else 'it'}"
            )
    return None


def claims_channel(root: Path):
    """(state layer, machine name) for cross-machine claims, or (None, "").

    P2-5b checks other machines' published claims before granting one, and
    P2-5a publishes the grant so those machines can see it — but only if a
    caller supplies a layer and a name. `rite claim` supplied neither, so
    both were inert and two machines could claim the same path.

    Returns nothing at all unless coordination is configured AND this
    machine is enrolled, so a single-machine project keeps claiming exactly
    as it does today: local, offline, no round trip.

    ⚠ With a layer, a claim NEEDS the remote: a store that cannot be read
    refuses the claim rather than granting one it could not check (the same
    fail-closed rule the rest of this layer follows). That is P2-5b's
    bargain, not a new one — a machine that cannot see other machines'
    claims cannot safely take a shared path.
    """
    from rite_ai.config.parse import load_project

    project = load_project(root)
    if isinstance(project, list):
        return None, ""
    config = project.config.coordination
    if not config.managers or not config.remote:
        return None, ""
    name = this_manager(root)
    if name is None or (config.managers and name not in config.managers):
        return None, ""

    from rite_ai.coordination.git_backend import GitStateLayer

    layer = GitStateLayer(
        config.remote,
        root / ".rite" / "coordination-cache.git",
        state_branch=config.state_branch,
    )
    return layer, name
