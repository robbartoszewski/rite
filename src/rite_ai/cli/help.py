"""Help-text formatting for rite's own commands.

Click rewraps a command's docstring paragraph by paragraph, which is right
for prose and wrong for the `Examples:` block nearly every rite command
ends with — two example command lines get reflowed onto one wrapped line::

    Examples:   rite board create "Fix the bug"   rite board create "New
    feature" --role board --label epic

which is not a command anyone can copy. Click's own escape for this is a
``\\b`` line before the paragraph to be left alone; forty-five docstrings
each remembering to carry one is a rule that only holds until the next
command is written. So the marker is inserted here instead, once, for any
paragraph that contains an indented line — which is what an example block,
and only an example block, looks like.
"""

from __future__ import annotations

import inspect

import click

NO_REWRAP = "\b"


def preserve_indented_blocks(text: str) -> str:
    """Mark every paragraph containing an indented line as pre-formatted,
    so click prints it as written instead of reflowing it."""
    if not text:
        return text
    out: list[str] = []
    for paragraph in inspect.cleandoc(text).split("\n\n"):
        lines = paragraph.split("\n")
        indented = any(line[:1].isspace() for line in lines if line.strip())
        if indented and lines[0].strip() != NO_REWRAP:
            out.append(f"{NO_REWRAP}\n{paragraph}")
        else:
            out.append(paragraph)
    return "\n\n".join(out)


class RiteCommand(click.Command):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = preserve_indented_blocks(self.help or "")

    def invoke(self, ctx):
        """Turn an unreadable state file into an instruction, not a stack
        trace. `Group.invoke` wraps the whole dispatch chain, so this covers
        every subcommand without each one catching for itself.

        Recovery has to be spelled out because the safe move is not the
        obvious one: deleting `claims.json` drops the claims, which reads
        like data loss, but the alternative — carrying on against a ledger
        rite cannot read — is a second worker being handed a file the first
        one still holds. Losing the record of who holds what is recoverable
        by re-claiming; two sessions editing the same file believing they
        have it exclusively is not."""
        from rite_ai.state import CorruptStateError

        try:
            return super().invoke(ctx)
        except CorruptStateError as e:
            click.echo(f"error: {e}", err=True)
            click.echo("", err=True)
            click.echo(
                "refused rather than continuing against state rite cannot read.",
                err=True,
            )
            click.echo(
                f"  inspect it:  cat {e.path}\n"
                f"  or reset it: rm {e.path}\n"
                "\nResetting loses the records in that file — for claims.json that\n"
                "means workers must re-run `rite claim`, which is the safe\n"
                "direction. Nothing else in .rite/ is touched either way.",
                err=True,
            )
            raise SystemExit(1) from None


class RiteGroup(click.Group):
    command_class = RiteCommand
    # click reads `group_class = type` as "subgroups are this same class",
    # so `rite board`, `rite pool` and the rest inherit the formatting
    # without each having to name it.
    group_class = type

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = preserve_indented_blocks(self.help or "")
