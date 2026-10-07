"""A Manager recovers a stalled Worker itself: restart in place when the work
is intact, re-stage when the sandbox is gone, back off and report when it keeps
dying (SCRUM-38).

The planner is a pure function over hand-built `StallReport`s; the applier's
side effects are injected. No watchdog, yoloai or claims ledger runs here — the
assertions are about what rite DECIDES and DOES, keyed on the stall signal.
"""

from __future__ import annotations

import subprocess

from rite_ai.config.models import SandboxConfig
from rite_ai.managers import recovery as R
from rite_ai.reporting.heartbeat import StallReport


def _rep(worker, *, silent=600.0, known=True, ticket="RT-1"):
    return StallReport(
        worker=worker, last_seen=0.0, seconds_silent=silent, ticket=ticket, known=known
    )


# --- the planner -----------------------------------------------------------


def test_a_present_sandbox_is_restarted_in_place():
    out = R._plan_recovery(
        [_rep("w1")], classify=lambda r: "present", ledger={}, now=1000.0
    )
    assert [(a.worker, a.kind) for a in out] == [("w1", R.RESTART)]


def test_a_gone_sandbox_is_restaged():
    out = R._plan_recovery(
        [_rep("w1")], classify=lambda r: "gone", ledger={}, now=1000.0
    )
    assert [(a.worker, a.kind) for a in out] == [("w1", R.RESTAGE)]


def test_an_unknown_sandbox_is_left_alone():
    # yoloai could not answer — acting on "I cannot tell" is how a live Worker
    # gets reaped.
    out = R._plan_recovery(
        [_rep("w1")], classify=lambda r: "unknown", ledger={}, now=1000.0
    )
    assert out == []


def test_an_unreadable_heartbeat_is_left_alone():
    out = R._plan_recovery(
        [_rep("w1", known=False)], classify=lambda r: "present", ledger={}, now=1000.0
    )
    assert out == []


def test_recovery_backs_off_between_attempts():
    ledger = {"w1": R.LedgerEntry(attempts=1, first_at=0.0, last_at=1000.0)}
    # within the backoff window → wait, do nothing
    assert (
        R._plan_recovery(
            [_rep("w1")], classify=lambda r: "present", ledger=ledger, now=1010.0
        )
        == []
    )
    # past it → act again
    out = R._plan_recovery(
        [_rep("w1")], classify=lambda r: "present", ledger=ledger, now=1000.0 + 400
    )
    assert [a.kind for a in out] == [R.RESTART]


def test_an_exhausted_budget_reports_rather_than_loops():
    ledger = {"w1": R.LedgerEntry(attempts=R.DEFAULT_MAX_RESTARTS, last_at=1.0)}
    out = R._plan_recovery(
        [_rep("w1")], classify=lambda r: "present", ledger=ledger, now=1e9
    )
    assert [a.kind for a in out] == [R.REPORT]


def test_at_most_one_action_per_cycle_most_silent_first():
    out = R._plan_recovery(
        [_rep("w1", silent=100), _rep("w2", silent=900)],
        classify=lambda r: "present",
        ledger={},
        now=1000.0,
    )
    assert [a.worker for a in out] == ["w2"]  # the longest-silent, and only one


def test_backoff_grows_then_caps():
    assert R._backoff_seconds(0) == 0.0
    assert R._backoff_seconds(1) == R.DEFAULT_BASE_BACKOFF
    assert R._backoff_seconds(2) == 2 * R.DEFAULT_BASE_BACKOFF
    assert R._backoff_seconds(99) == R.DEFAULT_BACKOFF_CAP


# --- the ledger ------------------------------------------------------------


def test_the_ledger_round_trips(tmp_path):
    root = tmp_path
    (root / ".rite").mkdir()
    R._write_ledger(root, {"w1": R.LedgerEntry(attempts=2, first_at=1.0, last_at=3.0)})
    back = R._read_ledger(root)
    assert back == {"w1": R.LedgerEntry(attempts=2, first_at=1.0, last_at=3.0)}


def test_a_corrupt_ledger_reads_as_empty(tmp_path):
    root = tmp_path
    (root / ".rite").mkdir()
    # Where the ledger lives since SCRUM-59: outside every Manager's grant.
    R._ledger_path(root).parent.mkdir(parents=True, exist_ok=True)
    R._ledger_path(root).write_text("{not json")
    assert R._read_ledger(root) == {}


# --- the applier -----------------------------------------------------------


def _applier(tmp_path):
    root = tmp_path
    (root / ".rite").mkdir()
    said: list[str] = []
    return root, said, said.append


