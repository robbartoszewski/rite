"""`rite init` inside a repository warns that it is becoming its own root.

Measured in the v0.7.0 dogfood: `rite init` inside `~/open-source/yoloai`, a
clone of a fork contributed from upstream, registered the clone as its own
module (`path: ./`), then rewrote its CLAUDE.md and `.gitignore` and added
`.rite/`, `.claude/` and a CI workflow, all as uncommitted changes in the
repository the contribution was to go out from. Nothing on screen said the
repository was doubling as the project root, or that a separate root was the
way to keep rite out of someone else's project.

The warning comes before the answer, while only an empty `.rite/` exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.main import cli

WARNING = "this repository is also becoming the project root"


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


def _repo(path: Path, origin: str | None = None) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "main.py").write_text("print('hi')\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "code")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    return path


def _init(root: Path, *args: str, input: str | None = None) -> str:
    result = CliRunner().invoke(cli, ["init", *args, str(root)], input=input)
    assert result.exit_code == 0, result.output
    return result.output


def test_interactive_init_warns_before_asking(tmp_path: Path):
    app = _repo(tmp_path / "app", "https://github.com/someone/app.git")
    # spec or existing code? y · path [.] · changes · role · add ./? Enter ·
    # declare a Worker? n (S20)
    out = _init(app, input="y\n\n\n\n\n\nn\n")

    assert WARNING in out
    # Before the question, so the answer can still be no.
    assert out.index(WARNING) < out.index("Add this directory (./) as module 'app'?")
    # What to do instead, with this repository's own name and URL.
    assert "mkdir ../app-rite && cd ../app-rite && rite init" in out
    assert "rite add module app https://github.com/someone/app.git" in out


def test_yes_warns_too(tmp_path: Path):
    """Nobody is there to ask, which makes the line more needed, not less."""
    app = _repo(tmp_path / "app")
    out = _init(app, "--yes")

    assert WARNING in out
    assert out.index(WARNING) < out.index("--yes: added this directory (./)")
    assert "rite add module app <url>" in out


def test_control_a_repository_inside_the_root_is_not_warned_about(tmp_path: Path):
    """A workspace that is not itself a repository, holding one: the root is
    not doubling as anything, so there is nothing to warn about."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _repo(ws / "api")
    out = _init(ws, "--yes")

    assert "--yes: added api/ as module 'api'" in out
    assert WARNING not in out
