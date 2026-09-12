"""`rite doctor` had a check for the disarmable layer of the publish gate and
none for the load-bearing one.

SPEC §11.5.1: `core.hooksPath` can disarm the pre-push hook on the developer's
machine without any action by the developer and with no signal that it
happened, "which makes [CI] the load-bearing one, not the backup, and makes a
red or absent CI gate a publish-blocking condition rather than an untidiness."

Doctor reported the hook and said nothing about CI. That asymmetry is how the
hook defect existed in the first place — nobody asked the question, so nobody
got the wrong answer — and it survived the change that made `rite init`
generate the workflow.

The check is on whether a job WOULD RUN the gate, not on whether the file is
present. Presence is the proxy; running it is the property. The project's own
checklist line says so, and this same substitution has now produced the same
defect three times (see `rite_ai.gate.ci.workflow_runs_gate`).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.init import run_init
from rite_ai.cli.main import cli
from rite_ai.gate.ci import (
    CI_WORKFLOW_MARKER,
    CI_WORKFLOW_REL_PATH,
    ci_workflow_status,
    workflow_runs_gate,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

RUNS_GATE = (
    "name: Publish gate\non: [push]\njobs:\n  publish-gate:\n"
    "    runs-on: ubuntu-latest\n    steps:\n      - run: rite publish check\n"
)
INERT = (
    "name: Publish gate\non: [push]\njobs:\n  publish-gate:\n"
    "    runs-on: ubuntu-latest\n    steps:\n      - run: echo hello\n"
)


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "config", "--local", "core.hooksPath", ".git/hooks"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def _write(root: Path, text: str) -> Path:
    path = root / CI_WORKFLOW_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- the status function ----------------------------------------------------


def test_a_workflow_that_runs_the_gate_is_active(tmp_path):
    root = _repo(tmp_path)
    _write(root, RUNS_GATE)
    status = ci_workflow_status(root)
    assert status.active
    assert status.runs_gate
    assert not status.is_ours  # rite did not write this one, and it still counts


def test_a_missing_workflow_names_the_command_that_installs_one(tmp_path):
    root = _repo(tmp_path)
    status = ci_workflow_status(root)
    assert status.state == "missing"
    assert not status.active
    assert "rite publish install-ci" in status.detail


def test_a_present_but_inert_workflow_is_not_active(tmp_path):
    """Presence is the proxy. A file at the right path that runs `echo` is
    the §11.5.1 shape: reports itself installed, does nothing."""
    root = _repo(tmp_path)
    _write(root, INERT)
    status = ci_workflow_status(root)
    assert status.state == "inert"
    assert not status.active
    assert "no job in it runs" in status.detail


def test_a_rite_written_workflow_edited_into_a_no_op_is_still_inert(tmp_path):
    """Authorship is never what decides `active`. A workflow rite wrote and
    the user has since gutted is ours and dead."""
    root = _repo(tmp_path)
    _write(root, f"{CI_WORKFLOW_MARKER}\n{INERT}")
    status = ci_workflow_status(root)
    assert status.state == "inert"
    assert status.is_ours is True
    assert status.active is False


def test_the_generated_templates_own_header_is_not_mistaken_for_a_gate(tmp_path):
    """The header comment of the workflow `rite init` generates says `rite
    publish check` twice. A substring check over the file reported a header
    plus an empty `jobs:` block as an armed gate — the third iteration of one
    defect, found inside the fix for the second."""
    from rite_ai.cli.init.scaffold import render_ci_workflow

    header = "\n".join(
        line
        for line in render_ci_workflow().splitlines()
        if line.startswith("#") or not line.strip()
    )
    assert header.count("rite publish check") >= 2, "template no longer baits this"

    root = _repo(tmp_path)
    _write(root, f"{header}\nname: Publish gate\non: [push]\njobs: {{}}\n")
    assert ci_workflow_status(root).state == "inert"


def test_a_non_repo_is_not_reported_on_at_all(tmp_path):
    assert ci_workflow_status(tmp_path).state == "not_a_repo"


def test_an_unreadable_workflow_is_unknown_not_active(tmp_path):
    root = _repo(tmp_path)
    path = _write(root, RUNS_GATE)
    # root ignores the mode bits, so the branch under test is simply
    # unreachable there — skipped rather than passing vacuously.
    if os.geteuid() == 0:
        pytest.skip("chmod does not restrict root")
    path.chmod(0o000)
    try:
        status = ci_workflow_status(root)
        assert status.state == "unreadable"
        assert status.active is False
    finally:
        path.chmod(0o644)


def test_unparseable_yaml_still_ignores_comments(tmp_path):
    assert workflow_runs_gate("# rite publish check\n\tbroken: [yaml(\n") is False


# --- doctor ------------------------------------------------------------------


def test_doctor_reports_a_missing_ci_workflow_as_a_problem(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    run_init(root, yes=True)
    (root / CI_WORKFLOW_REL_PATH).unlink()
    monkeypatch.chdir(root)

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "publish gate CI" in result.output
    assert "rite publish install-ci" in result.output


def test_doctor_reports_an_inert_workflow_rather_than_its_presence(
    tmp_path, monkeypatch
):
    """The whole point of the check. A file is there; doctor must not read
    that as coverage."""
    root = _repo(tmp_path)
    run_init(root, yes=True)
    _write(root, INERT)
    monkeypatch.chdir(root)

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "does not run in CI" in result.output


def test_doctor_says_active_when_a_job_really_runs_the_gate(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    run_init(root, yes=True)
    monkeypatch.chdir(root)

    result = CliRunner().invoke(cli, ["doctor"])

    assert "publish gate CI (project root): active" in result.output
    assert "publish gate does not run in CI" not in result.output


def test_doctor_scopes_the_ci_line_to_the_project_root(tmp_path, monkeypatch):
    """`rite init` writes the workflow into the project root only — the gate
    reads its scan patterns and suppressions from `.rite/`, which lives there.
    A bare "active" would read as "every repo is covered", and a module repo
    pushing to its own remote is covered by this line not at all."""
    root = _repo(tmp_path)
    run_init(root, yes=True)
    monkeypatch.chdir(root)

    result = CliRunner().invoke(cli, ["doctor"])

    ci_lines = [ln for ln in result.output.splitlines() if "publish gate CI" in ln]
    assert len(ci_lines) == 1
    assert "project root" in ci_lines[0]


def test_doctor_does_not_claim_anything_about_the_ci_run(tmp_path, monkeypatch):
    """Whether the last run was green is a remote fact and doctor is local and
    read-only. A line asserting it would be the "states only what was actually
    measured" failure the checklist names."""
    root = _repo(tmp_path)
    run_init(root, yes=True)
    monkeypatch.chdir(root)

    out = CliRunner().invoke(cli, ["doctor"]).output.lower()

    # Whole words, not substrings: "red" is the last three letters of
    # "registered", which doctor prints on any project with no modules. A
    # substring check here failed for a reason that had nothing to do with
    # what it claimed to test — the same proxy-for-the-property mistake this
    # file exists to guard against, one level up in the test itself.
    for claim in ("last run", "green", "passing", "succeeded", "red"):
        assert not re.search(rf"\b{re.escape(claim)}\b", out), claim


