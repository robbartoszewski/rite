"""What a Worker is told to do with its commits, per start (PB1).

Written into TICKET.md by `rite sandbox start`, from the SAME parse that
writes the start record (`record.write`), so what the Worker was told and
what rite later compares against cannot differ.

**Per start, not in CLAUDE.md.** CLAUDE.md is written once, when a Worker is
added, and a strategy is config that can change between tickets. TICKET.md is
written every start, which is where a per-strategy instruction belongs.

**No Worker merges, under any strategy.** Merging after checks is either the
User's or, with `auto_merge`, rite's own gated step (a green matched to the
head SHA that contains the base's current tip, `merge_gate`). A Worker
merging its own PR bypasses both, and with `main`'s "require branches to be
up to date" off, GitHub accepts a stale green.

**No Worker pushes, under any strategy** (PB1 piece 4). rite pushes and
opens the pull request on the host, after its publish gate passes on exactly
the commits being sent, with the Worker's GitHub token, which rite holds
on the host and the Worker never does (`WORKER_SERVICES`).
"""

from __future__ import annotations

from rite_ai.config.models import Module, ProjectConfig
from rite_ai.publishing.settings import effective


def _for_module(module: Module, strategy: str, ticket: str) -> str:
    where = f"`{module.name}` ({strategy})"
    after = {
        "commit": "rite brings your commits into the project and nothing leaves "
        "this machine until a person decides",
        "push": f"rite pushes your commits to `{module.branch}` after its "
        "publish gate passes",
        "pull_request": f"rite pushes your branch and opens a pull request "
        f"against `{module.branch}` after its publish gate passes",
    }.get(strategy, "rite decides what happens to your commits")
    return (
        f"- {where}: commit on branch `{ticket}`. **Do not push, open a pull "
        f"request or merge**: when your Manager delivers the ticket, {after}. "
        "A commit is enough; an uncommitted change is not collected."
    )


def for_worker(config: ProjectConfig, modules: list[Module], ticket: str) -> str:
    """The TICKET.md section: one line per module, then what is common."""
    lines = [
        _for_module(m, effective(config.publish, m).strategy, ticket) for m in modules
    ]
    lines += [
        "",
        "These were in force when you were started; rite recorded them, and "
        "they are what your work is delivered under. Never push to "
        "a module's own branch directly, under any strategy.",
    ]
    return "\n".join(lines)
