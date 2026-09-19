"""`rite pool fill` actually starts something — against real tmux.

The twin of `tests/test_loop_start_really_starts.py`, and it exists for the
same reason one module over. `fill` reported "pool at 2/2 — 2 session(s)
started", exited 0, and wrote both slots to `pool.json` with
`unreachable_since=None`; a second later `tmux ls` showed neither. It had
verified that `tmux new-session -d` exited 0 — which answers "was a session
created", not "is the command in it running".

`loop.start` was given a settle-and-verify after exactly this. `fill` was
not, and kept the facade in the module whose whole job is "a real, named,
live session ready to attach to".

Every existing test of `fill` patches BOTH `subprocess.run` and
`is_tmux_session_alive`, so they assert the intent and pass while the
behaviour is a facade. These use the real binary and the real session table.
Skipped rather than faked where tmux is absent: a mocked version of this
file would reproduce the hole it was written to close.

⚠ `command` is never `claude` here. Every pooled slot runs its command for
real, and spent quota is the one kind of damage this module's own docstring
says no cleanup gets back. `false` is the realistic failure — a configured
command that cannot run in tmux's environment — and `sleep` is the
realistic success.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from rite_ai.config.models import PoolConfig
from rite_ai.pool import _read_state, fill, is_tmux_session_alive

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="needs real tmux; mocking it is the bug"
)

LIVE = "sleep 300"
DIES = "false"


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".rite").mkdir()
    yield tmp_path
    for slot in _read_state(tmp_path):
        subprocess.run(["tmux", "kill-session", "-t", slot.name], capture_output=True)


def _kill(names):
    for name in names:
        subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


def test_a_command_that_dies_immediately_is_not_reported_as_started(project):
    result = fill(project, PoolConfig(coordinator_standby=2), count=2, command=DIES)
    _kill(result.started)

    assert not result.ok, (
        "fill reported success for a command that exited on its first line. "
        "`tmux new-session -d` exits 0 once the session EXISTS; whether the "
        "command survived is a different question and has to be asked"
    )
    assert "exited immediately" in result.message
    assert result.started == []


def test_nothing_is_recorded_live_that_was_never_observed_alive(project):
    """The state file is the lasting damage. `rite pool status` reads it back
    as fact, so a phantom slot makes fill and status contradict each other
    for ever: status says run fill, fill says success, status says 0/2."""
    fill(project, PoolConfig(coordinator_standby=2), count=2, command=DIES)

    assert _read_state(project) == [], (
        "pool.json recorded slots whose liveness was never observed — "
        "`unreachable_since=None` means 'known reachable', and nothing knew"
    )


def test_a_failed_attempt_does_not_burn_the_slot_name(project):
    """A dead slot keeps its name because it links a departed session to the
    claims it left. A session whose command never ran has no claims, so
    reserving its name costs a name per failed attempt and buys nothing."""
    config = PoolConfig(coordinator_standby=1)
    first = fill(project, config, count=1, command=DIES)
    second = fill(project, config, count=1, command=DIES)
    _kill(first.started + second.started)

    for result in (first, second):
        assert not result.ok
    assert "-0" in first.message and "-0" in second.message, (
        "the second attempt used a different slot name, so each failure "
        "permanently consumes one"
    )


def test_a_command_that_survives_is_started_and_recorded(project):
    """The other half. A verify step that never passes is its own defect."""
    result = fill(project, PoolConfig(coordinator_standby=2), count=2, command=LIVE)
    try:
        assert result.ok, result.message
        assert len(result.started) == 2
        for name in result.started:
            assert is_tmux_session_alive(name), f"{name} was reported but is not there"
        recorded = _read_state(project)
        assert [s.name for s in recorded] == result.started
        assert all(s.unreachable_since is None for s in recorded)
    finally:
        _kill(result.started)


def test_topping_up_an_already_full_pool_starts_nothing(project):
    config = PoolConfig(coordinator_standby=1)
    first = fill(project, config, count=1, command=LIVE)
    try:
        assert first.ok, first.message
        again = fill(project, config, count=1, command=LIVE)
        assert again.ok
        assert again.started == [], "a full pool started another session"
    finally:
        _kill(first.started)
