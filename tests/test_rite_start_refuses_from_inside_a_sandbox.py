"""SCRUM-81: `rite start` inside a Manager's own sandbox refuses, not crashes.

A Manager orienting itself ran `rite start lead` from inside its own session.
`hold_run` takes an flock on a directory its own docstring places "under no
path any profile grants" — so from inside a sandbox the `os.chmod` on the way
to that lock raised, and rite exited through an unhandled `PermissionError`
traceback. Measured 2026-10-08: PermissionError chmod
'~/Library/Application Support/rite/managers/rite-mgr-…-lead'. The Manager
journalled its own crash.

`BlockingIOError` was already handled — the lock is held by a live run — and
this was not, though both mean "not yours to take".

⚠ **And it must not be reported as the held-lock case.** That path says
"another `rite start` holds its run lock", and here nothing does: the lock was
never looked at. Telling a Manager its own role is already running, when the
truth is that it asked from the wrong side of a boundary, sends it hunting a
process that does not exist.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init", "--yes"])
    cfg = tmp_path / ".rite" / "config.yaml"
    cfg.write_text(
        cfg.read_text().replace(
            "manager_roles: []",
            "manager_roles:\n  - {name: lead, engine: claude, preset: lead}",
        )
    )
    return tmp_path.resolve()


def _start_with_hold_run_raising(monkeypatch, error):
    def refuse(*_a, **_kw):
        raise error

    monkeypatch.setattr("rite_ai.managers.github_access.hold_run", refuse)
    return CliRunner().invoke(
        cli, ["start", "lead", "--sessions", "1", "--minutes", "5"]
    )


def test_an_unreachable_run_lock_is_a_refusal_and_not_a_traceback(project, monkeypatch):
    result = _start_with_hold_run_raising(
        monkeypatch, PermissionError(1, "Operation not permitted")
    )
    assert result.exit_code == 1, result.output
    # 🔴 The defect: the error escaped as itself.
    assert not isinstance(result.exception, PermissionError), (
        "the PermissionError reached the operator as a traceback"
    )
    assert isinstance(result.exception, SystemExit), repr(result.exception)


def test_it_says_what_actually_happened_and_not_that_a_run_holds_the_lock(
    project, monkeypatch
):
    """The wrong message is worse than none: it names a cause that is absent."""
    result = _start_with_hold_run_raising(
        monkeypatch, PermissionError(1, "Operation not permitted")
    )
    out = result.output
    assert "outside every sandbox grant" in out, out
    assert "PermissionError" in out, "the operator is not told what was raised"
    assert "holds its run lock" not in out, (
        "reported as the held-lock case, which it is not — nothing holds it"
    )


def test_it_points_a_manager_at_commands_that_do_work_in_a_sandbox(
    project, monkeypatch
):
    """The crash happened because a Manager was told to orient with `rite
    start`. A refusal that does not say what to run instead leaves it stuck."""
    out = _start_with_hold_run_raising(
        monkeypatch, PermissionError(1, "Operation not permitted")
    ).output
    assert "rite status" in out and "rite handover show" in out, out


def test_a_held_lock_still_reports_as_a_held_lock(project, monkeypatch):
    """The CONTROL. The new branch must not swallow the case that was already
    handled — `None` still means a live run owns it, with its own message."""
    monkeypatch.setattr(
        "rite_ai.managers.github_access.hold_run", lambda *_a, **_kw: None
    )
    result = CliRunner().invoke(
        cli, ["start", "lead", "--sessions", "1", "--minutes", "5"]
    )
    assert result.exit_code == 1, result.output
    assert "holds its run lock" in result.output
    assert "outside every sandbox grant" not in result.output
