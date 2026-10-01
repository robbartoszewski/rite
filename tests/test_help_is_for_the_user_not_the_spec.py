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

# rite's own decision ids (`(D-46)`), and its design documents by path or name
# (`docs/design/V070_TICKET_REFINEMENT.md`). Robert, 2026-10-01: stripped from
# help like the section numbers, for the same reason — the reader has neither.
DECISION = re.compile(r"\bD-\d+\b")
DESIGN_DOC = re.compile(r"docs/design/\S*|\bV0\d0_[A-Z_]+\.md\b")

SPEC_GROUP = "spec"
"""⚠ The one exemption, and it is narrow. `rite spec slice` and `rite spec
show` read the USER's own spec, where `D-12` is the syntax for a decision in
it: their examples show the reader how to type one. A rite design reference
anywhere in that group, a document path included, is still refused."""


def leaks(path: tuple, out: str) -> list[str]:
    """Every reference in `rite <path> --help` the reader cannot open."""
    found = sorted(set(re.findall(r"\S*§\S*", out)))
    if LEAK.search(out):
        found.append("a SPEC section")
    if not (path and path[0] == SPEC_GROUP):
        found += sorted(set(DECISION.findall(out)))
    found += sorted(set(DESIGN_DOC.findall(out)))
    return found


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

    found = leaks(path, out)
    assert not found, f"`rite {' '.join(path)} --help` cites {found}"


def test_the_guard_would_catch_a_new_one():
    """The control: the assertion above is not vacuously true."""
    assert LEAK.search("Is this healthy? (SPEC §9.8)")
    assert LEAK.search("The standup a check-in opens with (plan § K4)")
    assert not LEAK.search("Is this healthy? Token presence, external tools")


def test_the_decision_and_design_doc_guards_would_catch_a_new_one():
    """Controls for the second rule: each kind is caught outside `spec`, and
    the `spec` exemption covers decision-id SYNTAX only, never a design doc."""
    assert leaks(("budget",), "reporting only (D-38): no Worker-count") == ["D-38"]
    assert leaks(("refine",), "(docs/design/V070_TICKET_REFINEMENT.md).")
    assert leaks(("refine",), "see V070_TICKET_REFINEMENT.md for why")
    assert leaks(("spec", "slice"), "rite spec slice D-12 --worker alpha") == []
    assert leaks(("spec", "slice"), "(docs/design/V070_SPEC_DIGEST.md)")
    assert leaks(("question", "withdraw"), "docs/adr/0004-storage.md:12") == [], (
        "a path in the USER's own docs is an example, not a rite reference"
    )


def test_re_adding_a_decision_id_to_real_help_goes_red(monkeypatch):
    """End to end: put one back into a real command, and the walk sees it."""
    budget = cli.commands["budget"]
    monkeypatch.setattr(budget, "help", budget.help + " (D-38)")
    out = CliRunner().invoke(cli, ["budget", "--help"]).output
    assert leaks(("budget",), out) == ["D-38"]
