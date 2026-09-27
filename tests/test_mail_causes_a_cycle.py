"""Mail CAUSES a cycle: a reply reaches an Owner that is still running (DF2).

Robert's acceptance bar: the secondary's reply is delivered and acted on within
the same `rite start`, and not because of timing — "If a feature works only
because of timing of independent things, it's bad."

**What was measured before this.** The next cycle started for one reason only
(the last session ended cleanly and the board said continue), and the 2-second
poll ran only while a session was alive. So whether a reply landed was decided
by ceiling x cycle length against the secondary's speed. Reproduced with a fake
engine on a virtual clock (the audit session's `repro.py`, whose shape and rows
this file keeps rather than rewrites):

    ceiling 3,  7 s cycles, reply at 23 s -> never delivered
    ceiling 5,  7 s cycles, reply at 23 s -> cycle 5, after four empty sessions
    ceiling 3, 12 s cycles, reply at 23 s -> cycle 3
    ceiling 10, 2 s cycles, reply at 23 s -> never delivered, ten sessions spent

**The pass condition, stated so it can be checked:** in every case, the cycle
carrying the reply starts AT OR AFTER the reply's arrival, and it is the first
cycle to start after it — with a ceiling of 2 for the Owner and 1 for the
secondary. The secondary here finishes in the gaps between the Owner's ticks,
which is the moment a wrongly ordered wait loses a reply.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import rite_ai.managers.supervise as sup
from rite_ai.managers import mailbox, routing
from rite_ai.managers.supervise import StartResult, supervise

OWNER, SECONDARY = "lead", "small"
NAMES = [OWNER, SECONDARY]
REPLY = "REPLY-FROM-SMALL"
TASK = "write HELLO.txt"


class _Ending:
    kind = "finished"
    resume = True
    status = 0
    detail = ""


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The harness's patches: no tmux, no real session; and one more — the
    wait's pause advances the virtual clock and lets the other Manager act."""
    root = tmp_path
    (root / ".rite").mkdir()
    state = {"t": 0.0, "between": []}

    def pause(_seconds):
        state["t"] += 2.0
        for step in state["between"]:
            step()

    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(sup, "ending", lambda n, human_was_present, pane="": _Ending())
    monkeypatch.setattr(sup, "stop_session", lambda s: None)
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    monkeypatch.setattr(sup, "_sleep", pause)
    state["root"] = root
    return state


def _router(root: Path, manager: str):
    """The real routing step `rite start` builds (`main._router_for`)."""

    def step(say):
        routing.deliver_routes(root, manager, OWNER, NAMES, say)
        if manager == OWNER:
            routing.collect_reports(root, OWNER, NAMES, say)

    return step


def _secondary_answers_at(world, at: float, *, progress_at: float | None = None):
    """A secondary that is running (its supervisor recorded), takes what was
    routed, and at virtual time `at` replies and ends the cycle that carried
    it. With `progress_at`, it says "working on it" first, mid-cycle."""
    root = world["root"]
    routing.record_supervisor(root, SECONDARY, os.getpid())
    done = {"taken": [], "progress": False, "final": False}

    def step():
        if not done["taken"]:
            done["taken"] = [
                m.path.name for m in mailbox.take(root, SECONDARY, mailbox.INBOX)
            ]
        if not done["taken"]:
            return
        now = world["t"]
        if progress_at is not None and not done["progress"] and now >= progress_at:
            mailbox.send(root, SECONDARY, mailbox.OUTBOX, "PROGRESS: working on it")
            done["progress"] = True
        if not done["final"] and now >= at:
            mailbox.send(root, SECONDARY, mailbox.OUTBOX, REPLY)
            routing._record_handled(root, SECONDARY, done["taken"])
            done["final"] = True
            done["final_at"] = now

    world["between"].append(step)
    return done


