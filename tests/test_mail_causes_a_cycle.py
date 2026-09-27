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
    """Robert's decision 4, option (c): seen running then gone, plus the
    recorded lifecycle. Each ending is named, because "finished", "died" and
    "never started" need different responses from a person."""

    def test_a_secondary_that_finished_its_run_ends_the_wait_and_says_finished(
        self, world
    ):
        root = world["root"]
        routing.record_supervisor(root, SECONDARY, os.getpid())

        def ends():
            if world["t"] >= 30:
                routing.forget_supervisor(root, SECONDARY, os.getpid())

        world["between"].append(ends)
        result, starts, prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        # Decision 3: stopping with the work outstanding is TOLD to the Owner
        # first, as a note that starts its session, so the person hears it.
        assert len(starts) == 3, starts
        assert "STOPPED WITH ROUTED WORK OUTSTANDING" in prompts[2]
        assert "'small' finished its run" in result.reason, result
        assert "DIED" not in result.reason and "never" not in result.reason

    def test_a_secondary_that_died_ends_the_wait_and_says_died(self, world):
        root = world["root"]
        routing.record_supervisor(root, SECONDARY, os.getpid())

        def killed():
            # A killed run never reaches its `finally`: the record still says
            # running, and the recorded process is gone.
            if world["t"] >= 30:
                # It took the route into a session first, then was killed.
                mailbox.take(root, SECONDARY, mailbox.INBOX)
                path = routing._ledger_dir(root, SECONDARY) / routing.SUPERVISOR_FILE
                data = routing._load(path)
                if data.get("pid") == os.getpid():
                    data["pid"] = 2**22 + 12345
                    routing._store(path, data)

        world["between"].append(killed)
        result, _starts, _prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert "'small' DIED" in result.reason, result
        assert "may not have happened" in result.reason
        # It had TAKEN the message: saying it waits in the inbox would be false.
        assert "taken into a session that did not finish" in result.reason
        assert "wait in its inbox" not in result.reason

    def test_a_secondary_that_died_before_the_owner_ever_looked_is_still_died(
        self, world
    ):
        """Found by observation through `rite start`: the secondary took the
        route and was killed while the Owner's first session still ran, so the
        Owner never saw it alive and waited out the window. Its record says
        its run started during the Owner's run, which proves it ran."""
        root = world["root"]

        def starts_then_is_killed():
            path = routing._ledger_dir(root, SECONDARY) / routing.SUPERVISOR_FILE
            if world["t"] >= 2 and not path.exists():
                routing.record_supervisor(root, SECONDARY, os.getpid())
                data = routing._load(path)
                data["pid"] = 2**22 + 12345  # killed before the Owner looks
                routing._store(path, data)

        world["between"].append(starts_then_is_killed)
        result, _starts, _prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert "'small' DIED" in result.reason, result

    def test_alive_when_handed_the_work_then_killed_is_died(self, world):
        """The observed case exactly: the secondary started BEFORE the Owner
        (so its record predates the Owner's run), was alive when the route was
        delivered, and was killed before the Owner's first wait. The Owner's
        supervisor saw it running at delivery, and recorded that."""
        root = world["root"]
        routing.record_supervisor(root, SECONDARY, os.getpid())
        path = routing._ledger_dir(root, SECONDARY) / routing.SUPERVISOR_FILE
        data = routing._load(path)
        data["started_at"] = 1.0  # started before the Owner's run
        routing._store(path, data)

        def killed_after_delivery():
            if world["t"] >= 10:
                d = routing._load(path)
                if d.get("pid") == os.getpid():
                    d["pid"] = 2**22 + 12345
                    routing._store(path, d)

        world["between"].append(killed_after_delivery)
        result, _starts, _prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert "'small' DIED" in result.reason, result

    def test_a_death_recorded_before_this_run_is_not_this_runs_death(self, world):
        """The start-order protection holds for a stale record: killed in an
        EARLIER run, it may be about to start now, so it is waited on."""
        root = world["root"]
        routing.record_supervisor(root, SECONDARY, 2**22 + 12345)
        path = routing._ledger_dir(root, SECONDARY) / routing.SUPERVISOR_FILE
        data = routing._load(path)
        data["started_at"] = 1.0  # long before this run
        routing._store(path, data)

        def arrives_late():
            if (
                world["t"] >= 700
                and routing._supervisor_state(root, SECONDARY) != routing.RUNNING
            ):
                _secondary_answers_at(world, 720)

        world["between"].append(arrives_late)
        result, _starts, prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert _carrying(prompts, REPLY), result.reason

    def test_a_secondary_never_started_is_refused_at_once_not_waited_on(self, world):
        """The hole in "seen running, then gone": a secondary that never ran
        was never seen, so the Owner would have waited forever for it."""
        result, starts, _prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert "REFUSING TO WAIT" in result.reason, result
        assert "never been started here" in result.reason
        assert "rite start small" in result.reason
        assert "wait in its inbox" in result.reason  # never taken: still there
        assert len(starts) == 2, "refused when the wait began, not after one"

    def test_a_secondary_that_ran_before_but_not_yet_now_is_waited_on_loudly(
        self, world
    ):
        """⚠ Not gone: it has a recorded start, but this run has not seen it.
        Ending here would end the wait by start order."""
        root = world["root"]
        routing.record_supervisor(root, SECONDARY, os.getpid())
        routing.forget_supervisor(root, SECONDARY, os.getpid())  # an earlier run

        def arrives_late():
            if (
                world["t"] >= 700
                and routing._supervisor_state(root, SECONDARY) != routing.RUNNING
            ):
                _secondary_answers_at(world, 720)

        world["between"].append(arrives_late)
        result, _starts, prompts, said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert _carrying(prompts, REPLY), result.reason
        assert any("NOT RUNNING" in line for line in said), said
        assert any(line.startswith("⚠ still:") for line in said), said


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
        assert "the Owner 'lead' finished its run" in result.reason, result

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


