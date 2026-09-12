from pathlib import Path
from unittest.mock import patch

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.lifecycle import perform_handover, start, stop
from rite_ai.reporting.outbox import list_pending
from rite_ai.tickets.interface import BackendError


def _setup(tmp_path: Path, ticket_backend_type: str = "none") -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        f"ticket_backend:\n  type: {ticket_backend_type}\n  site: test.atlassian.net\n"
        "  projects: {workers: RW, board: SCRUM}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n"
        "  stall_threshold: 3\n"
    )
    return tmp_path


class TestStart:
    def test_starts_healthy_project(self, tmp_path: Path):
        root = _setup(tmp_path)
        result = start(root)
        assert result.ok
        assert "ready" in result.message
        assert any("acme" in a for a in result.actions)

    def test_fails_without_rite_dir(self, tmp_path: Path):
        result = start(tmp_path)
        assert not result.ok
        assert "rite init" in result.message

    def test_reports_pending_outbox(self, tmp_path: Path):
        root = _setup(tmp_path)
        from rite_ai.reporting.outbox import enqueue

        enqueue(root, "handover", {"worker": "alpha"})
        result = start(root)
        assert result.ok
        assert any("outbox" in a for a in result.actions)

    def test_reads_handover_snapshot_before_orientation(self, tmp_path: Path):
        """SPEC §9.10.1: a fresh session reconstructs where the previous
        one left off from the snapshot, before evaluating orientation."""
        root = _setup(tmp_path)
        from rite_ai.handover import write_snapshot

        write_snapshot(
            root, ticket="RW-9", next_step="add tests", blockers=["waiting on x"]
        )
        result = start(root)
        assert result.ok
        assert any("RW-9" in a for a in result.actions)
        assert any("waiting on x" in a for a in result.actions)

    def test_missing_snapshot_is_not_an_error(self, tmp_path: Path):
        root = _setup(tmp_path)
        result = start(root)
        assert result.ok
        assert not any("handover snapshot" in a for a in result.actions)


class TestStop:
    def test_stop_releases_claims(self, tmp_path: Path):
        root = _setup(tmp_path)
        claims_path = root / ".rite" / "claims.json"
        ledger = ClaimsLedger(claims_path)
        ledger.claim(["src/app.py"], "alpha", "T-1")
        result = stop(root, worker="alpha")
        assert result.ok
        assert result.released_claims == 1

    def test_stop_without_claims(self, tmp_path: Path):
        root = _setup(tmp_path)
        result = stop(root)
        assert result.ok
        assert result.released_claims == 0

    def test_stop_fails_without_rite_dir(self, tmp_path: Path):
        result = stop(tmp_path)
        assert not result.ok

    def test_stop_writes_an_outbox_entry_when_there_is_a_ticket(self, tmp_path: Path):
        root = _setup(tmp_path)
        ClaimsLedger(root / ".rite" / "claims.json").claim(
            ["src/app.py"], "alpha", "RW-3"
        )
        stop(root, worker="alpha", reason="done for today")
        msgs = list_pending(root)
        assert len(msgs) == 1
        assert msgs[0].kind == "handover"
        assert msgs[0].payload["reason"] == "done for today"

    def test_stop_with_no_ticket_queues_nothing(self, tmp_path: Path):
        """A handover with no ticket has nothing to deliver, and
        `_deliver_via_backend` DROPS one unread ("nothing to
        comment/label"). Queueing it wrote a file the next `rite start`
        deleted without acting on, and made `stop` report the board as
        not updated when there was never anything to update. The durable
        record of the handover is the snapshot, which is still
        written."""
        root = _setup(tmp_path)

        result = stop(root, reason="done for today")

        assert result.ok is True
        assert result.queued is False
        assert list_pending(root) == []
        # Asserted through the reader, not a literal path: snapshots are
        # keyed per worker now, and the fact that matters is that one was
        # recorded, not where it landed.
        from rite_ai.handover import read_snapshots

        assert read_snapshots(root)


