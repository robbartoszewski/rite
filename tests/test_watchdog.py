from pathlib import Path

from rite_ai.reporting.heartbeat import write_heartbeat
from rite_ai.reporting.outbox import enqueue
from rite_ai.watchdog import run_watchdog_check


def _setup(tmp_path: Path, worker_names: list[str] | None = None) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    for name in worker_names or []:
        worker_dir = tmp_path / "workers" / name
        worker_dir.mkdir(parents=True, exist_ok=True)
        (worker_dir / "worker.yml").write_text(
            f'worker:\n  name: "{name}"\n  modules: []\n'
        )
    return tmp_path


class TestRunWatchdogCheck:
    def test_no_workers_no_outbox_needs_no_attention(self, tmp_path: Path):
        root = _setup(tmp_path)
        result = run_watchdog_check(root)
        assert result.needs_attention is False
        assert result.reasons == []

    def test_config_error_needs_attention(self, tmp_path: Path):
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        (rite_dir / "config.yaml").write_text("ticket_backend: [not, a, mapping]\n")
        result = run_watchdog_check(tmp_path)
        assert result.needs_attention is True
        assert any("config error" in r for r in result.reasons)

    def test_blocker_in_outbox_needs_attention(self, tmp_path: Path):
        root = _setup(tmp_path)
        enqueue(root, "blocker", {"detail": "missing staging credentials"})
        result = run_watchdog_check(root)
        assert result.needs_attention is True
        assert len(result.blockers) == 1
        assert any("missing staging credentials" in r for r in result.reasons)

    def test_handover_in_outbox_does_not_need_attention(self, tmp_path: Path):
        """`handover`/`handover-label` are routine — drained by `start`'s
        own flush, not something the watchdog should wake anyone for."""
        root = _setup(tmp_path)
        enqueue(root, "handover", {"ticket": "RW-1", "reason": "clean shutdown"})
        result = run_watchdog_check(root)
        assert result.needs_attention is False

    def test_fresh_heartbeat_does_not_need_attention(self, tmp_path: Path):
        root = _setup(tmp_path, worker_names=["alpha"])
        write_heartbeat(root, "alpha")
        result = run_watchdog_check(root)
        assert result.needs_attention is False
        assert result.stalled == []

    def test_stale_heartbeat_needs_attention(self, tmp_path: Path):
        root = _setup(tmp_path, worker_names=["alpha"])
        write_heartbeat(root, "alpha")
        # Force staleness without sleeping: rewrite with a timestamp far in
        # the past, well past the 10min x 3 = 1800s threshold.
        import json

        hb_path = root / ".rite" / "heartbeats" / "alpha.json"
        data = json.loads(hb_path.read_text())
        data["timestamp"] -= 3600
        hb_path.write_text(json.dumps(data))

        result = run_watchdog_check(root)
        assert result.needs_attention is True
        assert len(result.stalled) == 1
        assert result.stalled[0].worker == "alpha"
        assert any("alpha" in r for r in result.reasons)

    def test_worker_with_no_heartbeat_at_all_needs_attention(self, tmp_path: Path):
        root = _setup(tmp_path, worker_names=["alpha"])
        result = run_watchdog_check(root)
        assert result.needs_attention is True
        assert len(result.stalled) == 1

    def test_no_heartbeat_reason_says_so_not_a_huge_number(self, tmp_path: Path):
        """Previously read '...stalled — 1789035785s since last
        heartbeat' for any worker that had simply never started — the
        current epoch timestamp, misread as a duration. Reproduced by
        hand: create a worker, run `rite watchdog` before it ever runs."""
        root = _setup(tmp_path, worker_names=["alpha"])
        result = run_watchdog_check(root)
        assert any("no heartbeat ever recorded" in r for r in result.reasons)
        assert not any("789" in r for r in result.reasons)  # no epoch-sized number


