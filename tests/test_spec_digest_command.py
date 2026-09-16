"""The `/spec-digest` command (S-10).

It drives `rite spec` subcommands that are built last, deliberately, so the CLI
contention lands after the domain work. Until they exist the command is written
but not installed: a command a session follows that names subcommands which do
not exist sends it into errors it cannot resolve.
"""

import re
from pathlib import Path

from rite_ai.cli.init.claude_gen import _COMMAND_FILES
from rite_ai.cli.main import cli

TEMPLATE = (
    Path(__file__).resolve().parent.parent / "templates" / "commands" / "spec-digest.md"
)


def _text() -> str:
    return TEMPLATE.read_text()


def test_it_has_a_description():
    assert _text().startswith("---\ndescription: ")


def test_it_is_installed_exactly_when_every_subcommand_it_names_exists():
    named = set(re.findall(r"rite spec ([a-z]+)", _text()))
    spec_group = cli.commands["spec"]
    existing = set(spec_group.commands)
    installed = "spec-digest.md" in _COMMAND_FILES
    assert named, "the command names no rite spec subcommands"
    assert installed == named.issubset(existing), (
        f"installed={installed}, but missing subcommands: {sorted(named - existing)}"
    )


def test_it_says_which_scope_round_two_ran_at():
    text = _text()
    assert "neighbourhood-scoped" in text
    assert "run round 2\n   over every unit" in text
    assert "whether it covered every unit or only the\n     changed ones" in text


def test_the_two_rounds_ask_different_questions():
    text = _text()
    round1 = text[text.index("4. **Round 1") : text.index("5. **Round 2")]
    round2 = text[text.index("5. **Round 2") : text.index("6. **The gate")]
    assert "**only** its source range" in round1
    assert "outside* its own range" in round2
    assert "exception that qualifies it" in round2


def test_it_refuses_a_spec_that_does_not_decompose_before_spending_tokens():
    text = _text()
    first = text[text.index("1. **Check") : text.index("2. **Find")]
    assert "rite spec index" in first and "stop" in first


def test_hashes_are_never_written_by_hand():
    assert "Do not write `source_sha` or `body_sha` yourself" in _text()


def test_it_finishes_on_the_gate():
    text = _text()
    assert text.index("rite spec verify") > text.index("5. **Round 2")
    assert "Do not finish on a failing gate" in text
