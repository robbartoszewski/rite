"""`rite start <manager>` prompts the session (§9.14.11a, D-90).

A Manager started with an empty prompt waits for a human to type, which is
the behaviour the command exists to remove.

⚠ **The two properties worth the most here are not "a prompt is sent".**
They are that delivery is CONFIRMED rather than assumed, and that a RESUMED
session is not prompted again. The first because a `send-keys` can be
swallowed by a shell not yet reading — measured in `session.py`'s capability
probe, which treats a lost `exit 3` as "no answer" for exactly this reason.
The second because re-issuing an instruction into a conversation that is
mid-task tells the agent to begin something it is in the middle of.
"""

from __future__ import annotations

import subprocess
import time
import uuid

import pytest

from rite_ai.managers.prompt import Delivery, deliver, for_manager
from rite_ai.managers.session import StartResult
from rite_ai.managers.supervise import supervise

tmux_only = pytest.mark.skipif(
    subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
    reason="tmux not installed",
)


class TestComposition:
    def test_the_manager_knows_which_manager_it_is(self):
        assert "planner" in for_manager("planner")

    def test_extra_is_appended_verbatim(self):
        """`journal.instructions` owns its own wording; this module must not
        paraphrase it, or the two drift."""
        assert for_manager("p", extra="\n\nJOURNAL TEXT").endswith("JOURNAL TEXT")

    def test_no_extra_is_the_empty_string_not_a_branch(self):
        """Same contract as `journal.start_notice`: the disabled case is one
        shape, so the caller concatenates unconditionally."""
        assert for_manager("p") == for_manager("p", extra="")


class TestOnlyTheFirstSessionIsPrompted:
    """D-90's stated default, which is the half of the decision that was
    inferred rather than given — so it is the half most worth pinning."""

    def _run(self, tmp_path, cycles: int):
        (tmp_path / ".rite").mkdir(exist_ok=True)
        sent: list[tuple[str, str]] = []
        started: list[str] = []

        def starter(root, manager, *, engine, resume_id, max_sessions, window_seconds):
            started.append(resume_id)
            return StartResult(True, "ok", session=f"s{len(started)}", attach="a")

        import rite_ai.managers.supervise as sup

        return sent, started, starter, sup

    def test_a_fresh_session_is_prompted(self, tmp_path, monkeypatch):
        sent, started, starter, sup = self._run(tmp_path, 1)
        monkeypatch.setattr(
            sup, "deliver_prompt", lambda n, t: sent.append((n, t)) or Delivery(True)
        )
        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False})()
        )
        monkeypatch.setattr(sup, "was_attached", lambda n: False)
        monkeypatch.setattr(
            sup,
            "ending",
            lambda n, human_was_present, pane="": type(
                "E", (), {"kind": "quit", "resume": False, "status": 0, "detail": ""}
            )(),
        )
        supervise(
            tmp_path,
            "lead",
            engine="sh",
            max_sessions=1,
            window_seconds=0,
            prompt="HELLO MANAGER",
            verdict=lambda _r: "ready",
            starter=starter,
        )
        assert [t for _n, t in sent] == ["HELLO MANAGER"]

    def test_a_resumed_session_is_NOT_prompted_again(self, tmp_path, monkeypatch):
        sent, started, starter, sup = self._run(tmp_path, 2)
        monkeypatch.setattr(
            sup, "deliver_prompt", lambda n, t: sent.append((n, t)) or Delivery(True)
        )
        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False})()
        )
        monkeypatch.setattr(sup, "was_attached", lambda n: False)
        monkeypatch.setattr(
            sup,
            "ending",
            lambda n, human_was_present, pane="": type(
                "E", (), {"kind": "finished", "resume": True, "status": 0, "detail": ""}
            )(),
        )
        supervise(
            tmp_path,
            "lead",
            engine="sh",
            max_sessions=3,
            window_seconds=0,
            prompt="HELLO MANAGER",
            verdict=lambda _r: "ready",
            starter=starter,
            resume_id_for=lambda root, manager, since: "SESSION-ID",
        )
        assert started == ["", "SESSION-ID", "SESSION-ID"], started
        assert len(sent) == 1, (
            f"a resumed session was prompted again ({len(sent)} sends) — the "
            f"tool told the agent to begin what it was in the middle of"
        )