class TestBlockedWorkerIsNotAStall:
    """A worker that is beating normally and has written an open blocker.

    Measured 2026-09-11 against a live sandboxed worker: it heartbeated on
    schedule, wrote `--blocker "QUESTION: ..."`, and `rite watchdog`
    answered "ok — nothing needs attention" with exit 0. The stall check
    sees only silence; nothing a Worker writes ever reaches the outbox.
    """

    def test_open_blocker_needs_attention_while_beating(self, tmp_path: Path):
        from rite_ai.handover import write_snapshot

        root = _setup(tmp_path, ["w1"])
        write_heartbeat(root, "w1", ticket="RT-9")
        write_snapshot(
            root, ticket="RT-9", blockers=["QUESTION: drop or keep?"], worker="w1"
        )

        result = run_watchdog_check(root)

        assert result.needs_attention
        assert [b.worker for b in result.blocked] == ["w1"]
        assert result.stalled == []

    def test_reason_says_blocked_not_stalled(self, tmp_path: Path):
        """The wording is load-bearing: reading 'blocked' as 'stalled'
        sends a Manager to force-release a session that is sitting there
        waiting for an answer."""
        from rite_ai.handover import write_snapshot

        root = _setup(tmp_path, ["w1"])
        write_heartbeat(root, "w1", ticket="RT-9")
        write_snapshot(
            root, ticket="RT-9", blockers=["QUESTION: drop or keep?"], worker="w1"
        )

        reason = run_watchdog_check(root).reasons[0]

        assert "BLOCKED and waiting" in reason
        assert "stalled" not in reason
        assert "QUESTION: drop or keep?" in reason
        assert "RT-9" in reason

    def test_blocker_clears_when_the_worker_writes_a_clean_snapshot(
        self, tmp_path: Path
    ):
        """No separate resolve step to forget: the snapshot is overwritten
        whole, so a blocker disappears the moment it is not rewritten."""
        from rite_ai.handover import write_snapshot

        root = _setup(tmp_path, ["w1"])
        write_heartbeat(root, "w1", ticket="RT-9")
        write_snapshot(
            root, ticket="RT-9", blockers=["QUESTION: drop or keep?"], worker="w1"
        )
        assert run_watchdog_check(root).needs_attention

        write_snapshot(root, ticket="RT-9", next_step="open PR", worker="w1")

        assert not run_watchdog_check(root).needs_attention

    def test_blank_blockers_are_not_blockers(self, tmp_path: Path):
        from rite_ai.handover import write_snapshot

        root = _setup(tmp_path, ["w1"])
        write_heartbeat(root, "w1")
        write_snapshot(root, blockers=["", "   "], worker="w1")

        assert not run_watchdog_check(root).needs_attention

    def test_blocked_and_stalled_are_reported_separately(self, tmp_path: Path):
        """A worker can be both — it asked, then died. Both lines appear,
        and the blocked line says the question may be moot."""
        import time

        from rite_ai.handover import write_snapshot

        root = _setup(tmp_path, ["w1"])
        write_heartbeat(root, "w1", ticket="RT-9")
        write_snapshot(
            root, ticket="RT-9", blockers=["QUESTION: drop or keep?"], worker="w1"
        )
        hb = root / ".rite" / "heartbeats" / "w1.json"
        import json

        data = json.loads(hb.read_text())
        data["timestamp"] = time.time() - 99999
        hb.write_text(json.dumps(data))

        result = run_watchdog_check(root)

        assert [s.worker for s in result.stalled] == ["w1"]
        assert [b.worker for b in result.blocked] == ["w1"]
        assert any("stalled" in r for r in result.reasons)
        assert any(
            "stopped beating, so the question may be moot" in r for r in result.reasons
        )


class TestWatchdogExitCodes:
    """0 nothing · 2 answerable question · 1 something may be wrong.

    The split exists because a Manager scripting on this has to tell an
    investigation from a reply.
    """

    def _invoke(self, root: Path) -> int:
        import os

        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        cwd = os.getcwd()
        os.chdir(root)
        try:
            return CliRunner().invoke(cli, ["watchdog"]).exit_code
        finally:
            os.chdir(cwd)

    def test_clean_project_exits_zero(self, tmp_path: Path):
        root = _setup(tmp_path, ["w1"])
        write_heartbeat(root, "w1")
        assert self._invoke(root) == 0

    def test_answerable_question_exits_two(self, tmp_path: Path):
        from rite_ai.handover import write_snapshot

        root = _setup(tmp_path, ["w1"])
        write_heartbeat(root, "w1", ticket="RT-9")
        write_snapshot(
            root, ticket="RT-9", blockers=["QUESTION: drop or keep?"], worker="w1"
        )
        assert self._invoke(root) == 2

    def test_a_stall_alongside_a_question_exits_one(self, tmp_path: Path):
        """1 wins: it is the one you cannot resolve by typing an answer."""
        import json
        import time

        from rite_ai.handover import write_snapshot

        root = _setup(tmp_path, ["w1"])
        write_heartbeat(root, "w1", ticket="RT-9")
        write_snapshot(
            root, ticket="RT-9", blockers=["QUESTION: drop or keep?"], worker="w1"
        )
        hb = root / ".rite" / "heartbeats" / "w1.json"
        data = json.loads(hb.read_text())
        data["timestamp"] = time.time() - 99999
        hb.write_text(json.dumps(data))
        assert self._invoke(root) == 1

    def test_a_plain_stall_still_exits_one(self, tmp_path: Path):
        """Backward compatibility: `rite watchdog || notify` is unchanged."""
        root = _setup(tmp_path, ["w1"])
        assert self._invoke(root) == 1