def _finishes_just_before_reason_is_read(world, monkeypatch):
    """The secondary takes the route, replies and records it handled at the
    instant BEFORE the Owner's supervisor reads `Waiting.reason()`, with no
    collect after its last boundary. That is the sub-second gap between the
    boundary's `router(say)` and the decision to stop."""
    root = world["root"]
    routing.record_supervisor(root, SECONDARY, os.getpid())
    done = {"final": False}
    real_reason = routing.Waiting.reason

    def reason_after_the_secondary_finishes(self):
        if not done["final"] and mailbox.read(root, SECONDARY, mailbox.INBOX):
            taken = [m.path.name for m in mailbox.take(root, SECONDARY, mailbox.INBOX)]
            mailbox.send(root, SECONDARY, mailbox.OUTBOX, REPLY)
            routing._record_handled(root, SECONDARY, taken)
            done.update(final=True, final_at=world["t"])
        return real_reason(self)

    monkeypatch.setattr(routing.Waiting, "reason", reason_after_the_secondary_finishes)
    return done


class TestTheStopDecisionCollectsFirst:
    """⚠ Tag blocker 3. The ceiling check and the idle verdict read
    `reason()` BEFORE anything collected from the secondaries' outboxes. A
    reply (or rite's silent-finish note) written after the boundary's collect
    and marked handled before that read made `reason()` say "nothing
    outstanding, nothing waiting", and the Owner stopped with the reply
    stranded until the next `rite start`. Pinned deterministically at both
    reads."""

    def test_at_the_ceiling(self, world, monkeypatch):
        done = _finishes_just_before_reason_is_read(world, monkeypatch)
        result, starts, prompts, said = _run_owner(world, ceiling=1, cycle_secs=7)
        assert done["final"], said
        assert _carrying(prompts, REPLY), (result.reason, starts, said)

    def test_on_an_idle_board(self, world, monkeypatch):
        done = _finishes_just_before_reason_is_read(world, monkeypatch)
        asked = []

        def board():
            asked.append(1)
            return "ready" if len(asked) == 1 else "idle"

        result, starts, prompts, said = _run_owner(
            world, ceiling=5, cycle_secs=7, verdict=board
        )
        assert done["final"], said
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
        state = routing._supervisor_state
        assert state(tmp_path, SECONDARY) == routing.NEVER  # no start recorded
        routing.record_supervisor(tmp_path, SECONDARY, os.getpid())
        assert state(tmp_path, SECONDARY) == routing.RUNNING
        routing.forget_supervisor(tmp_path, SECONDARY, os.getpid())
        assert state(tmp_path, SECONDARY) == routing.ENDED  # kept, not deleted
        routing.record_supervisor(tmp_path, SECONDARY, 2**22 + 12345)  # no such pid
        assert state(tmp_path, SECONDARY) == routing.DIED

    def test_a_recycled_pid_is_not_taken_for_the_recorded_process(self, tmp_path):
        """⚠ Recorded identity, not a liveness poll: a live pid that started at
        another time is a different process, so the recorded one DIED."""
        routing.record_supervisor(tmp_path, SECONDARY, os.getpid())
        path = routing._ledger_dir(tmp_path, SECONDARY) / routing.SUPERVISOR_FILE
        data = routing._load(path)
        assert data["process_start"], "the start time must be recorded"
        data["process_start"] = "linux:1"
        routing._store(path, data)
        assert routing._supervisor_state(tmp_path, SECONDARY) == routing.DIED

    def test_supervise_records_itself_for_the_whole_run_and_removes_it(self, world):
        root = world["root"]
        seen: list[bool] = []

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
            seen.append(routing._supervisor_state(root, OWNER) == routing.RUNNING)
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
        assert routing._supervisor_state(root, OWNER) == routing.ENDED


