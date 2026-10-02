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
    BOARD_RECHECK_SECONDS,
    SPIN_PASSES_BEFORE_STOPPING,
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

    @pytest.mark.parametrize("sessions, minutes", [(2, None), (None, 10)])
    def test_a_caller_giving_one_bound_is_refused_not_defaulted(
        self, world, sessions, minutes
    ):
        go, starts, _ = _run(world, sessions=sessions, minutes=minutes)
        with pytest.raises(ValueError, match="both"):
            go()
        assert starts == []


class TestABoundedRunIsUnchanged:
    def test_a_bounded_run_still_stops_on_its_ceiling(self, world):
        """⚠ The control: the bounds are opt-in now, not gone."""
        go, starts, _said = _run(world, sessions=2, minutes=60)

        result = go()

        assert result.ok
        assert "ceiling reached" in result.reason
        assert len(starts) == 2


class TestAPerpetualRunHasNoSessionCeiling:
    """🔴 The salvaged loop "bounded" a perpetual run with 20 sessions per
    cycle, then waited one poll and began the next cycle with a fresh 20, so
    it bounded nothing (measured: 357 working sessions a virtual hour). That
    machinery is gone (Robert, 2026-10-02: the bound is concurrency, not a
    rate). A perpetual run starts one session at a time, the schedule caps its
    Workers, and the waits hold it when there is nothing to do."""

    def test_a_working_run_passes_where_the_old_ceiling_was(self, world):
        go, starts, said = _run(world, sessions=None, minutes=None, stop_after=25)

        result = go()

        assert "stopped by the operator" in result.reason
        assert len(starts) == 25
        assert not any("cycle ended" in s for s in said)

    def test_the_record_holds_every_session(self, world):
        root = world["root"]
        go, starts, _said = _run(world, sessions=None, minutes=None, stop_after=5)

        go()

        from rite_ai.managers.routing import supervisor_record

        rec = supervisor_record(root, OWNER)
        assert rec.get("state") == "ended"
        assert rec.get("sessions_started", 0) >= len(starts) - 1


