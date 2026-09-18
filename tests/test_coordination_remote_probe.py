"""P2-1e: doctor finds out whether the coordination remote accepts force-push.

Real git against local bare repositories, configured the way a host's branch
protection behaves: `receive.denyNonFastForwards` refuses force-push, and
`receive.denyDeletes` refuses branch deletion."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.coordination.remote_probe import probe_force_push


def _run(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def _remote(tmp_path: Path, **config: str) -> Path:
    bare = tmp_path / "coord.git"
    _run("init", "-q", "--bare", str(bare), cwd=tmp_path)
    for key, value in config.items():
        _run("config", key, value, cwd=bare)
    return bare


def _refs(bare: Path) -> dict[str, str]:
    out = _run("for-each-ref", "--format=%(refname) %(objectname)", cwd=bare).stdout
    return dict(line.split() for line in out.splitlines())


def _with_state_branch(tmp_path: Path, bare: Path) -> str:
    seed = tmp_path / "seed"
    _run("init", "-q", str(seed), cwd=tmp_path)
    _run(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "state",
        cwd=seed,
    )
    _run(
        "-c",
        "core.hooksPath=/dev/null",
        "push",
        "-q",
        str(bare),
        "HEAD:refs/heads/state",
        cwd=seed,
    )
    return _refs(bare)["refs/heads/state"]


def _probes_left(bare: Path) -> list[str]:
    return [r for r in _refs(bare) if "rite-probe" in r]


def test_an_accepting_remote_passes_and_is_left_clean(tmp_path):
    bare = _remote(tmp_path)
    state = _with_state_branch(tmp_path, bare)
    result = probe_force_push(str(bare), "state", tmp_path)
    assert result.ok, result.detail
    assert not result.leftover_ref
    assert _probes_left(bare) == []
    assert _refs(bare)["refs/heads/state"] == state, (
        "the probe touched the state branch"
    )


def test_a_remote_that_refuses_force_push_is_a_problem_with_the_fix(tmp_path):
    bare = _remote(tmp_path, **{"receive.denyNonFastForwards": "true"})
    state = _with_state_branch(tmp_path, bare)
    result = probe_force_push(str(bare), "state", tmp_path)
    assert not result.ok
    assert "refused a force-push" in result.detail
    assert "allow force-push on `state`" in result.remedy
    assert _probes_left(bare) == [], "cleanup must still run after a refusal"
    assert _refs(bare)["refs/heads/state"] == state


def test_a_probe_branch_the_remote_will_not_delete_is_named(tmp_path):
    bare = _remote(tmp_path, **{"receive.denyDeletes": "true"})
    result = probe_force_push(str(bare), "state", tmp_path)
    assert result.ok
    assert result.leftover_ref.startswith("refs/heads/state-rite-probe-")
    assert _probes_left(bare) == [result.leftover_ref]


def test_an_unreachable_remote_is_a_problem_not_a_crash(tmp_path):
    result = probe_force_push(str(tmp_path / "does-not-exist.git"), "state", tmp_path)
    assert not result.ok
    assert "could not create a branch" in result.detail


def test_a_remote_name_is_resolved_in_the_project_repo(tmp_path):
    bare = _remote(tmp_path)
    project = tmp_path / "project"
    _run("init", "-q", str(project), cwd=tmp_path)
    _run("remote", "add", "coord", str(bare), cwd=project)
    assert probe_force_push("coord", "state", project).ok


def test_an_unknown_remote_name_says_what_to_set(tmp_path):
    project = tmp_path / "project"
    _run("init", "-q", str(project), cwd=tmp_path)
    result = probe_force_push("nowhere", "state", project)
    assert not result.ok
    assert "coordination.remote" in result.remedy


def _project(tmp_path: Path, monkeypatch, remote: str = "") -> Path:
    root = tmp_path / "proj"
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    coord = (
        f"coordination:\n  remote: {remote}\n  state_branch: state\n" if remote else ""
    )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: false\n" + coord
    )
    monkeypatch.chdir(root)
    return root


def test_doctor_reports_a_refused_force_push_as_a_problem(tmp_path, monkeypatch):
    bare = _remote(tmp_path, **{"receive.denyNonFastForwards": "true"})
    _project(tmp_path, monkeypatch, remote=str(bare))
    with patch("keyring.get_password", return_value=None):
        result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 1
    assert (
        "coordination remote: the coordination remote refused a force-push"
        in result.output
    )
    assert "allow force-push on `state`" in result.output


def test_doctor_pushes_nothing_without_a_coordination_remote(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.coordination.remote_probe.probe_force_push") as probe,
    ):
        result = CliRunner().invoke(cli, ["doctor"])
    probe.assert_not_called()
    assert "coordination remote" not in result.output