def _secondary_replies(world, times_and_texts, *, handled_at):
    """A running secondary that takes the route and replies at each (time,
    text), then ends the cycle that carried it at `handled_at`."""
    root = world["root"]
    routing.record_supervisor(root, SECONDARY, os.getpid())
    state = {"taken": [], "sent": 0, "handled": False}
    pending = list(times_and_texts)

    def step():
        if not state["taken"]:
            state["taken"] = [
                m.path.name for m in mailbox.take(root, SECONDARY, mailbox.INBOX)
            ]
        if not state["taken"]:
            return
        while pending and world["t"] >= pending[0][0]:
            mailbox.send(root, SECONDARY, mailbox.OUTBOX, pending.pop(0)[1])
            state["sent"] += 1
        if not state["handled"] and world["t"] >= handled_at:
            routing._record_handled(root, SECONDARY, state["taken"])
            state["handled"] = True

    world["between"].append(step)
    return state


class TestTheMailStartedCap:
    """W15 (a): past the ceiling, mail may start at most --sessions plus 2 per
    message routed this run; one for the reply, one for a correction."""

    def test_a_reply_and_its_correction_fit_inside_the_cap(self, world):
        """The case the allowance is sized for must never hit it."""
        _secondary_replies(
            world,
            [(30, "Created TOP.txt"), (90, "Correction: TOP.txt FAILED")],
            handled_at=95,
        )
        result, starts, prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert _carrying(prompts, "Created TOP.txt")
        assert _carrying(prompts, "Correction: TOP.txt FAILED")
        assert "CAP" not in result.reason, result
        assert len(starts) <= 2 + 2 * 1

    def test_a_secondary_repeating_itself_stops_at_the_cap_and_says_so(self, world):
        """The observed W15 case: three replies drove five Owner sessions
        against a ceiling of two. Now the fifth is refused, as the CAP."""
        _secondary_replies(
            world,
            [(30, "written"), (90, "written again"), (150, "written, third")],
            handled_at=200,
        )
        result, starts, _prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert len(starts) == 4, starts
        assert "MAIL-STARTED CAP, not the ceiling" in result.reason, result
        assert "--sessions 2 plus 2 per message routed this run (1) = 4" in (
            result.reason
        )

    def test_the_ceiling_message_is_still_the_ceilings_own(self, world):
        """No routed work: the ceiling is reached and named as the ceiling."""
        root = world["root"]
        starts: list[float] = []

        def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
            starts.append(world["t"])
            world["t"] += 5
            return StartResult(True, "ok", session=f"s{len(starts)}", attach="a")

        result = supervise(
            root,
            OWNER,
            waiting=routing.Waiting(root, OWNER, OWNER),
            engine="claude",
            max_sessions=2,
            window_seconds=0,
            prompt="OPEN",
            starter=starter,
            verdict=lambda r: "ready",
            resume_id_for=lambda r, m, since=0.0: "sess-1",
            poll=0,
            now=lambda: world["t"],
        )
        assert result.reason.startswith("ceiling reached: 2 session(s)"), result


class TestRitesOwnNotesAreNotChargedToTheReplyAllowance:
    """Audit finding: a budget sized for one kind of session, spent by
    another. A reply and a correction use the reply allowance; a DIED note
    after them must still start a session, or the person is never told."""

    def test_a_death_after_a_reply_and_a_correction_is_still_told(self, world):
        root = world["root"]
        _secondary_replies(
            world,
            [(30, "Created TOP.txt"), (90, "Correction: FAILED")],
            handled_at=10**9,
        )

        def killed_after_both():
            if world["t"] >= 150:
                path = routing._ledger_dir(root, SECONDARY) / routing.SUPERVISOR_FILE
                d = routing._load(path)
                if d.get("pid") == os.getpid():
                    d["pid"] = 2**22 + 12345
                    routing._store(path, d)

        world["between"].append(killed_after_both)
        result, starts, prompts, _said = _run_owner(world, ceiling=2, cycle_secs=7)
        assert any("DIED WITH ROUTED WORK OUTSTANDING" in p for p in prompts), (
            result.reason,
            starts,
        )
        assert "MAIL-STARTED CAP" not in result.reason, result
