"""`rite start <manager>` actually starts something — against real tmux.

The twin of `test_loop_start_really_starts.py`, and it exists because a
review found this module had **no tests at all**: "a command that dies
immediately is refused and nothing is recorded" was a hand-run with nothing
keeping it true, in the session-lifecycle path where L-5's facade lived.

Skipped rather than faked where tmux is absent: a mocked version of this
file would reproduce the hole it was written to close.

⚠ `claude` is never a command here. Every Manager session runs its command
for real, and spent quota is the one damage no cleanup reverses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rite_ai.managers import read_instance
from rite_ai.managers.session import (
    Liveness,
    liveness,
    pane_pid,
    running,
    session_name,
    start,
)

# THE HARD FAIL ITS SIBLING HAS, AND THIS FILE DID NOT.
#
# `test_loop_start_really_starts.py` carries a CI-only guard so that removing
# tmux from the workflow goes RED rather than returning its tests to silent
# skips. This file copied that file's `skipif` and not its guard — so two
# thirds of the "prove it against real tmux" family could have vanished from
# CI while the third went red, and the ones that vanished cover `rite pool
# fill` and `rite start <manager>` ACTUALLY STARTING SOMETHING: the exact
# behaviour both commands once reported without doing.
#
# Locally a skip is still right — a contributor without tmux should not be
# blocked. The property guarded is "the machine that gates merges ran these".
_IN_CI = os.environ.get("CI") == "true"
if _IN_CI and shutil.which("tmux") is None:  # pragma: no cover - CI-only guard
    raise RuntimeError(
        "tmux is missing in CI, so the only real coverage of this command "
        "would skip silently — install it in the workflow rather than "
        "letting these tests disappear"
    )

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="needs real tmux; mocking it is the bug"
)

LIVE = "sleep 300"
DIES = "false"


@pytest.fixture
def project(tmp_path: Path):
    (tmp_path / ".rite").mkdir()
    yield tmp_path
    for name in (session_name(tmp_path, m) for m in ("lead", "planner")):
        subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


def test_a_surviving_command_is_started_and_recorded(project):
    result = start(project, "lead", command=LIVE, max_sessions=3)
    assert result.ok, result.message
    assert liveness(result.session).alive
    recorded = read_instance(project, "lead")
    assert recorded is not None
    assert recorded.session == result.session
    assert "tmux attach -t" in result.attach


def test_the_recorded_pid_is_the_PANE_not_ours(project):
    """A draft recorded `os.getpid()` — the CLI's own pid, dead seconds
    later and recyclable. A recorded value naming a different process than
    it claims to is worse than none, because it reads as evidence."""
    import os

    result = start(project, "lead", command=LIVE, max_sessions=1)
    assert result.ok, result.message
    recorded = read_instance(project, "lead")
    assert recorded.pid == pane_pid(result.session)
    assert recorded.pid != os.getpid()
    assert recorded.pid > 0


def test_the_recorded_engine_is_what_RAN(project):
    """`launch = command or engine or "claude"`, so an explicit command
    means the configured engine is not what is running."""
    result = start(project, "lead", engine="claude", command=LIVE, max_sessions=1)
    assert result.ok, result.message
    assert read_instance(project, "lead").engine == LIVE


def test_a_command_that_dies_is_refused_and_records_NOTHING(project):
    result = start(project, "lead", command=DIES, max_sessions=1)
    assert not result.ok
    assert "exited immediately" in result.message
    assert read_instance(project, "lead") is None, (
        "a phantom instance survived a session that never ran — `rite start` "
        "would then refuse forever against something that does not exist"
    )


def test_a_second_start_is_refused_and_says_how_to_reach_the_first(project):
    first = start(project, "lead", command=LIVE, max_sessions=1)
    assert first.ok, first.message
    second = start(project, "lead", command=LIVE, max_sessions=1)
    assert not second.ok
    assert "already running" in second.message
    assert "tmux attach -t" in second.message


def test_a_stale_record_recovers_rather_than_wedging(project):
    """This design's ordinary ending is an ungraceful terminal close, so a
    record whose session is gone is the common morning state. Refusing on it
    would wedge the project until somebody deleted a file by hand."""
    first = start(project, "lead", command=LIVE, max_sessions=1)
    assert first.ok
    subprocess.run(["tmux", "kill-session", "-t", first.session], capture_output=True)
    assert running(project, "lead") is None
    again = start(project, "lead", command=LIVE, max_sessions=1)
    assert again.ok, f"a stale record wedged the Manager: {again.message}"


def test_a_ceiling_of_zero_is_refused_before_anything_starts(project):
    result = start(project, "lead", command=LIVE, max_sessions=0)
    assert not result.ok
    assert "permits no sessions" in result.message
    assert not liveness(session_name(project, "lead")).alive


@pytest.mark.parametrize("hostile", ["../escape", "a/b", "..", ".", ""])
def test_a_hostile_manager_name_never_reaches_tmux(project, hostile):
    """Asserted on the PROPERTY, not the wording. A first version required
    the phrase "one path segment", which `..` does not use — it is refused
    as a directory reference instead. A test that pins the message fails on
    a better message."""
    result = start(project, hostile, command=LIVE, max_sessions=1)
    assert not result.ok
    assert "refusing to start" in result.message
    assert not (project / ".rite" / "managers").exists(), (
        "a refused name still created a directory"
    )


class TestTheDuplicateCheckFailsClosed:
    """D-74. A check that cannot run must refuse, never permit — here a
    false negative starts a second PAID session."""

    def test_an_unanswerable_liveness_check_refuses(self, project, monkeypatch):
        from rite_ai.managers import session as S

        monkeypatch.setattr(
            S, "liveness", lambda _n: Liveness(False, known=False, detail="timed out")
        )
        result = start(project, "lead", command=LIVE, max_sessions=1)
        assert not result.ok, (
            "a liveness check that could not answer permitted a start — "
            "that is the fail-OPEN §5.1.1 forbids, and it would make two "
            "paid Manager sessions"
        )
        assert "cannot tell" in result.message
        assert "timed out" in result.message

    def test_liveness_distinguishes_absent_from_unanswerable(self):
        """Two answers where there must be three: `has-session` returning
        non-zero means NOT RUNNING; failing to run means NOT KNOWN."""
        gone = liveness("rite-mgr-definitely-not-a-real-session-xyz")
        assert gone.known and not gone.alive


class TestTheAuthMessageDoesNotAssertWhatRiteCannotKnow:
    """⚠ Measured on this project's own machine, and the measurement is the
    reason this exists.

    `claude` started INTERACTIVELY in a tmux pane authenticates and runs.
    `claude -p` in the SAME pane, same keychain item, with a TTY, dies
    saying *"OAuth session expired and could not be refreshed"* — with the
    credential valid, eleven days from its last write, against a one-year
    lifetime. The engine named a refusal for a read it never completed.

    Relaying that verbatim would make rite repeat a false claim in the
    first place a new user sees something go wrong. A dogfood operator told
    her credential expired would re-authenticate, fail again, and have no
    way to know why.
    """

    def test_it_says_what_it_cannot_tell(self, monkeypatch):
        import rite_ai.managers.session as s

        monkeypatch.setattr(
            s,
            "_pane_text",
            lambda _n: (
                "Failed to authenticate: OAuth session expired and "
                "could not be refreshed"
            ),
        )
        msg = s._why_the_engine_died("sess", "claude")
        assert "cannot tell" in msg, "rite asserted a cause it cannot establish"
        assert "look identical" in msg or "identical" in msg

    def test_it_gives_the_command_that_discriminates(self, monkeypatch):
        """A message that names a problem without the one command that
        separates its two causes leaves the user to guess, and the obvious
        guess — re-authenticate — is the wrong one here."""
        import rite_ai.managers.session as s

        monkeypatch.setattr(s, "_pane_text", lambda _n: "failed to authenticate")
        msg = s._why_the_engine_died("sess", "claude")
        assert "run `claude` on its own" in msg
        assert "credential is FINE" in msg

    def test_it_does_NOT_relay_the_engine_s_own_words(self, monkeypatch):
        """⚠ Two reasons, and the second is the one that matters. A pane can
        carry secrets — an injected token on a launch line is the measured
        case elsewhere in this project. And repeating the engine's CLAIM
        would make rite assert it."""
        import rite_ai.managers.session as s

        poisoned = (
            "Failed to authenticate: OAuth session expired\n"
            "TOKEN=sk-ant-oat01-SHOULD-NEVER-APPEAR-IN-OUTPUT"
        )
        monkeypatch.setattr(s, "_pane_text", lambda _n: poisoned)
        msg = s._why_the_engine_died("sess", "claude")
        assert "SHOULD-NEVER-APPEAR-IN-OUTPUT" not in msg, (
            "rite echoed pane contents back to the user — a pane can carry "
            "an injected credential"
        )
        assert "OAuth session expired" not in msg, (
            "rite repeated the engine's claim, which has been measured "
            "false about a valid credential"
        )

    def test_a_NON_auth_death_keeps_the_ordinary_message(self, monkeypatch):
        """The narrow case must stay narrow: a command that simply is not
        installed should not be described as an authentication problem."""
        import rite_ai.managers.session as s

        monkeypatch.setattr(s, "_pane_text", lambda _n: "bash: nosuchthing: not found")
        msg = s._why_the_engine_died("sess", "nosuchthing")
        assert "authentication" not in msg.lower()
        assert "started and exited immediately" in msg