# --- every spelling of "run the gate" this project ships ---------------------


def test_the_module_invocation_rites_own_workflow_uses_is_recognised():
    """`rite doctor` reported rite's OWN CI gate as broken.

    `.github/workflows/publish-gate.yml` here runs `uv run python -m
    rite_ai.gate check`, which reaches the identical `run_gate`;
    `gate/__main__.py`'s docstring names that workflow as the reason the
    standalone module path exists at all. Matching only the console-script
    spelling made doctor exit 1 on a false problem in the repository that
    ships the check — and offer `install-ci --force`, which would have
    overwritten a working file.

    A false alarm in a health check is how people learn to ignore health
    checks.
    """
    assert workflow_runs_gate(
        "on: [push]\njobs:\n  g:\n    steps:\n"
        "      - run: uv run python -m rite_ai.gate check\n"
    )


def test_the_alias_console_script_is_recognised():
    """`pyproject.toml` ships `rite` and `rite-ai` as two real console
    scripts on one entry point — the alias is the same program, and it is
    what a user whose PATH already has PyPI's unrelated `rite` must use."""
    assert workflow_runs_gate(
        "on: [push]\njobs:\n  g:\n    steps:\n      - run: rite-ai publish check\n"
    )


def test_rites_own_workflow_is_reported_active(tmp_path):
    """Not a constructed string — the actual file in this repository, read
    off disk. The regression was found by running `rite doctor` here."""
    own = REPO_ROOT / ".github" / "workflows" / "publish-gate.yml"
    assert own.is_file(), "rite's own dogfood workflow is gone — test is stale"
    assert workflow_runs_gate(own.read_text())


def test_a_workflow_running_no_gate_spelling_is_still_inert(tmp_path):
    """The widening must not have turned the check into 'mentions rite'."""
    root = _repo(tmp_path)
    _write(
        root,
        "on: [push]\njobs:\n  g:\n    steps:\n"
        "      - run: echo rite publish is a great tool\n",
    )
    assert ci_workflow_status(root).state == "inert"