class TestTheSpinGuard:
    """A pass of the loop either starts a session or waits. One that does
    neither, repeated, is a spin, and the run stops and says so.

    ⚠ **Not reachable through a working loop**, and that is the point: with
    the per-cycle rollover gone, no pass ends without a session or a wait. The
    guard is for the next defect. So it is pinned here directly, and the loop's
    counting of it is pinned by `test_a_waiting_run_is_never_counted_as_one`.
    """

    def test_it_stops_at_the_count(self):
        assert _spinning(SPIN_PASSES_BEFORE_STOPPING - 1, []) is None
        stopped = _spinning(SPIN_PASSES_BEFORE_STOPPING, ["a"])
        assert stopped is not None
        assert "spinning rather than waiting" in stopped.reason
        assert "defect in rite" in stopped.reason
        assert stopped.cycles == ["a"]

    def test_a_waiting_run_is_never_counted_as_one(self, world):
        """Hours of waiting on a quiet board are not a spin: every pass
        waited. `world` raises `RanForever` after 200 waits, which is the
        expected end here; a spin verdict would end it sooner."""
        root = world["root"]

        def starter(*a, **k):
            raise AssertionError("a quiet board started a session")

        with pytest.raises(RanForever):
            supervise(
                root,
                OWNER,
                engine="claude",
                prompt="OPEN",
                starter=starter,
                verdict=lambda r: LoopAnswer.of(
                    SimpleNamespace(verdict="idle", ready=[], blocked={})
                ),
                resume_id_for=lambda r, m, since=0.0: "sess-1",
                note=[].append,
                poll=0,
                now=lambda: world["t"],
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

    def test_a_closed_window_waits_and_says_when_it_opens(self, patient, monkeypatch):
        monkeypatch.setattr(sup, "_next_open", lambda root: "Mon 09:00")
        result, starts, said = _perpetual_run(patient, lambda: _answer("closed"))
        assert result.reason.startswith("stopped by the operator"), result.reason
        assert starts == []
        assert any("next opens Mon 09:00" in s for s in said)

    def test_the_window_opening_starts_a_session(self, patient):
        board = {"now": _answer("closed")}

        def opens():
            if patient["t"] >= 3 * 3600 and board["now"] == "closed":
                board["now"] = _answer("ready", ["KAN-6"])

        patient["between"].append(opens)
        result, starts, _ = _perpetual_run(patient, lambda: board["now"], useful=1)
        assert result.reason.startswith("stopped by the operator"), result.reason
        assert starts and starts[0] >= 3 * 3600

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


def test_a_queued_worker_starts_when_a_slot_frees_with_no_manager_session(
    patient, monkeypatch
):
    """Queue-not-discard, end to end in a perpetual run: a request waits for a
    slot, the quiet-board wait wakes when one frees, and the loop starts the
    Worker at its top. The Worker does not wait for a Manager session: the
    only sessions are the ones delivering rite's two notes ("Queued", then
    "Started"), which is how every broker outcome reaches the Manager (DF13)."""
    import json

    from rite_ai.managers import broker as broker_mod
    from rite_ai.managers.broker import NO_SLOT, requests_dir

    sup._QUEUED_TOLD.clear()
    root = patient["root"]
    where = requests_dir(root, OWNER)
    where.mkdir(parents=True)
    (where / "1.json").write_text(json.dumps({"worker": "w1", "ticket": "RT-1"}))
    asked: list[float] = []

    def broker(raw):
        asked.append(patient["t"])
        if patient["t"] < 2 * 3600:
            return NO_SLOT, "the schedule allows 1 Worker(s) right now"
        return True, "started Worker 'w1' on ticket RT-1"

    monkeypatch.setattr(broker_mod, "slot_free", lambda r: patient["t"] >= 2 * 3600)

    sessions: list[str] = []

    def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
        sessions.append(kw.get("prompt") or "")
        patient["t"] += 10.0
        return StartResult(True, "ok", session=f"s{len(sessions)}", attach="a")

    result = supervise(
        root,
        OWNER,
        engine="claude",
        prompt="OPEN",
        starter=starter,
        verdict=lambda r: _answer("idle"),
        resume_id_for=lambda r, m, since=0.0: "sess-1",
        note=[].append,
        poll=0,
        now=lambda: patient["t"],
        broker=broker,
    )
    assert result.reason.startswith("stopped by the operator"), result.reason
    assert asked and asked[0] < 2 * 3600, "it was not tried while the slot was full"
    assert any(t >= 2 * 3600 for t in asked), "it was never retried"
    assert not broker_mod.queued(root, OWNER), "it is still queued after starting"
    started_at = next(t for t in asked if t >= 2 * 3600)
    assert len(sessions) <= 2, f"{len(sessions)} Manager sessions for two notes"
    assert all("Queued" in p or "Started" in p for p in sessions), sessions
    assert started_at < 2 * 3600 + 2 * BOARD_RECHECK_SECONDS, (
        "the Worker waited for something other than its slot"
    )


def test_waking_again_and_again_without_a_session_is_not_a_spin(patient):
    """A night of a board that keeps changing while nothing can start: the
    verdict flips between `idle` and `closed`, each flip wakes the wait, and
    no session starts. Every pass waited, so the spin guard must not fire;
    only the operator ends this run. (Mutation-found: with waits not marked,
    the run stopped as "spinning" after three wakes.)"""
    flips = {"n": 0}

    def board():
        flips["n"] += 1
        return _answer("idle" if (flips["n"] // 2) % 2 else "closed")

    result, starts, _ = _perpetual_run(patient, board)
    assert result.reason.startswith("stopped by the operator"), result.reason
    assert starts == []
    assert flips["n"] > 3 * SPIN_PASSES_BEFORE_STOPPING
