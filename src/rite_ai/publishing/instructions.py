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

**Until PB1 piece 4**, rite does not push. Under `push` and `pull_request`
the Worker still pushes its ticket branch and opens the PR itself; under
`commit` it never pushes. Piece 4 changes the first two to "never push" too.
"""

from __future__ import annotations

from rite_ai.config.models import Module, ProjectConfig
from rite_ai.publishing.settings import effective


def _for_module(module: Module, strategy: str, ticket: str) -> str:
    where = f"`{module.name}` ({strategy})"
    if strategy == "commit":
        return (
            f"- {where}: commit on branch `{ticket}`. **Do not push, open a pull "
            "request or merge**: under `commit` nothing leaves this machine "
            "until a person decides. rite collects your commits into the "
            "project when your Manager delivers the ticket, so a commit is "
            "enough; an uncommitted change is not collected."
        )
    return (
        f"- {where}: commit on branch `{ticket}`, push that branch "
        f"(`git push -u origin {ticket}`) and open a pull request against "
        f"`{module.branch}`. **Do not merge it**, whatever the checks say: "
        "merging is the User's, or rite's own gated step."
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
