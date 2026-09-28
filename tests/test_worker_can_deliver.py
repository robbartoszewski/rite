"""A Worker is started only when its work could leave the sandbox.

Measured in the v0.6.0 dogfood: the Worker started on KAN-7 had no module,
then no GitHub token (`fatal: could not read Username for
'https://github.com'`), and asked the user to drop a PAT into its files
directory. `rite doctor` had printed `github_token: not set` and counted
nothing (#28). Every one of those states is now refused by `rite sandbox
start` before a sandbox is spent, and the token state is a doctor problem.

What is real here: git, the clones, their origins, and `git ls-remote
--get-url` applying the sandbox's rewrite. What is not: yoloAI, and GitHub
itself — `push_access_refusal` is exercised with `urlopen` replaced, and was
measured against GitHub separately (see its docstring).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.sandbox import (
    CloneRemote,
    clone_remotes,
    push_access_refusal,
    remote_access_refusal,
    sandbox_git_environment,
)

GH = "/opt/homebrew/bin/gh"


def _git(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def _clone_with_origin(workdir: Path, name: str, origin: str) -> Path:
    clone = workdir / name
    clone.mkdir(parents=True)
    _git(clone, "init", "-q")
    (clone / "f").write_text("x\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "x")
    _git(clone, "remote", "add", "origin", origin)
    return clone


# --- where a clone pushes -----------------------------------------------------------


def test_clone_remotes_reads_each_clones_own_origin(tmp_path: Path):
    _clone_with_origin(tmp_path, "a", "git@github.com:acme/a.git")
    _clone_with_origin(tmp_path, "b", "https://github.com/acme/b")
    _clone_with_origin(tmp_path, "c", "https://gitlab.com/acme/c.git")
    _clone_with_origin(tmp_path, "d", str(tmp_path / "somewhere"))
    _clone_with_origin(tmp_path, "e", f"file://{tmp_path}/elsewhere")

    found = {r.clone.name: r.github for r in clone_remotes(tmp_path)}

    assert found == {"a": ("acme", "a"), "b": ("acme", "b"), "c": None}


# --- refused before starting, without asking GitHub ---------------------------------


def _remote(name: str, url: str, github) -> CloneRemote:
    return CloneRemote(Path(name), url, github)


def test_no_token_is_refused_and_names_the_command():
    remotes = [_remote("app", "https://github.com/acme/app", ("acme", "app"))]
    refusal = remote_access_refusal("alpha", remotes, None, GH)
    assert refusal is not None
    assert "no GitHub token" in refusal
    assert "acme/app" in refusal
    assert "rite credential set github_token" in refusal
    assert "\n" not in refusal, "the supervisor relays one line"


def test_no_gh_is_refused():
    remotes = [_remote("app", "https://github.com/acme/app", ("acme", "app"))]
    refusal = remote_access_refusal("alpha", remotes, "tok", None)
    assert refusal is not None and "gh" in refusal


def test_a_remote_rite_has_no_credential_for_is_refused():
    remotes = [_remote("app", "https://gitlab.com/acme/app.git", None)]
    refusal = remote_access_refusal("alpha", remotes, "tok", GH)
    assert refusal is not None and "github.com only" in refusal


def test_local_origins_only_need_nothing():
    assert remote_access_refusal("alpha", [], None, None) is None


# --- the push check -----------------------------------------------------------------


def _remotes() -> list[CloneRemote]:
    return [_remote("app", "git@github.com:acme/app.git", ("acme", "app"))]


class _Answer:
    """What `urlopen` returns, as a context manager."""

    def __init__(self, status: int, content_type: str) -> None:
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int):
    import urllib.error

    return urllib.error.HTTPError("u", code, "no", {}, None)


def test_the_push_check_asks_what_a_push_asks_with_the_workers_token():
    """The question is receive-pack's, asked with a GET, so nothing can be
    written; and it goes to HTTPS whatever the origin says, because that is
    what the sandbox's rewrite makes it."""
    import base64

    seen = {}

    def urlopen(request, timeout):
        seen["request"] = request
        return _Answer(200, "application/x-git-receive-pack-advertisement")

    with patch("urllib.request.urlopen", side_effect=urlopen):
        assert push_access_refusal("alpha", _remotes(), "workers-token") is None

    request = seen["request"]
    assert request.get_method() == "GET"
    assert request.full_url == (
        "https://github.com/acme/app.git/info/refs?service=git-receive-pack"
    )
    sent = request.get_header("Authorization").removeprefix("Basic ")
    assert base64.b64decode(sent).decode().endswith(":workers-token")


@pytest.mark.parametrize(
    "code, why",
    [
        (401, "does not accept the token"),
        (403, "can read it but not write it"),
        (404, "does not exist"),
    ],
)
def test_a_token_that_cannot_push_is_refused(code: int, why: str):
    with patch("urllib.request.urlopen", side_effect=_http_error(code)):
        refusal = push_access_refusal("alpha", _remotes(), "tok")
    assert refusal is not None
    assert "cannot push to acme/app" in refusal and why in refusal


