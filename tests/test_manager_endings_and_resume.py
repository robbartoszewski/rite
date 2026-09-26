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

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

import rite_ai.managers.supervise as supervise_mod
from rite_ai.managers import session as session_module
from rite_ai.managers.session import (
    CRASHED,
    FINISHED,
    QUIT,
    UNCLEAR,
    Attachment,
    Ending,
    Liveness,
    StartResult,
    Stopped,
    ending,
    exit_status_available,
    liveness,
    session_exists,
    start,
    was_attached,
)
from rite_ai.managers.supervise import supervise
from rite_ai.managers.transcripts import latest_session_id, project_transcript_dir

tmux_only = pytest.mark.skipif(
    subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
    reason="needs real tmux; mocking it is the bug",
)

# ⚠ NO CAPABILITY SKIP. A `reports_exit_status` mark skipped these tests
# wherever the probe said tmux "does not populate #{pane_dead_status}", and a
# runtime stand-down skipped any single ending that came back without one —
# "what is untested here is tmux, not rite". Both were wrong: on Linux tmux
# records each exit only when the NEXT child of the server exits, `ending` had
# no answer for that, and the skips encoded the defect as expected behaviour.
# `ending` now nudges the server to reap (`_nudge_reap`), so an absent status
# on a real session is a failure here, as it is for a user.


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    return tmp_path


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
        assert how.kind == CRASHED and how.status == 9, shown
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
        shown = "" if how.kind == QUIT else _what_tmux_showed(result.session, how)
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
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


