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