def test_a_200_that_is_not_receive_pack_is_not_permission():
    """A proxy or captive portal answering 200 with a page is not GitHub
    saying yes."""
    with patch("urllib.request.urlopen", return_value=_Answer(200, "text/html")):
        assert push_access_refusal("alpha", _remotes(), "tok") is not None


def test_a_check_that_cannot_finish_refuses():
    """ "Could not ask" is not "allowed"."""
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        refusal = push_access_refusal("alpha", _remotes(), "tok")
    assert refusal is not None and "refused rather than trusted" in refusal


# --- inside the sandbox, an SSH origin goes to HTTPS ---------------------------------


@pytest.mark.parametrize(
    "origin", ["git@github.com:acme/app.git", "ssh://git@github.com/acme/app.git"]
)
def test_an_ssh_origin_is_fetched_over_https_inside(tmp_path: Path, origin: str):
    """Measured: inside the sandbox ssh cannot read ~/.ssh/known_hosts, so an
    SSH origin fails before any key is tried. Real git resolves the URL."""
    clone = _clone_with_origin(tmp_path, "app", origin)
    env = {**os.environ, **sandbox_git_environment(GH)}

    url = _git(clone, "ls-remote", "--get-url", "origin", env=env)

    assert url == "https://github.com/acme/app.git"


def test_control_without_the_sandbox_environment_it_stays_ssh(tmp_path: Path):
    clone = _clone_with_origin(tmp_path, "app", "git@github.com:acme/app.git")
    assert _git(clone, "ls-remote", "--get-url", "origin").startswith("git@")


# --- `rite sandbox start` -----------------------------------------------------------


def _project(tmp_path: Path, monkeypatch, *, modules: bool, origin: str) -> None:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text(
        f"modules:\n  app:\n    path: app/\n    url: {origin}\n    branch: main\n"
        if modules
        else "modules: {}\n"
    )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
        "  backend: seatbelt\n"
    )
    worker = tmp_path / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n"
        f"  modules: {'[app]' if modules else '[]'}\n"
    )
    if modules:
        _clone_with_origin(worker, "app", origin)
    monkeypatch.chdir(tmp_path)


def _start(*extra: str):
    """`rite sandbox start`, failing the test if a sandbox is ever created."""
    with patch(
        "rite_ai.sandbox.start_worker",
        side_effect=AssertionError("a sandbox was started"),
    ):
        return CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--allow-dirty", *extra]
        )


def test_start_refuses_a_worker_with_no_module(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, modules=False, origin="")
    result = _start("--ticket", "KAN-7")
    assert result.exit_code == 1, result.output
    assert "no code to work on" in result.output.strip().splitlines()[-1]


def test_start_refuses_a_github_worker_with_no_token(tmp_path, monkeypatch):
    """The KAN-7 state, reproduced: a real clone of a GitHub repo, no token."""
    _project(
        tmp_path, monkeypatch, modules=True, origin="https://github.com/acme/app.git"
    )
    monkeypatch.delenv("RITE_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = _start("--ticket", "KAN-7")
    assert result.exit_code == 1, result.output
    last = result.output.strip().splitlines()[-1]
    assert "no GitHub token" in last and "acme/app" in last


def test_start_asks_github_before_starting(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, modules=True, origin="git@github.com:acme/app.git")
    monkeypatch.setenv("RITE_GITHUB_TOKEN", "tok")
    with patch(
        "rite_ai.sandbox.push_access_refusal",
        return_value="not starting 'alpha': its GitHub token cannot push",
    ) as check:
        result = _start("--ticket", "KAN-7")
    assert result.exit_code == 1, result.output
    assert check.call_args[0][2] == "tok"
    assert "cannot push" in result.output.strip().splitlines()[-1]


def test_start_proceeds_when_the_token_can_push(tmp_path, monkeypatch):
    from rite_ai.sandbox import SandboxResult

    _project(tmp_path, monkeypatch, modules=True, origin="git@github.com:acme/app.git")
    monkeypatch.setenv("RITE_GITHUB_TOKEN", "tok")
    with (
        patch("rite_ai.sandbox.push_access_refusal", return_value=None),
        patch(
            "rite_ai.sandbox.start_worker",
            return_value=SandboxResult(True, "sandbox started"),
        ) as start,
    ):
        result = CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--allow-dirty", "--ticket", "KAN-7"]
        )
    assert result.exit_code == 0, result.output
    start.assert_called_once()


# --- doctor (#28) -------------------------------------------------------------------


def test_doctor_counts_a_missing_worker_token_as_a_problem(tmp_path, monkeypatch):
    _project(
        tmp_path, monkeypatch, modules=True, origin="https://github.com/acme/app.git"
    )
    monkeypatch.delenv("RITE_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    out = CliRunner().invoke(cli, ["doctor"]).output
    assert "workers: no GitHub token for alpha" in out, out


def test_doctor_is_quiet_about_it_once_there_is_one(tmp_path, monkeypatch):
    _project(
        tmp_path, monkeypatch, modules=True, origin="https://github.com/acme/app.git"
    )
    monkeypatch.setenv("RITE_GITHUB_TOKEN", "tok")
    out = CliRunner().invoke(cli, ["doctor"]).output
    assert "workers: no GitHub token" not in out, out
