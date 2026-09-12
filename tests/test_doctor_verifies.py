"""`rite doctor` must verify, not assert existence.

Doctor is what someone runs to ask "is this set up correctly", and on the
one question where getting it wrong leaks secrets to a public remote it
answered from the presence of a file. Each case below was planted for
real first — a hook stripped of its execute bit pushed a live credential
to a remote with exit 0 while doctor printed "active" and "ok".
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import _tool_runs, cli
from rite_ai.gate.ci import CI_WORKFLOW_REL_PATH
from rite_ai.gate.hook import gate_hook_status, install_pre_push_hook


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    # Neutralise any global core.hooksPath on the machine running these.
    subprocess.run(
        ["git", "config", "--local", "core.hooksPath", ".git/hooks"],
        cwd=root,
        check=True,
    )
    return root


def _project(root: Path, brief: str | None = None) -> Path:
    _repo(root)
    (root / ".rite").mkdir(exist_ok=True)
    (root / ".rite" / "brief.yaml").write_text(
        brief if brief is not None else "project:\n  name: p\n  role: manager\n"
    )
    (root / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nschedule:\n  timezone: Europe/Warsaw\n"
    )
    install_pre_push_hook(root)
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


class TestTheHookMustBeRunnableNotMerelyPresent:
    def test_a_hook_without_the_execute_bit_is_not_active(self, tmp_path: Path):
        """git skips a non-executable hook and says so in a hint nobody
        reads during a scripted push. Measured: the same commit that had
        just been blocked pushed a live credential to the remote."""
        root = _project(tmp_path / "p")
        hook = root / ".git" / "hooks" / "pre-push"
        hook.chmod(hook.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
        status = gate_hook_status(root)
        assert not status.active
        assert status.state == "not_executable"
        assert "not executable" in status.detail

    def test_the_detail_names_a_fix_that_works(self, tmp_path: Path):
        root = _project(tmp_path / "p")
        hook = root / ".git" / "hooks" / "pre-push"
        hook.chmod(0o644)
        assert "chmod +x" in gate_hook_status(root).detail
        install_pre_push_hook(root, force=True)
        assert os.access(hook, os.X_OK)
        assert gate_hook_status(root).active

    def test_an_executable_rite_hook_is_still_active(self, tmp_path: Path):
        root = _project(tmp_path / "p")
        assert gate_hook_status(root).active

    def test_doctor_fails_on_a_non_executable_hook(self, tmp_path, monkeypatch):
        root = _project(tmp_path / "p")
        (root / ".git" / "hooks" / "pre-push").chmod(0o644)
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1, result.output
        assert "not executable" in result.output
        assert "\nok" not in result.output


class TestBriefIsParsedNotStatted:
    def test_an_unparseable_brief_is_a_problem(self, tmp_path, monkeypatch):
        root = _project(tmp_path / "p", brief="project:\n  name: [unclosed\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1, result.output
        assert "brief.yaml: UNUSABLE" in result.output

    def test_a_brief_missing_the_required_name_is_a_problem(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path / "p", brief="project:\n  role: manager\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1
        assert "project.name" in result.output

    def test_a_good_brief_reports_the_name_it_parsed(self, tmp_path, monkeypatch):
        root = _project(
            tmp_path / "p", brief="project:\n  name: acme\n  role: manager\n"
        )
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert "brief.yaml: ok (acme)" in result.output

    def test_a_missing_brief_still_reads_as_missing(self, tmp_path, monkeypatch):
        root = _project(tmp_path / "p")
        (root / ".rite" / "brief.yaml").unlink()
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert "brief.yaml: missing" in result.output
        assert result.exit_code == 1


class TestToolsAreRun:
    def test_a_tool_that_exits_non_zero_is_broken(self, tmp_path: Path):
        fake = tmp_path / "bin" / "thing"
        fake.parent.mkdir(parents=True)
        fake.write_text("#!/bin/sh\nexit 127\n")
        fake.chmod(0o755)
        ok, detail = _tool_runs(str(fake), ["version"])
        assert not ok
        assert "exited 127" in detail

    def test_a_tool_that_cannot_be_executed_is_broken(self, tmp_path: Path):
        fake = tmp_path / "bin" / "thing"
        fake.parent.mkdir(parents=True)
        fake.write_text("not a program")
        fake.chmod(0o644)
        ok, detail = _tool_runs(str(fake), ["version"])
        assert not ok

    def test_a_working_tool_reports_what_it_printed(self, tmp_path: Path):
        fake = tmp_path / "bin" / "thing"
        fake.parent.mkdir(parents=True)
        fake.write_text("#!/bin/sh\necho 9.9.9\n")
        fake.chmod(0o755)
        ok, detail = _tool_runs(str(fake), ["version"])
        assert ok
        assert detail == "9.9.9"

    def test_long_version_output_is_visibly_elided(self, tmp_path: Path):
        """A hard cut mid-word reads as part of the sentence after it."""
        fake = tmp_path / "bin" / "thing"
        fake.parent.mkdir(parents=True)
        fake.write_text("#!/bin/sh\necho " + "x" * 200 + "\n")
        fake.chmod(0o755)
        ok, detail = _tool_runs(str(fake), ["version"])
        assert ok
        assert detail.endswith("…")

    def test_doctor_fails_when_gitleaks_is_present_but_broken(
        self, tmp_path, monkeypatch
    ):
        """The publish gate depends on gitleaks; a broken one degrades the
        gate silently."""
        root = _project(tmp_path / "p")
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        (fake_bin / "gitleaks").write_text("#!/bin/sh\nexit 127\n")
        (fake_bin / "gitleaks").chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1, result.output
        assert "tool gitleaks: BROKEN" in result.output


class TestConfigErrorsAreLouderNotQuieter:
    def test_a_config_that_will_not_load_is_reported(self, tmp_path, monkeypatch):
        """This branch previously did nothing at all, so a broken config
        skipped the schedule and sandbox rows and still reached "ok"."""
        root = _project(tmp_path / "p")
        (root / ".rite" / "modules.yaml").write_text("modules: [oh: no\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1, result.output
        assert "\nok" not in result.output

    def test_a_broken_config_yaml_is_reported(self, tmp_path, monkeypatch):
        root = _project(tmp_path / "p")
        (root / ".rite" / "config.yaml").write_text("schedule:\n  timezone: [broken\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1, result.output
        assert "config:" in result.output
        assert "config.yaml" in result.output

    def test_one_broken_file_is_one_problem_not_two(self, tmp_path, monkeypatch):
        """brief.yaml and modules.yaml each have their own row; repeating
        them from the config-load errors counted one file twice."""
        root = _project(tmp_path / "p")
        (root / ".rite" / "brief.yaml").unlink()
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.output.count("brief.yaml") == 1, result.output
        assert "1 problem(s) found" in result.output
