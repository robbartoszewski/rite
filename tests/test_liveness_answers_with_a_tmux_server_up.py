"""`liveness()` must answer the same way whether or not tmux is busy.

WHY THIS EXISTS. `tests/test_manager_session_really_starts.py` already
asserts the property — "absent is not the same as unanswerable" — and it
was passing. It passed because the machine happened to have no tmux server
running. With ANY unrelated tmux session open it goes red, and so does most
of that file.

The defect it hides is not a test problem. Measured on tmux 3.7c, through
`subprocess` rather than a shell — zsh expands a leading `=` itself, which
silently corrupts this measurement when taken at a prompt:

    display-message -p -t no-such-session '#{pane_dead}'  -> rc=0 out=''

An unresolvable target is not an error so long as a server is up.
`liveness()` read the exit code for absence and the text for deadness, so an
empty reply took neither branch cleanly: `"" != "1"` is True, and a session
that does not exist reported **alive, and known to be alive**.

Whether this is a CHANGE in tmux or long-standing behaviour is NOT
established here — it was not measured against an older tmux, and the two
possibilities differ in what they imply (a change means CI on an older tmux
was immune and the defect is newer than the code; long-standing means it
has always been live and CI simply never ran with a second session open).
The tests below do not depend on which it is: they hold a server up and
assert the property directly.

What that costs in production: `start()` uses `liveness()` for the duplicate
check, so `rite start <manager>` refuses every Manager — "a tmux session
named … already exists but this project has no record of it" — for a session
that does not exist, on any machine where the user has tmux open. The
feature is unusable for tmux users, which is the population that has it.

THE GUARD IS THE SERVER, and it is the whole point of this file: every test
here holds an unrelated tmux session open for its duration. The property
under test is only false while a server is running, so a version of this
file that did not start one would pass against the broken code.

⚠ `claude` is never a command here. Panes run `sleep` and `true`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

import pytest

from rite_ai.managers.session import liveness

_HAS_TMUX = shutil.which("tmux") is not None
_IN_CI = os.environ.get("CI") == "true"

# Removing tmux from the workflow must go RED, not quietly return this file
# to skips — the sibling guard, which this family has been bitten by once.
if _IN_CI and not _HAS_TMUX:
    raise RuntimeError(
        "tmux is missing in CI. These tests prove `liveness()` against a real "
        "tmux server; skipping them in CI would retire the check silently."
    )

pytestmark = pytest.mark.skipif(not _HAS_TMUX, reason="tmux is not installed")


def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args], capture_output=True, text=True, errors="replace", timeout=30
    )


@pytest.fixture
def server_is_up():
    """Hold an unrelated tmux session open, and take only it away again.

    Never `kill-server`: the user's own sessions, and other rite sessions,
    live on this server.
    """
    name = f"rite-test-bystander-{uuid.uuid4().hex[:8]}"
    _tmux("new-session", "-d", "-s", name, "sleep 600")
    try:
        assert _tmux("has-session", "-t", f"={name}").returncode == 0, (
            "could not start the bystander session, so this file would prove "
            "nothing — it only tests what happens while a server is up"
        )
        yield name
    finally:
        _tmux("kill-session", "-t", f"={name}")


def test_a_session_that_does_not_exist_is_absent_and_known(server_is_up):
    """The regression. Absent must not read as alive."""
    gone = liveness(f"rite-mgr-definitely-absent-{uuid.uuid4().hex[:8]}")
    assert not gone.alive, (
        "a session that does not exist reported ALIVE while a tmux server was "
        "running — this is what makes `rite start <manager>` refuse every "
        f"Manager for a session nobody started (detail: {gone.detail!r})"
    )
    assert gone.known, (
        "absent was reported as unanswerable. That fails closed, so it is not "
        "dangerous, but it wedges `rite start` exactly as the fail-open did: "
        "the duplicate check refuses on 'cannot tell' as well as on 'alive'"
    )


def test_a_running_session_is_still_alive(server_is_up):
    """The other direction, so the fix cannot be 'always report absent'."""
    name = f"rite-test-live-{uuid.uuid4().hex[:8]}"
    _tmux("new-session", "-d", "-s", name, "sleep 600")
    try:
        answer = liveness(name)
        assert answer.alive and answer.known, (
            "a session that IS running reported not-alive — a false negative "
            "here starts a SECOND paid Manager session (§5.1.1)"
        )
    finally:
        _tmux("kill-session", "-t", f"={name}")


def test_a_dead_pane_is_not_alive(server_is_up):
    """`has-session` is not enough on its own.

    `start` sets `remain-on-exit on` so the exit status survives, so a
    session whose command has exited still EXISTS. Whatever answers the
    empty-expansion case must not answer this one by existence alone.
    """
    name = f"rite-test-dead-{uuid.uuid4().hex[:8]}"
    # Start something long-lived, turn `remain-on-exit` on while it is still
    # running, and only THEN replace it with a command that exits. Creating
    # the session with `true` races: the pane can be gone — taking the whole
    # session with it — before the option is set, and the test then proves
    # nothing while still passing.
    _tmux("new-session", "-d", "-s", name, "sleep 600")
    try:
        _tmux("set-option", "-t", name, "remain-on-exit", "on")
        _tmux("respawn-pane", "-k", "-t", name, "true")
        for _ in range(100):
            got = _tmux("display-message", "-p", "-t", name, "#{pane_dead}")
            if got.stdout.strip() == "1":
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                "the pane never reached a dead state, so the assertion below "
                "would have been vacuous — skipping here would hide that"
            )
        answer = liveness(name)
        assert not answer.alive, (
            "a session whose command has exited reported ALIVE — the duplicate "
            "check would refuse to start a Manager because a finished one is "
            "still sitting there"
        )
        assert answer.known, "a dead pane is a known answer, not an unreadable one"
    finally:
        _tmux("kill-session", "-t", f"={name}")


def test_an_exact_name_is_not_matched_by_a_longer_one(server_is_up):
    """tmux resolves `-t` by exact match, then fnmatch, then PREFIX.

    So `lead` can be answered by `leader`. `name_problem` accepts both, and
    two Managers in one project are named adjacently by design.
    """
    stem = f"rite-test-prefix-{uuid.uuid4().hex[:8]}"
    longer = f"{stem}-extra"
    _tmux("new-session", "-d", "-s", longer, "sleep 600")
    try:
        answer = liveness(stem)
        assert not answer.alive, (
            f"asked about {stem!r} and got an answer about {longer!r} — tmux "
            "prefix-matched the target. A Manager named `lead` reads as alive "
            "because a `leader` exists, or worse, reads as FINISHED and gets "
            "resumed"
        )
    finally:
        _tmux("kill-session", "-t", f"={longer}")