def _run_owner(world, *, ceiling: int, cycle_secs: float, verdict="ready"):
    root = world["root"]
    starts: list[float] = []
    prompts: list[str] = []
    said: list[str] = []

    def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
        starts.append(world["t"])
        prompts.append(kw.get("prompt") or "")
        if len(starts) == 1:
            # The Owner's model routes in its first cycle, as `rite route` does.
            routing.request(root, OWNER, SECONDARY, TASK)
        # The session runs; the secondary may act while it does.
        end = world["t"] + cycle_secs
        while world["t"] < end:
            world["t"] = min(end, world["t"] + 1.0)
            for step in world["between"]:
                step()
        return StartResult(True, "ok", session=f"s{len(starts)}", attach="a")

    result = supervise(
        root,
        OWNER,
        waiting=routing.Waiting(root, OWNER, OWNER, clock=lambda: world["t"]),
        engine="claude",
        max_sessions=ceiling,
        window_seconds=0,
        prompt="OPEN",
        starter=starter,
        router=_router(root, OWNER),
        verdict=(lambda r: verdict()) if callable(verdict) else (lambda r: verdict),
        resume_id_for=lambda r, m, since=0.0: "sess-1",
        note=said.append,
        poll=0,
        now=lambda: world["t"],
    )
    return result, starts, prompts, said


def _carrying(prompts, text):
    return [i for i, p in enumerate(prompts) if text in p]


# The audit session's rows, plus the ceiling Robert set for the Owner (2).
SWEEP = [
    (3, 7, 23),
    (3, 7, 30),
    (5, 7, 23),
    (10, 7, 23),
    (3, 12, 23),
    (10, 2, 23),
    (2, 7, 23),
    (2, 7, 3),
    (2, 40, 23),
    (1, 7, 23),
]


class TestAReplyReachesAStillRunningOwner:
    @pytest.mark.parametrize("ceiling,cycle_secs,reply_at", SWEEP)
    def test_the_cycle_carrying_the_reply_starts_after_it_arrives(
        self, world, ceiling, cycle_secs, reply_at
    ):
        done = _secondary_answers_at(world, reply_at)
        result, starts, prompts, said = _run_owner(
            world, ceiling=ceiling, cycle_secs=cycle_secs
        )
        carrying = _carrying(prompts, REPLY)
        assert carrying, (result.reason, starts, said)
        first = carrying[0]
        assert starts[first] >= done["final_at"], (starts, done)
        # The FIRST cycle to start after the reply is the one carrying it.
        assert all(s < done["final_at"] for s in starts[:first]), starts
        assert len(carrying) == 1, "delivered once, not twice"

    @pytest.mark.parametrize("ceiling,cycle_secs,reply_at", SWEEP)
    def test_no_session_is_started_for_nothing_past_the_ceiling(
        self, world, ceiling, cycle_secs, reply_at
    ):
        """Waiting spends nothing: past the ceiling, every cycle carries mail."""
        _secondary_answers_at(world, reply_at)
        _result, starts, prompts, _said = _run_owner(
            world, ceiling=ceiling, cycle_secs=cycle_secs
        )
        for i in range(ceiling, len(starts)):
            assert "[from Manager" in prompts[i], (i, starts)

    def test_an_idle_board_waits_instead_of_stopping(self, world):
        """The board has one thing, the Owner routes it and the board goes
        idle. Before, `idle` ended the run with the reply still to come."""
        done = _secondary_answers_at(world, 30)
        asked = []

        def board():
            asked.append(1)
            return "ready" if len(asked) == 1 else "idle"

        result, starts, prompts, said = _run_owner(
            world, ceiling=5, cycle_secs=5, verdict=board
        )
        carrying = _carrying(prompts, REPLY)
        assert len(starts) == 2 and carrying == [1], (starts, result.reason, said)
        assert starts[1] >= done["final_at"]

    def test_the_ceiling_bending_is_said(self, world):
        _secondary_answers_at(world, 23)
        _result, _starts, _prompts, said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert any("SOFT while" in line for line in said), said


class TestAProgressReplyDoesNotEndTheWait:
    def test_both_the_progress_and_the_final_reply_arrive(self, world):
        """A reply does not mean the work is done; the cycle's END does. With
        the count cleared by the first reply, the final one missed the Owner."""
        done = _secondary_answers_at(world, 60, progress_at=20)
        result, starts, prompts, said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert _carrying(prompts, "PROGRESS"), (result.reason, said)
        carrying = _carrying(prompts, REPLY)
        assert carrying and starts[carrying[0]] >= done["final_at"], (starts, said)


