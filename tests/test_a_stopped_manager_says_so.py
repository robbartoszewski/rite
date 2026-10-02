"""SCRUM-20: a Manager's run records that it ran, why it ended, and what it left.

🔴 **What this cost, measured 2026-10-02.** Manager `lead` stopped while Worker
beta sat on an unanswered question with a claim held and uncommitted work in a
live sandbox. Three things were true at once and all three were silent:

- `rite status` said "recorded but not running — the recorded process is gone"
  about a Manager that WAS running (pid 7309, tmux session seconds old). It was
  reading the INSTANCE record, whose pid is dead between cycles by design;
- nothing recorded why the previous run had ended, or how many sessions it had
  started, so "ceiling reached" and "crashed having started nothing" read the
  same;
- nothing said beta had been left alone. `rite status` offered "no handover
  snapshot recorded yet" and the person learned it an hour later.

⚠ **The root cause was narrower than it looked.** `record_supervisor` and
`forget_supervisor` already existed, with a four-state model and an `ended`
reason — but `supervise()` only called them when `waiting is not None`, which
`_waiting_for` returns only for a root several Managers share. A LONE Manager —
the commonest project there is — recorded nothing at all.
"""

from __future__ import annotations

import os
from pathlib import Path

from rite_ai.managers import supervise as sup
from rite_ai.managers.routing import (
    DIED,
    ENDED,
    NEVER,
    RUNNING,
    forget_supervisor,
    record_supervisor,
    supervisor_record,
    supervisor_state,
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestTheLifecycleIsRecordedForEveryRun:
    """Not only for a Manager that may wait."""

    def test_a_run_records_its_start(self, tmp_path):
        root = _project(tmp_path)
        assert supervisor_state(root, "lead") == NEVER

        record_supervisor(root, "lead", os.getpid())

        assert supervisor_state(root, "lead") == RUNNING

    def test_and_its_end_with_the_reason(self, tmp_path):
        root = _project(tmp_path)
        record_supervisor(root, "lead", os.getpid())

        forget_supervisor(root, "lead", os.getpid(), "ceiling reached: 20 session(s)")

        assert supervisor_state(root, "lead") == ENDED
        assert "ceiling reached" in supervisor_record(root, "lead")["ended"]

    def test_the_counts_are_recorded_beside_the_reason(self, tmp_path):
        """ "Ended on the ceiling" and "ended having started nothing" were the
        same sentence."""
        root = _project(tmp_path)
        record_supervisor(root, "lead", os.getpid())

        forget_supervisor(
            root,
            "lead",
            os.getpid(),
            "window closed",
            counts={
                "sessions_started": 7,
                "sessions_that_did_work": 5,
                "sessions_that_changed_nothing": 2,
            },
        )

        rec = supervisor_record(root, "lead")
        assert rec["sessions_started"] == 7
        assert rec["sessions_that_did_work"] == 5
        assert rec["sessions_that_changed_nothing"] == 2

    def test_a_killed_run_reads_as_died_not_ended(self, tmp_path):
        """⚠ The distinction that matters: a run that recorded its own end is
        not the same as one that was killed past its `finally`."""
        root = _project(tmp_path)
        record_supervisor(root, "lead", 999_999)  # a pid that is not alive

        assert supervisor_state(root, "lead") == DIED

    def test_supervise_records_a_lone_managers_run(self, tmp_path, monkeypatch):
        """🔴 The actual defect. `waiting=None` is a LONE Manager, and the
        record used to be skipped entirely for it."""
        root = _project(tmp_path)
        seen = {}

        def fake(r, m, **kw):
            seen["state_during"] = supervisor_state(r, m)
            return sup.SuperviseResult(True, "window closed", [])

        monkeypatch.setattr(sup, "_supervise", fake)
        sup.supervise(root, "lead", waiting=None)

        assert seen["state_during"] == RUNNING, "recorded while it ran"
        assert supervisor_state(root, "lead") == ENDED
        assert supervisor_record(root, "lead")["ended"] == "window closed"


class TestWhatItLeftBehind:
    """A Manager's Workers OUTLIVE it: their sandboxes are their own
    processes."""

    def _with_a_claim(self, tmp_path):
        from rite_ai.claims.ledger import ClaimsLedger

        root = _project(tmp_path)
        (root / "workers" / "beta").mkdir(parents=True)
        (root / "workers" / "beta" / "worker.yml").write_text(
            "worker:\n  name: beta\n  modules: []\n"
        )
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["mod/a.py"], "beta", ticket="KAN-29")
        return root

    def test_a_worker_holding_a_claim_is_reported(self, tmp_path):
        root = self._with_a_claim(tmp_path)

        left = sup._workers_left_mid_flight(root)

        assert [w["worker"] for w in left] == ["beta"]
        assert left[0]["ticket"] == "KAN-29"

    def test_the_claim_is_NOT_released(self, tmp_path):
        """🔴 The property that makes this safe. `perform_handover` releases
        claims, which is right for a stopped Worker — but a running Worker's
        claim is what stops a second Worker editing the same files."""
        from rite_ai.claims.ledger import ClaimsLedger

        root = self._with_a_claim(tmp_path)
        sup._hand_over_on_stop(root, "lead", "window closed", None)

        held = ClaimsLedger(root / ".rite" / "claims.json").claims_for("beta")
        assert [c.ticket for c in held] == ["KAN-29"], "still held"
        assert held[0].paths == ["mod/a.py"]

    def test_a_project_with_nothing_held_reports_nothing(self, tmp_path):
        """⚠ The control: a report that always fires is not a report."""
        root = _project(tmp_path)

        assert sup._workers_left_mid_flight(root) == []
        assert sup._hand_over_on_stop(root, "lead", "window closed", None) == []

    def test_the_person_is_told_which_workers_were_left(self, tmp_path, monkeypatch):
        root = self._with_a_claim(tmp_path)
        said = []
        monkeypatch.setattr(
            "rite_ai.managers.asking.raise_to_person",
            lambda r, m, **kw: said.append(kw.get("text", "")),
        )

        sup._hand_over_on_stop(root, "lead", "ceiling reached", None)

        assert said, "the person is told"
        assert "beta" in said[0] and "KAN-29" in said[0]
        assert "nothing was released" in said[0]

    def test_a_report_can_never_end_the_run(self, tmp_path, monkeypatch):
        """🔴 It is called from the run's `finally`. The first draft raised
        ImportError from a mistyped class and took the run with it."""
        root = self._with_a_claim(tmp_path)
        monkeypatch.setattr(
            sup,
            "_workers_left_mid_flight",
            lambda r: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        notes = []

        assert sup._hand_over_on_stop(root, "lead", "x", notes.append) == []
        assert any("could not work out" in n for n in notes)

    def test_what_was_left_reaches_the_lifecycle_record(self, tmp_path, monkeypatch):
        """A reader that sees ENDED must be able to see what was left."""
        root = self._with_a_claim(tmp_path)
        monkeypatch.setattr(
            "rite_ai.managers.asking.raise_to_person", lambda *a, **k: None
        )
        monkeypatch.setattr(
            sup,
            "_supervise",
            lambda r, m, **kw: sup.SuperviseResult(True, "ceiling reached", []),
        )

        sup.supervise(root, "lead", waiting=None)

        rec = supervisor_record(root, "lead")
        assert [w["worker"] for w in rec["left_unattended"]] == ["beta"]


class TestTheCountsSurviveTheRealPath:
    """⚠ Added because a mutation survived: the counts were pinned only where
    a test wrote them itself, so `supervise` could stop recording them and
    every test still passed."""

    def test_supervise_records_what_the_run_did(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        cycles = [
            sup.Cycle(number=1, session="s1"),
            sup.Cycle(number=2, session="s2", idle=True),
        ]
        monkeypatch.setattr(
            sup,
            "_supervise",
            lambda r, m, **kw: sup.SuperviseResult(True, "window closed", cycles),
        )

        sup.supervise(root, "lead", waiting=None)

        rec = supervisor_record(root, "lead")
        assert rec["sessions_started"] == 2
        assert rec["sessions_that_did_work"] == 1
        assert rec["sessions_that_changed_nothing"] == 1


class TestStatusReportsItTruthfully:
    """⚠ Also added because a mutation survived: `status.py` could go back to
    ignoring the supervisor record entirely with every test green. This is the
    half the person actually reads."""

    def _recorded_instance(self, tmp_path, monkeypatch, pid):
        from rite_ai.managers import ManagerInstance

        root = _project(tmp_path)
        monkeypatch.setattr(
            "rite_ai.managers.running_instances",
            lambda r: [ManagerInstance(name="lead", pid=pid, session="rite-mgr-lead")],
        )
        return root

    def test_a_manager_between_cycles_is_reported_RUNNING(self, tmp_path, monkeypatch):
        """🔴 The measured defect: pid 7309 alive, tmux session seconds old,
        and `rite status` said the recorded process was gone."""
        from rite_ai.reporting.status import _manager_lines

        # The pane's pid is dead (999999), which is the normal between-cycles
        # state; the supervisor's is this process, which is alive.
        root = self._recorded_instance(tmp_path, monkeypatch, 999_999)
        record_supervisor(root, "lead", os.getpid())

        out = "\n".join(_manager_lines(root))

        assert "running" in out
        assert "recorded process is gone" not in out

    def test_an_ended_run_is_reported_with_its_reason_and_counts(
        self, tmp_path, monkeypatch
    ):
        from rite_ai.reporting.status import _manager_lines

        root = self._recorded_instance(tmp_path, monkeypatch, 999_999)
        record_supervisor(root, "lead", os.getpid())
        forget_supervisor(
            root,
            "lead",
            os.getpid(),
            "ceiling reached: 20 session(s)",
            counts={
                "sessions_started": 20,
                "sessions_that_did_work": 18,
                "sessions_that_changed_nothing": 2,
            },
        )

        out = "\n".join(_manager_lines(root))

        assert "finished" in out
        assert "ceiling reached" in out
        assert "18 session(s) did work" in out

    def test_a_killed_run_is_not_called_finished(self, tmp_path, monkeypatch):
        from rite_ai.reporting.status import _manager_lines

        root = self._recorded_instance(tmp_path, monkeypatch, 999_999)
        record_supervisor(root, "lead", 999_998)  # recorded, never ended, gone

        out = "\n".join(_manager_lines(root))

        assert "WITHOUT recording an end" in out

    def test_unattended_workers_are_named_in_status(self, tmp_path, monkeypatch):
        from rite_ai.reporting.status import _manager_lines

        root = self._recorded_instance(tmp_path, monkeypatch, 999_999)
        record_supervisor(root, "lead", os.getpid())
        forget_supervisor(
            root,
            "lead",
            os.getpid(),
            "window closed",
            counts={"left_unattended": [{"worker": "beta", "ticket": "KAN-29"}]},
        )

        out = "\n".join(_manager_lines(root))

        assert "left 1 Worker(s) holding work: beta" in out
