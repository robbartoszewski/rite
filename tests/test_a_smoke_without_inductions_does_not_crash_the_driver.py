"""A scenario that induces no failure must not crash the driver.

🔴 Measured 2026-10-08. The happy-path smokes set `kill_worker: ""`, and
`maybe_kill` marks `kill_done` for them so nothing downstream waits for a kill
that will never happen. That let `maybe_restart_with_stale_state` through to
`kills[0]` on an empty inductions list:

    IndexError: list index out of range   (driver.py:180)

It killed a mixed smoke run AFTER the Claude Manager had started the GPU Worker
and the pipeline had reached `defined` — the loop was working and the harness
fell over on top of it, which is the worst possible place for an instrument to
have a bug.
"""

from __future__ import annotations

from tools.e2e_v071 import driver
from tools.e2e_v071.config import load


class _Obs:
    def events(self):
        return []


def _state():
    return driver.DriverState()


def test_no_kill_worker_means_no_kill_and_no_crash(tmp_path):
    from tools.e2e_v071.runlog import RunDir

    run = RunDir(tmp_path / "run")
    run.path.mkdir(parents=True, exist_ok=True)
    fleet = load("smoke_mixed")
    assert fleet.kill_worker == "", "this scenario is supposed to induce nothing"

    st = _state()
    driver.maybe_kill(run, _Obs(), fleet, {"gpu-slug": "1"}, st, {})
    assert st.kill_done, "nothing downstream should wait for a kill"

    # 🔴 The crash: this used to raise IndexError on an empty inductions list.
    driver.maybe_restart_with_stale_state(
        run, _Obs(), fleet, st, {}, stop_owner=lambda: None, start_owner=lambda: None
    )
    assert st.restart_done, "a scenario with no kill has nothing to restart from"


def test_a_scenario_that_does_induce_one_still_waits_for_it(tmp_path):
    """The CONTROL. The guard must not mark the full gate's restart done before
    its kill has happened — that would skip the induction it exists to make."""
    from tools.e2e_v071.runlog import RunDir

    run = RunDir(tmp_path / "run")
    run.path.mkdir(parents=True, exist_ok=True)
    fleet = load("mixed")
    assert fleet.kill_worker, "the full gate does induce a kill"

    st = _state()
    st.kill_done = True  # as if the kill had been made
    driver.maybe_restart_with_stale_state(
        run, _Obs(), fleet, st, {}, stop_owner=lambda: None, start_owner=lambda: None
    )
    assert not st.restart_done, (
        "the restart was marked done with no kill recorded — the full gate's "
        "stale-claim induction would never run"
    )


# ---- the approver is not started before it has anything to review ----------


class _Plan:
    """An Observer stand-in: only `decomposition` is consulted."""

    def __init__(self, plan):
        self.plan = plan

    def decomposition(self, _ticket):
        if isinstance(self.plan, Exception):
            raise self.plan
        return self.plan


def test_no_plan_means_the_approver_is_not_started():
    """🔴 Two decompose turns (30 and 45 minutes) died because the approver was
    running GPU cycles while there was no plan to approve: its model and the
    author's do not both fit, so each cycle evicted whichever was mid-generation.
    """
    fleet = load("smoke_local")
    assert not driver.a_plan_awaits_review(_Plan(None), fleet, {"gpu-slug": "1"})


def test_a_written_plan_starts_it():
    """The control: deferring must not become never."""
    fleet = load("smoke_local")
    assert driver.a_plan_awaits_review(
        _Plan({"approval": "pending"}), fleet, {"gpu-slug": "1"}
    )


def test_an_approved_plan_needs_no_further_review():
    fleet = load("smoke_local")
    assert not driver.a_plan_awaits_review(
        _Plan({"approval": "approved"}), fleet, {"gpu-slug": "1"}
    )


def test_an_unreadable_plan_waits_rather_than_spending_a_turn():
    """Starting early costs a GPU turn and an eviction; the next poll asks
    again, so "cannot tell" declines."""
    fleet = load("smoke_local")
    assert not driver.a_plan_awaits_review(
        _Plan(RuntimeError("rite read failed")), fleet, {"gpu-slug": "1"}
    )
