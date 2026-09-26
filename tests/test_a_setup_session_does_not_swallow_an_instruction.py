"""A setup session does not swallow a delivered instruction.

Ruled by Robert, 2026-09-26.

Observed with real engines: in a no-board setup session a Claude Owner declined a
person's routing instruction, citing "my explicit mandate for this session
(ticket-backend setup only)", and a local Goose Owner silently did the setup work
instead in 3 of 5 runs. The setup prompt said "and nothing else" / "do not start
any other work", contradicting the delivery note that calls an INSTRUCTION an
instruction.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.cli.main import _setup_prompt

TWO = (
    "coordination:\n  managers:\n    - lead\n    - helper\n  manager_roles:\n"
    "    - name: lead\n      engine: claude\n      preset: lead\n"
    "    - name: helper\n      engine: claude\n      preset: executor\n"
)


def _root(tmp_path: Path, config: str = "") -> Path:
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text(config)
    return tmp_path


def test_an_instruction_comes_first_and_the_reply_says_what_happened(tmp_path):
    said = _setup_prompt(_root(tmp_path), "lead")
    assert "INSTRUCTION delivered to you this session comes FIRST" in said
    assert "say in your reply what you did about the instruction" in said
    # The two lines a real Claude Owner cited as its exclusive mandate.
    assert "and nothing else" not in said
    assert "Do not start any other work" not in said
    # The setup work itself is still asked for.
    assert "ticket_backend" in said and "no queue" in said.lower()


def test_a_secondary_leaves_setup_to_the_owner(tmp_path):
    said = _setup_prompt(_root(tmp_path, TWO), "helper")
    assert "Setting one up is the Owner's job — 'lead'" in said
    assert "do NOT edit `.rite/config.yaml`" in said
    assert "INSTRUCTION delivered to you this session comes FIRST" in said


def test_the_owner_still_does_the_setup(tmp_path):
    said = _setup_prompt(_root(tmp_path, TWO), "lead")
    assert "make the change they ask for" in said
