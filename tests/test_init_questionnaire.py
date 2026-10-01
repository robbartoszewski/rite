"""End-to-end tests for `run_init` — the full seven-section flow, driven
through a throwaway click command (never through `rite_ai.cli.main`, which this
module does not own — see `src/rite_ai/cli/init/__init__.py`'s docstring for the
interface contract the CLI entry point wires up).
"""

from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.init import run_init


@pytest.fixture(autouse=True)
def _sandbox_branch_one(monkeypatch):
    """The answer sequences below are written for init's branch 1: yoloAI
    present and verified, so the sandbox question is asked once. Pinned,
    because the host decided it before: Linux skips the question, a Mac
    without yoloAI adds an install offer (which ran a real `brew install`
    on the first macOS CI run), and a Mac with yoloAI asked the real binary.
    The branches themselves are `test_init_sandbox_branches.py`'s."""
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: True)
    monkeypatch.setattr(sb, "is_installed", lambda: True)
    monkeypatch.setattr(sb, "choose_backend", lambda: sb.BackendChoice("seatbelt"))


@click.command()
@click.option("--config", "config", type=click.Path(exists=True))
@click.option("--yes", is_flag=True)
@click.argument("directory", default=".")
def _init_cmd(config: str | None, yes: bool, directory: str) -> None:
    result = run_init(
        Path(directory), config_path=Path(config) if config else None, yes=yes
    )
    click.echo(f"STATUS:{result.status}")


# All-defaults interactive answer sequence: no existing spec or code, name,
# root_branch, module name (blank = no repos found), kind, features,
# platform, languages, frameworks, architecture, ticket backend ("3" = None for
# now, to skip the extra JIRA-site prompt), sandbox, kb link, kb file, kb commit.
# … kb commit, then (S15) declare Manager 'lead'? Enter
# No role answer: C7 made init Owner-only, so that question is not put.
_ALL_BLANK = "n\n" + "\n".join([""] * 9 + ["3"] + [""] * 5) + "\n"


def test_interactive_all_defaults_creates_every_file(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path)], input=_ALL_BLANK)
    assert result.exit_code == 0, result.output
    assert "STATUS:created" in result.output

    rite_dir = tmp_path / ".rite"
    assert (rite_dir / "brief.yaml").exists()
    assert (rite_dir / "modules.yaml").exists()
    assert (rite_dir / "config.yaml").exists()
    assert (rite_dir / "context" / "INDEX.md").exists()
    assert (rite_dir / "kb" / "INDEX.md").exists()
    assert (rite_dir / "review-checklist.md").exists()
    assert (tmp_path / "CLAUDE.md").exists()
    for agent in [
        "reviewer-round1",
        "reviewer-terminating",
        "reviewer-seam",
        "reviewer-decisions",
    ]:
        assert (tmp_path / ".claude" / "agents" / f"{agent}.md").exists()
    for cmd in ["ticket", "review", "refine", "spec"]:
        assert (tmp_path / ".claude" / "commands" / f"{cmd}.md").exists()

    brief = yaml.safe_load((rite_dir / "brief.yaml").read_text())
    assert brief["project"]["name"] == tmp_path.name
    assert brief["project"]["role"] == "owner"
    assert brief["project"]["root_branch"] == "main"

    config = yaml.safe_load((rite_dir / "config.yaml").read_text())
    assert config["ticket_backend"]["type"] == "none"


def test_interactive_output_shows_progress_sections(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path)], input=_ALL_BLANK)
    assert "[1/7]" in result.output
    assert "[7/7]" in result.output
    # An empty directory, every question skipped: no module, so init says it
    # is not ready rather than "Ready." (S13, the --yes gap's interactive twin).
    assert "Initialised, but NOT ready for work: no module is registered" in (
        result.output
    )
    assert "Ready. Start a Dispatch session" not in result.output


def test_interactive_project_name_prompt_prefilled_with_dirname(tmp_path: Path):
    project_dir = tmp_path / "my-cool-app"
    project_dir.mkdir()
    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(project_dir)], input=_ALL_BLANK)
    assert f"[{project_dir.name}]" in result.output


def test_interactive_custom_answers_are_used(tmp_path: Path):
    answers = (
        "\n".join(
            [
                "n",  # no existing spec or code
                "myapp",  # project name
                "develop",  # root branch
                "",  # no module
                "backend",  # kind (typed value, not label)
                "An API for cases",  # features
                "",  # platform default
                "python, typescript",  # languages
                "fastify",  # frameworks
                "event sourcing",  # architecture
                "3",  # ticket backend: none
                "",  # sandbox: accept the default
                "",  # kb link
                "",  # kb file
                "",  # kb commit default yes
                "",  # declare Manager 'lead'? default yes (S15)
            ]
        )
        + "\n"
    )
    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path)], input=answers)
    assert result.exit_code == 0, result.output

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["project"]["name"] == "myapp"
    assert brief["project"]["root_branch"] == "develop"
    assert brief["what"]["kind"] == "backend"
    assert brief["what"]["features"] == "An API for cases"
    assert brief["technology"]["languages"] == ["python", "typescript"]
    assert brief["technology"]["frameworks"] == ["fastify"]
    assert brief["technology"]["architecture"] == "event sourcing"

    claude_md = (tmp_path / "CLAUDE.md").read_text()
    assert "myapp" in claude_md
    assert "event sourcing" in claude_md


