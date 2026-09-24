"""A Manager replies through `rite reply`, not by hand-writing JSON (C5).

WHY THIS EXISTS. The User side stopped hand-writing the mailbox format when
`rite message` landed; the Manager side was still told a JSON shape, a
filename pattern and a directory. `read` skips a file it cannot use rather
than failing the run, so a reply written with the wrong key was on disk and
never shown. Measured: `{"message": ...}` in the outbox, and `rite replies`
said "nothing new". A question the Manager believes it asked, and the User
never sees.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import MANAGER_ENV
from rite_ai.managers.mailbox import how_to_reply


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    return tmp_path


def test_the_instruction_names_a_command_and_no_format(tmp_path):
    said = how_to_reply(tmp_path, "lead")
    assert 'rite reply --manager lead "' in said
    for shape in ('"text"', "timestamp", ".json", "<pid>"):
        assert shape not in said, f"the Manager is still handed a format: {shape}"


@pytest.mark.parametrize("identity", ["lead", None], ids=["in-session", "no-env"])
def test_what_the_manager_is_told_to_run_reaches_the_user(
    project, monkeypatch, identity
):
    """The instructed command, run exactly as written, then read the way the
    User reads. `no-env` is a Manager on a tmux without `-e`, which is why the
    instruction spells `--manager`."""
    if identity:
        monkeypatch.setenv(MANAGER_ENV, identity)
    else:
        monkeypatch.delenv(MANAGER_ENV, raising=False)
    runner = CliRunner()
    sent = runner.invoke(cli, ["reply", "--manager", "lead", "skip ticket 12?"])
    assert sent.exit_code == 0, sent.output
    shown = runner.invoke(cli, ["replies", "lead"])
    assert "skip ticket 12?" in shown.output, shown.output


@pytest.mark.parametrize(
    "args, why",
    [
        (["reply", "--manager", "lead", "   "], "empty"),
        (["reply", "--manager", "ghost", "x"], "no Manager named"),
        (["reply", "x"], "no --manager"),
    ],
)
def test_what_cannot_be_delivered_is_refused_not_written(
    project, monkeypatch, args, why
):
    monkeypatch.delenv(MANAGER_ENV, raising=False)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1
    assert why in result.output
    assert not list((project / ".rite").rglob("mail/out/*.json"))
