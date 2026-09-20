"""Three outcomes, a restart for exactly one, and a resume that resumes.

Two defects measured by running `rite start` rather than reading it.

**The supervisor restarted a session the human deliberately quit.** Both
"the agent finished" and "the human typed exit" are a dead pane with status
0, so it could not tell them apart and chose the expensive reading.

**And the resume path did not resume.** The session id came from an
injectable whose default returned `""`, so every cycle ran a bare `claude`
with no `--resume` and a fresh context — identical from outside to one that
worked, which is this week's recurring shape.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

import rite_ai.managers.supervise as supervise_mod
from rite_ai.managers.session import (
    CRASHED,
    FINISHED,
    QUIT,
    UNCLEAR,
    Ending,
    Liveness,
    StartResult,
    ending,
    liveness,
    start,
    was_attached,
)
from rite_ai.managers.supervise import supervise
from rite_ai.managers.transcripts import latest_session_id, project_transcript_dir

tmux_only = pytest.mark.skipif(
    subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
    reason="needs real tmux; mocking it is the bug",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    return tmp_path


class TestTheThreeEndings:
    """A restart is right for exactly one of them."""

    @tmux_only
    def test_a_clean_unattended_exit_is_FINISHED_and_resumes(self, project):
        result = start(project, "lead", command="sh", max_sessions=1)
        assert result.ok, result.message
        subprocess.run(
            ["tmux", "send-keys", "-t", result.session, "exit 0", "Enter"],
            capture_output=True,
        )
        for _ in range(20):
            if not liveness(result.session).alive:
                break
            time.sleep(0.2)
        how = ending(result.session, human_was_present=False)
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
        assert how.kind == FINISHED and how.resume

    @tmux_only
    def test_a_nonzero_exit_is_CRASHED_and_does_not_resume(self, project):
        result = start(project, "lead", command="sh", max_sessions=1)
        subprocess.run(
            ["tmux", "send-keys", "-t", result.session, "exit 9", "Enter"],
            capture_output=True,
        )
        for _ in range(20):
            if not liveness(result.session).alive:
                break
            time.sleep(0.2)
        how = ending(result.session, human_was_present=False)
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
        assert how.kind == CRASHED and how.status == 9
        assert not how.resume, "a crash that repeats would repeat at the user's expense"

    @tmux_only
    def test_a_clean_exit_with_a_human_present_is_QUIT(self, project):
        """The measured defect: this and FINISHED are the same exit status,
        so the only difference is whether somebody was there."""
        result = start(project, "lead", command="sh", max_sessions=1)
        subprocess.run(
            ["tmux", "send-keys", "-t", result.session, "exit 0", "Enter"],
            capture_output=True,
        )
        for _ in range(20):
            if not liveness(result.session).alive:
                break
            time.sleep(0.2)
        how = ending(result.session, human_was_present=True)
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
        assert how.kind == QUIT
        assert not how.resume, "the human said stop and it restarted anyway"

    def test_a_session_that_is_gone_is_UNCLEAR_and_does_not_resume(self):
        """Fail safe: refusing when unsure costs one command, restarting
        when unsure spends money."""
        how = ending("rite-mgr-no-such-session-xyz", human_was_present=False)
        assert how.kind == UNCLEAR and not how.resume

    def test_resume_is_true_for_exactly_one_kind(self):
        for kind in (QUIT, CRASHED, UNCLEAR):
            assert not Ending(kind).resume
        assert Ending(FINISHED).resume


class TestLivenessUnderRemainOnExit:
    @tmux_only
    def test_a_dead_pane_is_not_alive_even_though_the_session_exists(self, project):
        """⚠ `remain-on-exit on` keeps the session so its exit status
        survives, so `has-session` returns 0 for a pane whose command has
        exited. Asking `has-session` here would mean the supervisor never
        noticed a session ending."""
        result = start(project, "lead", command="sh", max_sessions=1)
        subprocess.run(
            ["tmux", "send-keys", "-t", result.session, "exit 0", "Enter"],
            capture_output=True,
        )
        time.sleep(1.0)
        still_exists = subprocess.run(
            ["tmux", "has-session", "-t", result.session], capture_output=True
        )
        answer = liveness(result.session)
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
        assert still_exists.returncode == 0, "remain-on-exit did not hold the session"
        assert not answer.alive and answer.known


class TestTheResumeIdIsReal:
    def test_a_transcript_directory_is_named_from_the_project_path(self, tmp_path):
        where = project_transcript_dir(Path("/a/b/c"), base=tmp_path)
        assert where.name == "-a-b-c"

    def test_the_id_comes_from_the_FILE_CONTENTS_not_the_name(self, tmp_path):
        """A stray `.jsonl` would otherwise become an id nobody can resume."""
        directory = project_transcript_dir(Path("/a/b"), base=tmp_path)
        directory.mkdir(parents=True)
        (directory / "not-a-session.jsonl").write_text('{"noise": 1}\n')
        assert latest_session_id(Path("/a/b"), base=tmp_path) == ""

        (directory / "real.jsonl").write_text('{"sessionId": "abc-123"}\n')
        assert latest_session_id(Path("/a/b"), base=tmp_path) == "abc-123"

    def test_an_absent_directory_returns_empty_rather_than_guessing(self, tmp_path):
        assert latest_session_id(Path("/nope"), base=tmp_path) == ""

    def test_since_excludes_older_transcripts(self, tmp_path):
        directory = project_transcript_dir(Path("/a/b"), base=tmp_path)
        directory.mkdir(parents=True)
        old = directory / "old.jsonl"
        old.write_text('{"sessionId": "old-one"}\n')
        import os

        os.utime(old, (1000, 1000))
        assert latest_session_id(Path("/a/b"), base=tmp_path) == "old-one"
        assert latest_session_id(Path("/a/b"), since=time.time(), base=tmp_path) == ""


class TestTheSupervisorRefusesToResumeIntoNothing:
    def test_no_transcript_means_stop_rather_than_a_fresh_context(
        self, project, monkeypatch
    ):
        """Continuing without an id starts a NEW conversation with the
        ticket half-done and no memory of it — which looks identical from
        outside to a resume that worked."""
        monkeypatch.setattr(
            supervise_mod, "liveness", lambda _n: Liveness(False, known=True)
        )
        monkeypatch.setattr(supervise_mod, "was_attached", lambda _n: False)
        monkeypatch.setattr(
            supervise_mod, "ending", lambda _n, human_was_present: Ending(FINISHED)
        )
        result = supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=5,
            window_seconds=0,
            starter=lambda r, m, **kw: StartResult(True, "ok", session="s1"),
            resume_id_for=lambda _r, _m, _since=0.0: "",
            poll=0,
        )
        assert result.sessions_started == 1
        assert "no transcript was found to resume from" in result.reason

    def test_a_real_id_is_passed_to_the_next_cycle(self, project, monkeypatch):
        seen: list[str] = []

        def starter(root, manager, *, engine, resume_id, max_sessions, window_seconds):
            seen.append(resume_id)
            return StartResult(True, "ok", session=f"s{len(seen)}")

        monkeypatch.setattr(
            supervise_mod, "liveness", lambda _n: Liveness(False, known=True)
        )
        monkeypatch.setattr(supervise_mod, "was_attached", lambda _n: False)
        monkeypatch.setattr(
            supervise_mod, "ending", lambda _n, human_was_present: Ending(FINISHED)
        )
        supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=3,
            window_seconds=0,
            starter=starter,
            resume_id_for=lambda _r, _m, _since=0.0: "sess-42",
            poll=0,
        )
        assert seen == ["", "sess-42", "sess-42"]


@tmux_only
def test_was_attached_is_false_for_an_unattached_session(project):
    """⚠ Only the FALSE direction is tested, and the docstring on
    `was_attached` says why: nothing here can create a genuinely attached
    client. The TRUE branch is load-bearing and unverified."""
    result = start(project, "lead", command="sh", max_sessions=1)
    answer = was_attached(result.session)
    subprocess.run(["tmux", "kill-session", "-t", result.session], capture_output=True)
    assert answer is False