def test_interactive_detects_and_adds_repos(tmp_path: Path):
    import subprocess

    backend = tmp_path / "backend"
    backend.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=backend, check=True)

    answers = (
        "\n".join(
            [
                "n",  # no existing spec or code
                "",  # role
                "",  # name
                "",  # root branch
                "",  # "Add all as modules?" -> default Yes
                "",  # kind
                "",  # features
                "",  # platform
                "",  # languages
                "",  # frameworks
                "",  # architecture
                "3",  # ticket backend
                "",  # sandbox: accept the default
                "",  # kb link
                "",  # kb file
                "",  # kb commit
                "",  # declare Manager 'lead'? default yes (S15)
                "n",  # declare a Worker? (S20)
            ]
        )
        + "\n"
    )
    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path)], input=answers)
    assert result.exit_code == 0, result.output

    modules = yaml.safe_load((tmp_path / ".rite" / "modules.yaml").read_text())
    assert "backend" in modules["modules"]


def test_interactive_reinit_declined_leaves_project_untouched(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text("project:\n  name: original\n")

    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path)], input="n\nn\n")
    assert result.exit_code == 0
    assert "STATUS:already_initialized" in result.output
    assert (rite_dir / "brief.yaml").read_text() == "project:\n  name: original\n"


def test_interactive_reinit_confirmed_wipes_and_recreates(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text("project:\n  name: original\n")

    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path)], input="n\ny\n" + _ALL_BLANK[2:])
    assert result.exit_code == 0, result.output
    assert "STATUS:created" in result.output
    brief = yaml.safe_load((rite_dir / "brief.yaml").read_text())
    assert brief["project"]["name"] == tmp_path.name


def test_non_interactive_yes_never_prompts(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert "STATUS:created" in result.output
    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["project"]["name"] == tmp_path.name
    assert brief["project"]["role"] == "owner"
    config = yaml.safe_load((tmp_path / ".rite" / "config.yaml").read_text())
    assert config["ticket_backend"]["type"] == "none"


def test_non_interactive_yes_on_existing_rite_is_a_noop(tmp_path: Path):
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text("project:\n  name: original\n")

    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path), "--yes"])
    assert result.exit_code == 0
    assert "STATUS:already_initialized" in result.output
    assert "original" in (rite_dir / "brief.yaml").read_text()


def test_config_file_and_yes_fully_automated(tmp_path: Path):
    config_file = tmp_path / "preset.yaml"
    config_file.write_text(
        """\
project:
  name: from-config
  role: owner
  root_branch: trunk
what:
  kind: library
  features: "A shared utility library"
technology:
  languages: [rust]
  architecture: "hexagonal"
operations:
  ticket_backend: github
knowledge:
  commit: false
"""
    )
    runner = CliRunner()
    result = runner.invoke(
        _init_cmd, [str(tmp_path), "--config", str(config_file), "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "STATUS:created" in result.output

    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["project"]["name"] == "from-config"
    assert brief["project"]["root_branch"] == "trunk"
    assert brief["what"]["kind"] == "library"
    assert brief["technology"]["languages"] == ["rust"]
    assert brief["technology"]["architecture"] == "hexagonal"

    config = yaml.safe_load((tmp_path / ".rite" / "config.yaml").read_text())
    assert config["ticket_backend"]["type"] == "github"


def test_config_file_with_explicit_modules_skips_detection(tmp_path: Path):
    config_file = tmp_path / "preset.yaml"
    config_file.write_text(
        """\
project:
  name: p
modules:
  backend:
    path: backend/
    url: git@example.com:org/backend.git
    branch: main
    description: API server
"""
    )
    runner = CliRunner()
    result = runner.invoke(
        _init_cmd, [str(tmp_path), "--config", str(config_file), "--yes"]
    )
    assert result.exit_code == 0, result.output

    modules = yaml.safe_load((tmp_path / ".rite" / "modules.yaml").read_text())
    assert modules["modules"]["backend"]["url"] == "git@example.com:org/backend.git"
    assert modules["modules"]["backend"]["description"] == "API server"


def test_config_file_not_found_is_a_clean_error(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        _init_cmd, [str(tmp_path), "--config", str(tmp_path / "missing.yaml")]
    )
    # click itself rejects a non-existent --config path (type=click.Path(exists=True))
    assert result.exit_code != 0


def test_the_role_is_owner_and_no_owner_ref_is_asked_for(tmp_path: Path):
    """C7: a Manager MACHINE needs multi-manager (0.9.0), so neither the role
    nor the Owner's-project question that followed it is put. The Manager
    branch and `_borrow_owner_config` are kept, unreferenced, for the restore
    — `test_init_is_owner_only_until_multi_manager.py` asserts both, and
    rehearses the restore by flipping the constant."""
    runner = CliRunner()
    result = runner.invoke(_init_cmd, [str(tmp_path)], input=_ALL_BLANK)
    assert result.exit_code == 0, result.output

    assert "Owner's project URL or config path?" not in result.output
    brief = yaml.safe_load((tmp_path / ".rite" / "brief.yaml").read_text())
    assert brief["project"]["role"] == "owner"
    assert "Role: Owner" in (tmp_path / "CLAUDE.md").read_text()