class TestTheWaitEndsForAStatedReason:
    def test_a_secondary_that_is_provably_gone_ends_the_wait(self, world):
        root = world["root"]
        routing.record_supervisor(root, SECONDARY, os.getpid())

        def dies():
            if world["t"] >= 30:
                routing.forget_supervisor(root, SECONDARY, os.getpid())

        world["between"].append(dies)
        result, starts, _prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert len(starts) == 2
        assert "has stopped" in result.reason and "small" in result.reason, result

    def test_a_secondary_never_seen_running_is_not_gone_and_it_is_said(
        self, world, monkeypatch
    ):
        """⚠ Seen-then-absent, not merely absent: a secondary started a moment
        after the Owner would otherwise end the Owner's wait by start order."""
        said_lines: list[str] = []
        root = world["root"]

        def arrives_late():
            if world["t"] >= 700 and not routing._supervisor_running(root, SECONDARY):
                _secondary_answers_at(world, 720)

        world["between"].append(arrives_late)
        result, starts, prompts, said = _run_owner(world, ceiling=2, cycle_secs=7)
        said_lines += said
        assert _carrying(prompts, REPLY), result.reason
        assert any("NOT RUNNING" in line for line in said_lines), said_lines
        assert any(line.startswith("⚠ still:") for line in said_lines), said_lines


class TestTheSecondarySide:
    """The race the second opinion found: a secondary read its routed
    instruction only at ITS next cycle start, so an idle board or a spent
    ceiling left the route sitting until its next `rite start`."""

    def _run_secondary(self, world, *, ceiling: int, verdict: str, routes_at):
        root = world["root"]
        starts: list[float] = []
        prompts: list[str] = []
        said: list[str] = []
        pending = list(routes_at)

        def owner_routes():
            while pending and world["t"] >= pending[0]:
                pending.pop(0)
                routing.request(root, OWNER, SECONDARY, f"{TASK} #{len(starts)}")
                routing.deliver_routes(root, OWNER, OWNER, NAMES, lambda _m: None)

        world["between"].append(owner_routes)

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
            starts.append(world["t"])
            prompts.append(kw.get("prompt") or "")
            world["t"] += 7
            return StartResult(True, "ok", session=f"s{len(starts)}", attach="a")

        result = supervise(
            root,
            SECONDARY,
            waiting=routing.Waiting(root, SECONDARY, OWNER, clock=lambda: world["t"]),
            engine="goose",
            max_sessions=ceiling,
            window_seconds=0,
            prompt="OPEN",
            starter=starter,
            router=_router(root, SECONDARY),
            verdict=lambda r: verdict,
            resume_id_for=lambda r, m, since=0.0: "sess-1",
            note=said.append,
            poll=0,
            now=lambda: world["t"],
        )
        return result, starts, prompts, said

    def test_an_idle_secondary_starts_no_session_until_work_arrives(self, world):
        root = world["root"]
        routing.record_supervisor(root, OWNER, os.getpid())
        world["between"].append(
            lambda: (
                world["t"] >= 200
                and routing.forget_supervisor(root, OWNER, os.getpid())
            )
        )
        result, starts, prompts, _said = self._run_secondary(
            world, ceiling=1, verdict="idle", routes_at=[40]
        )
        assert starts and starts[0] >= 40, starts
        assert TASK in prompts[0]
        assert "Owner 'lead' has stopped" in result.reason, result

    def test_a_route_after_the_ceiling_still_starts_a_cycle(self, world):
        root = world["root"]
        routing.record_supervisor(root, OWNER, os.getpid())
        world["between"].append(
            lambda: (
                world["t"] >= 300
                and routing.forget_supervisor(root, OWNER, os.getpid())
            )
        )
        _result, starts, prompts, _said = self._run_secondary(
            world, ceiling=1, verdict="idle", routes_at=[40, 120]
        )
        assert len(starts) == 2 and starts[1] >= 120, starts
        assert all(TASK in p for p in prompts)

    def test_its_finished_cycle_is_recorded_as_handled(self, world):
        root = world["root"]
        routing.record_supervisor(root, OWNER, os.getpid())
        world["between"].append(
            lambda: (
                world["t"] >= 100
                and routing.forget_supervisor(root, OWNER, os.getpid())
            )
        )
        self._run_secondary(world, ceiling=1, verdict="idle", routes_at=[10])
        assert routing._outstanding(root, OWNER) == {}


