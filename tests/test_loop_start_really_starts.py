"""`rite loop start` actually starts something — against real tmux.

This file exists because it did not. `start` reported "started as
rite-loop-…", exited 0, and there was no session: the command handed to tmux
was the literal `rite`, which was not executable in the session's
environment, so it died on its first line. tmux still exited 0, because its
exit code answers "was a session created", not "is the command running".

Every test of `start` mocked `_tmux` and `is_alive`, so they tested the
intent and passed while the behaviour was a facade. That is the defect this
session has spent the night filing in other people's code — a check whose
result is read from the wrong place — so these tests use the real binary and
assert against the real session table.

Skipped rather than faked where tmux is absent: a mocked version of this file
would reproduce exactly the hole it was written to close.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from rite_ai.loop.session import (
    Refused,
    Started,
    is_alive,
    session_name,
    start,
    status,
)

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="needs real tmux; mocking it is the bug"
)


@pytest.fixture
def project(tmp_path):
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: looptest\n  role: owner\nwhat:\n  kind: app\n"
        "technology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nexpertise: {}\n"
        "publish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        "schedule:\n  timezone: UTC\n  windows:\n    - hours: 00:00-23:59\n"
        "      workers: 2\n"
    )
    yield tmp_path
    subprocess.run(
        ["tmux", "kill-session", "-t", session_name(tmp_path)],
        capture_output=True,
        check=False,
    )


def test_a_command_that_dies_immediately_is_not_reported_as_started(project):
    """The actual bug. tmux exits 0 for a session it created even when the
    command inside it died on the first line, so `start` believed it."""
    result = start(project, command="definitely-not-a-real-binary-xyz")

    assert isinstance(result, Refused), result
    assert "exited immediately" in result.reason
    assert not is_alive(session_name(project))


def test_the_refusal_says_what_the_dead_session_printed(project):
    """ "It died" sends somebody to guess. `command not found` ends it."""
    result = start(project, command="definitely-not-a-real-binary-xyz")

    assert isinstance(result, Refused)
    assert "command not found" in result.reason


@pytest.fixture
def fake_rite(tmp_path):
    """A stand-in that accepts `loop run --watch --interval N` and stays up.

    `start` appends the subcommand, so the command it is given must be a rite
    binary and not an arbitrary process — which is itself worth pinning: the
    original defect was that this string is executed with arguments appended,
    and nothing checked the result could run at all.
    """
    script = tmp_path / "fake-rite"
    script.write_text("#!/bin/sh\nexec sleep 120\n")
    script.chmod(0o755)
    return str(script)


def test_a_real_command_starts_and_status_agrees(project, fake_rite):
    """`start` and `status` disagreed: one said started, the other said not
    running, in the same second."""
    result = start(project, command=fake_rite)

    assert isinstance(result, Started), result
    assert is_alive(session_name(project))
    assert status(project).running is True


def test_a_second_start_is_refused_while_one_is_running(project, fake_rite):
    """It was not. Both reported success, which is how two loops would end up
    dispatching against one board."""
    assert isinstance(start(project, command=fake_rite), Started)

    second = start(project, command=fake_rite)

    assert isinstance(second, Refused), second
    assert "already running" in second.reason


def test_status_reports_not_running_when_nothing_is(project):
    assert status(project).running is False


def test_the_command_defaults_to_the_rite_that_is_running(project):
    """Not the literal `rite`, which assumes a PATH the caller may not have —
    and which can be a different checkout even when it exists."""
    from rite_ai.loop.session import rite_command

    resolved = rite_command()
    assert resolved is None or resolved.endswith(("rite", "rite-ai"))


def test_a_dead_loop_leaves_its_reasons_in_the_log(project):
    """The pane dies with the session, so the first diagnostic reported "it
    printed nothing" about a loop that had explained itself at length. The
    log outlives the session."""
    from rite_ai.loop.session import log_path

    script = project / "noisy-rite"
    script.write_text("#!/bin/sh\necho 'no ticket backend is configured'\nexit 1\n")
    script.chmod(0o755)

    result = start(project, command=str(script))

    assert isinstance(result, Refused), result
    assert "no ticket backend is configured" in result.reason
    assert "no ticket backend is configured" in log_path(project).read_text()


def test_an_empty_log_is_reported_as_a_guess_not_a_fact(project):
    """The shell writes `command not found` into the log, so the empty-log
    branch is rarely reached — and when it is, there is genuinely nothing to
    go on, so it says "usually means" rather than asserting a cause."""
    from rite_ai.loop.session import _why_it_died, log_path

    log_path(project).write_text("")

    assert "usually means" in _why_it_died(project)


# --- status answers the 3am question ----------------------------------------------


def test_status_reports_the_pid_of_what_is_actually_running(project, fake_rite):
    """It said "(pid unknown)" for a loop that was plainly running, because it
    read the lock file — and `start` releases its own lock before spawning, so
    the lock belongs to a process that has not taken it yet. tmux knows what
    it is running; ask the thing that knows."""
    assert isinstance(start(project, command=fake_rite), Started)

    live = status(project)

    assert live.running
    assert live.pid > 0, live
    # And it is a real process, not a number off a file.
    import os

    os.kill(live.pid, 0)


def test_status_tells_a_reader_how_to_watch_and_how_to_stop(project, fake_rite):
    """Someone whose loop misbehaves at 3am should not have to remember the
    session name or that `stop` is the verb rather than a kill."""
    start(project, command=fake_rite)

    text = "\n".join(status(project).lines())

    assert "tmux attach -t" in text
    assert "rite loop stop" in text


def test_status_reports_how_long_it_has_been_up(project, fake_rite):
    start(project, command=fake_rite)

    assert status(project).uptime >= 0
    assert "up " in "\n".join(status(project).lines())


def test_a_lock_held_by_someone_else_is_flagged_rather_than_preferred(
    project, fake_rite
):
    """Two processes thinking they are this project's loop is worth saying
    before somebody acts, not after."""
    from rite_ai.loop.session import lock_path

    start(project, command=fake_rite)
    lock_path(project).write_text("999999 0\n")

    text = "\n".join(status(project).lines())

    assert "the loop lock is held by pid 999999" in text


# --- the loop is visible from the command people already run -----------------------


def test_rite_status_says_when_a_loop_is_running(project):
    """A loop is the one thing in a project that keeps changing the answers
    in `rite status` while nobody is watching. A reader who cannot see it is
    reading a report with an author they do not know about.

    Driven through the LOCK rather than by starting a session, because
    `collect_status` deliberately never shells out — it reads the lock and
    checks the pid with a signal. Starting a real loop here would test tmux
    instead of the thing this asserts."""
    import os

    from rite_ai.loop.session import lock_path
    from rite_ai.reporting.status import collect_status, format_status

    lock_path(project).write_text(f"{os.getpid()} 0\n")

    text = format_status(collect_status(project, board=False))

    assert f"loop: running (pid {os.getpid()})" in text


def test_a_stale_lock_from_a_dead_process_is_not_a_running_loop(project):
    """A loop killed by a reboot leaves its lock behind. Reporting that as
    running is the same wrong answer `rite loop start` used to give."""
    from rite_ai.loop.session import lock_path
    from rite_ai.reporting.status import collect_status

    lock_path(project).write_text("999999 0\n")

    assert collect_status(project, board=False).loop == "not running"


def test_rite_status_never_shells_out_to_ask(project):
    """`collect_status` is the read-only path and a test already pins that it
    spawns nothing. Asking tmux here put a process spawn into the command
    people run most often."""
    from unittest.mock import patch

    from rite_ai.reporting.status import collect_status

    with patch("rite_ai.loop.session.subprocess.run") as ran:
        collect_status(project, board=False)

    ran.assert_not_called()


def test_rite_status_says_when_no_loop_is_running(project):
    """ "Not running" is worth saying: silence would read the same as a
    project that has never had one."""
    from rite_ai.reporting.status import collect_status, format_status

    text = format_status(collect_status(project, board=False))

    assert "loop: not running" in text


def test_rite_status_says_when_a_running_loop_is_draining(project):
    import os

    from rite_ai.loop.session import lock_path, request_drain
    from rite_ai.reporting.status import collect_status, format_status

    lock_path(project).write_text(f"{os.getpid()} 0\n")
    request_drain(project, "going home")

    text = format_status(collect_status(project, board=False))

    assert "draining" in text
