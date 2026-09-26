"""A Manager started with `--record-issues` is told how to record, setup or not.

`rite start` prints "recording issues to …/journal" for a setup session (a
project with no board) as for any other. The setup session's prompt was
composed without the journal instructions, so that Manager was never told
how to record — measured through the CLI: 0 mentions of `rite journal` in
the prompt it received. Pinned both ways, and off stays off.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers.journal import instructions

ONE = (
    "coordination:\n  managers:\n    - lead\n  manager_roles:\n"
    "    - name: lead\n      engine: claude\n"
)


def _prompt(tmp_path, monkeypatch, *, board: bool, record: bool) -> str:
    import rite_ai.cli.main as main_mod
    import rite_ai.managers.supervise as sup_mod

    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "brief.yaml").write_text(
        "project:\n  name: t\n  role: owner\n"
    )
    (tmp_path / ".rite" / "config.yaml").write_text(ONE)
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
    args = ["start", "lead", "--sessions", "1", "--minutes", "5"]
    result = CliRunner().invoke(cli, args + (["--record-issues"] if record else []))
    assert "prompt" in seen, result.output
    return seen["prompt"]


@pytest.mark.parametrize("board", [True, False], ids=["working", "setup"])
def test_a_recording_manager_is_told_how_to_record(tmp_path, monkeypatch, board):
    said = _prompt(tmp_path, monkeypatch, board=board, record=True)
    told = instructions(tmp_path, "lead", enabled=True)
    assert told and told in said


@pytest.mark.parametrize("board", [True, False], ids=["working", "setup"])
def test_without_the_flag_it_is_not_told_about_a_journal(tmp_path, monkeypatch, board):
    said = _prompt(tmp_path, monkeypatch, board=board, record=False)
    assert "rite journal" not in said