class TestPerformHandover:
    def test_writes_a_final_handover_snapshot(self, tmp_path: Path):
        """§9.10.1: the snapshot and the transition record must never
        disagree at the moment of transition."""
        root = _setup(tmp_path)
        claims_path = root / ".rite" / "claims.json"
        ledger = ClaimsLedger(claims_path)
        ledger.claim(["src/app.py"], "alpha", "RW-3")

        perform_handover(root, worker="alpha", reason="stall detected")

        from rite_ai.handover import read_snapshot

        snapshot = read_snapshot(root)
        assert snapshot is not None
        assert snapshot.ticket == "RW-3"
        assert "stall detected" in snapshot.progress

    def test_releases_and_enqueues(self, tmp_path: Path):
        root = _setup(tmp_path)
        claims_path = root / ".rite" / "claims.json"
        ledger = ClaimsLedger(claims_path)
        ledger.claim(["src/app.py"], "alpha", "T-1")
        ledger.claim(["src/db.py"], "alpha", "T-1")

        result = perform_handover(root, worker="alpha", reason="stall")
        assert result.released_claims == 2
        assert result.outbox_path

        msgs = list_pending(root)
        assert len(msgs) == 1
        assert msgs[0].payload["released_claims"] == 2

    def test_convergence_stop_and_handover_same_state(self, tmp_path: Path):
        """Clean stop and dirty handover must produce identical board state."""
        (tmp_path / "clean").mkdir()
        (tmp_path / "dirty").mkdir()
        root_clean = _setup(tmp_path / "clean")
        root_dirty = _setup(tmp_path / "dirty")
        for root in [root_clean, root_dirty]:
            claims_path = root / ".rite" / "claims.json"
            ledger = ClaimsLedger(claims_path)
            ledger.claim(["src/app.py"], "alpha", "T-1")

        stop(root_clean, worker="alpha", reason="clean shutdown")
        perform_handover(root_dirty, worker="alpha", reason="stall detected")

        clean_claims = ClaimsLedger(root_clean / ".rite" / "claims.json").list_claims()
        dirty_claims = ClaimsLedger(root_dirty / ".rite" / "claims.json").list_claims()
        assert clean_claims == dirty_claims == []

        clean_msgs = list_pending(root_clean)
        dirty_msgs = list_pending(root_dirty)
        assert len(clean_msgs) == 1
        assert len(dirty_msgs) == 1
        assert clean_msgs[0].kind == dirty_msgs[0].kind == "handover"


class TestPerformHandoverReachesTicketBackend:
    """Stage 2 #1/#2: `perform_handover` must actually call the ticket
    backend when a ticket ID and a working backend are available, falling
    back to the local outbox only when the backend call fails or none is
    configured."""

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_delivers_via_backend_when_ticket_and_backend_present(
        self, mock_create, tmp_path: Path
    ):
        root = _setup(tmp_path, ticket_backend_type="jira")
        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = None

        result = perform_handover(root, worker="alpha", reason="stall", ticket="RW-1")

        assert result.ticket_commented is True
        assert result.outbox_path == ""
        assert list_pending(root) == []
        backend.comment.assert_called_once()
        assert backend.comment.call_args[0][0] == "RW-1"
        # `remove` carries the departing worker: returning a ticket to
        # the pool is add-`scheduled` AND drop-`<worker>`, not just the
        # first half. See the regression test below.
        backend.label.assert_called_once_with("RW-1", ["scheduled"], remove=["alpha"])

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_comment_success_with_label_failure_does_not_requeue_the_comment(
        self, mock_create, tmp_path: Path
    ):
        """A label failure after a successful comment must not leave the
        original `handover` message in the outbox — retrying THAT message
        would re-post the comment, which is worse than a missing label. The
        label failure is instead surfaced (`label_failed`) and its own
        retryable `handover-label` message is queued — not silently lost."""
        root = _setup(tmp_path, ticket_backend_type="jira")
        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = BackendError("label does not exist on project")

        result = perform_handover(root, worker="alpha", reason="stall", ticket="RW-1")

        assert result.ticket_commented is True
        assert result.outbox_path == ""
        assert result.label_failed is True
        backend.comment.assert_called_once()

        msgs = list_pending(root)
        assert len(msgs) == 1
        assert msgs[0].kind == "handover-label"
        assert msgs[0].payload == {
            "ticket": "RW-1",
            "labels": ["scheduled"],
            "remove": ["alpha"],
        }

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_falls_back_to_outbox_when_backend_call_fails(
        self, mock_create, tmp_path: Path
    ):
        root = _setup(tmp_path, ticket_backend_type="jira")
        backend = mock_create.return_value
        backend.comment.return_value = BackendError("JIRA unreachable")

        result = perform_handover(root, worker="alpha", reason="stall", ticket="RW-1")

        assert result.ticket_commented is False
        assert result.outbox_path
        msgs = list_pending(root)
        assert len(msgs) == 1
        assert msgs[0].payload["ticket"] == "RW-1"

    def test_no_ticket_and_no_claims_queues_nothing(self, tmp_path: Path):
        """Nothing to comment on means nothing to queue — see
        `TestStop.test_stop_with_no_ticket_queues_nothing`."""
        root = _setup(tmp_path, ticket_backend_type="jira")
        result = perform_handover(root, worker="alpha", reason="stall")
        assert result.ticket_commented is False
        assert result.outbox_path == ""
        assert list_pending(root) == []

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_resolves_ticket_from_released_claims_when_none_given(
        self, mock_create, tmp_path: Path
    ):
        """This is the real-usage path: `stop_cmd` has no way to know a
        ticket ID unless the caller passes one, and no production caller
        did — this is what made every prior `perform_handover` fix
        unreachable in practice. The ticket lives on the claims about to be
        released (`rite claim --ticket …`, per templates/commands/
        ticket.md), so resolve it from there instead of requiring it to be
        passed explicitly."""
        root = _setup(tmp_path, ticket_backend_type="jira")
        claims_path = root / ".rite" / "claims.json"
        ledger = ClaimsLedger(claims_path)
        ledger.claim(["src/app.py"], "alpha", "RW-9")

        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = None

        result = perform_handover(root, worker="alpha", reason="stall")

        assert result.ticket_commented is True
        backend.comment.assert_called_once()
        assert backend.comment.call_args[0][0] == "RW-9"

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_explicit_ticket_argument_wins_over_claims(
        self, mock_create, tmp_path: Path
    ):
        root = _setup(tmp_path, ticket_backend_type="jira")
        claims_path = root / ".rite" / "claims.json"
        ledger = ClaimsLedger(claims_path)
        ledger.claim(["src/app.py"], "alpha", "RW-9")

        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = None

        result = perform_handover(
            root, worker="alpha", reason="stall", ticket="RW-EXPLICIT"
        )

        assert result.ticket_commented is True
        assert backend.comment.call_args[0][0] == "RW-EXPLICIT"

    def test_falls_back_to_outbox_when_no_backend_configured(self, tmp_path: Path):
        root = _setup(tmp_path, ticket_backend_type="none")
        result = perform_handover(root, worker="alpha", reason="stall", ticket="RW-1")
        assert result.ticket_commented is False
        assert result.outbox_path
        assert len(list_pending(root)) == 1


