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
from rite_ai.publishing.deliver import _open_pull_request
from tests.test_rite_delivers_a_finished_task import TICKET, Project, _commit, _git


class _Report:
    def __init__(self, code: int):
        self.exit_code = code


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
    assert gate.call_args.kwargs == {"rev_range": f"main..{TICKET}"}
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
