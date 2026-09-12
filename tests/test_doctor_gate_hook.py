"""Regression tests for `rite doctor` reporting a project healthy while
the publish gate could not run on a push.

`rite init` checks this once, at creation, and says so loudly — measured
on a real machine with a global `core.hooksPath`, it refused to install
and named the directory git would really read. Nothing asked again
afterwards, and the answer changes without anyone touching the project:
`core.hooksPath` can be set globally by a setup script, by a tool, or on
a new machine, long after the hook was installed.

Measured on a real cloned repo: the pre-push hook deleted AND
`core.hooksPath` pointed elsewhere, and `rite doctor` printed its full
report and then "ok", exit 0. The README describes that command as "is
this healthy — tools, credentials, schedule", and a publish gate that
silently does not run on push is exactly the condition it exists to
surface. rite already had the detection; doctor did not call it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.gate.ci import CI_WORKFLOW_REL_PATH
from rite_ai.gate.hook import gate_hook_status, install_pre_push_hook


def _repo(tmp_path: Path) -> Path:
    """A real git repo, pinned to its OWN hooks directory.

    Pinned deliberately: this machine, and plenty of real ones, carry a
    global `core.hooksPath`, so a test that did not set the local
    override would be measuring the developer's git config rather than
    the code."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "config", "--local", "core.hooksPath", ".git/hooks"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def _project(tmp_path: Path) -> Path:
    root = _repo(tmp_path)
    rite = root / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    # Arm the CI half of the gate too. `rite doctor` checks both layers
    # (SPEC §11.5.1 makes CI the load-bearing one), so a fixture with only a
    # hook is a project doctor is right to call unhealthy — and these tests
    # are about the hook, not about that.
    workflow = root / CI_WORKFLOW_REL_PATH
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: Publish gate\non: [push]\njobs:\n  g:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: rite publish check\n"
    )
    return root


class TestHookStatus:
    def test_a_rite_installed_hook_is_active(self, tmp_path: Path):
        root = _repo(tmp_path)
        assert install_pre_push_hook(root).ok
        assert gate_hook_status(root).state == "active"

    def test_no_hook_at_all(self, tmp_path: Path):
        root = _repo(tmp_path)
        status = gate_hook_status(root)
        assert status.state == "missing"
        assert "rite publish install-hook" in status.detail

    def test_a_redirected_hooks_path(self, tmp_path: Path):
        root = _repo(tmp_path)
        install_pre_push_hook(root)
        elsewhere = tmp_path.parent / "shared-hooks"
        elsewhere.mkdir(exist_ok=True)
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", str(elsewhere)],
            cwd=root,
            check=True,
        )

        status = gate_hook_status(root)

        # The hook file is right there and perfectly correct. It will
        # never run, which is the whole point — and "missing" would send
        # the user to an installer that refuses.
        assert (root / ".git" / "hooks" / "pre-push").is_file()
        assert status.state == "redirected"
        assert "core.hooksPath" in status.detail

    def test_someone_elses_hook(self, tmp_path: Path):
        root = _repo(tmp_path)
        hook = root / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nnpm test\n")

        status = gate_hook_status(root)

        assert status.state == "foreign"
        assert "--force" in status.detail

    def test_a_hand_written_hook_that_runs_the_gate_counts_as_active(
        self, tmp_path: Path
    ):
        """The redirected-hooks advice tells the user to add `exec rite
        publish pre-push` to their own shared hook. Having followed it,
        they must not then be told the gate is inactive."""
        root = _repo(tmp_path)
        hook = root / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nnpm test\nexec rite publish pre-push\n")
        # Executable, because a hand-written hook git actually runs is —
        # without the bit git skips it, which is now its own state.
        hook.chmod(0o755)

        assert gate_hook_status(root).state == "active"

    def test_a_directory_that_is_not_a_repo_is_not_a_problem(self, tmp_path: Path):
        assert gate_hook_status(tmp_path).state == "not_a_repo"


class TestDoctorReportsIt:
    def test_a_missing_hook_makes_doctor_fail(self, tmp_path, monkeypatch):
        """The original symptom: this printed "ok" and exited 0."""
        root = _project(tmp_path)
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["doctor"])

        assert result.exit_code == 1
        assert "publish gate does not run on push" not in result.output  # summary only
        assert "no pre-push hook" in result.output
        assert "\nok" not in result.output

    def test_an_installed_hook_reports_active_and_passes(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        install_pre_push_hook(root)
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["doctor"])

        assert result.exit_code == 0
        assert "publish gate hook (project root): active" in result.output

    def test_a_module_with_no_hook_is_named(self, tmp_path, monkeypatch):
        """The hook is installed into every registered repo, so doctor has
        to check every registered repo — a module pushing unguarded is the
        same hole as the root pushing unguarded."""
        root = _project(tmp_path)
        install_pre_push_hook(root)
        module = root / "backend"
        _repo(module)
        (root / ".rite" / "modules.yaml").write_text(
            "modules:\n  backend:\n    path: backend/\n"
        )
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["doctor"])

        assert result.exit_code == 1
        assert "publish gate hook (module backend)" in result.output

    def test_a_module_directory_that_is_not_a_repo_is_left_to_the_module_check(
        self, tmp_path, monkeypatch
    ):
        """`doctor` already reports "module X: not a git repository" — the
        hook check must not pile a second, less useful line on top of it."""
        root = _project(tmp_path)
        install_pre_push_hook(root)
        (root / "backend").mkdir()
        (root / ".rite" / "modules.yaml").write_text(
            "modules:\n  backend:\n    path: backend/\n"
        )
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["doctor"])

        assert "not a git repository" in result.output
        assert "publish gate hook (module backend)" not in result.output


@pytest.mark.parametrize("state", ["missing", "foreign"])
def test_every_inactive_state_counts_as_a_problem(tmp_path, monkeypatch, state: str):
    root = _project(tmp_path)
    if state == "foreign":
        hook = root / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nnpm test\n")
    monkeypatch.chdir(root)

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "problem(s) found" in result.output
