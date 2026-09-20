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
    STATUS_READS,
    UNCLEAR,
    Ending,
    Liveness,
    StartResult,
    ending,
    exit_status_available,
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

# ⚠ A CAPABILITY, not a platform. Measured on CI: a Linux tmux left
# `#{pane_dead_status}` empty where macOS filled it, so `ending` answered
# `unclear` for every outcome and the supervisor could never resume there.
# Skipping on the capability rather than on `sys.platform` states what the
# dependency actually is — and `exit_status_available` is the same probe the
# CLI uses to warn a user, so this test and that warning cannot disagree.
reports_exit_status = pytest.mark.skipif(
    not exit_status_available(),
    reason="this tmux does not populate #{pane_dead_status}; outcomes are unclear",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    return tmp_path


def _platform_supplied_a_status(name: str, how) -> None:
    """Skip when tmux did not feed this session a status at all.

    ⚠ **This is the one dangerous shape in the file — a test that stands
    down when the thing fails — so the reason it is right here has to be
    checked, not asserted.**

    Measured on tmux 3.4 under CI: `#{pane_dead}` is 1 and
    `#{pane_dead_status}` stays EMPTY for six seconds and never arrives,
    for a real session, in a run where the capability probe succeeded three
    times. A probe's answer does not predict a session's answer, so there
    is no machine-level gate that can work — the capability is per-session
    on that platform.

    What makes standing down acceptable is that **no logic goes untested
    when it happens.** The whole finished/quit/crashed/unclear mapping is
    asserted by `TestTheStatusArrivesAfterTheDeath` with scripted tmux
    replies, deterministically, on every platform. These tests exist to
    check the OTHER half — that real tmux, driven by `start`, actually
    feeds that mapping — and when the platform declines to supply the
    input there is nothing of rite's left to assert.

    It skips ONLY on an absent status. A status that arrives and is read
    wrongly still fails, which is the defect this file was written for.
    """
    if how.kind == UNCLEAR and "no exit status" in how.detail:
        pytest.skip(
            "this tmux did not supply an exit status for this session "
            f"({how.detail}). The mapping is covered by the scripted tests; "
            "what is untested here is tmux, not rite."
        )


def _what_tmux_showed(name: str, how) -> str:
    """⚠ A diagnostic, because this failed on Linux four times and the
    message said only `unclear` — the one word that does not distinguish
    the four ways it is reached.

    ⚠ **Called BEFORE the test kills its session, and that is the whole
    point.** The first version ran inside the assert message, which the
    tests reach only after `kill-session` — and killing the last session
    stops the tmux server, so every probe came back `no server running` and
    a whole CI cycle produced no information. A diagnostic that runs after
    the thing it measures is gone measures nothing.
    """
    bits = [f"ending={how.kind!r} status={how.status} detail={how.detail!r}"]
    for label, argv in (
        ("version", ["tmux", "-V"]),
        ("sessions", ["tmux", "list-sessions"]),
        ("option", ["tmux", "show-window-options", "-t", name, "remain-on-exit"]),
    ):
        got = subprocess.run(argv, capture_output=True, text=True)
        bits.append(
            f"{label}: rc={got.returncode} {(got.stdout or got.stderr).strip()!r}"
        )
    # Does the status EVER arrive? Three more seconds of it, sampled, so the
    # answer is "no, never" or "yes, at 4.1s" rather than another guess.
    trail = []
    for _ in range(15):
        got = subprocess.run(
            [
                "tmux",
                "display-message",
                "-p",
                "-t",
                name,
                "#{pane_dead}/#{pane_dead_status}",
            ],
            capture_output=True,
            text=True,
        )
        trail.append(
            (got.stdout or got.stderr).strip() if got.returncode == 0 else "gone"
        )
        time.sleep(0.2)
    bits.append(f"after: {trail}")
    return "\n  ".join(bits)


class TestTheThreeEndings:
    """A restart is right for exactly one of them."""

    @tmux_only
    @reports_exit_status
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
        shown = "" if how.kind == FINISHED else _what_tmux_showed(result.session, how)
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
        # AFTER the kill: `skip` raises, and a skip placed before cleanup
        # leaks a tmux session per run.
        _platform_supplied_a_status(result.session, how)
        assert how.kind == FINISHED and how.resume, shown

    def test_an_absent_exit_status_is_UNCLEAR_not_zero(self, monkeypatch):
        """⚠ Caught by Linux CI. A draft parsed an empty
        `#{pane_dead_status}` as 0, so a tmux that does not populate it
        turned every ending into a clean one — `exit 9` read as FINISHED and
        would have been resumed. macOS filled the field and Linux did not,
        which is the platform-vocabulary split that produced three defects
        this week."""
        import rite_ai.managers.session as session_mod

        class Reply:
            returncode = 0
            stdout = "1|"

        monkeypatch.setattr(session_mod.subprocess, "run", lambda *a, **k: Reply())
        how = ending("any-session", human_was_present=False)
        assert how.kind == UNCLEAR
        assert not how.resume, "an unreadable exit status permitted a resume"

    @tmux_only
    @reports_exit_status
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
        shown = "" if how.kind == CRASHED else _what_tmux_showed(result.session, how)
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
        # AFTER the kill: `skip` raises, and a skip placed before cleanup
        # leaks a tmux session per run.
        _platform_supplied_a_status(result.session, how)
        assert how.kind == CRASHED and how.status == 9, shown
        assert not how.resume, "a crash that repeats would repeat at the user's expense"

    @tmux_only
    @reports_exit_status
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
        shown = "" if how.kind == QUIT else _what_tmux_showed(result.session, how)
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
        # AFTER the kill: `skip` raises, and a skip placed before cleanup
        # leaks a tmux session per run.
        _platform_supplied_a_status(result.session, how)
        assert how.kind == QUIT, shown
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


@tmux_only
def test_a_tmux_that_cannot_report_a_status_is_warned_about_not_asserted():
    """⚠ **This test used to assert something that was never true**, and
    Linux CI eventually caught it out.

    It read `exit_status_available() is False` as "then every ending here
    will be unclear" and drove a real session to prove it. On tmux 3.4 the
    probe answered NO and a real session in the SAME RUN, seconds later,
    reported its status perfectly: the capability is INTERMITTENT there,
    not absent. A one-shot probe cannot characterise intermittent
    behaviour, so a universal derived from one sample is a false property
    — and the test failed for being wrong, not for finding a defect.

    What IS true, and is what the supervisor rests on, is the fail-safe
    direction: an ending that cannot be read never resumes. That is
    asserted by the scripted tests below, which hold on every platform
    because they do not depend on winning a race.

    What remains here is the honest claim: the probe is a sample, and where
    it says no, the user is WARNED rather than promised anything.
    """
    answer = exit_status_available()
    assert isinstance(answer, bool), "a probe must answer, not raise"
    # Cached, so a second call cannot contradict the first within a run —
    # which is what makes the startup warning and the supervisor's own
    # behaviour consistent with each other even on a flaky tmux.
    assert exit_status_available() is answer


class TestTheStatusArrivesAfterTheDeath:
    """⚠ The defect that made Linux CI red three times, as a property.

    `#{pane_dead}` and `#{pane_dead_status}` do not arrive together — tmux
    marks a pane dead when its fd closes and fills the status in when it
    reaps the child, and nothing orders those two. Reading once turned that
    window into a permanent `unclear`, so every CLEAN exit on Linux failed
    to resume while `exit 9` passed, because a crash lost the race less
    often. These tests run on any platform because they drive the race
    directly rather than waiting to be unlucky on one.
    """

    def _replies(self, monkeypatch, answers: list[str]):
        import rite_ai.managers.session as session_mod

        seen: list[str] = []

        class Reply:
            returncode = 0

            def __init__(self, out: str):
                self.stdout = out

        def fake_run(*args, **kwargs):
            out = answers[min(len(seen), len(answers) - 1)]
            seen.append(out)
            return Reply(out)

        monkeypatch.setattr(session_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(session_mod.time, "sleep", lambda _s: None)
        return seen

    def test_a_status_that_arrives_late_is_waited_for(self, monkeypatch):
        seen = self._replies(monkeypatch, ["1|", "1|", "1|0"])
        how = ending("any-session", human_was_present=False)
        assert how.kind == FINISHED, (
            f"a status that arrived on the third read was called unknown: {how.detail}"
        )
        assert how.resume
        assert len(seen) == 3, "it stopped asking before the status arrived"

    def test_a_late_nonzero_status_is_still_CRASHED(self, monkeypatch):
        self._replies(monkeypatch, ["1|", "1|9"])
        how = ending("any-session", human_was_present=False)
        assert how.kind == CRASHED and how.status == 9, how.detail
        assert not how.resume

    def test_a_status_that_never_arrives_is_still_UNCLEAR(self, monkeypatch):
        """The fail-safe the waiting must not spend. Waiting can turn an
        unknown into a known; it must never turn an unknown into a guess."""
        seen = self._replies(monkeypatch, ["1|"])
        how = ending("any-session", human_was_present=False)
        assert how.kind == UNCLEAR
        assert not how.resume, "waiting was allowed to invent a clean exit"
        assert len(seen) == STATUS_READS, "it gave up before it had waited"

    def test_a_late_clean_status_with_a_human_present_is_QUIT(self, monkeypatch):
        """The human/agent distinction, scripted — so it is covered on a
        platform whose tmux never supplies a real status."""
        self._replies(monkeypatch, ["1|", "1|0"])
        how = ending("any-session", human_was_present=True)
        assert how.kind == QUIT
        assert not how.resume, "the human said stop and it restarted anyway"

    def test_a_pane_that_is_not_dead_is_not_waited_for(self, monkeypatch):
        """Only the dead-without-status window is a race. A live pane is an
        answer, and polling it would stall every caller by a second."""
        seen = self._replies(monkeypatch, ["0|"])
        how = ending("any-session", human_was_present=False)
        assert how.kind == UNCLEAR
        assert len(seen) == 1, "it waited on a pane that was simply still running"


class TestTheStandDownIsBounded:
    """⚠ `_platform_supplied_a_status` is the one shape in this file that
    lets a failure pass quietly, so what it will and will not swallow is
    itself tested. A stand-down nobody has bounded is how a suite goes
    green by ceasing to ask.
    """

    def test_it_stands_down_when_tmux_supplied_nothing(self):
        absent = ending.__globals__["Ending"](
            UNCLEAR, detail="the pane is dead but tmux reported no exit status ('')"
        )
        with pytest.raises(BaseException) as caught:
            _platform_supplied_a_status("any", absent)
        assert "Skipped" in type(caught.value).__name__

    @pytest.mark.parametrize(
        "how",
        [
            Ending(FINISHED, detail="the command exited cleanly, unattended"),
            Ending(CRASHED, status=9, detail="the command exited 9"),
            Ending(QUIT, detail="it exited cleanly while somebody was attached"),
            Ending(UNCLEAR, detail="the pane is not dead"),
            Ending(UNCLEAR, detail="the session is gone, with its exit status"),
        ],
        ids=["finished", "crashed", "quit", "not-dead", "session-gone"],
    )
    def test_it_does_not_stand_down_on_anything_else(self, how):
        """A status that ARRIVES and is read wrongly must still fail — that
        is the defect this file exists for. So must a pane that never died
        and a session that vanished, which are rite's problems, not tmux's
        reticence."""
        _platform_supplied_a_status("any", how)
