"""SCRUM-20 (perpetual): `rite start lead` with no bounds runs until stopped.

Robert, 2026-10-02: vanilla `rite start` runs FOREVER by default — until Ctrl-C
or the machine dies. The mandatory `--sessions`/`--minutes` stop being
mandatory.

🔴 **D-69 and D-82 are not repealed, they are rescoped.** Both bounds still
exist and both still apply — PER CYCLE. Reaching one ends the CYCLE; the
Manager waits and continues. That is the whole difference, and it is what these
tests pin: the same ceiling that used to return a result now begins another
cycle.

⚠ **What bounds a perpetual run, stated here because it is the whole story:**
the per-cycle ceilings these tests pin, and the schedule (its own change). There
is no third thing, and these tests are where the first of the two is held.

⚠ **Ctrl-C is the off switch**, and the only one that records why: it raises
through `supervise`'s `finally`, so the lifecycle record says the operator
stopped it. `rite manager stop` kills the tmux session, which skips that and is
recorded as DIED — a cooperative stop marker is its own ticket.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import rite_ai.managers.supervise as sup
from rite_ai.cli.main import LoopAnswer
from rite_ai.managers import mailbox
from rite_ai.managers.supervise import (
    EMPTY_CYCLES_BEFORE_STOPPING,
    PERPETUAL_CYCLE_SECONDS,
    PERPETUAL_SESSIONS_PER_CYCLE,
    StartResult,
    _perpetual,
    _spinning,
    supervise,
)

OWNER = "lead"


class _Ending:
    kind = "finished"
    resume = True
    status = 0
    detail = ""


class RanForever(Exception):
    """Raised by the fake clock's pause once a test has waited absurdly often.

    🔴 **A test of a loop with no bound must not be able to hang.** Every
    assertion here is about a run that is supposed to END — on the operator, or
    on the spin gate — so a run that keeps waiting is a FAILURE, and this is
    what makes it present as one. Measured: removing the spin gate made this
    file run forever instead of going red, which in CI is a stuck job rather
    than a reported defect.
    """


WAITS_BEFORE_CALLING_IT_FOREVER = 200


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path
    (root / ".rite").mkdir()
    state = {"t": 0.0, "root": root, "waits": 0}

    def pause(_seconds):
        state["waits"] += 1
        if state["waits"] > WAITS_BEFORE_CALLING_IT_FOREVER:
            raise RanForever(f"the run waited {state['waits']} times without ending")
        state["t"] += 2.0

    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(sup, "ending", lambda n, human_was_present, pane="": _Ending())
    monkeypatch.setattr(sup, "stop_session", lambda s: None)
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    monkeypatch.setattr(sup, "_sleep", pause)
    return state


def _board(ready=("KAN-6", "KAN-7", "KAN-8", "KAN-9")):
    return LoopAnswer.of(
        SimpleNamespace(verdict="ready", ready=list(ready), blocked={})
    )


def _run(world, *, sessions, minutes, stop_after=None, cycle_secs=10.0):
    """A run whose every session does work. `stop_after` raises KeyboardInterrupt
    once that many sessions have started — standing in for the operator, which is
    the only thing that ends a perpetual run.

    ⚠ The supervisor CATCHES KeyboardInterrupt by design (`_torn_down`: Ctrl-C
    ends the session, drops the record and says so), so these assert on the
    RESULT, not on a propagating exception. A first draft of this file expected
    the raise and was wrong about the product, not about the loop.
    """
    root = world["root"]
    starts: list[float] = []
    said: list[str] = []

    def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
        starts.append(world["t"])
        if stop_after is not None and len(starts) >= stop_after:
            raise KeyboardInterrupt
        mailbox.send(root, OWNER, mailbox.OUTBOX, f"did something {len(starts)}")
        world["t"] += cycle_secs
        return StartResult(True, "ok", session=f"s{len(starts)}", attach="a")

    kw = {}
    if sessions is not None:
        kw["max_sessions"] = sessions
    if minutes is not None:
        kw["window_seconds"] = minutes * 60

    def go():
        return supervise(
            root,
            OWNER,
            engine="claude",
            prompt="OPEN",
            starter=starter,
            verdict=lambda r: _board(),
            resume_id_for=lambda r, m, since=0.0: "sess-1",
            note=said.append,
            poll=0,
            now=lambda: world["t"],
            **kw,
        )

    return go, starts, said


class TestWhichRunsArePerpetual:
    def test_no_bound_at_all_is_perpetual(self):
        assert _perpetual(None, None) is True

    def test_both_bounds_is_not(self):
        assert _perpetual(20, 3600.0) is False

    @pytest.mark.parametrize("pair", [(20, None), (None, 3600.0)])
    def test_one_bound_without_the_other_is_not_perpetual(self, pair):
        """⚠ A half-bounded run silently becoming perpetual is the surprise this
        avoids. The CLI refuses the combination outright; this pins that the
        loop would not treat it as forever either."""
        assert _perpetual(*pair) is False


class TestTheCeilingEndsTheCycleNotTheRun:
    def test_a_bounded_run_still_stops_on_its_ceiling(self, world):
        """⚠ The control. If this changed, the bounds would have been removed
        rather than rescoped."""
        go, starts, _said = _run(world, sessions=2, minutes=60)

        result = go()

        assert result.ok
        assert "ceiling reached" in result.reason
        assert len(starts) == 2

    def test_a_perpetual_run_passes_its_per_cycle_ceiling_and_keeps_going(
        self, world, monkeypatch
    ):
        """🔴 The property. With the per-cycle ceiling lowered to 2, a run that
        used to stop at 2 sessions now starts a 3rd — in a new cycle."""
        monkeypatch.setattr(sup, "PERPETUAL_SESSIONS_PER_CYCLE", 2)
        go, starts, said = _run(world, sessions=None, minutes=None, stop_after=5)

        result = go()

        assert "stopped by the operator" in result.reason
        assert len(starts) >= 3, "it did not pass the per-cycle ceiling"
        assert any("cycle ended" in s for s in said)
        assert any("session(s) in it did work" in s for s in said)

    def test_and_says_it_waits_and_continues(self, world, monkeypatch):
        monkeypatch.setattr(sup, "PERPETUAL_SESSIONS_PER_CYCLE", 2)
        go, _starts, said = _run(world, sessions=None, minutes=None, stop_after=4)

        go()

        assert any("no bound from you" in s and "Ctrl-C ends it" in s for s in said)

    def test_the_window_also_ends_the_cycle_not_the_run(self, world, monkeypatch):
        """The clock half of D-82, rescoped the same way."""
        monkeypatch.setattr(sup, "PERPETUAL_CYCLE_SECONDS", 15.0)
        monkeypatch.setattr(sup, "PERPETUAL_SESSIONS_PER_CYCLE", 99)
        go, starts, said = _run(
            world, sessions=None, minutes=None, stop_after=4, cycle_secs=10.0
        )

        go()

        assert any("cycle ended" in s and "window elapsed" in s for s in said)
        assert len(starts) >= 3, "the window ended the run instead of the cycle"


class TestTheHistoryIsKeptAcrossCycles:
    def test_the_record_still_holds_every_session(self, world, monkeypatch):
        """⚠ The per-cycle counters reset; `cycles` must not. SCRUM-20's
        lifecycle record reads it, so a reset that dropped the history would
        undo the thing just fixed."""
        monkeypatch.setattr(sup, "PERPETUAL_SESSIONS_PER_CYCLE", 2)
        root = world["root"]
        go, starts, _said = _run(world, sessions=None, minutes=None, stop_after=5)

        go()

        from rite_ai.managers.routing import supervisor_record

        rec = supervisor_record(root, OWNER)
        # KeyboardInterrupt runs the `finally`, so the run recorded its end.
        assert rec.get("state") == "ended"
        assert rec.get("sessions_started", 0) >= len(starts) - 1


class TestTheDefaultsAreStated:
    def test_the_per_cycle_numbers_exist_and_are_sane(self):
        assert PERPETUAL_SESSIONS_PER_CYCLE > 0
        assert PERPETUAL_CYCLE_SECONDS > 0


class TestASpinningRunStopsRatherThanHanging:
    """🔴 The relaunch gate. "Runs forever" is only shippable if a run that is
    making no progress ENDS and says so — a perpetual loop that begins cycle
    after cycle without starting a session is cheap, silent and looks exactly
    like working.

    ⚠ **Every assertion here came out of a mutation that HUNG.** Returning the
    whole run from `this_cycle()` left the per-cycle count never below its
    ceiling, so each iteration ended the cycle at once and the test never
    finished. A defect that presents as a hang is the worst case for a suite,
    so it gets its own stop.
    """

    def test_a_run_whose_cycles_start_nothing_stops(self, world, monkeypatch):
        """A ceiling of 0 is reached before any session, so every cycle is
        empty. Without the gate this test would not return."""
        monkeypatch.setattr(sup, "PERPETUAL_SESSIONS_PER_CYCLE", 0)
        go, starts, said = _run(world, sessions=None, minutes=None)

        result = go()

        assert starts == [], "a cycle that was supposed to start nothing did"
        assert "spinning rather than waiting" in result.reason
        assert "defect in rite" in result.reason
        assert said, "it stopped without saying anything"

    def test_it_waits_that_many_cycles_before_giving_up(self, world, monkeypatch):
        """Not on the first empty cycle: one is a wait, several in a row is a
        spin. So the gate is counted, and this pins the count it uses."""
        monkeypatch.setattr(sup, "PERPETUAL_SESSIONS_PER_CYCLE", 0)
        go, _starts, said = _run(world, sessions=None, minutes=None)

        go()

        assert (
            sum(1 for s in said if "cycle ended" in s) == EMPTY_CYCLES_BEFORE_STOPPING
        )

    def test_a_productive_cycle_clears_the_count(self, world, monkeypatch):
        """🔴 **The half that keeps "forever" forever.** The gate counts
        CONSECUTIVE empty cycles. Counted cumulatively it would stop every long
        run after three quiet patches, which is the bug, not the guard."""
        monkeypatch.setattr(sup, "PERPETUAL_SESSIONS_PER_CYCLE", 1)
        go, starts, _said = _run(world, sessions=None, minutes=None, stop_after=8)

        result = go()

        assert len(starts) == 8, "it stopped before the operator did"
        assert "spinning" not in result.reason
        assert "stopped by the operator" in result.reason

    def test_the_gate_itself_is_counted(self):
        assert _spinning(EMPTY_CYCLES_BEFORE_STOPPING - 1, []) is None
        assert _spinning(EMPTY_CYCLES_BEFORE_STOPPING, []) is not None

    def test_the_gate_reports_the_history_it_stopped_on(self):
        """It stops because of a defect, so what it returns has to be enough to
        chase one: the sessions the run did start come back with it."""
        result = _spinning(EMPTY_CYCLES_BEFORE_STOPPING, ["a", "b"])

        assert result is not None
        assert result.cycles == ["a", "b"]


class TestEachCycleGetsItsOwnWholeWindow:
    def test_a_cycle_ended_by_the_ceiling_re_arms_the_clock(self, world, monkeypatch):
        """⚠ The window is PER CYCLE, so ending a cycle on the ceiling has to
        move the deadline with it. Left where it was, the very next iteration
        finds it already past and ends a second cycle — one that started
        nothing — reporting "window elapsed" for a window that never ran out.
        Mutation-found: the loop self-heals, so only the false line shows it.
        """
        monkeypatch.setattr(sup, "PERPETUAL_SESSIONS_PER_CYCLE", 2)
        # ⚠ The window has to be SHORT enough that a deadline left behind is
        # already past. With the hour-long default, every cycle finishes long
        # before it and the defect is invisible — which is how it survived the
        # first version of this test.
        monkeypatch.setattr(sup, "PERPETUAL_CYCLE_SECONDS", 30.0)
        go, starts, said = _run(
            world, sessions=None, minutes=None, stop_after=6, cycle_secs=10.0
        )

        go()

        assert len(starts) == 6
        assert sum(1 for s in said if "session(s) in it did work" in s) >= 2
        assert not [s for s in said if "window elapsed" in s], (
            "a cycle ended on the ceiling, then blamed a window that had not elapsed"
        )


# --- A perpetual run WAITS where a bounded one would end (SCRUM-20) ---------
#
# 🔴 Found by probing the salvaged loop, each measured on it before the fix: a
# Manager waiting on the User, or on a quiet board, ENDED a run nobody bounded
# ("window elapsed while waiting for mail" after one hour; "done: the board
# listed nothing ready" at once). Waiting on answers is most of what a
# perpetual Manager does. Here the only thing that ends the run is the
# operator, standing in as a KeyboardInterrupt at a five-hour horizon.

HORIZON = 5 * 3600.0


@pytest.fixture
def patient(tmp_path, monkeypatch):
    """`world`, but a wait advances 30 virtual seconds and the operator
    presses Ctrl-C at `HORIZON`. Hours of waiting must reach that point."""
    root = tmp_path
    (root / ".rite").mkdir()
    state = {"t": 0.0, "root": root, "between": []}

    def pause(_seconds):
        state["t"] += 30.0
        for step in state["between"]:
            step()
        if state["t"] >= HORIZON:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(sup, "ending", lambda n, human_was_present, pane="": _Ending())
    monkeypatch.setattr(sup, "stop_session", lambda s: None)
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    monkeypatch.setattr(sup, "_sleep", pause)
    return state


def _answer(verdict, ready=()):
    return LoopAnswer.of(
        SimpleNamespace(verdict=verdict, ready=list(ready), blocked={})
    )


def _perpetual_run(patient, board, useful=0):
    """No bounds. The first `useful` sessions each write a reply; the rest
    change nothing. Returns (result, start times, what was said)."""
    root = patient["root"]
    starts: list[float] = []
    said: list[str] = []

    def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
        starts.append(patient["t"])
        if len(starts) <= useful:
            mailbox.send(root, OWNER, mailbox.OUTBOX, f"did something {len(starts)}")
        patient["t"] += 10.0
        return StartResult(True, "ok", session=f"s{len(starts)}", attach="a")

    result = supervise(
        root,
        OWNER,
        engine="claude",
        prompt="OPEN",
        starter=starter,
        verdict=lambda r: board(),
        resume_id_for=lambda r, m, since=0.0: "sess-1",
        note=said.append,
        poll=0,
        now=lambda: patient["t"],
    )
    return result, starts, said


class TestAPerpetualRunWaitsRatherThanEnding:
    def test_waiting_on_the_user_does_not_end_the_run(self, patient):
        result, starts, _ = _perpetual_run(
            patient, lambda: _answer("waiting-on-user", ["KAN-7"])
        )
        assert result.reason.startswith("stopped by the operator"), result.reason
        assert patient["t"] >= HORIZON and starts == []

    def test_a_quiet_board_does_not_end_the_run(self, patient):
        result, starts, said = _perpetual_run(patient, lambda: _answer("idle"))
        assert result.reason.startswith("stopped by the operator"), result.reason
        assert starts == []
        assert any("the board lists nothing ready" in s for s in said)

    def test_work_appearing_on_a_quiet_board_starts_a_session(self, patient):
        board = {"now": _answer("idle")}

        def filed():
            if patient["t"] >= 2 * 3600 and board["now"] == "idle":
                board["now"] = _answer("ready", ["KAN-10"])

        patient["between"].append(filed)
        result, starts, _ = _perpetual_run(patient, lambda: board["now"], useful=1)
        assert result.reason.startswith("stopped by the operator"), result.reason
        assert starts and starts[0] >= 2 * 3600

    def test_a_manager_whose_session_changed_nothing_waits_past_the_hour(self, patient):
        """The probe's shape: one useful session, then nothing to do on an
        unchanged board. It ended after an hour, 'window elapsed'."""
        result, starts, _ = _perpetual_run(
            patient, lambda: _answer("ready", ["KAN-6"]), useful=1
        )
        assert result.reason.startswith("stopped by the operator"), result.reason
        assert len(starts) == 2

    @pytest.mark.parametrize("fault", ["deadlocked", "unknown"])
    def test_a_fault_still_ends_the_run(self, patient, fault):
        result, starts, _ = _perpetual_run(patient, lambda: _answer(fault))
        assert not result.reason.startswith("stopped by the operator"), result.reason
        assert patient["t"] < HORIZON and starts == []

    def test_an_unrecognised_verdict_still_ends_the_run(self, patient):
        result, _, _ = _perpetual_run(patient, lambda: "not a verdict")
        assert "not one of its verdicts" in result.reason
        assert patient["t"] < HORIZON

    def test_control_a_bounded_run_on_a_quiet_board_still_ends(self, patient):
        root = patient["root"]
        result = supervise(
            root,
            OWNER,
            engine="claude",
            prompt="OPEN",
            starter=lambda *a, **k: StartResult(True, "ok", session="s", attach="a"),
            verdict=lambda r: _answer("idle"),
            resume_id_for=lambda r, m, since=0.0: "sess-1",
            note=[].append,
            poll=0,
            now=lambda: patient["t"],
            max_sessions=5,
            window_seconds=600,
        )
        assert "nothing ready" in result.reason