class TestALoneManagerIsUnchanged:
    """No `waiting`: the audit's reproduction, byte for byte in outcome."""

    @pytest.mark.parametrize(
        "ceiling,cycle_secs,expected",
        [(3, 7, [0.0, 7.0, 14.0]), (10, 2, [float(2 * i) for i in range(10)])],
    )
    def test_the_ceiling_is_still_a_hard_count(
        self, world, ceiling, cycle_secs, expected
    ):
        root = world["root"]
        starts: list[float] = []

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
            starts.append(world["t"])
            world["t"] += cycle_secs
            return StartResult(True, "ok", session=f"s{len(starts)}", attach="a")

        result = supervise(
            root,
            OWNER,
            engine="claude",
            max_sessions=ceiling,
            window_seconds=0,
            prompt="OPEN",
            starter=starter,
            verdict=lambda r: "ready",
            resume_id_for=lambda r, m, since=0.0: "sess-1",
            poll=0,
            now=lambda: world["t"],
        )
        assert starts == expected
        assert result.reason.startswith("ceiling reached")


class TestTheWaitReadsHandledBeforeCollecting:
    """⚠ The order inside one tick. If the wait collected replies first and
    read "handled" second, a secondary finishing between the two would be seen
    as handled with its reply still uncollected, and the wait would end with
    the reply left for the next `rite start`. Pinned by making the secondary
    finish at exactly that point."""

    def test_a_secondary_finishing_mid_tick_is_still_delivered(
        self, world, monkeypatch
    ):
        done = _secondary_answers_at(world, 10**9)  # never on its own
        step = world["between"][-1]
        real_over = routing.Waiting.over

        def over_after_the_secondary_finishes(self):
            if not done["final"] and world["t"] >= 23 and done["taken"]:
                done_at = world["t"]
                mailbox.send(world["root"], SECONDARY, mailbox.OUTBOX, REPLY)
                routing._record_handled(world["root"], SECONDARY, done["taken"])
                done.update(final=True, final_at=done_at)
            return real_over(self)

        world["between"].remove(step)
        world["between"].append(
            lambda: (
                done["taken"]
                or done.update(
                    taken=[
                        m.path.name
                        for m in mailbox.take(world["root"], SECONDARY, mailbox.INBOX)
                    ]
                )
            )
        )
        monkeypatch.setattr(routing.Waiting, "over", over_after_the_secondary_finishes)
        result, starts, prompts, said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert _carrying(prompts, REPLY), (result.reason, starts, said)


class TestTheLedgerIsTheSupervisorsNotTheModels:
    def test_a_delivered_route_is_outstanding_until_its_cycle_is_handled(
        self, tmp_path
    ):
        routing.request(tmp_path, OWNER, SECONDARY, TASK)
        routing.deliver_routes(tmp_path, OWNER, OWNER, NAMES, lambda _m: None)
        (name,) = routing._outstanding(tmp_path, OWNER)[SECONDARY]
        # A reply alone does not clear it: only the end of the cycle does.
        mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "PROGRESS")
        assert routing._outstanding(tmp_path, OWNER) == {SECONDARY: [name]}
        routing._record_handled(tmp_path, SECONDARY, [name])
        assert routing._outstanding(tmp_path, OWNER) == {}

    def test_it_lives_where_no_manager_profile_grants_a_write(self, tmp_path):
        """Beside the mailbox, outside `mail/`: a Manager is granted its own
        outbox and nothing else there, on both platforms."""
        where = routing._ledger_dir(tmp_path, SECONDARY)
        mail = mailbox.mail_root(tmp_path, SECONDARY)
        assert where.parent == mail.parent and not where.is_relative_to(mail)

    def test_a_dead_supervisor_is_provably_gone(self, tmp_path):
        assert not routing._supervisor_running(tmp_path, SECONDARY)  # no record
        routing.record_supervisor(tmp_path, SECONDARY, os.getpid())
        assert routing._supervisor_running(tmp_path, SECONDARY)
        routing.record_supervisor(tmp_path, SECONDARY, 2**22 + 12345)  # no such pid
        assert not routing._supervisor_running(tmp_path, SECONDARY)

    def test_supervise_records_itself_for_the_whole_run_and_removes_it(self, world):
        root = world["root"]
        seen: list[bool] = []

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
            seen.append(routing._supervisor_running(root, OWNER))
            return StartResult(True, "ok", session="s1", attach="a")

        supervise(
            root,
            OWNER,
            waiting=routing.Waiting(root, OWNER, OWNER),
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            prompt="OPEN",
            starter=starter,
            verdict=lambda r: "ready",
            resume_id_for=lambda r, m, since=0.0: "sess-1",
            poll=0,
            now=lambda: world["t"],
        )
        assert seen == [True]
        assert not routing._supervisor_running(root, OWNER)
