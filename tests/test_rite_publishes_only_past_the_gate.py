"""`push` and `pull_request`: rite sends the work, only past its publish gate.

The properties (SPEC §5.1.1, PB1 piece 4):
- nothing reaches a remote unless rite's publish gate passed on exactly the
  commits being sent, and a gate that could not run is not a pass;
- no force: a module branch that moved is refused by the remote, and the
  work is still on the project's branch, and the note says so;
- a pull request is opened, or the open one reported, and never merged.

Real: git, a bare origin, the collect step, and `gh` as a subprocess (a
fake binary on PATH, which records what it was asked). Not real: yoloAI
(as in the collect tests) and, where named, the gate's verdict.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from rite_ai.gate.gate import EXIT_CLEAN, EXIT_ERROR, EXIT_FAIL
from rite_ai.publishing.deliver import _open_pull_request, _pull_request_target_refusal
from tests.test_rite_delivers_a_finished_task import TICKET, Project, _commit, _git


class _Report:
    def __init__(self, code: int):
        self.exit_code = code
        # What a refused delivery's note now quotes (SCRUM-59, `gate.brief`).
        self.errors: list[str] = []
        self.findings: list = []


def _gate(code: int):
    return patch("rite_ai.gate.gate.run_gate", return_value=_Report(code))


def _origin_main(p: Project) -> str:
    return _git(p.origin, "rev-parse", "refs/heads/main")


def test_push_lands_the_work_on_the_modules_branch(tmp_path):
    p = Project(tmp_path, "push")
    p.start()
    shas = p.work()
    with _gate(EXIT_CLEAN) as gate:
        got = p.deliver()
    assert got.ok, got
    assert _origin_main(p) == shas[-1]
    # Exactly the commits being sent, and the PROJECT's rules over the
    # module repository — `root` holds `.rite/`, `project` is the repo
    # beneath it, and one path for both either scans a tree git cannot
    # answer about (SCRUM-60) or drops the project's suppressions.
    assert gate.call_args.kwargs == {
        # Full ref names (SCRUM-59): no tag named like the ticket is gated.
        "rev_range": f"refs/heads/main..refs/heads/{TICKET}",
        "config_root": p.root,
    }
    assert gate.call_args.args[0] != p.root
    assert "pushed to main" in got.outcomes[0].note()


@pytest.mark.parametrize("code", [EXIT_FAIL, EXIT_ERROR])
def test_nothing_leaves_when_the_gate_fails_or_cannot_run(tmp_path, code):
    p = Project(tmp_path, "push")
    p.start()
    p.work()
    before = _origin_main(p)
    with _gate(code):
        got = p.deliver()
    assert not got.ok
    assert _origin_main(p) == before
    note = got.outcomes[0].note()
    assert f"publish gate did not pass (exit {code})" in note
    assert f"committed locally on {TICKET}" in note  # nothing lost
    assert "rite publish check --rev-range main.." in note  # its fix
    assert not p.destroyed


def test_a_moved_branch_is_refused_not_forced(tmp_path):
    p = Project(tmp_path, "push")
    p.start()
    p.work()
    # Someone else lands on main after the Worker branched.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(p.origin), str(other)], check=True)
    moved = _commit(other, "x.txt", "x\n", "elsewhere")
    _git(other, "push", "-q", "origin", "HEAD:main")
    with _gate(EXIT_CLEAN):
        got = p.deliver()
    assert not got.ok
    assert _origin_main(p) == moved  # untouched
    note = got.outcomes[0].note()
    assert "origin refused the push to main" in note
    assert "update" in note and p.branch(TICKET) is not None


def test_a_pull_request_needs_a_github_origin_and_says_so(tmp_path):
    p = Project(tmp_path, "pull_request")
    p.start()
    shas = p.work()
    with _gate(EXIT_CLEAN):
        got = p.deliver()
    assert not got.ok
    # The branch was pushed (the PR step is what is refused), and said.
    assert _git(p.origin, "rev-parse", f"refs/heads/{TICKET}") == shas[-1]
    assert "not on GitHub" in got.outcomes[0].note()


def _fake_gh(tmp_path: Path, open_prs: str = "[]") -> Path:
    """A `gh` that records its arguments and answers like the real one."""
    log = tmp_path / "gh.log"
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'case "$2" in\n'
        f"  list) echo '{open_prs}' ;;\n"
        "  create) echo https://github.com/acme/svc/pull/7 ;;\n"
        "esac\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return log


def _pr(tmp_path: Path, p: Project, log_prs: str = "[]"):
    from rite_ai.config.models import Module

    log = _fake_gh(tmp_path, log_prs)
    env = dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    module = Module(name="svc", path="svc/", url="git@github.com:acme/svc.git")
    _git(p.svc, "branch", TICKET)
    with patch.dict(os.environ, {"PATH": env["PATH"]}):
        got = _open_pull_request(p.svc, module, TICKET, TICKET, env, "home")
    return got, log.read_text()


def test_a_pull_request_is_opened_against_the_modules_branch(tmp_path):
    p = Project(tmp_path, "pull_request")
    got, calls = _pr(tmp_path, p)
    assert got.ok and "PR https://github.com/acme/svc/pull/7" in got.note()
    create = [c for c in calls.splitlines() if c.startswith("pr create")]
    assert create and "--repo acme/svc --base main --head KAN-8" in create[0]
    assert "--draft" in create[0].split()  # a draft, always (Robert, 2026-09-29)
    assert "--body-file" in create[0]
    assert "merge" not in calls


def test_an_open_pull_request_is_reported_not_duplicated(tmp_path):
    p = Project(tmp_path, "pull_request")
    got, calls = _pr(
        tmp_path, p, '[{"number":3,"url":"https://github.com/acme/svc/pull/3"}]'
    )
    assert got.ok and "pull/3" in got.note()
    assert "pr create" not in calls


def test_every_worker_is_told_never_to_push(tmp_path):
    from rite_ai.config.models import Module, ProjectConfig, PublishConfig
    from rite_ai.publishing.instructions import for_worker

    for strategy in ("commit", "push", "pull_request"):
        text = for_worker(
            ProjectConfig(publish=PublishConfig(strategy=strategy)),
            [Module(name="svc", path="svc/")],
            TICKET,
        )
        assert "Do not push, open a pull request or merge" in text, strategy
        assert "git push" not in text, strategy


needs_gitleaks = pytest.mark.skipif(
    __import__("shutil").which("gitleaks") is None,
    reason="the real publish gate needs gitleaks; CI installs it",
)


@needs_gitleaks
def test_the_real_gate_stops_a_secret_and_passes_clean_work(tmp_path):
    """The gate unpatched: the floor is rite's actual scanner, on exactly the
    commits being sent."""
    p = Project(tmp_path, "push")
    p.start()
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    _commit(p.clone, "config.py", f'TOKEN = "{token}"\n', "add config")
    before = _origin_main(p)
    got = p.deliver()
    assert not got.ok, got
    assert _origin_main(p) == before
    assert "publish gate did not pass" in got.outcomes[0].note()

    clean = Project(tmp_path / "clean", "push")
    clean.start()
    shas = clean.work()
    assert clean.deliver().ok
    assert _origin_main(clean) == shas[-1]


# --- Where rite may open a pull request (Robert, 2026-09-29) -----------------
#
# A draft, on a repository the operator owns, against its default branch: a
# property of rite's own PR code, checked BEFORE the push. The owner is the
# owner of the token rite pushes with, asked of GitHub; anything rite cannot
# establish refuses.


def _gh_api(tmp_path: Path, login: str | None, default: str | None) -> dict:
    """A `gh` answering `api user` and `api repos/…` as given; None = fails."""
    gh = tmp_path / "ghbin" / "gh"
    gh.parent.mkdir(exist_ok=True)
    user = f"echo {login}" if login else "echo 'HTTP 401' >&2; exit 1"
    repo = f"echo {default}" if default else "echo 'HTTP 404' >&2; exit 1"
    gh.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        f"  'api user') {user} ;;\n"
        f"  api\\ repos/*) {repo} ;;\n"
        "  *) exit 3 ;;\n"
        "esac\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return dict(os.environ, PATH=f"{gh.parent}:{os.environ['PATH']}")


def _clone_with_origin(tmp_path: Path, origin: str) -> Path:
    clone = tmp_path / "svc"
    clone.mkdir()
    _git(clone, "init", "-q")
    _git(clone, "remote", "add", "origin", origin)
    return clone


def _module(url: str, branch: str = "main"):
    from rite_ai.config.models import Module

    return Module(name="svc", path="svc/", url=url, branch=branch)


def test_the_operators_own_fork_against_its_default_branch_is_allowed(tmp_path):
    clone = _clone_with_origin(tmp_path, "https://github.com/me/svc.git")
    env = _gh_api(tmp_path, "me", "main")
    assert (
        _pull_request_target_refusal(clone, _module("git@github.com:me/svc.git"), env)
        is None
    )


@pytest.mark.parametrize(
    "origin, url, why",
    [
        (
            "https://github.com/up/svc.git",
            "https://github.com/me/svc.git",
            "up/svc is not me's",
        ),
        (
            "https://github.com/me/svc.git",
            "https://github.com/up/svc.git",
            "up/svc is not me's",
        ),
        (
            "https://github.com/up/svc.git",
            "https://github.com/up/svc.git",
            "up/svc is not me's",
        ),
    ],
    ids=["pushed-upstream", "pr-upstream", "both-upstream"],
)
def test_a_repository_the_operator_does_not_own_is_refused(tmp_path, origin, url, why):
    clone = _clone_with_origin(tmp_path, origin)
    env = _gh_api(tmp_path, "me", "main")
    got = _pull_request_target_refusal(clone, _module(url), env)
    assert got is not None and why in got[0], got
    assert "your fork" in got[1]


@pytest.mark.parametrize(
    "login, default, branch, why",
    [
        (None, "main", "main", "could not establish whose GitHub token"),
        ("me", None, "main", "could not read me/svc's default branch"),
        ("me", "main", "develop", "not me/svc's default branch main"),
    ],
    ids=["owner-unreadable", "default-unreadable", "not-the-default-branch"],
)
def test_what_rite_cannot_establish_refuses(tmp_path, login, default, branch, why):
    clone = _clone_with_origin(tmp_path, "https://github.com/me/svc.git")
    env = _gh_api(tmp_path, login, default)
    got = _pull_request_target_refusal(
        clone, _module("https://github.com/me/svc.git", branch), env
    )
    assert got is not None and why in got[0], got


def test_a_refused_target_is_refused_before_anything_is_pushed(tmp_path, monkeypatch):
    """End to end: a GitHub module whose origin rite cannot place (here a
    bare directory) is refused, and origin never receives the branch. The
    token is set so that nothing else stops the push first."""
    monkeypatch.setenv("RITE_GITHUB_TOKEN", "not-a-real-token")
    p = Project(tmp_path, "pull_request")
    (p.root / ".rite" / "modules.yaml").write_text(
        "modules:\n  svc:\n    path: svc/\n    url: https://github.com/up/svc.git\n"
        "    branch: main\n"
    )
    p.start()
    p.work()
    with _gate(EXIT_CLEAN):
        got = p.deliver()
    assert not got.ok
    note = got.outcomes[0].note()
    assert "could not read which GitHub repository svc's origin is" in note, note
    assert "not pushed" in note
    assert (
        subprocess.run(
            [
                "git",
                "-C",
                str(p.origin),
                "rev-parse",
                "--verify",
                f"refs/heads/{TICKET}",
            ],
            capture_output=True,
        ).returncode
        != 0
    ), "the branch reached origin although the target was refused"