class TestStartFlushesOutbox:
    """Stage 2 #2: `start()` must actually flush queued handovers through
    the ticket backend, not merely count them for display."""

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_flushes_pending_handover_on_start(self, mock_create, tmp_path: Path):
        root = _setup(tmp_path, ticket_backend_type="jira")
        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = None

        from rite_ai.reporting.outbox import enqueue

        enqueue(
            root, "handover", {"worker": "alpha", "reason": "stall", "ticket": "RW-1"}
        )

        result = start(root)
        assert result.ok
        assert any("flushed 1" in a for a in result.actions)
        assert list_pending(root) == []
        backend.comment.assert_called_once()

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_an_unreadable_pool_file_does_not_cost_the_outbox_flush(
        self, mock_create, tmp_path: Path
    ):
        """`probe` refuses a corrupt `pool.json` rather than reading it as
        empty — correct for `fill`, which would otherwise start a full set
        of sessions on top of the running ones. Propagated out of `start`
        it aborted the bring-up *before* the flush: the delivery §9.10
        promises an offline `stop`, and the one write nothing else in the
        package performs. Losing the pool line is the acceptable cost of an
        unreadable pool file; losing the handover is not."""
        root = _setup(tmp_path, ticket_backend_type="jira")
        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = None

        from rite_ai.reporting.outbox import enqueue

        enqueue(
            root, "handover", {"worker": "alpha", "reason": "stall", "ticket": "RW-1"}
        )
        (root / ".rite" / "pool.json").write_text('"not an object"')

        result = start(root)
        assert result.ok
        assert any("flushed 1" in a for a in result.actions)
        assert list_pending(root) == []
        backend.comment.assert_called_once()
        # and it says why the pool line is missing rather than omitting it
        assert any("state unreadable" in a for a in result.actions)

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_reports_flush_failure_without_losing_the_message(
        self, mock_create, tmp_path: Path
    ):
        root = _setup(tmp_path, ticket_backend_type="jira")
        backend = mock_create.return_value
        backend.comment.return_value = BackendError("JIRA unreachable")

        from rite_ai.reporting.outbox import enqueue

        enqueue(
            root, "handover", {"worker": "alpha", "reason": "stall", "ticket": "RW-1"}
        )

        result = start(root)
        assert result.ok
        assert any("flushed 0/1" in a for a in result.actions)
        assert len(list_pending(root)) == 1


