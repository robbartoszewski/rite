"""Kind, description and frameworks are offered from the project's manifests.

`init` asked all three cold — Full-stack, blank and blank — while the answers
sat in manifests it already opens: the module-command detector reads
`pyproject.toml` to look for pytest and discards the rest. A Python package
that installs a command and has no frontend was offered Full-stack, and
pressing Enter accepted it.
"""

import json
from pathlib import Path

import click
import yaml
from click.testing import CliRunner

from rite_ai.cli.init import run_init
from rite_ai.cli.init.detect import detect_description, detect_frameworks, detect_kind

_PACKAGE_WITH_A_COMMAND = """\
[project]
name = "rite-ai"
description = "Multi-session Claude coordination for teams"
dependencies = ["click>=8.1", "httpx>=0.28.1", "pyyaml>=6.0.3"]

[project.scripts]
rite = "rite_ai.cli.main:cli"

[dependency-groups]
dev = ["pytest>=9.1.1", "ruff>=0.16.6"]
"""


def _pyproject(dir_path: Path, text: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "pyproject.toml").write_text(text)


def _package_json(dir_path: Path, data: dict) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "package.json").write_text(json.dumps(data))


@click.command()
@click.option("--yes", is_flag=True)
@click.argument("directory")
def _init_cmd(yes: bool, directory: str) -> None:
    result = run_init(Path(directory), yes=yes)
    click.echo(f"STATUS:{result.status}")


# role, name, root branch, module name (blank: no git repos here), kind,
# features, platform, languages, frameworks, architecture — then "3" (no ticket
# backend) — then blanks for this machine's sandbox and knowledge prompts.
# Surplus blank lines are never read.
_ENTER_THROUGH = "\n".join([""] * 10 + ["3"] + [""] * 12) + "\n"


# --- detection --------------------------------------------------------------


def test_a_package_that_installs_a_command_is_a_library(tmp_path: Path):
    _pyproject(tmp_path / "rite", _PACKAGE_WITH_A_COMMAND)
    assert detect_kind(tmp_path) == "library"


def test_the_description_comes_from_the_manifest(tmp_path: Path):
    _pyproject(tmp_path / "rite", _PACKAGE_WITH_A_COMMAND)
    assert detect_description(tmp_path) == "Multi-session Claude coordination for teams"


def test_frameworks_are_named_not_every_dependency(tmp_path: Path):
    # click and pytest are frameworks the project is built and tested on;
    # httpx, pyyaml and ruff are libraries and tools it uses.
    _pyproject(tmp_path / "rite", _PACKAGE_WITH_A_COMMAND)
    assert detect_frameworks(tmp_path) == ["click", "pytest"]


def test_a_frontend_and_a_web_framework_is_full_stack(tmp_path: Path):
    _package_json(
        tmp_path / "app",
        {
            "dependencies": {"react": "^18", "express": "^4"},
            "devDependencies": {"jest": "^29"},
        },
    )
    assert detect_kind(tmp_path) == "full-stack"
    assert detect_frameworks(tmp_path) == ["react", "express", "jest"]


def test_a_frontend_alone_is_frontend(tmp_path: Path):
    _package_json(tmp_path / "web", {"dependencies": {"vue": "^3"}})
    assert detect_kind(tmp_path) == "frontend"


def test_a_web_framework_alone_is_backend(tmp_path: Path):
    _pyproject(
        tmp_path / "api", '[project]\nname = "api"\ndependencies = ["fastapi"]\n'
    )
    assert detect_kind(tmp_path) == "backend"


def test_a_flutter_project_is_mobile(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pubspec.yaml").write_text("name: app\n")
    assert detect_kind(tmp_path) == "mobile"


def test_a_command_beside_a_frontend_is_not_a_library(tmp_path: Path):
    _pyproject(tmp_path / "cli", _PACKAGE_WITH_A_COMMAND)
    _package_json(tmp_path / "web", {"dependencies": {"react": "^18"}})
    assert detect_kind(tmp_path) != "library"


def test_nothing_to_read_gives_no_answers(tmp_path: Path):
    assert detect_kind(tmp_path) is None
    assert detect_description(tmp_path) is None
    assert detect_frameworks(tmp_path) == []


def test_descriptions_that_disagree_give_no_answer(tmp_path: Path):
    _pyproject(tmp_path / "a", '[project]\nname = "a"\ndescription = "One thing"\n')
    _pyproject(tmp_path / "b", '[project]\nname = "b"\ndescription = "Another"\n')
    assert detect_description(tmp_path) is None


def test_a_broken_manifest_is_skipped_not_fatal(tmp_path: Path):
    _pyproject(tmp_path / "rite", "[project\nthis is not toml")
    assert detect_kind(tmp_path) is None
    assert detect_frameworks(tmp_path) == []


# --- the prompts --------------------------------------------------------------


def test_enter_through_accepts_what_the_manifests_say(tmp_path: Path):
    _pyproject(tmp_path / "rite", _PACKAGE_WITH_A_COMMAND)

    result = CliRunner().invoke(_init_cmd, [str(tmp_path)], input=_ENTER_THROUGH)
    assert result.exit_code == 0, result.output
    assert "[Multi-session Claude coordination for teams]" in result.output
    assert "Frameworks? [click, pytest]" in result.output

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["what"]["kind"] == "library"
    assert brief["what"]["features"] == "Multi-session Claude coordination for teams"
    assert brief["technology"]["frameworks"] == ["click", "pytest"]


def test_non_interactive_init_takes_the_manifest_answers(tmp_path: Path):
    _pyproject(tmp_path / "rite", _PACKAGE_WITH_A_COMMAND)

    result = CliRunner().invoke(_init_cmd, ["--yes", str(tmp_path)])
    assert result.exit_code == 0, result.output

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["what"]["kind"] == "library"
    assert brief["what"]["features"] == "Multi-session Claude coordination for teams"
    assert brief["technology"]["frameworks"] == ["click", "pytest"]


def test_nothing_detected_keeps_the_previous_defaults(tmp_path: Path):
    result = CliRunner().invoke(_init_cmd, ["--yes", str(tmp_path)])
    assert result.exit_code == 0, result.output

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["what"]["kind"] == "full-stack"
    assert brief["what"]["features"] == ""
    assert brief["technology"]["frameworks"] == []
