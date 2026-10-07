"""Each Manager is told its place among the others in its root (MM-5).

The Owner is told it may route, to whom, and that what comes back is context.
A secondary is told where its instructions come from IN WORDS — routed by the
Owner, or from this machine — because a Manager inferring its own authority
from what happens to reach it is the failure this design exists to prevent.
A one-Manager project's prompt is unchanged.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli

TWO = (
    "coordination:\n  managers:\n    - lead\n    - helper\n  manager_roles:\n"
    "    - name: lead\n      engine: claude\n      preset: lead\n"
    "    - name: helper\n      engine: claude\n      preset: executor\n"
)
ONE = (
    "coordination:\n  managers:\n    - lead\n  manager_roles:\n"
    "    - name: lead\n      engine: claude\n"
)


def _start(tmp_path, monkeypatch, config: str, manager: str, *, board: bool) -> str:
    import rite_ai.cli.main as main_mod
    import rite_ai.managers.supervise as sup_mod

    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "brief.yaml").write_text(
        "project:\n  name: t\n  role: owner\n"
    )
    (tmp_path / ".rite" / "config.yaml").write_text(config)
    monkeypatch.chdir(tmp_path)
    if board:
        present = (object(), "present", "", {"type": "github", "repo": "a/b"})
        monkeypatch.setattr(main_mod, "_board_for_manager", lambda root: present)
    seen: dict = {}

    def fake_supervise(root, name, **kw):
        seen.update(kw)
        return type("R", (), {"ok": True, "reason": "done", "cycles": []})()

    monkeypatch.setattr(sup_mod, "supervise", fake_supervise)
    result = CliRunner().invoke(
        cli, ["start", manager, "--sessions", "1", "--minutes", "5"]
    )
    assert "prompt" in seen, result.output
    return seen["prompt"]


@pytest.mark.parametrize("board", [True, False], ids=["working", "setup"])
def test_the_owner_is_told_it_routes_and_to_whom(tmp_path, monkeypatch, board):
    said = _start(tmp_path, monkeypatch, TWO, "lead", board=board)
    assert "You are the OWNER" in said
    assert "- 'helper': engine claude; duties execute" in said
    assert " route --ticket <ID> <manager> --from-file " in said
    assert " chore <message-id>" in said
    assert "no authority over you" in said
    # A6: a reply is verified before a person is told it happened.
    assert "A Manager's reply is a CLAIM, not evidence" in said
    assert "VERIFIER line" in said and "can be wrong" in said


@pytest.mark.parametrize("board", [True, False], ids=["working", "setup"])
def test_a_secondary_is_told_where_its_instructions_come_from(
    tmp_path, monkeypatch, board
):
    said = _start(tmp_path, monkeypatch, TWO, "helper", board=board)
    assert "You are NOT the Owner. The Owner is 'lead'." in said
    assert "routed by the Owner Manager 'lead'" in said
    assert "You do not read Slack" in said
    assert " reply --manager helper " in said
    # A6: check each claim with a tool first; a failed step is reported FAILED.
    assert "BEFORE you run it, CHECK every part you are about to claim" in said
    assert "is reported as FAILED" in said


def test_a_lone_managers_prompt_is_unchanged(tmp_path, monkeypatch):
    said = _start(tmp_path, monkeypatch, ONE, "lead", board=True)
    assert "Other Managers" not in said


def test_with_no_single_owner_they_are_told_nobody_routes(tmp_path):
    from rite_ai.config.managers import parse_managers
    from rite_ai.managers.routing import briefing

    roles = parse_managers(
        [
            {"name": "a", "engine": "claude", "preset": "executor"},
            {"name": "b", "engine": "claude", "preset": "executor"},
        ]
    ).roles
    said = briefing("a", "", list(roles), root=tmp_path)
    assert "No Manager here holds 'route'" in said
    assert "Work only on instructions from this machine" in said


# --- TR3: asking belongs before routing, and a Manager does not implement ---


@pytest.mark.parametrize("board", [True, False], ids=["working", "setup"])
def test_the_owner_is_told_to_ask_the_user_before_routing(tmp_path, monkeypatch, board):
    said = _start(tmp_path, monkeypatch, TWO, "lead", board=board)
    # The sentence that told the Owner to settle gaps itself is gone, not
    # kept beside the new one.
    assert "asking you back" not in said
    assert "ask the User before you" in said
    assert "`rite ask`, as above" in said
    assert "Never route a guess" in said


@pytest.mark.parametrize("board", [True, False], ids=["working", "setup"])
def test_a_secondary_hands_a_gap_back_rather_than_filling_it(
    tmp_path, monkeypatch, board
):
    said = _start(tmp_path, monkeypatch, TWO, "helper", board=board)
    assert "cannot be done as written" in said
    assert "Do not fill the gap yourself" in said


@pytest.mark.parametrize(
    "config, manager", [(ONE, "lead"), (TWO, "lead")], ids=["lone", "owner"]
)
def test_the_owner_is_told_not_to_implement_tickets_itself(
    tmp_path, monkeypatch, config, manager
):
    said = _start(tmp_path, monkeypatch, config, manager, board=True)
    assert "## You do not implement tickets yourself" in said
    # Exactly once: a lone Manager's prompt is composed from the same parts.
    assert said.count("You do not implement tickets yourself") == 1


def test_a_secondary_may_do_only_chores_and_trivial_tickets_through_a_pr(
    tmp_path, monkeypatch
):
    """Robert, Q4: the executor's own path is for chores and trivial tickets,
    on a branch and through a pull request; real work goes to a Worker."""
    said = _start(tmp_path, monkeypatch, TWO, "helper", board=True)
    assert "You do not implement tickets yourself" not in said
    assert said.count("## Routed work you do yourself") == 1
    assert "chore, a ticket breakdown or similar trivial work" in said
    assert "No heavy implementation" in said
    assert "never a commit to a default branch" in said
    assert "request one (above)" in said


def test_a_manager_whose_owner_cannot_be_named_is_given_the_rule():
    from rite_ai.managers.prompt import ROUTED_TICKET_WORK, TICKET_WORK, ticket_work

    # Another Manager is the Owner, in one root: a secondary, given its own.
    assert ticket_work("helper", "lead", one_root=True) == ROUTED_TICKET_WORK
    # Everyone else is: itself the Owner, no single Owner, or a `remote`
    # where the election, not this process, decides who the Owner is.
    assert ticket_work("lead", "lead", one_root=True) == TICKET_WORK
    assert ticket_work("a", "", one_root=True) == TICKET_WORK
    assert ticket_work("helper", "lead", one_root=False) == TICKET_WORK
