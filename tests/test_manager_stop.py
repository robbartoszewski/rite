"""Ctrl-C stops both; a bound stops only supervision (§9.14.12, D-85).

⚠ **The whole item is a distinction between two events that reach the same
code**, so every test here has to be able to tell them apart, and a test
that cannot passes regardless. That is the standard this release arrived at
the hard way: a confirmation loop in `prompt.py` was replaced wholesale by
`return success` and ten tests stayed green.

- **ceiling or window reached** -> supervision stops, the pane SURVIVES.
  The user may be mid-conversation and a ceiling is an accounting limit.
- **Ctrl-C** -> both stop. A human said stop, and leaving a live session
  spending quota with only the restarts halted is not what was asked for.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

import pytest

from rite_ai.managers import ManagerInstance, read_instance, record_instance
from rite_ai.managers.session import StartResult, session_name, stop
from rite_ai.managers.supervise import supervise

tmux_only = pytest.mark.skipif(
    subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
    reason="tmux not installed",
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir(exist_ok=True)
    return tmp_path


def _recorded(root: Path, name: str = "lead") -> None:
    record_instance(
        root,
        ManagerInstance(
            name=name,
            session=session_name(root, name),
            pid=1,
            engine="sh",
            max_sessions=1,
            window_seconds=0.0,
        ),
    )


class TestCtrlCStopsBothAndLeavesNoPhantom:
    def _harness(self, monkeypatch, alive_forever: bool = True):
        import rite_ai.managers.supervise as sup

        killed: list[str] = []
        said: list[str] = []
        monkeypatch.setattr(
            sup,
            "stop_session",
            lambda n: (
                killed.append(n)
                or __import__("rite_ai.managers.session", fromlist=["Stopped"]).Stopped(
                    True, True, f"stopped {n}"
                )
            ),
        )
        monkeypatch.setattr(sup, "was_attached", lambda n: False)
        monkeypatch.setattr(sup, "deliver_prompt", lambda n, t: None)

        def interrupt(_n):
            raise KeyboardInterrupt

        monkeypatch.setattr(sup, "liveness", interrupt)
        return killed, said

    def test_the_session_is_stopped(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        _recorded(root)
        killed, said = self._harness(monkeypatch)

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds):
            return StartResult(True, "ok", session="s1", attach="a")

        result = supervise(
            root,
            "lead",
            engine="sh",
            max_sessions=5,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            note=said.append,
        )
        assert killed == ["s1"], "Ctrl-C left the session running"
        assert result.ok
        assert "and its session" in result.reason, result.reason

    def test_the_instance_record_is_cleared(self, tmp_path, monkeypatch):
        """⚠ The stale-lock defect in a new place. A stop that leaves a
        record makes the next `rite start` believe a Manager is running,
        and that class wedged the loop earlier this week."""
        root = _project(tmp_path)
        _recorded(root)
        assert read_instance(root, "lead") is not None, "fixture did not record"
        killed, said = self._harness(monkeypatch)

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds):
            return StartResult(True, "ok", session="s1", attach="a")

        supervise(
            root,
            "lead",
            engine="sh",
            max_sessions=5,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            note=said.append,
        )
        assert read_instance(root, "lead") is None, (
            "a phantom record survived Ctrl-C — the next `rite start` would "
            "refuse, believing this Manager is still running"
        )

    def test_it_says_what_it_did(self, tmp_path, monkeypatch):
        """Silence after Ctrl-C is indistinguishable from a signal that did
        not land, which is how a user presses it three times."""
        root = _project(tmp_path)
        _recorded(root)
        killed, said = self._harness(monkeypatch)

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds):
            return StartResult(True, "ok", session="s1", attach="a")

        result = supervise(
            root,
            "lead",
            engine="sh",
            max_sessions=5,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            note=said.append,
        )
        assert "lead" in result.reason and "stopped" in result.reason


class TestABoundLeavesTheSessionAlive:
    """The other half of the distinction, and the one that is easy to break
    while fixing the first."""

    def test_reaching_the_ceiling_does_not_stop_the_session(
        self, tmp_path, monkeypatch
    ):
        """⚠ An earlier version of this test never reached the ceiling.

        With `max_sessions=1` and a `quit` ending, supervise returns on the
        ENDING before the loop comes round to the ceiling check — so the
        branch under test never ran, and a mutation that made a bound kill
        the session left it green. Found by mutation, which is the only
        thing that would have found it: the test passed, against code that
        was correct, for a reason unrelated to the property.

        It now ends each session `finished` with a transcript to resume
        from, so the loop comes round and the SECOND pass hits the ceiling,
        and it asserts that the ceiling is what stopped it.
        """
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        killed: list[str] = []
        monkeypatch.setattr(sup, "stop_session", lambda n: killed.append(n))
        monkeypatch.setattr(sup, "was_attached", lambda n: False)
        monkeypatch.setattr(sup, "deliver_prompt", lambda n, t: None)
        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False})()
        )
        monkeypatch.setattr(
            sup,
            "ending",
            lambda n, human_was_present, pane="": type(
                "E",
                (),
                {"kind": "finished", "resume": True, "status": 0, "detail": ""},
            )(),
        )

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds):
            return StartResult(True, "ok", session="s1", attach="a")

        result = supervise(
            root,
            "lead",
            engine="sh",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            resume_id_for=lambda root, manager, since: "SESSION-ID",
        )
        assert "ceiling reached" in result.reason, (
            f"this test did not reach the branch it is about: {result.reason!r}"
        )
        assert killed == [], (
            "a bound killed the session — the user was mid-conversation and "
            "a ceiling is an accounting limit, not an instruction to stop"
        )

    def test_a_bound_keeps_the_record(self, tmp_path, monkeypatch):
        """The session is still running, so the record is TRUE. Clearing it
        would make `rite start` offer to start a second one."""
        import rite_ai.managers.supervise as sup

        root = _project(tmp_path)
        _recorded(root)
        monkeypatch.setattr(sup, "was_attached", lambda n: False)
        monkeypatch.setattr(sup, "deliver_prompt", lambda n, t: None)
        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False})()
        )
        monkeypatch.setattr(
            sup,
            "ending",
            lambda n, human_was_present, pane="": type(
                "E", (), {"kind": "quit", "resume": False, "status": 0, "detail": ""}
            )(),
        )

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds):
            return StartResult(True, "ok", session="s1", attach="a")

        supervise(
            root,
            "lead",
            engine="sh",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
        )
        assert read_instance(root, "lead") is not None, (
            "a bound cleared the record of a session that is still running"
        )


@tmux_only
class TestStopAgainstRealTmux:
    def test_it_stops_a_live_session(self):
        name = f"stoppable-{uuid.uuid4().hex[:6]}"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "sh"], capture_output=True
        )
        time.sleep(0.4)
        got = stop(name)
        assert got.ok and got.killed, got.detail
        assert (
            subprocess.run(
                ["tmux", "has-session", "-t", f"={name}"], capture_output=True
            ).returncode
            != 0
        )

    def test_stopping_nothing_is_a_SUCCESS_not_a_failure(self):
        """⚠ Idempotence is the requirement, not a nicety. Both callers —
        Ctrl-C and `rite manager stop` — can arrive when the session is
        already gone, and a teardown that only works on the happy path
        leaves exactly the phantom it exists to remove."""
        got = stop(f"absent-{uuid.uuid4().hex[:6]}")
        assert got.ok, "an already-stopped session reported as a failure"
        assert not got.killed

    def test_a_DEAD_pane_still_stops_cleanly(self):
        """The dead-pane case: `remain-on-exit` keeps the session after its
        command exits, so it EXISTS and is not alive. A stop must remove it
        rather than treating it as already gone and leaving it on screen."""
        name = f"deadstop-{uuid.uuid4().hex[:6]}"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "sh"], capture_output=True
        )
        time.sleep(0.4)
        subprocess.run(
            ["tmux", "set-window-option", "-t", name, "remain-on-exit", "on"],
            capture_output=True,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", name, "exit 0", "Enter"], capture_output=True
        )
        for _ in range(25):
            time.sleep(0.1)
            dead = subprocess.run(
                ["tmux", "display-message", "-p", "-t", name, "#{pane_dead}"],
                capture_output=True,
                text=True,
            )
            if (dead.stdout or "").strip() == "1":
                break
        got = stop(name)
        assert got.ok and got.killed, f"a dead pane was left behind: {got.detail}"
        assert (
            subprocess.run(
                ["tmux", "has-session", "-t", f"={name}"], capture_output=True
            ).returncode
            != 0
        )

    def test_it_does_not_kill_a_session_whose_name_it_merely_prefixes(self):
        """`-t` prefix-matches. Unlike a misread exit status, this one
        destroys somebody's work."""
        name = f"leader-{uuid.uuid4().hex[:6]}"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "sh"], capture_output=True
        )
        time.sleep(0.4)
        try:
            got = stop(name[:-1])
            assert not got.killed, "a stop aimed at a prefix killed another session"
            assert (
                subprocess.run(
                    ["tmux", "has-session", "-t", f"={name}"], capture_output=True
                ).returncode
                == 0
            )
        finally:
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