def test_the_applier_restarts_and_records_an_attempt(tmp_path):
    root, said, say = _applier(tmp_path)
    restarted: list[str] = []
    out = R.recover_stalled_workers(
        root,
        "lead",
        say,
        now=5000.0,
        stalls_of=lambda: [_rep("w1")],
        classify=lambda r: "present",
        do_restart=lambda r: restarted.append(r.worker) or (True, "restarted"),
        do_restage=lambda r: (False, "should not be called"),
    )
    assert [a.kind for a in out] == [R.RESTART]
    assert restarted == ["w1"]
    assert R._read_ledger(root)["w1"].attempts == 1
    assert any("restarted in place" in m for m in said)


def test_the_applier_restages_a_gone_sandbox(tmp_path):
    root, said, say = _applier(tmp_path)
    restaged: list[str] = []
    out = R.recover_stalled_workers(
        root,
        "lead",
        say,
        now=5000.0,
        stalls_of=lambda: [_rep("w1", ticket="RT-9")],
        classify=lambda r: "gone",
        do_restart=lambda r: (False, "should not be called"),
        do_restage=lambda r: restaged.append(r.worker) or (True, "released 2 path(s)"),
    )
    assert [a.kind for a in out] == [R.RESTAGE]
    assert restaged == ["w1"]
    assert any("re-staged" in m and "RT-9" in m for m in said)


def test_a_worker_that_recovered_has_its_ledger_cleared(tmp_path):
    root, said, say = _applier(tmp_path)
    R._write_ledger(root, {"w1": R.LedgerEntry(attempts=2, last_at=1.0)})
    # w1 is no longer stalled this cycle → its backoff is forgotten.
    R.recover_stalled_workers(
        root,
        "lead",
        say,
        now=9999.0,
        stalls_of=lambda: [],
        classify=lambda r: "present",
    )
    assert R._read_ledger(root) == {}


def test_recovery_never_raises_into_the_cycle(tmp_path):
    root, said, say = _applier(tmp_path)

    def boom():
        raise RuntimeError("watchdog exploded")

    out = R.recover_stalled_workers(root, "lead", say, now=1.0, stalls_of=boom)
    assert out == []
    assert any("recovery skipped" in m for m in said)


# --- restart_worker drives `yoloai restart --resume` -----------------------


def test_restart_worker_relaunches_in_place(tmp_path, monkeypatch):
    import rite_ai.sandbox as sb

    (tmp_path / ".rite").mkdir()
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(sb, "_yoloai_binary", lambda: "yoloai")
    monkeypatch.setattr(sb.subprocess, "run", fake_run)
    result = sb.restart_worker(
        "alpha", tmp_path, SandboxConfig(backend="seatbelt"), env={"FOO": "bar"}
    )
    assert result.ok, result.message
    args = seen["args"]
    assert args[0] == "yoloai"
    assert "restart" in args
    # restart in place, continuing the original prompt, re-supplying env
    assert "--resume" in args
    assert "--env" in args
    assert any(a == "FOO=bar" for a in args)
    # the sandbox name comes right after `restart`, and is NOT a fresh `new`
    assert "new" not in args
    assert args[args.index("restart") + 1] == sb.existing_sandbox_name(
        "alpha", tmp_path
    )


def test_restart_worker_refuses_a_github_token(tmp_path, monkeypatch):
    import rite_ai.sandbox as sb

    (tmp_path / ".rite").mkdir()
    monkeypatch.setattr(sb, "_yoloai_binary", lambda: "yoloai")
    # any GITHUB token key is refused before launch
    key = next(iter(sb._GITHUB_TOKEN_ENV))
    result = sb.restart_worker(
        "alpha", tmp_path, SandboxConfig(backend="seatbelt"), env={key: "x"}
    )
    assert not result.ok
    assert "GitHub token" in result.message


# --- the supervise loop calls recovery at the cycle boundary ---------------


def test_the_supervise_loop_runs_recovery_each_cycle(tmp_path, monkeypatch):
    import rite_ai.managers.supervise as supervise_mod
    from rite_ai.managers.session import FINISHED, Ending, Liveness, StartResult
    from rite_ai.managers.supervise import supervise

    (tmp_path / ".rite").mkdir()
    started: list[str] = []

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
        agent="",
    ):
        started.append(resume_id)
        return StartResult(True, "ok", session=f"fake-{len(started)}", attach="")

    monkeypatch.setattr(
        supervise_mod, "liveness", lambda _n: Liveness(False, known=True)
    )
    monkeypatch.setattr(supervise_mod, "was_attached", lambda _n: False)
    monkeypatch.setattr(
        supervise_mod,
        "ending",
        lambda _n, human_was_present, pane="": Ending(FINISHED),
    )

    calls: list[str] = []
    supervise(
        tmp_path,
        "lead",
        engine="claude",
        max_sessions=1,
        window_seconds=0,
        starter=starter,
        resume_id_for=lambda _r, _m, _s=0.0: "sid",
        poll=0,
        recover=lambda say: calls.append("recovered"),
    )
    assert calls == ["recovered"], "recovery did not run at the cycle boundary"
