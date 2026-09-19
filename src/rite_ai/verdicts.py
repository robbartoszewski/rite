"""Commands that cannot fail, which is the same as commands nobody is running.

A check produces output and an exit code, and only the exit code is a verdict.
A command whose status is not its own — `pytest | tail -1`, `make test || true`
— reports success for ever, whatever it did. It looks exactly like a passing
check, because "no complaint" is what both a healthy run and a never-run
produce. That symmetry is why this survives review: there is nothing to see.

**Three instances in one night, on rite itself** (EXC-1): a gate that exits 0
on its own help text and was reported clean several times on that basis;
`pytest | tail -3` under `set -e`, which pushed a commit to `main` with a
failing test named in the same output; and `uv run ruff check` returning 1
without stopping the script that ran it. Each discarded a real failure. Each
was caught by accident.

rite hands out commands of exactly this kind in two places, and checked
neither: `modules.yaml`'s recorded `test`/`lint`/`build`, which the generated
`CLAUDE.md` tells every Worker to "run as written"; and `Subtask.verify`,
which RL-7 has rite run ITSELF rather than trusting a report.

**Static, and it stays static.** Running an unknown recorded command to find
out whether it is safe is a worse idea than the thing being checked.

**It names, it does not judge silently.** A project may have a reason for a
pipeline. The failure mode here is silence, so the answer is a sentence, not a
refusal — except for a decomposition's verify, where three gates read the
artifact and a verify that cannot fail makes all three decorative.
"""

from __future__ import annotations

import re

_FILTERS = (
    "tail",
    "head",
    "grep",
    "tee",
    "cat",
    "less",
    "more",
    "sort",
    "uniq",
    "wc",
    "awk",
    "sed",
    "jq",
)
"""Last elements of a pipeline whose exit status replaces the real one. Not an
exhaustive list of filters — an exhaustive list is impossible — but these are
what people actually append to quieten output, which is the moment the verdict
is lost."""

_SWALLOWS = (
    "|| true",
    "|| :",
    "; true",
    "; exit 0",
    "&& true",
)

_PIPE = re.compile(r"\|(?!\|)")


def cannot_fail(command: str) -> str:
    """Why this command's exit code is not its own, or "" when it is.

    The reason is the return value because the caller's job is to tell a
    human, and a bare False does not survive being read at three in the
    morning next to forty other lines.
    """
    text = (command or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    for swallow in _SWALLOWS:
        if swallow in lowered:
            return (
                f"`{swallow.strip()}` discards the exit code, so this reports "
                "success whatever it did"
            )

    if text.rstrip().endswith("&"):
        return (
            "a trailing `&` backgrounds it, so the exit code belongs to the "
            "shell and not to the command"
        )

    parts = _PIPE.split(text)
    if len(parts) > 1:
        last = parts[-1].strip()
        # The pipeline's status is its LAST element's, so only that one
        # matters. `tail | pytest` is odd but not blind.
        head = last.split()[0] if last.split() else ""
        name = head.rsplit("/", 1)[-1]
        if name in _FILTERS:
            return (
                f"the pipeline ends in `{name}`, so the exit code is "
                f"`{name}`'s and not the command's — it reports success "
                "however the command exited"
            )
    return ""


def command_problems(commands: dict[str, str], where: str) -> list[str]:
    """One line per recorded command whose verdict has been lost.

    `where` names the thing being checked — a module, a subtask — because the
    same sentence about the same command in two places is how a reader learns
    to skim past it.
    """
    found: list[str] = []
    for key, command in sorted(commands.items()):
        if not command:
            continue
        why = cannot_fail(command)
        if why:
            found.append(f"{where}: `{key}` cannot fail — {why}")
    return found
