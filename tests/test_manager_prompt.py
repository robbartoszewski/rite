"""`rite start <manager>` prompts the session (§9.14.11a, D-90).

A Manager started with an empty prompt waits for a human to type, which is
the behaviour the command exists to remove.

⚠ **`Delivery.reached_terminal` is a narrow claim and the name says so.**
A tty echoes keystrokes whether or not the process reads them, so a session
running `sleep 300` returns True — a process that never reads stdin is
indistinguishable from one that did. The field was called `ok`, which
callers read as "the Manager got its prompt"; it never meant that. See the
dataclass docstring for the proxies that were checked and rejected.

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

from rite_ai.managers.prompt import deliver, for_manager
from rite_ai.managers.session import StartResult, Stopped
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


class TestOnlyTheFirstSessionGetsTheOpeningPrompt:
    """⚠ **D-90 amended, not worked around.** The decision that the prompt
    goes to the first session only was right while the engine was a REPL:
    later cycles were the same conversation continuing, so there was
    nothing to say. `-p` changed the premise — each cycle is a separate
    invocation that exits, and one launched with no input exits 1 rather
    than continuing. So every cycle carries an instruction, and a resumed
    one carries a DIFFERENT instruction.

    What D-90 was protecting still holds and is pinned here: the OPENING
    prompt is never re-issued, because telling a session to do work it has
    already done is how it gets done twice.
    """

    def _cycles(self, tmp_path, monkeypatch, endings):
        import rite_ai.managers.supervise as sup

        (tmp_path / ".rite").mkdir(exist_ok=True)
        seen: list[tuple[str, str]] = []

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
        ):
            seen.append((resume_id, prompt))
            return StartResult(True, "ok", session=f"s{len(seen)}", attach="a")

        kinds = iter(endings)

        def ending(n, human_was_present, pane=""):
            kind = next(kinds, "quit")
            return type(
                "E",
                (),
                {
                    "kind": kind,
                    "resume": kind == "finished",
                    "status": 0,
                    "detail": "",
                },
            )()

        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
        )
        monkeypatch.setattr(sup, "was_attached", lambda n: False)
        monkeypatch.setattr(sup, "ending", ending)
        monkeypatch.setattr(sup, "stop_session", lambda s: Stopped(True, True))
        monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
        supervise(
            tmp_path,
            "lead",
            engine="claude",
            max_sessions=3,
            window_seconds=0,
            prompt="OPENING: implement ticket ACME-1.",
            starter=starter,
            resume_id_for=lambda r, m, since=0.0: "sess-1",
            poll=0,
        )
        return seen

    def test_the_first_session_gets_the_opening_prompt(self, tmp_path, monkeypatch):
        seen = self._cycles(tmp_path, monkeypatch, ["quit"])
        # Containment, not equality: the mailbox appends reply
        # instructions to every cycle's prompt. The property is which
        # instruction the cycle carries, not that nothing was added to it.
        assert len(seen) == 1 and seen[0][0] == ""
        assert "OPENING: implement ticket ACME-1." in seen[0][1]

    def test_a_resumed_session_is_told_to_continue_instead(self, tmp_path, monkeypatch):
        from rite_ai.managers.supervise import CONTINUATION

        seen = self._cycles(tmp_path, monkeypatch, ["finished", "quit"])
        assert len(seen) == 2, seen
        assert "OPENING: implement ticket ACME-1." in seen[0][1]
        assert seen[1][0] == "sess-1"
        assert CONTINUATION in seen[1][1]
        # The half that matters: the opening instruction is NOT re-issued.
        assert "OPENING: implement ticket ACME-1." not in seen[1][1]

    def test_no_cycle_is_ever_launched_with_nothing_to_do(self, tmp_path, monkeypatch):
        """The measured failure: `claude -p` with no input exits 1."""
        seen = self._cycles(tmp_path, monkeypatch, ["finished", "finished", "quit"])
        assert len(seen) == 3, seen
        for resume_id, prompt in seen:
            assert prompt.strip(), f"cycle resumed from {resume_id!r} had no input"


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
            assert got.reached_terminal, got.detail
        finally:
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def test_a_session_that_does_not_exist_is_a_failed_delivery(self):
        got = deliver(f"absent-{uuid.uuid4().hex[:6]}", "hello")
        assert not got.reached_terminal
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
            assert not got.reached_terminal, (
                "a prompt was typed into a different session"
            )
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
            assert not got.reached_terminal, (
                "a prompt that never reached the pane was reported as "
                "delivered — the confirmation is not confirming"
            )
            assert "never appeared on the terminal" in got.detail
        finally:
            subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)

    def test_an_empty_prompt_sends_nothing_and_is_not_a_failure(self):
        got = deliver("any-name-at-all", "   ")
        assert got.reached_terminal and "nothing" in got.detail


@tmux_only
def test_a_process_that_never_reads_stdin_still_reports_reaching_the_terminal():
    """⚠ The limitation, asserted rather than left in a docstring — because
    a stated limitation nobody tests is a claim that drifts.

    `sleep 300` never touches stdin. The tty echoes the keystrokes anyway,
    so the pane contains the text and `reached_terminal` is True. This is
    the case §9.14.11a warns about ("a `send-keys` can be swallowed by a
    shell that is not yet reading") and the confirmation cannot see it.

    **If this test ever goes red, the guarantee got STRONGER** — somebody
    found a way to observe consumption — and `Delivery` should be renamed
    again rather than this test deleted.
    """
    name = f"deaf-{uuid.uuid4().hex[:6]}"
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", name, "sleep 300"], capture_output=True
    )
    time.sleep(0.6)
    try:
        got = deliver(name, f"RITE-NEVER-READ-{uuid.uuid4().hex[:6]}")
        assert got.reached_terminal, (
            "the echo case stopped reporting True — if consumption is now "
            "observable, rename the guarantee instead of deleting this"
        )
        assert "not observable" in got.detail, (
            "the detail no longer says what it cannot see"
        )
    finally:
        subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
