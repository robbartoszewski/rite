"""`rite init`'s existing-code route asks for the code when the path is empty.

v0.7.0 dogfood S11, measured on 0.7.0a2: `rite init` in an empty
`~/projects/yoloAI`, "existing spec or code", path `.`. Init printed that
languages, structure and conventions "will be taken from what's there", asked
what in it was stale, wrote a brief with every field empty, registered no
module, and said "Ready" — a root with nothing for a Worker to work on,
reported as a normal init.

Robert's design: when the path holds no code and no spec, ask for a repository
and offer to add it as a module. Accepting registers and clones it the way
`rite add module` does; declining (or `--yes`, with nobody to ask) says what is
missing and how to add it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.main import cli

CLAIM = "will be taken\nfrom what's there"
ASKED = "Where is the code? A repository URL to add as a module"
NOTHING = "has no code and no spec in it"
NO_MODULE = (
    "No module would be registered, so this project would have nothing to work on"
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _a_repository(tmp_path: Path) -> Path:
    """A bare repository with one commit, standing in for the fork on GitHub."""
    bare = tmp_path / "remote" / "yoloai.git"
    bare.parent.mkdir()
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    work = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    (work / "main.go").write_text("package main\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "code")
    _git(work, "push", "-q", "origin", "HEAD:main")
    return bare


def _init(root: Path, *args: str, input: str | None = None) -> str:
    result = CliRunner().invoke(cli, ["init", *args, str(root)], input=input)
    assert result.exit_code == 0, result.output
    return result.output


def _modules(root: Path) -> dict:
    return yaml.safe_load((root / ".rite" / "modules.yaml").read_text())["modules"]


def test_an_empty_path_asks_for_the_code_and_adds_it(tmp_path: Path):
    repo = _a_repository(tmp_path)
    root = tmp_path / "yoloAI"
    root.mkdir()
    # existing code? y · path [.] · where is the code? <url> · add it? Enter ·
    # role Enter · declare a Worker? n (S20)
    out = _init(root, input=f"y\n\n{repo}\n\n\nn\n")

    assert NOTHING in out and ASKED in out
    assert CLAIM not in out, "it still claims to read code from an empty path"
    modules = _modules(root)
    assert list(modules) == ["yoloai"]
    assert modules["yoloai"]["url"] == str(repo)
    assert modules["yoloai"]["path"] == "yoloai/"
    # Set up as `rite add module` sets it up: cloned, on the remote's branch.
    assert (root / "yoloai" / ".git").is_dir()
    assert (root / "yoloai" / "main.go").is_file()
    assert modules["yoloai"]["branch"] == "main"
    # And carried into what init writes after it.
    assert "yoloai" in (root / "CLAUDE.md").read_text()


def test_declining_leaves_an_honest_empty_state(tmp_path: Path):
    root = tmp_path / "yoloAI"
    root.mkdir()
    # existing code? y · path [.] · where is the code? Enter · role · sandbox
    out = _init(root, input="y\n\n\n\n\n")

    assert ASKED in out
    assert _modules(root) == {}
    assert NO_MODULE in out
    assert "rite add module <name> <repository URL>" in out
    assert CLAIM not in out


def test_saying_no_to_the_offered_module_leaves_it_out(tmp_path: Path):
    repo = _a_repository(tmp_path)
    root = tmp_path / "yoloAI"
    root.mkdir()
    out = _init(root, input=f"y\n\n{repo}\nn\n\n\n")

    assert _modules(root) == {}
    assert not (root / "yoloai").exists()
    assert NO_MODULE in out


def test_with_nobody_to_ask_it_says_so_instead(tmp_path: Path):
    """`--yes` with a preset path: no prompt, the same honest statement."""
    root = tmp_path / "yoloAI"
    root.mkdir()
    preset = tmp_path / "preset.yaml"
    preset.write_text("source:\n  path: .\n")
    out = _init(root, "--yes", "--config", str(preset))

    assert ASKED not in out
    assert NOTHING in out and NO_MODULE in out
    assert _modules(root) == {}


def test_control_a_path_with_code_is_not_asked(tmp_path: Path):
    """A repository with code at the path registers it, as before, and asks
    nothing about where the code is."""
    root = tmp_path / "app"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "main.go").write_text("package main\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "code")
    # existing code? y · path [.] · changes Enter · add ./? Enter · role ·
    # declare a Worker? n (S20)
    out = _init(root, input="y\n\n\n\n\nn\n")

    assert ASKED not in out and NOTHING not in out
    assert CLAIM in out
    assert list(_modules(root)) == ["app"]


def test_a_sentence_is_not_taken_for_a_repository(tmp_path: Path):
    """Found by an older test typing its answer into this prompt: "The feed
    poller is stale" was offered as module 'The feed poller is stale'."""
    repo = _a_repository(tmp_path)
    root = tmp_path / "yoloAI"
    root.mkdir()
    out = _init(root, input=f"y\n\nThe feed poller is stale\n{repo}\n\n\nn\n")

    assert "'The feed poller is stale' is not a repository URL" in out
    assert "as module 'The feed poller is stale'" not in out
    assert list(_modules(root)) == ["yoloai"]


def test_what_counts_as_a_repository(tmp_path: Path):
    from rite_ai.cli.init.questionnaire import looks_like_a_repository

    assert looks_like_a_repository("https://github.com/robbartoszewski/yoloai.git")
    assert looks_like_a_repository("git@github.com:robbartoszewski/yoloai.git")
    assert looks_like_a_repository(str(_a_repository(tmp_path)))
    assert not looks_like_a_repository("The feed poller is stale")
    assert not looks_like_a_repository(str(tmp_path / "not-there"))


def test_the_module_name_comes_from_the_url():
    from rite_ai.cli.init.questionnaire import module_name_for

    assert module_name_for("https://github.com/robbartoszewski/yoloai.git") == "yoloai"
    assert module_name_for("git@github.com:robbartoszewski/yoloai.git") == "yoloai"
    assert module_name_for("https://example.com/org/repo/") == "repo"