class TestStartSetupPhase:
    """SPEC §9.10 / D-50: `start` performs what is idempotent, local and
    free, and REPORTS what is persistent, networked or quota-spending.

    The measurement that produced D-50 found none of §9.10's four setup
    steps built, and the spec had marked them "not built" without deciding
    whose defect that was. These tests pin the resolution — including the
    negative halves, which are the ones that matter: a `start` that
    silently registered a cron entry or filled the pool would satisfy every
    wording-only assertion here while being the exact behaviour the
    decision rejects.
    """

    def test_reports_scheduler_not_installed_and_names_the_command(
        self, tmp_path: Path
    ):
        root = _setup(tmp_path)
        result = start(root)
        line = next(a for a in result.actions if a.startswith("scheduler"))
        assert "NOT INSTALLED" in line
        assert "rite scheduler install" in line

    def test_start_does_not_register_the_scheduler(self, tmp_path: Path):
        """The negative half. Registering the tick writes a cron entry or a
        launchd plist — a standing change to the machine that outlives the
        process. `rite scheduler install` is how that happens, explicitly."""
        from rite_ai.scheduler import is_installed

        root = _setup(tmp_path)
        start(root)
        assert not is_installed(root)

    def test_start_does_not_fill_the_coordinator_pool(self, tmp_path: Path):
        """Every pooled slot runs `claude`. Spent quota is the one kind of
        damage in this design that no cleanup reverses (§5.1.1), so a
        bring-up must never spawn sessions as a side effect (§2.5.1)."""
        root = _setup(tmp_path)
        start(root)
        assert not (root / ".rite" / "pool.json").exists()

    def test_start_reports_the_coordinator_pool_shortfall(self, tmp_path: Path):
        """The positive half, and the one that was missing. D-50 removed the
        top-up; P1.16 promises the *report* in its place. Without it `start`
        says nothing about the pool at all, and a Manager reading a silent
        bring-up concludes it has warm capacity it does not have.

        The wording is asserted to be `pool status`'s own — a second
        vocabulary for one shortfall is how two commands come to disagree
        about it."""
        root = _setup(tmp_path)
        result = start(root)
        assert any("coordinator pool at 0/2" in a for a in result.actions)
        assert any("rite pool fill" in a for a in result.actions)

    def test_start_reporting_the_pool_does_not_create_pool_state(self, tmp_path: Path):
        """The two halves have to hold together: reporting depth must not
        become a way of filling — or even of writing state — by accident.
        `probe` persists `pool.json` only where slots or the file already
        exist, which is what lets `start` read the pool under §9.10's rule
        that it must never spend quota or leave persistent state."""
        root = _setup(tmp_path)
        start(root)
        start(root)
        assert not (root / ".rite" / "pool.json").exists()

    def test_start_does_not_fetch_the_kb_cache(self, tmp_path: Path):
        """§8.7 puts `rite kb refresh` in the user's hands; §9.10 used to
        have `start` do it silently. `kb refresh` makes outbound requests,
        so a `start` run offline would hang or fail on it. §9.10 defers."""
        root = _setup(tmp_path)
        (root / ".rite" / "kb").mkdir()
        result = start(root)
        assert any("rite kb refresh" in a for a in result.actions)
        assert not (root / ".rite" / "kb" / ".cache").exists()

    def test_start_validates_the_schedule_it_runs_under(self, tmp_path: Path):
        """The health check `start` genuinely owes (D-50). A window over
        `sandbox.max_concurrent_workers` is refused by `rite schedule set`,
        but nothing stops it being hand-edited into config.yaml — which is
        exactly the case §9.8 says the check exists to re-catch."""
        root = _setup(tmp_path)
        config = root / ".rite" / "config.yaml"
        config.write_text(
            config.read_text() + "sandbox:\n  max_concurrent_workers: 5\n"
            "schedule:\n  timezone: Europe/Warsaw\n  windows:\n"
            "    - hours: '09:00-18:00'\n      workers: 99\n"
        )
        result = start(root)
        assert any(a.startswith("schedule:") and "99" in a for a in result.actions)
        assert any("rite doctor" in a for a in result.actions)

    def test_clean_schedule_produces_no_schedule_noise(self, tmp_path: Path):
        """The other direction, because a check that always fires is one
        nobody reads — the defect already found in the registry warning."""
        root = _setup(tmp_path)
        config = root / ".rite" / "config.yaml"
        config.write_text(
            config.read_text() + "sandbox:\n  max_concurrent_workers: 5\n"
            "schedule:\n  timezone: Europe/Warsaw\n  windows:\n"
            "    - hours: '09:00-18:00'\n      workers: 3\n"
            "    - hours: '18:00-09:00'\n      workers: 0\n"
        )
        result = start(root)
        assert not [a for a in result.actions if a.startswith("schedule:")]

    def test_start_is_idempotent_in_the_sense_the_spec_now_claims(self, tmp_path: Path):
        """Not "a no-op" — the old wording, which was false on any first
        call with a queued handover. Calling `start` twice does not do the
        work twice: the same actions, no accumulating state."""
        root = _setup(tmp_path)
        first = start(root)
        second = start(root)
        assert first.actions == second.actions
        assert first.ok and second.ok
