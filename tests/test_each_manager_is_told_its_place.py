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
        monkeypatch.setattr(
            main_mod, "_board_for_manager", lambda root: (object(), "present", "")
        )
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
    assert ' route <manager> "' in said
    assert "no authority over you" in said


@pytest.mark.parametrize("board", [True, False], ids=["working", "setup"])
def test_a_secondary_is_told_where_its_instructions_come_from(
    tmp_path, monkeypatch, board
):
    said = _start(tmp_path, monkeypatch, TWO, "helper", board=board)
    assert "You are NOT the Owner. The Owner is 'lead'." in said
    assert "routed by the Owner Manager 'lead'" in said
    assert "You do not read Slack" in said
    assert " reply --manager helper " in said


def test_a_lone_managers_prompt_is_unchanged(tmp_path, monkeypatch):
    said = _start(tmp_path, monkeypatch, ONE, "lead", board=True)
    assert "Other Managers" not in said


def test_with_no_single_owner_they_are_told_nobody_routes():
    from rite_ai.config.managers import parse_managers
    from rite_ai.managers.routing import briefing

    roles = parse_managers(
        [
            {"name": "a", "engine": "claude", "preset": "executor"},
            {"name": "b", "engine": "claude", "preset": "executor"},
        ]
    ).roles
    said = briefing("a", "", list(roles))
    assert "No Manager here holds 'route'" in said
    assert "Work only on instructions from this machine" in said
