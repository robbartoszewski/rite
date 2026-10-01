"""`rite init` says when the code it was pointed at is not a git repository.

Found assessing the 0.7.0a2 init beside dogfood S11: the existing-code route
on a directory holding code that is not a git repository registered no module,
and init printed "Ready" with no word that a Worker would have nothing to
clone. Its only warnings were about the pre-push hook and CI. A Worker's
workspace is its modules' clones, so this is the same dead end as S11's empty
path, with the code right there.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.main import cli

SAID = "has files in it but is not a git repository, so no module is registered"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)


def _init(root: Path, *args: str, input: str | None = None) -> str:
    result = CliRunner().invoke(cli, ["init", *args, str(root)], input=input)
    assert result.exit_code == 0, result.output
    return result.output


def _modules(root: Path) -> dict:
    return yaml.safe_load((root / ".rite" / "modules.yaml").read_text())["modules"]


def _code(root: Path) -> Path:
    root.mkdir()
    (root / "main.go").write_text("package main\n")
    return root


def test_the_existing_code_route_says_so(tmp_path: Path):
    root = _code(tmp_path / "app")
    # existing code? y · path [.] · changes Enter · role · sandbox
    out = _init(root, input="y\n\n\n\n\n\n")

    assert SAID in out
    assert "rite add module <name> <repository URL>" in out
    assert _modules(root) == {}


def test_the_from_scratch_route_says_so_too(tmp_path: Path):
    root = _code(tmp_path / "app")
    out = _init(root, "--yes")

    assert SAID in out
    assert _modules(root) == {}


def test_control_the_same_code_in_a_repository_is_registered(tmp_path: Path):
    root = _code(tmp_path / "app")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "c"]):
        subprocess.run(
            ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )
    # … changes · role · add ./? Enter · declare a Worker? n (S20)
    out = _init(root, input="y\n\n\n\n\n\nn\n")

    assert SAID not in out
    assert list(_modules(root)) == ["app"]


def test_control_an_empty_directory_is_not_this(tmp_path: Path):
    """Nothing there is a different finding (S11), not "files but no repo"."""
    root = tmp_path / "empty"
    root.mkdir()
    out = _init(root, "--yes")

    assert SAID not in out


def test_control_a_directory_inside_a_repository_is_not_said_to_be_outside_one(
    tmp_path: Path,
):
    """A subdirectory of a repository is IN one: "not a git repository" would
    be false there, so it is not said. (It registers no module of its own,
    which is a different question.)"""
    parent = tmp_path / "mono"
    parent.mkdir()
    subprocess.run(["git", "init", "-q", str(parent)], check=True)
    root = _code(parent / "service")
    out = _init(root, "--yes")

    assert SAID not in out