class TestTheExitStatusSurvivesACommandThatDiesAtOnce:
    """C15. `remain-on-exit` was set by a SECOND tmux call after
    `new-session` returned, with `check=False`. A command that exited first
    took its window with it; the call failed ("no such window") silently and
    the exit status was gone. Measured through `start` with `true`: the dead
    session was held 2/20 before, 20/20 after chaining the option into the
    same tmux command. Under load a slower command loses the same race, which
    is the candidate cause for this file's load-sensitive failure."""

    @tmux_only
    def test_every_launch_keeps_its_dead_pane_and_status(self, tmp_path):
        from rite_ai.managers.session import session_name

        for i in range(10):
            root = tmp_path / f"p{i}"
            (root / ".rite").mkdir(parents=True)
            start(root, "lead", command="true", max_sessions=1)
            name = session_name(root, "lead")
            try:
                assert session_exists(name), (
                    f"launch {i}: the session was destroyed before "
                    "remain-on-exit took effect, so its exit status is gone"
                )
                assert ending(name, human_was_present=False).status == 0
            finally:
                subprocess.run(["tmux", "kill-session", "-t", f"={name}"])


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

    @pytest.mark.parametrize(
        "root, claude_named_it",
        [
            (
                "/private/var/folders/4p/fvll8_4s5dsczftjs1dxfpz00000gn/T/permchk-7js6aj5r",
                "-private-var-folders-4p-fvll8-4s5dsczftjs1dxfpz00000gn-T-permchk-7js6aj5r",
            ),
            (
                "/Users/u/PapugaAI/deployment/.claude/worktrees/infallible-easley-86b93f",
                "-Users-u-PapugaAI-deployment--claude-worktrees-infallible-easley-86b93f",
            ),
        ],
        ids=["underscore", "dot"],
    )
    def test_the_directory_name_matches_what_claude_code_writes(
        self, tmp_path, root, claude_named_it
    ):
        """The expected names are COPIED from directories Claude Code created
        on a real machine, not derived from the rule — a test that derives
        its expectation from the function under test can only agree with it.
        rite replaced only `/`, so for these it looked in a directory that
        did not exist and never found a session to resume."""
        assert project_transcript_dir(Path(root), base=tmp_path).name == claude_named_it

    def test_the_id_comes_from_the_FILE_CONTENTS_not_the_name(self, tmp_path):
        """A stray `.jsonl` would otherwise become an id nobody can resume."""
        directory = project_transcript_dir(Path("/a/b"), base=tmp_path)
        directory.mkdir(parents=True)
        (directory / "not-a-session.jsonl").write_text('{"noise": 1}\n')
        assert latest_session_id(Path("/a/b"), base=tmp_path) == ""

        (directory / "real.jsonl").write_text('{"sessionId": "abc-123"}\n')
        assert latest_session_id(Path("/a/b"), base=tmp_path) == "abc-123"

    # ⚠ **TIES ARE MADE EXACT HERE, NOT HOPED FOR.** On Linux two writes in
    # one clock tick share an mtime to the nanosecond (197 of 200 in a
    # container), and the test above failed 24 of 30 runs there while passing
    # on CI by luck. These set identical mtimes with `os.utime`, and every
    # case runs with the names SWAPPED, so a tie broken by directory order
    # fails one of the two orders on every filesystem — a test that could
    # pass with the tie-break removed would prove nothing.

    @staticmethod
    def _session(directory, name, session_id, *moments):
        lines = [{"sessionId": session_id, "type": "summary"}]
        lines += [{"sessionId": session_id, "timestamp": m} for m in moments]
        path = directory / f"{name}.jsonl"
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return path

    @staticmethod
    def _tie(*paths):
        for path in paths:
            os.utime(path, ns=(1_790_000_000_123_456_789,) * 2)

    @pytest.mark.parametrize("stray, real", [("a", "b"), ("b", "a")])
    def test_a_stray_file_tied_with_a_session_does_not_hide_it(
        self, tmp_path, stray, real
    ):
        directory = project_transcript_dir(Path("/a/b"), base=tmp_path)
        directory.mkdir(parents=True)
        (directory / f"{stray}.jsonl").write_text('{"noise": 1}\n')
        self._session(directory, real, "abc-123", "2026-09-26T10:00:00.000Z")
        self._tie(*directory.glob("*.jsonl"))
        assert latest_session_id(Path("/a/b"), base=tmp_path) == "abc-123"

    @pytest.mark.parametrize("older, newer", [("a", "b"), ("b", "a")])
    def test_two_tied_sessions_are_ordered_by_what_they_recorded(
        self, tmp_path, older, newer
    ):
        """The case that resumed the wrong conversation."""
        directory = project_transcript_dir(Path("/a/b"), base=tmp_path)
        directory.mkdir(parents=True)
        self._session(directory, older, "old-session", "2026-09-26T10:00:00.000Z")
        self._session(directory, newer, "new-session", "2026-09-26T10:00:00.001Z")
        self._tie(*directory.glob("*.jsonl"))
        assert latest_session_id(Path("/a/b"), base=tmp_path) == "new-session"

    @pytest.mark.parametrize("older, newer", [("a", "b"), ("b", "a")])
    def test_the_latest_moment_counts_not_the_last_line(self, tmp_path, older, newer):
        """176 of 213 real transcripts go backwards somewhere in the file."""
        directory = project_transcript_dir(Path("/a/b"), base=tmp_path)
        directory.mkdir(parents=True)
        self._session(directory, older, "old-session", "2026-09-26T10:00:05.000Z")
        self._session(
            directory,
            newer,
            "new-session",
            "2026-09-26T10:00:09.000Z",
            "2026-09-26T09:00:00.000Z",
        )
        self._tie(*directory.glob("*.jsonl"))
        assert latest_session_id(Path("/a/b"), base=tmp_path) == "new-session"

    @pytest.mark.parametrize(
        "first, second",
        [
            ("2026-09-26T10:00:00.000Z", "2026-09-26T10:00:00.000Z"),
            ("2026-09-26T10:00:00.000Z", None),
        ],
        ids=["same-moment", "one-has-no-timestamp"],
    )
    def test_sessions_that_cannot_be_ordered_are_refused_not_guessed(
        self, tmp_path, first, second
    ):
        directory = project_transcript_dir(Path("/a/b"), base=tmp_path)
        directory.mkdir(parents=True)
        self._session(directory, "a", "one", first)
        self._session(directory, "b", "two", *([second] if second else []))
        self._tie(*directory.glob("*.jsonl"))
        assert latest_session_id(Path("/a/b"), base=tmp_path) == ""

    def test_a_tie_in_float_seconds_is_not_a_tie_in_nanoseconds(self, tmp_path):
        """`st_mtime` is a float and rounds; the newer file must still win
        on its mtime alone, whatever its contents say."""
        directory = project_transcript_dir(Path("/a/b"), base=tmp_path)
        directory.mkdir(parents=True)
        old = self._session(directory, "a", "old-session", "2026-09-26T11:00:00.000Z")
        new = self._session(directory, "b", "new-session", "2026-09-26T10:00:00.000Z")
        os.utime(old, ns=(1_790_000_000_123_456_700,) * 2)
        os.utime(new, ns=(1_790_000_000_123_456_789,) * 2)
        assert latest_session_id(Path("/a/b"), base=tmp_path) == "new-session"

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
            supervise_mod,
            "ending",
            lambda _n, human_was_present, pane="": Ending(FINISHED),
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

        def starter(
            root,
            manager,
            *,
            engine,
            resume_id,
            max_sessions,
            window_seconds,
            prompt="",
            permission="",
            agent="",
        ):
            seen.append(resume_id)
            return StartResult(True, "ok", session=f"s{len(seen)}")

        monkeypatch.setattr(
            supervise_mod, "liveness", lambda _n: Liveness(False, known=True)
        )
        monkeypatch.setattr(supervise_mod, "was_attached", lambda _n: False)
        monkeypatch.setattr(
            supervise_mod,
            "ending",
            lambda _n, human_was_present, pane="": Ending(FINISHED),
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
        # ⚠ **A VIRTUAL CLOCK, because the wait is now time-bounded.** Stubbing
        # `sleep` to a no-op was right for a fixed READ COUNT and turns a
        # deadline loop into a busy spin — measured: 915,421 reads inside a
        # 0.6s budget. Sleeping advances this clock instead, so the deadline is
        # exercised exactly and the test does not depend on the wall clock,
        # which is what made the old one look flaky under load.
        self._clock = {"now": 0.0}
        monkeypatch.setattr(
            session_mod.time,
            "sleep",
            lambda s: self._clock.__setitem__("now", self._clock["now"] + s),
        )
        monkeypatch.setattr(session_mod.time, "monotonic", lambda: self._clock["now"])
        # Existence is a SEPARATE question, answered by its own call, and
        # these tests are about what `ending` does once the session is
        # known to exist. Stubbed rather than scripted so the read counts
        # below stay about the status polling and nothing else.
        monkeypatch.setattr(session_mod, "session_exists", lambda _n: True)
        # `ending` asks the attachment question itself rather than trusting
        # only the sampled flag, and that is another subprocess call on the
        # same `display-message`; answered here so the read counts below
        # stay about the status polling.
        #
        # ⚠ Stubbed as a KNOWN "nobody", not left to the scripted replies.
        # `fake_run` answers every call with the same canned string, so the
        # attachment probe used to receive `1|0` and parse it as False —
        # and the test passed through a mis-parse. Now that an unreadable
        # count is `known=False`, that shows up as `unclear`, which is the
        # probe being honest rather than this test's subject changing.
        monkeypatch.setattr(
            session_mod, "attachment", lambda _n: Attachment(False, known=True)
        )
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
        unknown into a known; it must never turn an unknown into a guess.

        ⚠ **THE BUDGET IS TIME, NOT A READ COUNT, and this test asserts the
        time.** It used to assert `len(seen) == STATUS_READS`, which pinned the
        stand-in rather than the property: a fixed count is long enough on an
        idle machine and short enough on a loaded one, which is the defect a
        real run hit — a Manager that exited cleanly was reported `unclear` on
        a 4-core box under load.
        """
        seen = self._replies(monkeypatch, ["1|"])
        monkeypatch.setattr(session_module, "STATUS_DEADLINE", 1.5)
        how = ending("any-session", human_was_present=False)
        waited = self._clock["now"]

        assert how.kind == UNCLEAR
        assert not how.resume, "waiting was allowed to invent a clean exit"
        assert waited >= 1.5, f"it gave up after {waited:.2f}s, before its deadline"
        assert len(seen) >= 2, "it did not keep asking while the budget lasted"
        # ⚠ Backoff, not a hot loop. Pauses double from 0.1s, so 1.5s is filled
        # by about five reads; a fixed 0.1s pause would take fifteen, and
        # hammering a loaded box with subprocess round-trips is the thing the
        # deadline exists to avoid.
        assert len(seen) <= 8, f"{len(seen)} reads in {waited:.1f}s — not backing off"

    def test_the_deadline_always_buys_at_least_one_read(self, monkeypatch):
        """However tight the budget, one observation is always taken — so a
        zero or negative deadline cannot turn into "never looked"."""
        seen = self._replies(monkeypatch, ["1|"])
        monkeypatch.setattr(session_module, "STATUS_DEADLINE", 0.0)
        how = ending("any-session", human_was_present=False)
        assert how.kind == UNCLEAR
        assert len(seen) == 1, f"{len(seen)} reads for a zero deadline"

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

    def test_a_dead_pane_without_a_status_nudges_the_server(self, monkeypatch):
        """🔴 tmux on Linux records an exit only when the NEXT child exits, so
        the last Manager to end was never read. `ending` sends the server the
        SIGCHLD it did not act on, and the status then arrives."""
        import signal

        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(
            session_module.os, "kill", lambda pid, sig: sent.append((pid, sig))
        )
        self._replies(monkeypatch, ["1|||4242", "1|0||4242"])
        how = ending("any-session", human_was_present=False)
        assert how.kind == FINISHED, how.detail
        assert sent == [(4242, signal.SIGCHLD)], sent

    def test_nothing_is_signalled_without_a_readable_server_pid(self, monkeypatch):
        """A pid tmux did not give — an older tmux, a garbled reply — is not
        guessed at. The wait carries on exactly as before, then `unclear`."""
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(
            session_module.os, "kill", lambda pid, sig: sent.append((pid, sig))
        )
        for reply in ("1|", "1|||", "1|||#{pid}", "1|||1", "1|||0"):
            self._replies(monkeypatch, [reply])
            monkeypatch.setattr(session_module, "STATUS_DEADLINE", 0.5)
            assert ending("any-session", human_was_present=False).kind == UNCLEAR
        assert sent == [], sent

    def test_a_live_pane_is_never_signalled(self, monkeypatch):
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(
            session_module.os, "kill", lambda pid, sig: sent.append((pid, sig))
        )
        self._replies(monkeypatch, ["0|||4242"])
        ending("any-session", human_was_present=False)
        assert sent == []


@tmux_only
def test_the_last_exit_on_the_server_is_read_without_standing_down(project):
    """🔴 The defect behind "a Manager under load ends `unknown`", against
    real tmux and with NO stand-down.

    The real-tmux tests here used to skip when tmux supplied no status, on
    the grounds that it was tmux's failing and not rite's. On Linux that
    failing was systematic — each status arrives one child exit late — so
    the skip was hiding the one ending that matters: the last thing on the
    server to stop. `ending` now nudges the server to reap, and nothing in
    this file stands down on an absent status any more.
    """
    result = start(project, "lead", command="sh", max_sessions=1)
    assert result.ok, result.message
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", result.session, "exit 7", "Enter"],
            capture_output=True,
        )
        for _ in range(50):
            if not liveness(result.session).alive:
                break
            time.sleep(0.1)
        how = ending(result.session, human_was_present=False, pane=result.pane)
        shown = "" if how.kind == CRASHED else _what_tmux_showed(result.session, how)
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", result.session], capture_output=True
        )
    assert how.kind == CRASHED and how.status == 7, shown


@tmux_only
def test_was_attached_is_TRUE_for_a_real_attached_client():
    """⚠ **The branch that was reasoned rather than measured**, and the fix
    that depends on it was incomplete until it was.

    `was_attached` is what tells a human typing `exit` from an agent
    finishing — both are exit status 0 — so if it never fired in practice a
    human quitting would read as FINISHED and the supervisor would resume,
    which is the exact behaviour it was added to stop. The FALSE direction
    was confirmed early; every attempt at the TRUE one returned
    `list-clients: (none)`, including a `pty.fork`, and it shipped with the
    docstring saying so.

    **A tmux PANE is a real terminal.** Attaching to the target from inside
    another tmux session gives a genuine client on a genuine tty — which
    `pty.fork` never managed — and `$TMUX` must be cleared in the pane or
    tmux refuses the nested attach and the client silently never appears.
    That refusal is what made the first version of this measurement report
    `(none)` and look like another failure.

    Measured: `/dev/ttys015 … (attached,focused,UTF-8)`, and True.
    """
    target = f"wa-target-{uuid.uuid4().hex[:6]}"
    host = f"wa-host-{uuid.uuid4().hex[:6]}"

    def tx(*args):
        return subprocess.run(["tmux", *args], capture_output=True, text=True)

    try:
        tx("new-session", "-d", "-s", target, "sh")
        time.sleep(0.5)
        assert not was_attached(target), "nothing is attached yet"

        tx("new-session", "-d", "-s", host, "sh")
        time.sleep(0.5)
        # `TMUX=` or tmux refuses to nest and no client ever appears.
        tx("send-keys", "-t", host, f"TMUX= tmux attach -t {target}", "Enter")
        for _ in range(20):
            time.sleep(0.2)
            if "attached" in tx("list-clients", "-t", target).stdout:
                break
        else:
            pytest.skip(
                "no client could be attached in this environment, so there "
                "is nothing of rite's to assert — `was_attached`'s FALSE "
                "direction is covered by the tests above"
            )

        assert was_attached(target) is True, (
            "a real attached client on a real tty read as nobody there — a "
            "human typing `exit` would be resumed over"
        )
        tx("send-keys", "-t", host, "C-b", "d")
        for _ in range(15):
            time.sleep(0.2)
            if not was_attached(target):
                break
        assert not was_attached(target), "it stayed True after the client left"
    finally:
        tx("kill-session", "-t", target)
        tx("kill-session", "-t", host)


@tmux_only
class TestOneNameIsNotAnotherNamesPrefix:
    """⚠ tmux `-t <name>` is NOT an exact match — it falls back to fnmatch
    and then to PREFIX matching.

    With only `leader` running, measured on tmux 3.7c:

        tmux display-message -p -t lead '#{session_name}'  ->  'leader'

    So every `-t` question about a Manager called `lead` was answered about
    `leader`. For `ending` that means reading ANOTHER session's exit status
    and, if that neighbour finished cleanly and unattended, returning
    FINISHED — the one verdict that resumes. `pm` beside `pm2` is enough,
    and Manager names are chosen by users, not by rite.

    `=` is tmux's exact-match prefix and works on `has-session`. It must NOT
    be used on `display-message`, which wants a pane target and expands
    EMPTY for a session that genuinely exists — pinning it there would
    reintroduce the bug. Hence existence and content are two calls.
    """

    @pytest.fixture
    def only_leader(self):
        name = f"leader-{uuid.uuid4().hex[:6]}"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "sh"], capture_output=True
        )
        time.sleep(0.5)
        yield name
        subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def test_a_prefix_of_a_live_session_does_not_exist(self, only_leader):
        from rite_ai.managers.session import session_exists

        assert session_exists(only_leader)
        assert not session_exists(only_leader[:-1]), (
            "a prefix of a running session reported as existing, so every "
            "`-t` question about it is answered about the other session"
        )

    def test_ending_does_not_read_a_neighbours_exit_status(self, only_leader):
        how = ending(only_leader[:-1], human_was_present=False)
        assert how.kind == UNCLEAR, (
            f"a Manager whose name is a prefix of a LIVE neighbour read that "
            f"neighbour's ending as {how.kind!r}"
        )
        assert not how.resume, "and it would have been resumed"

    def test_pane_pid_does_not_record_a_neighbours_pid(self, only_leader):
        from rite_ai.managers.session import pane_pid

        assert pane_pid(only_leader) > 0
        assert pane_pid(only_leader[:-1]) == 0, (
            "a recorded value naming the wrong thing — §9.14.10"
        )

    def test_was_attached_does_not_answer_about_a_neighbour(self, only_leader):
        assert was_attached(only_leader[:-1]) is False


@tmux_only
class TestEndingAsksAboutTheManagersOwnPane:
    """⚠ Three defects found by a hostile review, each verified against real
    tmux before and after.

    All three share a shape: **the oracle knew and the caller had stopped
    listening.** `-t <session>` answers about the session's ACTIVE pane, not
    the Manager's; `#{pane_dead_signal}` carries `kill` in the same call
    that leaves `#{pane_dead_status}` empty; and `was_attached` fires while
    the sampled flag has already been read away.

    None was caught by 100 acceptance tests, because every multi-cycle test
    monkeypatches `ending`, `liveness` and `was_attached` at once — so the
    composition never ran.
    """

    def test_a_scratch_pane_exiting_is_not_the_manager_finishing(self, tmp_path):
        """A human attaches, runs `tmux split-window`, and that shell exits
        0. Before: `finished`, `resume=True` — the supervisor would have
        resumed a Manager that was still working."""
        (tmp_path / ".rite").mkdir(exist_ok=True)
        made = start(tmp_path, "m", command="sh", max_sessions=1)
        assert made.ok and made.pane, made.message
        try:
            subprocess.run(
                ["tmux", "split-window", "-t", made.session, "-d", "sh"],
                capture_output=True,
            )
            time.sleep(0.4)
            panes = subprocess.run(
                ["tmux", "list-panes", "-t", made.session, "-F", "#{pane_id}"],
                capture_output=True,
                text=True,
            ).stdout.split()
            other = [x for x in panes if x != made.pane]
            if not other:
                pytest.skip("could not create a second pane")
            subprocess.run(
                ["tmux", "send-keys", "-t", other[0], "exit 0", "Enter"],
                capture_output=True,
            )
            time.sleep(1.0)
            how = ending(made.session, human_was_present=False, pane=made.pane)
            assert how.kind != FINISHED, (
                "a scratch pane exiting 0 read as the Manager finishing — the "
                "supervisor would resume a Manager that is still working"
            )
            assert not how.resume
        finally:
            subprocess.run(
                ["tmux", "kill-session", "-t", made.session], capture_output=True
            )

    def test_a_killed_manager_is_a_crash_not_an_ambiguity(self, tmp_path):
        """⚠ `#{pane_dead_status}` is empty for a signalled death and
        `#{pane_dead_signal}` says `kill`. Before: UNCLEAR, and because
        `_stopped_because` treats anything but `crashed` as success, `rite
        start` exited 0 on an OOM-killed Manager. §9.14.4 requires a fault
        be distinguishable from a completion.

        ⚠ **THE "KNOWN LOAD-SENSITIVE" LABEL IS WITHDRAWN.** It carried one,
        on the evidence that it failed once on a GitHub runner and passed 3/3
        in a container. That reading was wrong: the test was faithfully
        reproducing a real defect, and the label would have taught the next
        person to ignore the thing that found it.

        Measured 2026-09-26 on a 4-core Ubuntu VM running two Managers and an
        8B local model: a Manager that exited CLEANLY — `pane_dead=1`,
        `pane_dead_status=0`, `pane_dead_signal=` empty, no OOM kill in dmesg
        or the journal — was reported `unclear`, and a routed message had
        nowhere to land because nothing resumed. On the same box IDLE the
        status appears about 1.0s after the exit and the run reports correctly.

        The cause was `ending`'s fixed budget of 30 reads 0.1s apart: long
        enough for an unloaded machine, short enough for a loaded one. It is a
        TIME budget with backoff now (`STATUS_DEADLINE`), so this test and the
        product no longer share a too-tight constant."""
        import os
        import signal as signals

        (tmp_path / ".rite").mkdir(exist_ok=True)
        made = start(tmp_path, "k", command="sh", max_sessions=1)
        assert made.ok and made.pane, made.message
        try:
            pid = subprocess.run(
                ["tmux", "display-message", "-p", "-t", made.pane, "#{pane_pid}"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            os.kill(int(pid), signals.SIGKILL)
            # ⚠ Bounded by TIME, not by a read count, for the same reason
            # `ending` is: a count is long enough on an idle box and short on a
            # loaded one. This waits for the pane to die; `ending` then does
            # its own time-bounded wait for the status.
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if not liveness(made.session).alive:
                    break
                time.sleep(0.1)
            how = ending(made.session, human_was_present=False, pane=made.pane)
            assert how.kind == CRASHED, (
                f"a SIGKILLed Manager reported {how.kind!r} — tmux knew, and "
                f"the caller did not ask: {how.detail}"
            )
            assert not how.resume
            assert "SIGKILL" in how.detail
        finally:
            subprocess.run(
                ["tmux", "kill-session", "-t", made.session], capture_output=True
            )


@tmux_only
class TestTheSecondCycleActuallyStarts:
    """⚠ **REAL tmux, REAL starter, two cycles.** The reason the resume path
    shipped unable to run is that every multi-cycle test above injects
    `starter`, so `tmux new-session` was never called a SECOND time anywhere
    in the suite — and the failure is entirely in the second call.

    Measured before the fix: cycle 1 ended FINISHED, `resume_from` was the
    right id, and cycle 2 died on `duplicate session:
    rite-mgr-<project>-lead`. `start` sets `remain-on-exit on` so the exit
    status survives for `ending` to read (§9.14.4) — which keeps the dead
    session ALIVE under the name the next cycle needs, because
    `session_name` is deterministic. The two halves of this release were
    mutually exclusive:

        remain-on-exit on   -> FINISHED is reachable, the name is taken
        remain-on-exit off  -> the name is free, ending() is always UNCLEAR

    So a test that fakes the starter cannot close this, and neither can one
    that tears the session down itself before the second `start` — that is
    the test supplying the step production was missing.
    """

    def _agent(self, tmp_path: Path) -> Path:
        """Exits cleanly, unattended, after outliving `settled_alive`."""
        agent = tmp_path / "agent.sh"
        agent.write_text("#!/bin/sh\nsleep 3\nexit 0\n")
        agent.chmod(0o755)
        return agent

    def _transcripts(self, monkeypatch, root: Path, tmp_path: Path) -> None:
        """A transcript newer than every cycle, so the id is never the
        reason a cycle does not start."""
        import rite_ai.managers.transcripts as transcripts_mod

        base = tmp_path / "transcripts"
        directory = project_transcript_dir(root, base)
        directory.mkdir(parents=True)
        written = directory / f"{uuid.uuid4()}.jsonl"
        written.write_text(json.dumps({"sessionId": written.stem}) + "\n")
        import os

        later = time.time() + 3600
        os.utime(written, (later, later))
        monkeypatch.setattr(transcripts_mod, "default_transcripts_dir", lambda: base)

    def test_a_second_cycle_starts_instead_of_colliding_with_the_first(
        self, project, tmp_path, monkeypatch
    ):
        from rite_ai.managers.session import session_name, stop

        self._transcripts(monkeypatch, project, tmp_path)
        name = session_name(project, "lead")
        stop(name)
        try:
            result = supervise(
                project,
                "lead",
                engine=str(self._agent(tmp_path)),
                max_sessions=2,
                window_seconds=0,
                poll=0.3,
            )
        finally:
            stop(name)

        assert len(result.cycles) == 2, (
            f"only {len(result.cycles)} cycle(s) ran and the supervisor said "
            f"{result.reason!r}. A resume that cannot take the session name "
            f"back is a resume that never happens."
        )
        assert "duplicate session" not in result.reason, result.reason
        assert result.cycles[0].resumed_from == ""
        assert result.cycles[1].resumed_from, (
            "cycle 2 started without an id, so it began a FRESH context"
        )
        assert result.ok, result.reason


class TestTheDefaultResumeIdIsCalled:
    """⚠ **The default itself, not an injected stand-in.**

    Every other supervisor test passes `resume_id_for`, so
    `_default_resume_id` had no test that ran it: replacing its body with
    `return ""` — the regression its own docstring recounts, where every
    "resume" ran a bare `claude` with a fresh context — left the whole
    suite green. An injectable whose default is never exercised is the
    uncalled function that docstring is about, one layer up.

    `starter` is still faked here because tmux is not the subject and this
    must run everywhere;
    `TestTheSecondCycleActuallyStarts` covers the same default against real
    tmux and skips where tmux cannot report a status, which is exactly
    where this one still has to work.
    """

    def _transcript(self, monkeypatch, root: Path, tmp_path: Path, sid: str) -> None:
        import os

        import rite_ai.managers.transcripts as transcripts_mod

        base = tmp_path / "transcripts"
        directory = project_transcript_dir(root, base)
        directory.mkdir(parents=True)
        written = directory / f"{sid}.jsonl"
        written.write_text(json.dumps({"sessionId": sid}) + "\n")
        later = time.time() + 3600
        os.utime(written, (later, later))
        monkeypatch.setattr(transcripts_mod, "default_transcripts_dir", lambda: base)

    def _run(self, project, monkeypatch, seen):
        def starter(
            root,
            manager,
            *,
            engine,
            resume_id,
            max_sessions,
            window_seconds,
            prompt="",
            permission="",
            agent="",
        ):
            seen.append(resume_id)
            return StartResult(True, "ok", session=f"s{len(seen)}")

        monkeypatch.setattr(
            supervise_mod, "liveness", lambda _n: Liveness(False, known=True)
        )
        monkeypatch.setattr(supervise_mod, "was_attached", lambda _n: False)
        monkeypatch.setattr(
            supervise_mod,
            "ending",
            lambda _n, human_was_present, pane="": Ending(FINISHED),
        )
        monkeypatch.setattr(
            supervise_mod, "stop_session", lambda _s: Stopped(True, True)
        )
        monkeypatch.setattr(supervise_mod, "forget_instance", lambda _r, _m: None)
        return supervise(
            project,
            "lead",
            engine="claude",
            max_sessions=3,
            window_seconds=0,
            starter=starter,
            poll=0,
        )

    def test_the_id_reaches_the_starter_without_being_injected(
        self, project, tmp_path, monkeypatch
    ):
        sid = "07719abc-1ecf-443d-a4a0-f9a9ae6cd314"
        self._transcript(monkeypatch, project, tmp_path, sid)
        seen: list[str] = []
        self._run(project, monkeypatch, seen)
        assert seen == ["", sid, sid], (
            "cycle 2 did not receive the id `_default_resume_id` reads from "
            "the transcript — every later cycle would start a FRESH context"
        )

    def test_a_transcript_whose_id_is_hostile_stops_rather_than_resuming(
        self, project, tmp_path, monkeypatch
    ):
        """A `sessionId` that is not shaped like one is not an id, so there
        is nothing to resume — and the supervisor stops rather than
        silently starting a fresh context or building a shell command out
        of it."""
        import os

        import rite_ai.managers.transcripts as transcripts_mod

        base = tmp_path / "transcripts"
        directory = project_transcript_dir(project, base)
        directory.mkdir(parents=True)
        written = directory / "stray.jsonl"
        written.write_text(json.dumps({"sessionId": "abc$(touch owned)"}) + "\n")
        later = time.time() + 3600
        os.utime(written, (later, later))
        monkeypatch.setattr(transcripts_mod, "default_transcripts_dir", lambda: base)

        seen: list[str] = []
        result = self._run(project, monkeypatch, seen)
        assert seen == [""], seen
        assert "no transcript was found to resume from" in result.reason
        assert not (Path.cwd() / "owned").exists()


class TestAnUnanswerableAttachmentDoesNotResume:
    """⚠ **The `liveness()` bug, in the other signal of the same module.**

    A clean exit is QUIT if somebody was attached and FINISHED if nobody
    was — the same exit status either way, so the attachment probe IS the
    distinction. It used to answer a bare False for every way of failing:
    tmux missing, a timeout, a refusal, an unreadable reply. False means
    FINISHED, and FINISHED is the one verdict that RESUMES — so a probe
    that merely could not run restarted the session a human had just quit,
    which is the behaviour the probe was added to prevent.

    `Ending`'s own docstring is the rule this enforces: **`resume` is False
    whenever the answer is not certain.**
    """

    @pytest.mark.parametrize(
        "why",
        [
            "tmux not found",
            "tmux refused: server exited unexpectedly",
            "tmux answered '', which is not a client count",
        ],
    )
    def test_a_probe_that_could_not_answer_is_UNCLEAR_not_FINISHED(
        self, monkeypatch, why
    ):
        how = _clean_exit_ending(
            monkeypatch, Attachment(False, known=False, detail=why)
        )
        assert how.kind == UNCLEAR, (
            f"an unanswerable attachment probe landed on {how.kind}; "
            "FINISHED would have resumed a session a human may have quit"
        )
        assert not how.resume
        assert why in how.detail

    def test_a_probe_that_answered_nobody_is_still_FINISHED(self, monkeypatch):
        """The fix must not turn the ordinary case into a refusal — that
        would stop every resume rather than the uncertain ones."""
        how = _clean_exit_ending(monkeypatch, Attachment(False, known=True))
        assert how.kind == FINISHED and how.resume, how.detail

    def test_a_probe_that_answered_somebody_is_QUIT(self, monkeypatch):
        how = _clean_exit_ending(monkeypatch, Attachment(True, known=True))
        assert how.kind == QUIT, how.detail

    def test_an_unanswerable_probe_is_QUIT_when_a_human_was_sampled(self, monkeypatch):
        """The sampled flag is independent evidence and still decides."""
        how = _clean_exit_ending(
            monkeypatch,
            Attachment(False, known=False, detail="x"),
            human_was_present=True,
        )
        assert how.kind == QUIT, how.detail


def _clean_exit_ending(monkeypatch, answer, human_was_present=False):
    """Drive `ending` to its exit-status-0 branch with scripted tmux
    replies, so the attachment question is the only thing that varies."""
    import rite_ai.managers.session as session_mod

    class _Reply:
        returncode = 0
        stdout = "1|0|"
        stderr = ""

    monkeypatch.setattr(session_mod, "_tmux", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(session_mod, "session_exists", lambda _n: True)
    monkeypatch.setattr(session_mod.subprocess, "run", lambda *a, **k: _Reply())
    monkeypatch.setattr(session_mod, "attachment", lambda _n: answer)
    return session_mod.ending("mgr", human_was_present=human_was_present)


@tmux_only
class TestALeftoverFromARunThatEndedBadly:
    """⚠ **The state a crashed run leaves, reached the way a user reaches
    it.**

    `start` sets `remain-on-exit on` so the exit status survives for
    `ending` to read — which means a session OUTLIVES the agent inside it.
    If the supervisor is then killed without running its teardown (a closed
    terminal, a SIGKILL, a reboot — anything that is not Ctrl-C, which
    `_torn_down` handles), what is left behind is a session holding the
    Manager's deterministic name with a DEAD pane and no instance record.

    Both of `start`'s guards pass on that state, correctly and for good
    reasons: `liveness` says not alive, because nothing is running, and
    `running` says None, because there is no record. So it fell through to
    `tmux new-session` and the operator got

        tmux refused to start the session: duplicate session: rite-mgr-…

    which names no remedy, for a situation one `rite manager stop` clears.

    ⚠ **Reached by the SEQUENCE, not by building the condition.** A fixture
    that hand-makes a dead session proves the guard fires on something
    shaped like the state; this starts a real Manager, lets its agent exit,
    and drops the record, which is what actually happens.
    """

    def _agent_that_exits(self, tmp_path: Path) -> Path:
        agent = tmp_path / "exits.sh"
        agent.write_text("#!/bin/sh\nsleep 3\nexit 0\n")
        agent.chmod(0o755)
        return agent

    def _leftover(self, project: Path, tmp_path: Path) -> str:
        """Start a Manager for real, let it end, then lose the record."""
        from rite_ai.managers import forget_instance
        from rite_ai.managers.session import ending, liveness

        first = start(
            project,
            "lead",
            command=str(self._agent_that_exits(tmp_path)),
            max_sessions=1,
            window_seconds=0,
        )
        assert first.ok, first.message
        for _ in range(60):
            if not liveness(first.session).alive:
                break
            time.sleep(0.2)
        how = ending(first.session, human_was_present=False, pane=first.pane)
        assert how.kind == FINISHED, (
            f"the agent did not end cleanly, so this is not the state under "
            f"test: {how.kind} — {how.detail}"
        )
        # The supervisor is killed here: no teardown, so the session stays
        # and the record goes with the process that would have cleared it.
        forget_instance(project, "lead")
        return first.session

    def test_the_refusal_names_the_remedy(self, project, tmp_path):
        from rite_ai.managers.session import stop

        session = self._leftover(project, tmp_path)
        try:
            again = start(
                project, "lead", command="sh", max_sessions=1, window_seconds=0
            )
            assert not again.ok, "a leftover session was silently taken over"
            assert "duplicate session" not in again.message, (
                "the operator got tmux's own words, which name no remedy: "
                f"{again.message!r}"
            )
            assert "rite manager stop lead" in again.message, (
                "the refusal does not name the command that clears this — "
                f"{again.message!r}"
            )
            # It must say the session is FINISHED, not imply work is at risk:
            # this is the case that is safe to clear, unlike a live one.
            assert "finished" in again.message.lower(), again.message
        finally:
            stop(session)

    def test_a_LIVE_leftover_still_refuses_to_adopt_or_kill(self, project, tmp_path):
        """The other half of the same guard, and it must NOT be told to run
        `rite manager stop`: a live session is somebody's work, and the
        existing advice — look at it, then decide — is right for it."""
        from rite_ai.managers import forget_instance
        from rite_ai.managers.session import stop

        alive = start(
            project, "lead", command="sleep 120", max_sessions=1, window_seconds=0
        )
        assert alive.ok, alive.message
        forget_instance(project, "lead")
        try:
            again = start(
                project, "lead", command="sh", max_sessions=1, window_seconds=0
            )
            assert not again.ok
            assert "rite will not adopt or kill it" in again.message, again.message
            assert "rite manager stop" not in again.message, (
                "a LIVE leftover was offered a command that would end it"
            )
        finally:
            stop(alive.session)


@tmux_only
class TestAStartThatFailedLeavesTheSameLeftover:
    """⚠ **The likeliest way a dogfood operator meets this, and it needs no
    crash at all.**

    `start` creates the session, then `settled_alive` finds the command
    already gone and reports failure — but the SESSION is still there, held
    by `remain-on-exit` with a dead pane and no record, because the failure
    path returns before anything is recorded. So an engine that exits at
    once — no Claude login, a missing binary, a bad flag — produces this
    state on the first run.

    Before the guard below, the operator then saw two unrelated errors in a
    row: "the session started and exited immediately" and, on the retry
    after fixing it, "duplicate session: rite-mgr-…". The second told them
    nothing about the first, and named no remedy.
    """

    def test_the_retry_after_an_immediate_exit_explains_itself(self, project, tmp_path):
        from rite_ai.managers.session import (
            session_exists,
            session_name,
            stop,
        )

        engine = tmp_path / "not-logged-in.sh"
        engine.write_text("#!/bin/sh\necho 'not logged in'\nexit 1\n")
        engine.chmod(0o755)
        name = session_name(project, "lead")
        stop(name)
        try:
            first = start(
                project, "lead", command=str(engine), max_sessions=1, window_seconds=0
            )
            assert not first.ok, "this engine was supposed to exit immediately"
            assert "exited immediately" in first.message, first.message

            # ⚠ THE PRECONDITION, MADE EXPLICIT — it does not hold on every
            # platform. This test asserts the RETRY meets a left-over
            # session, which requires the first run's session to survive its
            # command. `start` sets `remain-on-exit` AFTER `new-session`, so
            # an engine that exits instantly can win that race and take the
            # session with it.
            #
            # Measured: it survives on macOS and does NOT on Linux/CI, where
            # this test's first run therefore leaves nothing to collide
            # with. The test never ran on Linux before — the commit that
            # added it failed at LINT in 7s, so the suite never executed —
            # and its first Linux run failed on exactly this.
            #
            # Asserted rather than skipped silently: if the leftover is
            # absent the retry is an ordinary second start, and there is no
            # left-over message to demand. The race itself is a separate
            # finding and is NOT fixed here.
            leftover = session_exists(name)
            second = start(
                project, "lead", command=str(engine), max_sessions=1, window_seconds=0
            )
            assert not second.ok
            assert "duplicate session" not in second.message, (
                "the retry after a failed start handed the operator tmux's "
                f"own words: {second.message!r}"
            )
            if not leftover:
                pytest.skip(
                    "this tmux did not hold the session open after an "
                    "instant exit, so the retry had nothing to collide "
                    "with — the precondition for the left-over message"
                )
            assert "rite manager stop lead" in second.message, second.message
        finally:
            stop(name)
