"""`--help` is written for the person running the command, not for rite.

v0.7.0a4 S17. Command help carried the SPEC and plan section numbers the code
is organised by — "Is this healthy? (SPEC §9.8)", "The standup a check-in
opens with (plan § K4)". A user has neither document: those are a reference to
something they cannot open, in the one text rite shows them at the moment they
are lost.

⚠ **Only the HELP is cleaned.** The same references in comments and in
non-command docstrings are how a maintainer finds the design that a piece of
code answers to, and they stay — the test walks Click's rendered output, not
the source.

The guard is the test: the fix itself is wording, and wording comes back. A
new command copied from an old one brings the habit with it.
"""

from __future__ import annotations

import re

import click
import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli

# `§` in any form, and the two documents it points into by name.
LEAK = re.compile(r"§|\bSPEC\s+\d+\.\d|\bplan\s*§")


def _walk(cmd, path=()):
    yield path, cmd
    if isinstance(cmd, click.Group):
        for name, sub in sorted(cmd.commands.items()):
            yield from _walk(sub, path + (name,))


ALL = list(_walk(cli))


def test_there_are_commands_to_check():
    """A walk that found nothing would make every test below vacuous."""
    assert len(ALL) > 50, len(ALL)


@pytest.mark.parametrize(
    ("path", "command"), ALL, ids=[" ".join(p) or "rite" for p, _ in ALL]
)
def test_no_command_help_cites_a_spec_section(path, command):
    """The invariant, across EVERY command and group rite exposes."""
    out = CliRunner().invoke(cli, [*path, "--help"]).output or ""

    found = sorted(set(re.findall(r"\S*§\S*", out)))
    assert not found, f"`rite {' '.join(path)} --help` cites {found}"
    assert not LEAK.search(out), f"`rite {' '.join(path)} --help` cites the SPEC"


def test_the_guard_would_catch_a_new_one():
    """The control: the assertion above is not vacuously true."""
    assert LEAK.search("Is this healthy? (SPEC §9.8)")
    assert LEAK.search("The standup a check-in opens with (plan § K4)")
    assert not LEAK.search("Is this healthy? Token presence, external tools")
