"""Every commit a Worker's work leaves in credits rite and Claude (Robert,
2026-09-29), added by rite and never remembered by a Worker.

The form: a body line `🤖 Generated with rite (<rite's repo>)` and the trailer
`Co-Authored-By: Claude <noreply@anthropic.com>`, version-less on purpose.
Two places build those commits: the Worker's own (a hook rite installs in
each clone before the sandbox starts) and the squash `rite deliver` builds on
the host. Both are tested with real git; a control shows a commit made
without the hook carries neither line, and mutations (the install call
removed, the squash credit removed, the hook's trailer or body line removed)
turn these red.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rite_ai.config.models import SandboxConfig
from rite_ai.publishing.attribution import (
    BODY_LINE,
    RITE_URL,
    TRAILER,
    credit,
    install_hooks,
)
from rite_ai.sandbox import start_worker

CLAUDE_CODE_STYLE = (
    "Fix the retry loop\n\nIt retried forever.\n\n"
    "Co-Authored-By: Claude <noreply@anthropic.com>\n"
)
SIGNED = "Fix it\n\nSigned-off-by: Someone <s@example.invalid>\n"


def _git(*args, cwd, stdin=None):
    return subprocess.run(
        ["git", "-c", "user.name=W", "-c", "user.email=w@example.invalid", *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _clone(tmp_path: Path) -> Path:
    clone = tmp_path / "workers" / "alpha" / "mod"
    clone.mkdir(parents=True)
    _git("init", "-q", cwd=clone)
    return clone


def _commit(clone: Path, message: str, *extra: str) -> str:
    (clone / "f").write_text(os.urandom(4).hex())
    _git("add", "f", cwd=clone)
    _git(
        "-c",
        "core.hooksPath=.git/hooks",
        "commit",
        "-q",
        "-F",
        "-",
        *extra,
        cwd=clone,
        stdin=message,
    )
    return _git("log", "-1", "--format=%B", cwd=clone)


def _once(message: str) -> None:
    lines = message.split("\n")
    assert lines.count(BODY_LINE) == 1, message
    assert lines.count(TRAILER) == 1, message
    # The body line sits BEFORE the trailer block, or git no longer reads the
    # trailers as trailers.
    assert lines.index(BODY_LINE) < lines.index(TRAILER), message
    trailers = _git("interpret-trailers", "--parse", cwd=Path("."), stdin=message)
    assert TRAILER in trailers.splitlines(), (message, trailers)


def test_the_url_is_rites_repository():
    assert RITE_URL == "https://github.com/robbartoszewski/rite"
    assert BODY_LINE == f"🤖 Generated with rite ({RITE_URL})"
    assert TRAILER == "Co-Authored-By: Claude <noreply@anthropic.com>"
    assert "Opus" not in TRAILER and "Sonnet" not in TRAILER  # version-less


@pytest.mark.parametrize(
    "message",
    ["fix it\n", CLAUDE_CODE_STYLE, SIGNED, "subject\n\nbody para\n"],
    ids=["subject-only", "claude-code-trailer", "other-trailer", "body"],
)
def test_credit_adds_each_line_once_and_keeps_the_trailers_trailers(message):
    got = credit(message)
    _once(got)
    assert credit(got) == got  # idempotent
    if "Signed-off-by" in message:
        assert "Signed-off-by: Someone <s@example.invalid>" in _git(
            "interpret-trailers", "--parse", cwd=Path("."), stdin=got
        )


@pytest.mark.parametrize(
    "message",
    ["fix it\n", CLAUDE_CODE_STYLE, SIGNED, "subject\n\nbody para\n"],
    ids=["subject-only", "claude-code-trailer", "other-trailer", "body"],
)
@pytest.mark.parametrize("extra", [(), ("--no-verify",)], ids=["plain", "no-verify"])
def test_every_worker_commit_is_credited_by_the_hook(tmp_path, message, extra):
    """Real git through the hook rite installs. The hook and `credit` agree
    exactly, so the squash path and the Worker's path cannot drift."""
    clone = _clone(tmp_path)
    assert install_hooks(tmp_path / "workers" / "alpha") == []
    got = _commit(clone, message, *extra)
    _once(got)
    assert got.rstrip("\n") == credit(message).rstrip("\n")


def test_an_amend_repeats_nothing(tmp_path):
    clone = _clone(tmp_path)
    install_hooks(tmp_path / "workers" / "alpha")
    _commit(clone, CLAUDE_CODE_STYLE)
    _git(
        "-c",
        "core.hooksPath=.git/hooks",
        "commit",
        "-q",
        "--amend",
        "--no-edit",
        cwd=clone,
    )
    _once(_git("log", "-1", "--format=%B", cwd=clone))


def test_a_hook_already_there_still_runs_first(tmp_path):
    clone = _clone(tmp_path)
    theirs = clone / ".git" / "hooks" / "prepare-commit-msg"
    theirs.write_text('#!/bin/sh\necho "theirs ran" >> "$1"\n')
    theirs.chmod(0o755)
    install_hooks(tmp_path / "workers" / "alpha")
    got = _commit(clone, "fix it\n")
    assert "theirs ran" in got
    _once(got)


def test_control_without_the_hook_a_commit_carries_neither_line(tmp_path):
    clone = _clone(tmp_path)
    got = _commit(clone, "fix it\n")
    assert BODY_LINE not in got and TRAILER not in got


def _start(tmp_path: Path):
    with (
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.count_active_sandboxes", return_value=0),
        patch("rite_ai.sandbox.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        return start_worker(tmp_path, "alpha", SandboxConfig()), mock_run


def test_start_worker_installs_the_hook_before_the_sandbox_copies_the_clone(
    tmp_path,
):
    clone = _clone(tmp_path)
    result, _ = _start(tmp_path)
    assert result.ok, result.message
    hook = (clone / ".git" / "hooks" / "prepare-commit-msg").read_text()
    assert "attribution.install_hooks" in hook


def test_a_worker_whose_hook_cannot_be_written_is_not_started(tmp_path):
    clone = _clone(tmp_path)
    hooks = clone / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hooks.chmod(0o500)
    try:
        result, mock_run = _start(tmp_path)
    finally:
        hooks.chmod(0o755)
    assert not result.ok and "credit rite and" in result.message
    assert not [c for c in mock_run.call_args_list if "new" in c[0][0]]


def test_the_squash_rite_builds_is_credited_too(tmp_path):
    """`rite deliver` with `publish.squash` builds the commit itself, on the
    host, so no hook sees it."""
    from tests.test_rite_delivers_a_finished_task import TICKET, Project
    from tests.test_rite_delivers_a_finished_task import _git as g

    p = Project(tmp_path, "commit", squash=True)
    p.start()
    p.work()
    got = p.deliver()
    assert got.ok, got
    _once(g(p.svc, "log", "-1", "--format=%B", TICKET))
