"""`Root branch?` offers the branch detection already found.

Detection records each repository's current branch, and `modules.yaml` stores
it. The prompt ignored that and offered a hardcoded `main`. Pressing Enter
through `rite init` in a workspace whose module is on `phase-2` therefore wrote
the module on `phase-2` and the root branch as `main` — PRs aimed at a line
the project is not on, with nothing said.

The property under test is agreement: pressing Enter must not produce a root
branch that disagrees with the module `init` just registered.
"""

import subprocess
from pathlib import Path

import click
import yaml
from click.testing import CliRunner

from rite_ai.cli.init import run_init
from rite_ai.cli.init.detect import detect_repos, detect_root_branch


def _repo(path: Path, branch: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True)
    (path / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "x",
        ],
        cwd=path,
        check=True,
    )


@click.command()
@click.option("--yes", is_flag=True)
@click.argument("directory")
def _init_cmd(yes: bool, directory: str) -> None:
    result = run_init(Path(directory), yes=yes)
    click.echo(f"STATUS:{result.status}")


# role, name, root branch, "Add all as modules?", kind, features, platform,
# languages, frameworks, architecture — then "3" (no ticket backend, so no
# JIRA-site prompt) — then blanks for whatever this machine's sandbox and
# knowledge sections ask. Surplus blank lines are never read.
_ENTER_THROUGH = "\n".join([""] * 10 + ["3"] + [""] * 12) + "\n"


# --- detection --------------------------------------------------------------


def test_one_module_gives_its_branch(tmp_path: Path):
    _repo(tmp_path / "rite", "phase-2")
    assert detect_root_branch(tmp_path, detect_repos(tmp_path)) == "phase-2"


def test_modules_that_agree_give_the_shared_branch(tmp_path: Path):
    _repo(tmp_path / "api", "develop")
    _repo(tmp_path / "web", "develop")
    assert detect_root_branch(tmp_path, detect_repos(tmp_path)) == "develop"


def test_modules_that_disagree_give_no_answer(tmp_path: Path):
    _repo(tmp_path / "api", "main")
    _repo(tmp_path / "web", "phase-2")
    assert detect_root_branch(tmp_path, detect_repos(tmp_path)) is None


def test_nothing_detected_gives_no_answer(tmp_path: Path):
    assert detect_root_branch(tmp_path, detect_repos(tmp_path)) is None


def test_a_project_root_that_is_a_repo_uses_its_own_branch(tmp_path: Path):
    _repo(tmp_path, "trunk")
    _repo(tmp_path / "vendored", "phase-2")
    assert detect_root_branch(tmp_path, detect_repos(tmp_path)) == "trunk"


def test_an_enclosing_repo_is_not_the_projects_branch(tmp_path: Path):
    # `git -C <dir>` walks UP to the nearest repository, so asking git about a
    # project directory that merely sits inside some other checkout answers
    # with that checkout's branch. That is not this project's line.
    _repo(tmp_path, "outer-branch")
    project = tmp_path / "project"
    project.mkdir()
    assert detect_root_branch(project, detect_repos(project)) is None


# --- the prompt ---------------------------------------------------------------


def test_enter_through_keeps_root_branch_and_module_agreeing(tmp_path: Path):
    _repo(tmp_path / "rite", "phase-2")

    result = CliRunner().invoke(_init_cmd, [str(tmp_path)], input=_ENTER_THROUGH)
    assert result.exit_code == 0, result.output
    assert "Root branch? [phase-2]" in result.output

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    modules = yaml.safe_load((tmp_path / ".rite" / "modules.yaml").read_text())
    assert modules["modules"]["rite"]["branch"] == "phase-2"
    assert brief["project"]["root_branch"] == "phase-2"


def test_non_interactive_init_takes_the_detected_branch(tmp_path: Path):
    _repo(tmp_path / "rite", "phase-2")

    result = CliRunner().invoke(_init_cmd, ["--yes", str(tmp_path)])
    assert result.exit_code == 0, result.output

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["project"]["root_branch"] == "phase-2"


def test_disagreeing_modules_fall_back_to_main_and_say_why(tmp_path: Path):
    _repo(tmp_path / "api", "main")
    _repo(tmp_path / "web", "phase-2")

    result = CliRunner().invoke(_init_cmd, [str(tmp_path)], input=_ENTER_THROUGH)
    assert result.exit_code == 0, result.output
    assert "Root branch? [main]" in result.output
    assert "phase-2" in result.output.split("Root branch?")[0], (
        "the fallback to main must name the branches that disagreed, before asking"
    )

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["project"]["root_branch"] == "main"
