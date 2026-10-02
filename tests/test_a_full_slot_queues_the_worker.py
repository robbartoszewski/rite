"""A Worker asked for when no slot is free is queued and started later, not dropped.

🔴 Before: a request the broker or `rite sandbox start` turned down for want of
a slot (the project cap, the schedule window's count, the machine bound) was
discarded, and the Manager was told "NOT started… do not wait for it". A
discarded request is a lost instruction (Robert, 2026-10-02). Now the refusal
is TYPED all the way up (`SandboxResult.full`, exit status `EXIT_NO_SLOT`,
`broker.NO_SLOT`), and the supervisor puts the request back, tells the Manager
once, and asks again when a slot frees. Every retry is decided afresh by the
broker, so a queued request is never trusted more than a new one. For bounded
runs as well as perpetual ones.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import rite_ai.managers.supervise as sup
from rite_ai.managers import broker as broker_mod
from rite_ai.managers.broker import (
    NO_SLOT,
    Request,
    queued,
    requests_dir,
    take_requests,
)
from rite_ai.managers.mailbox import INBOX, read
from rite_ai.sandbox import EXIT_NO_SLOT

OWNER = "lead"
RAW = json.dumps({"worker": "w1", "ticket": "RT-1"})


def _ask(root):
    where = requests_dir(root, OWNER)
    where.mkdir(parents=True, exist_ok=True)
    (where / "1.json").write_text(RAW)


def _told(root):
    return [m.text for m in read(root, OWNER, INBOX)]


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".rite").mkdir()
    sup._QUEUED_TOLD.clear()
    return tmp_path


class TestTheSupervisorQueuesAFullSlot:
    def test_a_full_slot_puts_the_request_back_and_says_so_once(self, root):
        _ask(root)
        said: list[str] = []
        full = lambda raw: (NO_SLOT, "the schedule allows 1 Worker(s) right now")  # noqa: E731
        sup._honour_worker_requests(root, OWNER, full, said.append)
        assert queued(root, OWNER), "the request was dropped"
        assert [raw for _, raw in take_requests(root, OWNER)] == [RAW]
        assert any("Queued: no Worker slot is free" in t for t in _told(root))

        _ask(root)
        sup._honour_worker_requests(root, OWNER, full, said.append)
        assert sum("Queued:" in t for t in _told(root)) == 1, "told twice"
        assert queued(root, OWNER)

    def test_when_the_slot_frees_it_starts_and_says_started(self, root):
        _ask(root)
        sup._honour_worker_requests(
            root, OWNER, lambda raw: (NO_SLOT, "full"), [].append
        )
        sup._honour_worker_requests(
            root, OWNER, lambda raw: (True, "started Worker 'w1'"), [].append
        )
        assert not queued(root, OWNER)
        assert any(t.startswith("Started:") or "Started:" in t for t in _told(root))

    def test_control_a_real_refusal_is_still_final(self, root):
        _ask(root)
        sup._honour_worker_requests(
            root, OWNER, lambda raw: (False, "'worker' is not declared"), [].append
        )
        assert not queued(root, OWNER)
        assert any("NOT started" in t for t in _told(root))


class TestTheSignalIsTypedAllTheWayUp:
    def test_honour_reads_the_no_slot_exit_status(self, root, monkeypatch):
        def fake(argv, **kw):
            return subprocess.CompletedProcess(
                argv, EXIT_NO_SLOT, "", "the schedule allows 1 Worker(s) right now"
            )

        monkeypatch.setattr(broker_mod.subprocess, "run", fake)
        ok, said = broker_mod.honour(root, Request(worker="w1", ticket="RT-1"))
        assert ok is NO_SLOT and "schedule allows" in said

    def test_control_any_other_failure_is_false(self, root, monkeypatch):
        monkeypatch.setattr(
            broker_mod.subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom"),
        )
        ok, _ = broker_mod.honour(root, Request(worker="w1", ticket="RT-1"))
        assert ok is False

    def test_the_brokers_own_capacity_check_is_no_slot(self, root, monkeypatch):
        (root / "workers" / "w1").mkdir(parents=True)
        (root / "workers" / "w1" / "worker.yml").write_text("name: w1\n")
        board = SimpleNamespace(list_tickets=lambda: [SimpleNamespace(id="RT-1")])
        handle = broker_mod.for_project(root, board, capacity=1)
        import rite_ai.sandbox as sb

        monkeypatch.setattr(sb, "count_active_sandboxes", lambda *a, **k: 1)
        ok, said = handle(RAW)
        assert ok is NO_SLOT and "already running" in said

    def test_a_project_at_its_cap_exits_with_the_no_slot_status(
        self, tmp_path, monkeypatch
    ):
        """The real `rite sandbox start` and the real `start_worker`, with
        yoloAI faked: this project is at its cap of one (another of its own
        Workers is running), so the start is refused as FULL and the exit
        status says so."""
        from unittest.mock import MagicMock, patch

        from click.testing import CliRunner

        from rite_ai.cli.main import cli
        from rite_ai.sandbox import sandbox_name
        from tests.refined_board import board_with
        from tests.test_a_started_ticket_moves_on_the_board import (
            SITE,
            TICKET,
            _Board,
            _project,
        )

        _project(tmp_path, monkeypatch)
        config = tmp_path / ".rite" / "config.yaml"
        config.write_text(config.read_text() + "  max_concurrent_workers: 1\n")
        beta = tmp_path / "workers" / "beta"
        beta.mkdir(parents=True)
        (beta / "worker.yml").write_text(
            "worker:\n  name: beta\n  manager: ''\n  modules: [app]\n"
        )
        running = json.dumps(
            {"sandboxes": [{"environment": {"name": sandbox_name("beta", tmp_path)}}]}
        )

        def run(args, *a, **kw):
            return MagicMock(
                returncode=0, stdout=running if "ls" in args else "", stderr=""
            )

        with (
            board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE),
            patch("keyring.get_password", return_value=None),
            patch("rite_ai.sandbox.shutil.which", return_value="/usr/bin/yoloai"),
            patch("rite_ai.sandbox.subprocess.run", side_effect=run),
            patch("rite_ai.cli.main._ticket_backend", return_value=(_Board(), None)),
            patch("rite_ai.cli.main._worker_cannot_deliver", return_value=None),
        ):
            result = CliRunner().invoke(
                cli,
                ["sandbox", "start", "alpha", "--allow-dirty", "--ticket", "KAN-28"],
            )
        assert result.exit_code == EXIT_NO_SLOT, result.output
        assert "exceeding sandbox.max_concurrent_workers" in result.output

    def test_control_a_refusal_that_is_not_capacity_exits_1(
        self, tmp_path, monkeypatch
    ):
        from unittest.mock import patch

        from click.testing import CliRunner

        import rite_ai.sandbox as sb
        from rite_ai.cli.main import cli
        from tests.refined_board import board_with
        from tests.test_a_started_ticket_moves_on_the_board import (
            SITE,
            TICKET,
            _Board,
            _project,
        )

        _project(tmp_path, monkeypatch)
        with (
            board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE),
            patch("keyring.get_password", return_value=None),
            patch("rite_ai.cli.main._ticket_backend", return_value=(_Board(), None)),
            patch("rite_ai.cli.main._worker_cannot_deliver", return_value=None),
            patch.object(
                sb, "start_worker", lambda *a, **k: sb.SandboxResult(False, "broken")
            ),
        ):
            result = CliRunner().invoke(
                cli,
                ["sandbox", "start", "alpha", "--allow-dirty", "--ticket", "KAN-28"],
            )
        assert result.exit_code == 1, result.output


class TestAWaitingRunWakesForAFreedSlot:
    def test_the_wake_fires_only_with_a_queued_request_and_a_free_slot(
        self, root, monkeypatch
    ):
        clock = {"t": 0.0}
        wake = sup._with_a_freed_slot(root, OWNER, lambda: "", lambda: clock["t"])
        monkeypatch.setattr(broker_mod, "slot_free", lambda r: True)
        clock["t"] += sup.BOARD_RECHECK_SECONDS
        assert wake() == "", "nothing is queued"
        _ask(root)
        clock["t"] += sup.BOARD_RECHECK_SECONDS
        assert wake() == "a Worker slot freed for a queued request"
        monkeypatch.setattr(broker_mod, "slot_free", lambda r: False)
        clock["t"] += sup.BOARD_RECHECK_SECONDS
        assert wake() == ""
