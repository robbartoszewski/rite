"""The one place that finds a module's own instruction files and asks (S23).

⚠ **This module exists because S23 shipped in a CLI command instead.** The
find-and-ask lived in `cli/main.py::_ask_about_module_docs`, called from
`add_worker_cmd` and nowhere else, so a Worker created by `rite init` was
never offered the module's conventions at all — the module carried
`CLAUDE.md`, `AGENTS.md` and `CONTRIBUTING.md`, and `worker.yml` had no
`follow_module_docs` key to show it had been considered. Found in the v0.7.0
dogfood (C2); the same defect class as `claude_instructions` having a reader
and no writer, and as `offer_a_worker` calling `add_worker(root, name)` with
two of its five parameters (C1).

Every entry point that creates a Worker calls `settle_module_docs`. Adding a
third one is then a call, not a copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

# How to answer when there was nobody to ask, in the words of the command the
# user actually ran. The same guard, different remedies.
BY_FLAG = (
    "pass --follow-module-docs to follow them, or --no-follow-module-docs to "
    "say so explicitly"
)


def how_init_says_it(worker: str) -> str:
    return (
        "re-run `rite init` with somebody at the terminal to be asked, or "
        f"declare the Worker yourself with `rite add worker {worker} "
        "--follow-module-docs`"
    )


def somebody_is_there() -> bool:
    """Whether there is a person at the terminal to answer a question.

    Separate and tiny so a test can flip it: what it guards is a prompt,
    and a prompt cannot be exercised by a test that has already answered
    it.
    """
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):  # a closed or replaced stream
        return False


def settle_module_docs(
    root: Path,
    module_subset: list[str] | None,
    answer: bool | None,
    *,
    interactive: bool = True,
    how_to_answer: str = BY_FLAG,
) -> list[str]:
    """Which of the modules' own instruction files this Worker follows (S23).

    ⚠ **Asked, not assumed, in either direction.** A module's
    `CONTRIBUTING.md` is written for people and may contradict how rite
    drives a Worker. Following it silently would put instructions nobody
    chose into a Worker's brief; ignoring it silently loses the conventions
    the module actually has. So the files are found, named, and the
    question is put.

    Nothing found means nothing asked — a question with no subject is
    noise, and answering it changes nothing.

    🔴 **The no-tty guard is inside this function, not in its callers.**
    `interactive=False` (`rite init --yes`) and an unattached stdin are both
    "nobody answered", and a caller that got to decide which of those counts
    is a caller that can reintroduce the abort below by passing the wrong
    flag.
    """
    from rite_ai.workspace.manage import module_docs, modules_for_worker

    modules = modules_for_worker(root, module_subset)
    if isinstance(modules, str):
        # Whatever is wrong with the subset, `add_worker` refuses on it a
        # moment later with the same words. Saying it twice, or refusing
        # here, would put the error in two places.
        return []
    found = module_docs(root, modules)
    if not found:
        return []

    click.echo("these modules keep instructions of their own:")
    for path in found:
        click.echo(f"  {path}")
    if answer is None and not (interactive and somebody_is_there()):
        # 🔴 **A prompt is not an exception, it is the absence of an answer**
        # (defect class 15). `click.confirm` on an empty stdin ABORTS, so
        # asking unconditionally turned `rite add worker` in a script into a
        # command that creates no Worker — found by an existing test going
        # red. Not asked, not followed, and BOTH said, with how to answer it:
        # a default taken in silence is the other half of the same defect.
        click.echo(
            f"  not asked — nothing is attached to answer. Not followed; "
            f"{how_to_answer}",
            err=True,
        )
        return []
    if answer is None:
        answer = click.confirm("  should this Worker follow them?", default=True)
    if not answer:
        click.echo("  not followed — the Worker is not told to read them")
        return []
    click.echo("  followed — named in the Worker's CLAUDE.md, under rite's own")
    return found


__all__ = [
    "BY_FLAG",
    "how_init_says_it",
    "settle_module_docs",
    "somebody_is_there",
]
