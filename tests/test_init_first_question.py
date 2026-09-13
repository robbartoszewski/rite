"""`rite init`'s first question (SPEC §9.3).

Someone who already has a spec or code answers yes, gives a path, and is asked
one open question about what is stale — then init is done, and the existing
questionnaire is never reached. Someone who answers no gets that questionnaire
exactly as before.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.init import ALREADY_A_PROJECT, run_init
from rite_ai.cli.main import cli

EXISTING = "Do you have a spec or existing code for this project? [y/N]"
PATH = "Path: [.]"
CHANGES = "Anything stale, or that you'd like changed? Free text, or Enter to skip."
# The first prompt of the existing questionnaire.
ROLE = "Is this the Owner machine or a Manager machine?"


@click.command()
@click.option("--yes", is_flag=True)
@click.argument("directory")
def _init_cmd(yes: bool, directory: str) -> None:
    result = run_init(Path(directory), yes=yes)
    click.echo(f"STATUS:{result.status}")


@pytest.fixture(autouse=True)
def _no_verified_sandbox(monkeypatch):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)


def _brief(root: Path) -> dict:
    return yaml.safe_load((root / ".rite" / "brief.yaml").read_text())


# --- no ---------------------------------------------------------------------------


def test_no_is_the_default_and_leads_to_the_questionnaire(tmp_path: Path):
    answers = "\n" + "\n".join([""] * 10 + ["3"] + [""] * 4) + "\n"
    result = CliRunner().invoke(_init_cmd, [str(tmp_path)], input=answers)
    assert result.exit_code == 0, result.output
    assert EXISTING in result.output
    assert ROLE in result.output
    assert PATH not in result.output
    assert "STATUS:created" in result.output
    assert "source" not in _brief(tmp_path)


def test_yes_mode_without_a_preset_asks_nothing_and_records_no_source(
    tmp_path: Path,
):
    result = CliRunner().invoke(_init_cmd, ["--yes", str(tmp_path)])
    assert "STATUS:created" in result.output
    assert EXISTING not in result.output
    assert "source" not in _brief(tmp_path)


# --- yes --------------------------------------------------------------------------


def test_yes_then_enter_twice_is_three_prompts_and_done(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hi')\n")
    result = CliRunner().invoke(_init_cmd, [str(tmp_path)], input="y\n\n\n")
    assert result.exit_code == 0, result.output
    assert "STATUS:created" in result.output
    for prompt in (EXISTING, PATH, CHANGES):
        assert prompt in result.output
    assert "languages, structure and conventions will be taken" in result.output
    assert ROLE not in result.output, "reached the questionnaire"


def test_the_brief_holds_the_path_and_the_answer(tmp_path: Path):
    result = CliRunner().invoke(
        _init_cmd, [str(tmp_path)], input="y\n\nThe feed poller is stale\n"
    )
    assert result.exit_code == 0, result.output
    brief = _brief(tmp_path)
    assert brief["source"] == {
        "path": str(tmp_path.resolve()),
        "changes": "The feed poller is stale",
    }
    assert brief["what"] == {"kind": "", "features": ""}
    modules = yaml.safe_load((tmp_path / ".rite" / "modules.yaml").read_text())
    assert not modules["modules"]


def test_the_path_can_point_elsewhere(tmp_path: Path):
    (tmp_path / "code").mkdir()
    result = CliRunner().invoke(_init_cmd, [str(tmp_path)], input="y\ncode\n\n")
    assert result.exit_code == 0, result.output
    assert _brief(tmp_path)["source"]["path"] == str((tmp_path / "code").resolve())


def test_a_mistyped_path_is_asked_again_and_never_falls_through(tmp_path: Path):
    result = CliRunner().invoke(
        _init_cmd, [str(tmp_path)], input="y\nno-such-dir\n\n\n"
    )
    assert result.exit_code == 0, result.output
    assert "Nothing at" in result.output
    assert result.output.count(PATH) == 2
    assert ROLE not in result.output, "fell through to the from-scratch flow"
    assert _brief(tmp_path)["source"]["path"] == str(tmp_path.resolve())


def test_the_root_branch_is_the_one_the_source_is_on(tmp_path: Path):
    code = tmp_path / "code"
    code.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "phase-2"], cwd=code, check=True)
    CliRunner().invoke(_init_cmd, [str(tmp_path)], input="y\ncode\n\n")
    assert _brief(tmp_path)["project"]["root_branch"] == "phase-2"


# --- already a rite project -------------------------------------------------------


def _existing_project(root: Path) -> dict[str, str]:
    assert run_init(root, yes=True).status == "created"
    return {
        rel: (root / rel).read_text()
        for rel in (".rite/config.yaml", ".rite/modules.yaml", "CLAUDE.md")
    }


def test_an_existing_project_gets_the_changes_recorded_not_rebuilt(tmp_path: Path):
    before = _existing_project(tmp_path)
    result = CliRunner().invoke(
        cli, ["init", str(tmp_path)], input="y\n\nRename the backend module\n"
    )
    assert result.exit_code == 0, result.output
    assert ALREADY_A_PROJECT in result.output
    assert "Wipe and start over" not in result.output
    assert _brief(tmp_path)["source"]["changes"] == "Rename the backend module"
    after = {rel: (tmp_path / rel).read_text() for rel in before}
    assert after == before, "an existing project was rebuilt"


def test_enter_on_an_existing_project_changes_nothing(tmp_path: Path):
    _existing_project(tmp_path)
    brief_before = (tmp_path / ".rite" / "brief.yaml").read_text()
    result = CliRunner().invoke(cli, ["init", str(tmp_path)], input="y\n\n\n")
    assert result.exit_code == 0, result.output
    assert "Nothing to apply" in result.output
    assert (tmp_path / ".rite" / "brief.yaml").read_text() == brief_before


def test_a_second_request_is_kept_beside_the_first(tmp_path: Path):
    _existing_project(tmp_path)
    CliRunner().invoke(cli, ["init", str(tmp_path)], input="y\n\nFirst change\n")
    CliRunner().invoke(cli, ["init", str(tmp_path)], input="y\n\nSecond change\n")
    changes = _brief(tmp_path)["source"]["changes"]
    assert "First change" in changes and "Second change" in changes


def test_sections_init_does_not_write_survive(tmp_path: Path):
    _existing_project(tmp_path)
    brief_path = tmp_path / ".rite" / "brief.yaml"
    brief_path.write_text(brief_path.read_text() + "enriched:\n  note: kept\n")
    CliRunner().invoke(cli, ["init", str(tmp_path)], input="y\n\nA change\n")
    assert _brief(tmp_path)["enriched"] == {"note": "kept"}


# --- presets ----------------------------------------------------------------------


def test_a_preset_can_answer_the_first_question(tmp_path: Path):
    preset = tmp_path / "preset.yaml"
    preset.write_text("source:\n  path: .\n  changes: Split the API\n")
    result = CliRunner().invoke(
        cli, ["init", str(tmp_path), "--config", str(preset), "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert _brief(tmp_path)["source"] == {
        "path": str(tmp_path.resolve()),
        "changes": "Split the API",
    }


def test_a_preset_path_that_does_not_exist_is_an_error(tmp_path: Path):
    preset = tmp_path / "preset.yaml"
    preset.write_text("source:\n  path: nowhere\n")
    result = CliRunner().invoke(
        cli, ["init", str(tmp_path), "--config", str(preset), "--yes"]
    )
    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert not (tmp_path / ".rite").exists()
