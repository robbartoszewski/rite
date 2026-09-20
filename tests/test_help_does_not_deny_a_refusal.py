"""A command's `--help` must not deny a refusal the command can make.

WHY THIS EXISTS. `rite projects add` gained a refusal this release: an
alias that collides with a declared Manager name is rejected outright,
because `rite start <word>` matches Manager names first and such an alias
would never resolve. The refusal is real and reached before registration.

Its help text still read:

    Register a project under an alias — warns (does not refuse) if the
    aggregate scheduled load across all registered projects looks high.

Which is TRUE about the thing it names — the aggregate-load warning does
only warn — and false as a summary of the command, because it is the first
and only line a user reads and the command's most likely failure is a hard
refusal it does not mention. The sentence was accurate when written and
became misleading when behaviour was added around it.

⚠ THE GENERAL SHAPE, which is why this is a test and not just an edit: a
docstring is the help text. Adding a refusal to a command does not touch
its docstring, so nothing goes red, and the claim rots silently. This is
the `--help` case of the README-claims step in `docs/releasing.md` — the
same defect that had the README saying rite ran on one machine only.

This file is deliberately narrow. It does not try to verify that every
command documents every refusal — that would be a proxy measure, and a
command may reasonably not enumerate every error. It asserts only that a
command does not state the OPPOSITE of what it does.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


def _help(*path: str) -> str:
    result = CliRunner().invoke(cli, [*path, "--help"])
    assert result.exit_code == 0, f"`rite {' '.join(path)} --help` failed"
    return result.output


def test_projects_add_help_does_not_claim_it_never_refuses():
    text = _help("projects", "add")
    assert "does not refuse" not in text, (
        "`rite projects add --help` says the command does not refuse, and it "
        "refuses an alias that collides with a declared Manager name — the "
        "one failure a user is most likely to hit. If the phrase is meant to "
        "describe only the aggregate-load warning, say which behaviour it "
        "qualifies; as the summary line it reads as a claim about the command"
    )


def test_projects_add_help_mentions_the_collision_refusal():
    text = _help("projects", "add").lower()
    assert "manager" in text and (
        "refus" in text or "collid" in text or "collision" in text
    ), (
        "`rite projects add --help` does not mention that an alias colliding "
        "with a Manager name is refused. A user picking an alias has no way "
        "to know the constraint exists until the command rejects them"
    )


@pytest.mark.parametrize(
    "path",
    [
        ("projects", "add"),
        ("start",),
        ("loop", "start"),
    ],
)
def test_no_command_help_denies_refusing(path):
    """The same check across the commands that actually refuse.

    `rite start` refuses without `--sessions` and without `--minutes`;
    `rite loop start` refuses in a project it cannot read. None of them may
    advertise that they do not refuse.
    """
    assert "does not refuse" not in _help(*path), (
        f"`rite {' '.join(path)} --help` denies refusing, and this command "
        "has a refusal path"
    )
