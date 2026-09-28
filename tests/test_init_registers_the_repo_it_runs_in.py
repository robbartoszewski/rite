"""`rite init` in a single repository registers that repository (dogfood F2).

Measured in the v0.6.0 dogfood: `rite init` inside `pingr`, an ordinary
repository with code, printed "languages, structure and conventions will be
taken from what's there" and wrote `modules: {}`. A Worker's workspace is its
modules' clones, so the Worker started on KAN-7 had no source, and no Worker
in that run did code work end to end.

Pre-registered test: after `rite init` in a repo with code, a module is
listed and a Worker has the repo checked out, without `rite add module`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.main import cli


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "rite-home"))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo_with_code(tmp_path: Path) -> tuple[Path, Path]:
    """`app`, one commit of code, pushed to a bare `origin` it was cloned from."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "trunk", str(remote))
    app = tmp_path / "app"
    _git(tmp_path, "clone", "-q", str(remote), str(app))
    (app / "main.py").write_text("print('hi')\n")
    _git(app, "add", "-A")
    _git(app, "commit", "-qm", "code")
    _git(app, "push", "-q", "origin", "HEAD:trunk")
    return app, remote


def _modules(root: Path) -> dict:
    return yaml.safe_load((root / ".rite" / "modules.yaml").read_text())["modules"]


def _init(app: Path, *args: str, input: str | None = None):
    result = CliRunner().invoke(cli, ["init", *args, str(app)], input=input)
    assert result.exit_code == 0, result.output
    return result


def test_the_existing_code_answer_registers_the_repo(tmp_path: Path, monkeypatch):
    app, remote = _repo_with_code(tmp_path)
    # spec or existing code? y · path [.] · changes Enter · role Enter
    _init(app, input="y\n\n\n\n")

    assert _modules(app) == {
        "app": {
            "path": "./",
            "url": str(remote),
            "branch": "trunk",
            "description": "",
        }
    }


def test_yes_registers_the_repo(tmp_path: Path):
    app, remote = _repo_with_code(tmp_path)
    _init(app, "--yes")

    assert list(_modules(app)) == ["app"]
    assert _modules(app)["app"]["url"] == str(remote)


def test_a_worker_then_has_the_code_checked_out(tmp_path: Path, monkeypatch):
    """The half of the pre-registered test that matters: the clone exists."""
    app, _ = _repo_with_code(tmp_path)
    _init(app, input="y\n\n\n\n")
    monkeypatch.chdir(app)

    added = CliRunner().invoke(cli, ["add", "worker", "alpha"])

    assert added.exit_code == 0, added.output
    clone = app / "workers" / "alpha" / "app"
    assert (clone / "main.py").read_text() == "print('hi')\n"
    assert _git(clone, "branch", "--show-current") == "trunk"


def test_status_lists_the_module(tmp_path: Path, monkeypatch):
    app, _ = _repo_with_code(tmp_path)
    _init(app, "--yes")
    monkeypatch.chdir(app)

    out = CliRunner().invoke(cli, ["status"]).output

    assert "no modules registered" not in out
    assert "app: ./" in out


def test_a_repo_with_nothing_committed_is_not_a_module(tmp_path: Path):
    """A fresh `git init` cannot be cloned, and is usually a workspace about to
    receive its modules."""
    root = tmp_path / "ws"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "main.py").write_text("x\n")
    _init(root, "--yes")

    assert not _modules(root)


def test_a_workspace_of_repos_registers_those_and_not_itself(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "README.md").write_text("ws\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "ws")
    api = root / "api"
    api.mkdir()
    _git(api, "init", "-q")
    (api / "a.py").write_text("x\n")
    _git(api, "add", "-A")
    _git(api, "commit", "-qm", "api")
    _init(root, input="y\n\n\n\n")

    assert {n: m["path"] for n, m in _modules(root).items()} == {"api": "api/"}


def test_a_source_subdirectory_is_registered_by_its_path(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    code = root / "code"
    code.mkdir()
    _git(code, "init", "-q")
    (code / "a.py").write_text("x\n")
    _git(code, "add", "-A")
    _git(code, "commit", "-qm", "c")
    _init(root, input="y\ncode\n\n\n")

    assert {n: m["path"] for n, m in _modules(root).items()} == {"code": "code/"}


# --- doctor -----------------------------------------------------------------------


def _set_sandbox(root: Path, enabled: bool) -> None:
    path = root / ".rite" / "config.yaml"
    config = yaml.safe_load(path.read_text())
    config.setdefault("sandbox", {})["enabled"] = enabled
    path.write_text(yaml.safe_dump(config, sort_keys=False))


@pytest.mark.parametrize("enabled", [True, False])
def test_doctor_on_the_root_module(tmp_path: Path, monkeypatch, enabled: bool):
    """Every clone of a single repository carries the project's `.rite/`.
    Sandboxed Workers are given RITE_PROJECT_ROOT, so that is not a problem
    for them; an unsandboxed session in the clone would use a private
    ledger, so it still is."""
    app, _ = _repo_with_code(tmp_path)
    _init(app, "--yes")
    _set_sandbox(app, enabled)
    monkeypatch.chdir(app)

    out = CliRunner().invoke(cli, ["doctor"]).output

    flagged = "module app: is itself a rite project" in out
    cleared = "module app: is the project itself" in out
    assert (flagged, cleared) == (not enabled, enabled), out