class TestAFailedDeliveryIsReportedNotFatal:
    def test_the_run_continues_and_the_human_is_told(self, tmp_path, monkeypatch):
        (tmp_path / ".rite").mkdir(exist_ok=True)
        said: list[str] = []

        def starter(root, manager, *, engine, resume_id, max_sessions, window_seconds):
            return StartResult(True, "ok", session="s1", attach="a")

        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(
            sup, "deliver_prompt", lambda n, t: Delivery(False, "swallowed")
        )
        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False})()
        )
        monkeypatch.setattr(sup, "was_attached", lambda n: False)
        monkeypatch.setattr(
            sup,
            "ending",
            lambda n, human_was_present, pane="": type(
                "E", (), {"kind": "quit", "resume": False, "status": 0, "detail": ""}
            )(),
        )
        result = supervise(
            tmp_path,
            "lead",
            engine="sh",
            max_sessions=1,
            window_seconds=0,
            prompt="HELLO",
            verdict=lambda _r: "ready",
            starter=starter,
            note=said.append,
        )
        assert result.ok, "a lost prompt killed the run"
        assert any("may not have arrived" in m for m in said), said
        assert any("tmux attach" in m for m in said), "it did not say how to look"


@tmux_only
class TestDeliveryAgainstRealTmux:
    def test_it_confirms_the_prompt_reached_the_pane(self):
        name = f"prompt-{uuid.uuid4().hex[:6]}"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "sh"], capture_output=True
        )
        time.sleep(0.5)
        try:
            marker = f"echo RITE-MARKER-{uuid.uuid4().hex[:6]}"
            got = deliver(name, marker)
            assert got.ok, got.detail
        finally:
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def test_a_session_that_does_not_exist_is_a_failed_delivery(self):
        got = deliver(f"absent-{uuid.uuid4().hex[:6]}", "hello")
        assert not got.ok
        assert "no session" in got.detail

    def test_a_prefix_of_a_live_session_is_not_typed_into(self):
        """`-t` prefix-matches, so without the exact-existence check a
        prompt for `lead` lands in `leader`'s pane."""
        name = f"leader-{uuid.uuid4().hex[:6]}"
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "sh"], capture_output=True
        )
        time.sleep(0.5)
        try:
            got = deliver(name[:-1], "SHOULD NOT ARRIVE")
            assert not got.ok, "a prompt was typed into a different session"
        finally:
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def test_a_prompt_that_never_appears_is_a_FAILED_delivery(self):
        """⚠ The test this file did not have, and the property §9.14.11a
        calls load-bearing.

        Found by mutation: replacing the whole confirmation loop with
        `return Delivery(True, "sent")` left all ten other tests green. The
        confirmation was written, documented as the reason this module
        exists, and asserted by nothing — which is the class this release
        has been filing all day, committed an hour after filing it.

        A DEAD pane is the honest way to produce the failure: the session
        exists, so `session_exists` passes and `send-keys` is accepted, and
        the text can never appear because nothing is reading. That is
        exactly the shape of a shell that is not yet reading, which is what
        the confirmation exists for.
        """
        name = f"dead-{uuid.uuid4().hex[:6]}"
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
        try:
            got = deliver(name, f"RITE-NEVER-ARRIVES-{uuid.uuid4().hex[:6]}")
            assert not got.ok, (
                "a prompt that never reached the pane was reported as "
                "delivered — the confirmation is not confirming"
            )
            assert "did not appear" in got.detail
        finally:
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def test_an_empty_prompt_sends_nothing_and_is_not_a_failure(self):
        got = deliver("any-name-at-all", "   ")
        assert got.ok and "nothing" in got.detail
