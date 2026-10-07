"""The supervisor records the failures that matter, itself (SCRUM-71).

**What was wrong.** The `--record-issues` journal held 6 entries after the
a9 dogfood, all from a7/a8, and nothing from either a9 start. Across the
whole cycle it never captured the failures that actually mattered — the
replay of a finished ticket, the Owner-answer relay race, the misrouted
Owner replies, the restart of a finished Worker. Every one was found from
OUTSIDE the run.

For a perpetual unattended Manager the journal is the Owner's primary
feedback channel. **A journal that silently omits the biggest failures is
worse than no journal**, because it is read as a clean bill of health.

**Why the model cannot be asked to do it.** Every one of these happens
OUTSIDE the Manager's boundary — a Worker restarted on the host, a delivery
refused there, its own message never reaching the person. The Manager sees
only the result, so the supervisor records them from facts it holds.

**What is pinned here:**

* each induced failure produces EXACTLY ONE entry, which is the ticket's
  own acceptance criterion and the hard part: every one of these is
  reported from a loop that runs each cycle for as long as the condition
  lasts, so without a ledger one stall buries the other three failures
  under four hundred copies of itself;
* a normal cycle produces NONE;
* the ledger survives a restart, because a restarted Manager re-reads the
  same conditions;
* off is off (§9.15.1): no flag, no directory, no writes;
* the vocabulary is CLOSED — an unknown class is refused, not written;
* a journal that cannot be written never ends a run;
* nothing is `inferred`. §9.15.4: rite records what it saw, and does not
  grade its own run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rite_ai.managers import recording
from rite_ai.managers.journal import journal_dir


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    return tmp_path


def _entries(root: Path, manager: str = "lead") -> list[str]:
    where = journal_dir(root, manager)
    if not where.is_dir():
        return []
    return [p.read_text() for p in sorted(where.glob("*.md"))]


def _recorder(root: Path, *, enabled: bool = True, said=None):
    return recording.recorder_for(
        root, "lead", enabled=enabled, say=None if said is None else said.append
    )


def _an_event(kind: str = recording.RELAY_FAILED, subject: str = "KAN-7"):
    return recording.Event(
        kind,
        subject,
        "the send was refused: no channel is configured",
        "the message reaches the person it is for",
        anchor=f"ticket {subject}",
    )


class TestExactlyOnePerFailure:
    """⚠ The acceptance criterion, and the reason the ledger exists."""

    def test_the_first_time_writes_an_entry(self, tmp_path):
        root = _project(tmp_path)

        assert _recorder(root)(_an_event())
        assert len(_entries(root)) == 1

    def test_the_same_failure_every_cycle_writes_one(self, tmp_path):
        """A Worker that stays stalled is reported each cycle for as long as
        it stays stalled. Four hundred copies of one failure hide the other
        three, which is this ticket's defect in a different shape."""
        root = _project(tmp_path)
        record = _recorder(root)

        for _ in range(50):
            record(_an_event())

        assert len(_entries(root)) == 1

    def test_the_same_class_on_another_subject_is_another_failure(self, tmp_path):
        root = _project(tmp_path)
        record = _recorder(root)

        record(_an_event(subject="KAN-7"))
        record(_an_event(subject="KAN-8"))

        assert len(_entries(root)) == 2

    def test_a_different_reason_is_another_failure(self, tmp_path):
        root = _project(tmp_path)
        record = _recorder(root)

        record(_an_event())
        record(
            recording.Event(
                recording.RELAY_FAILED,
                "KAN-7",
                "the send was refused: Slack returned channel_not_found",
                "the message reaches the person it is for",
                anchor="ticket KAN-7",
            )
        )

        assert len(_entries(root)) == 2

    def test_a_restarted_manager_does_not_re_record(self, tmp_path):
        """⚠ The ledger is a FILE for this reason. A perpetual Manager is
        restarted, re-reads the same conditions, and would otherwise file
        the whole backlog again on every restart."""
        root = _project(tmp_path)
        _recorder(root)(_an_event())

        _recorder(root)(_an_event())  # a new process, same project

        assert len(_entries(root)) == 1

    def test_a_normal_cycle_produces_none(self, tmp_path):
        """The other half of the criterion. Nothing records on its own."""
        root = _project(tmp_path)
        _recorder(root)

        assert _entries(root) == []


class TestOffIsOff:
    """§9.15.1. A Manager running without the flag must leave nothing."""

    def test_nothing_is_written(self, tmp_path):
        root = _project(tmp_path)

        assert not _recorder(root, enabled=False)(_an_event())
        assert _entries(root) == []

    def test_not_even_a_directory(self, tmp_path):
        root = _project(tmp_path)

        _recorder(root, enabled=False)(_an_event())

        assert not journal_dir(root, "lead").exists()


class TestTheVocabularyIsClosed:
    """⚠ An open `record(kind, text)` becomes the dumping ground §9.15.0
    exists to prevent, one call site at a time, and nothing can then say
    what the journal covers."""

    def test_an_unknown_class_is_refused_and_said(self, tmp_path):
        root = _project(tmp_path)
        said: list[str] = []

        assert not _recorder(root, said=said)(_an_event(kind="made-up"))
        assert _entries(root) == []
        assert any("not one of the recordable events" in s for s in said)

    @pytest.mark.parametrize("kind", recording.EVENTS)
    def test_every_declared_class_can_actually_be_written(self, tmp_path, kind):
        """⚠ The other direction, and the one this ticket is about: a class
        declared and never writable is a claim about coverage that nothing
        honours."""
        root = _project(tmp_path)

        assert _recorder(root)(_an_event(kind=kind))

    def test_the_classes_nothing_can_record_yet_are_named_with_their_reason(self):
        """⚠ The ticket asks for reconciliation actions and a slot held by
        an idle sandbox. Neither exists to be recorded — SCRUM-64 builds
        reconciliation and SCRUM-70 fixes the held slot — so they are named
        as gaps rather than declared as coverage."""
        assert set(recording.NOT_YET) == {"reconciliation", "idle-slot-held"}
        for why in recording.NOT_YET.values():
            assert "SCRUM-" in why
        assert not set(recording.NOT_YET) & set(recording.EVENTS)


class TestTheEntryItself:
    def test_it_carries_the_observation_and_what_was_expected(self, tmp_path):
        root = _project(tmp_path)

        _recorder(root)(_an_event())

        (entry,) = _entries(root)
        assert "# observation" in entry
        assert "no channel is configured" in entry
        assert "reaches the person it is for" in entry

    def test_nothing_is_inferred(self, tmp_path):
        """§9.15.4: rite records what it saw and draws no conclusion. An
        `inferred` written by rite would be rite grading its own run."""
        root = _project(tmp_path)

        _recorder(root)(_an_event())

        (entry,) = _entries(root)
        assert "(nothing inferred)" in entry

    def test_it_always_has_an_anchor_even_when_the_caller_gave_none(self, tmp_path):
        """The journal refuses an unanchored entry (D-87), so a call site
        that forgot one would silently record nothing."""
        root = _project(tmp_path)

        assert _recorder(root)(
            recording.Event(recording.RELAY_FAILED, "KAN-7", "observed", "expected")
        )
        assert len(_entries(root)) == 1


class TestItNeverEndsTheRun:
    def test_the_production_recorder_is_not_mute(self):
        """⚠ **`say` was omitted where the recorder is actually built**, so
        a read-only journal or a refused class produced no entry AND no
        line anywhere — an empty journal reading as a clean bill of health,
        this ticket's exact defect. The tests saw the complaints only
        because they inject a `say`, so the suite was green while
        production was silent. Found by review, 2026-10-07."""
        import ast
        import inspect as inspect_mod

        from rite_ai.cli import main as main_mod

        source = inspect_mod.getsource(main_mod._start_a_manager)
        call = next(
            node
            for node in ast.walk(ast.parse(source.lstrip()))
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "recorder_for"
        )

        assert "say" in {kw.arg for kw in call.keywords}, (
            "the recorder is built without a `say`, so every failure to "
            "record one of these is silent in production"
        )

    def test_an_unwritable_journal_is_said_not_raised(self, tmp_path):
        from unittest.mock import patch

        root = _project(tmp_path)
        said: list[str] = []

        with patch(
            "rite_ai.managers.journal.write_observation",
            side_effect=OSError("read-only"),
        ):
            assert not _recorder(root, said=said)(_an_event())

        assert any("read-only" in s for s in said)

    def test_an_unreadable_ledger_costs_a_duplicate_not_silence(self, tmp_path):
        """⚠ Deliberately the fail-OPEN direction, and the only one in this
        file. Treating a corrupt ledger as "everything already recorded"
        would stop recording silently, which is this ticket's own defect;
        treating it as empty costs one duplicate entry."""
        from rite_ai.managers import manager_dir

        root = _project(tmp_path)
        _recorder(root)(_an_event())
        # ⚠ In the Manager's own state directory, never the journal's: the
        # journal is write-only (§9.15.5), which `_ledger_path` explains.
        ledger = manager_dir(root, "lead") / recording.LEDGER_FILE
        assert ledger.is_file()
        assert recording.LEDGER_FILE not in {
            p.name for p in journal_dir(root, "lead").iterdir()
        }
        ledger.write_text("not json at all")

        assert _recorder(root)(_an_event())
        assert len(_entries(root)) == 2

    def test_the_ledger_is_actually_bounded(self):
        """⚠ **This test used to assert `MAX_REMEMBERED > 0`**, which is
        true of any number and says nothing: removing the bound from
        `_write` left the whole file green while the ledger grew for the
        life of a perpetual run. Found by review, 2026-10-07. It now
        asserts what the bound DOES."""
        now = 1_000_000.0
        many = {f"k{i}": now for i in range(recording.MAX_REMEMBERED + 500)}

        kept = recording._live(many, now)

        assert len(kept) == recording.MAX_REMEMBERED

    def test_the_oldest_are_the_ones_dropped(self):
        now = 1_000_000.0
        keys = {f"k{i}": now - (recording.MAX_REMEMBERED - i) for i in range(10)}
        keys["newest"] = now

        kept = recording._live(keys, now)

        assert "newest" in kept


class TestTheLedgerExpires:
    """⚠ **Without an expiry "once per run" quietly becomes "once per
    project, ever"** (found by review). Some of these keys are deliberately
    low-cardinality — a broker refusal whose subject is the Manager itself —
    so a failure fixed in October and reintroduced in December would go
    unrecorded and the journal would say nothing."""

    def test_a_failure_that_comes_back_much_later_is_recorded_again(
        self, tmp_path, monkeypatch
    ):
        import time

        root = _project(tmp_path)
        assert _recorder(root)(_an_event())

        later = time.time() + recording.FORGET_AFTER_SECONDS + 60
        monkeypatch.setattr(time, "time", lambda: later)

        assert _recorder(root)(_an_event())
        assert len(_entries(root)) == 2

    def test_the_same_failure_within_the_window_is_not(self, tmp_path, monkeypatch):
        """The control: the expiry must not defeat the dedup."""
        import time

        root = _project(tmp_path)
        assert _recorder(root)(_an_event())

        later = time.time() + recording.FORGET_AFTER_SECONDS - 60
        monkeypatch.setattr(time, "time", lambda: later)

        assert not _recorder(root)(_an_event())
        assert len(_entries(root)) == 1

    def test_a_ledger_in_the_old_list_shape_is_still_read(self, tmp_path):
        """An upgrade mid-run must not re-file everything already filed."""
        import json

        from rite_ai.managers import manager_dir

        root = _project(tmp_path)
        _recorder(root)(_an_event())
        ledger = manager_dir(root, "lead") / recording.LEDGER_FILE
        keys = list(json.loads(ledger.read_text())["recorded"])
        ledger.write_text(json.dumps({"recorded": keys}))

        # A bare list carries no time, so it expires at the next read — which
        # costs one duplicate and never silence.
        assert recording._read(ledger) == dict.fromkeys(keys, 0.0)


class TestEachWiredSiteActuallyRecords:
    """⚠ **The half that matters most.** A recorder nothing calls is the
    defect this ticket is about, restated: the facility existed, was
    reachable, and recorded nothing. Each of these drives the REAL call
    site, not the recorder."""

    def test_a_refused_worker_start(self, tmp_path):
        from rite_ai.managers.broker import requests_dir
        from rite_ai.managers.supervise import _honour_worker_requests

        root = _project(tmp_path)
        where = requests_dir(root, "lead")
        where.mkdir(parents=True, exist_ok=True)
        (where / "1.json").write_text('{"worker":"ghost","ticket":"KAN-7"}')

        _honour_worker_requests(
            root,
            "lead",
            lambda _raw: (False, "there is no Worker called 'ghost'"),
            lambda _line: None,
            _recorder(root),
        )

        (entry,) = _entries(root)
        assert "ghost" in entry
        assert recording.WORKER_START_REFUSED.split("-")[0] in entry.lower()

    def test_a_queued_worker_start_is_NOT_recorded(self, tmp_path):
        """⚠ The control. A request queued for want of a slot is a queue,
        not a failure; recording it would fill the journal with a busy
        fleet's own capacity, every cycle."""
        from rite_ai.managers.broker import NO_SLOT, requests_dir
        from rite_ai.managers.supervise import _honour_worker_requests

        root = _project(tmp_path)
        where = requests_dir(root, "lead")
        where.mkdir(parents=True, exist_ok=True)
        (where / "1.json").write_text('{"worker":"alpha","ticket":"KAN-7"}')

        _honour_worker_requests(
            root,
            "lead",
            lambda _raw: (NO_SLOT, "2 Worker(s) already running"),
            lambda _line: None,
            _recorder(root),
        )

        assert _entries(root) == []

    def test_a_started_worker_is_NOT_recorded(self, tmp_path):
        from rite_ai.managers.broker import requests_dir
        from rite_ai.managers.supervise import _honour_worker_requests

        root = _project(tmp_path)
        where = requests_dir(root, "lead")
        where.mkdir(parents=True, exist_ok=True)
        (where / "1.json").write_text('{"worker":"alpha","ticket":"KAN-7"}')

        _honour_worker_requests(
            root,
            "lead",
            lambda _raw: (True, "started alpha on KAN-7"),
            lambda _line: None,
            _recorder(root),
        )

        assert _entries(root) == []

    def _deliver_returning(self, root: Path, result):
        from unittest.mock import patch

        from rite_ai.publishing.requests import honour_deliveries, requests_dir

        where = requests_dir(root, "lead")
        where.mkdir(parents=True, exist_ok=True)
        (where / "1.json").write_text('{"worker":"alpha","ticket":"KAN-7"}')
        with patch("rite_ai.publishing.deliver.deliver", return_value=result):
            honour_deliveries(root, "lead", lambda _line: None, _recorder(root))

    def test_a_refused_delivery(self, tmp_path):
        from rite_ai.publishing.deliver import Refused

        root = _project(tmp_path)

        self._deliver_returning(root, Refused("alpha has no sandbox"))

        (entry,) = _entries(root)
        assert "alpha/KAN-7" in entry
        assert "no sandbox" in entry

    def test_a_delivery_the_gate_held_is_recorded_as_a_GATE_refusal(self, tmp_path):
        """The two classes are told apart so an Owner can see at a glance
        whether the fleet is being stopped by the gate or by the plumbing."""
        from rite_ai.publishing.deliver import Refused

        root = _project(tmp_path)

        self._deliver_returning(
            root, Refused("the publish gate refused it: 17 findings")
        )

        (entry,) = _entries(root)
        assert "publish gate" in entry

    def test_one_module_failing_a_multi_module_delivery_is_recorded(self, tmp_path):
        """Per module, because a delivery of three can fail one and deliver
        two, and the Owner needs to know which."""
        from rite_ai.publishing.deliver import Delivered, Outcome

        root = _project(tmp_path)

        self._deliver_returning(
            root,
            Delivered(
                [
                    Outcome("svc", "KAN-7", True, "pushed"),
                    Outcome("web", "KAN-7", False, "uncommitted in the sandbox"),
                ],
                "sandbox kept",
            ),
        )

        (entry,) = _entries(root)
        assert "web" in entry and "uncommitted" in entry

    def test_a_clean_delivery_records_nothing(self, tmp_path):
        from rite_ai.publishing.deliver import Delivered, Outcome

        root = _project(tmp_path)

        self._deliver_returning(
            root,
            Delivered([Outcome("svc", "KAN-7", True, "pushed")], "sandbox removed"),
        )

        assert _entries(root) == []

    def _recovered(self, root: Path, actions):
        from rite_ai.cli.main import _recover_and_record

        return _recover_and_record(
            root,
            "lead",
            lambda _line: None,
            _recorder(root),
            lambda _r, _m, _s: actions,
        )

    def test_a_restarted_worker(self, tmp_path):
        from rite_ai.managers.recovery import RESTART, RecoveryAction

        root = _project(tmp_path)

        self._recovered(
            root, [RecoveryAction("alpha", RESTART, "KAN-7", "no heartbeat")]
        )

        (entry,) = _entries(root)
        assert "alpha" in entry and "restarted in place" in entry

    def test_a_restaged_ticket(self, tmp_path):
        from rite_ai.managers.recovery import RESTAGE, RecoveryAction

        root = _project(tmp_path)

        self._recovered(root, [RecoveryAction("alpha", RESTAGE, "KAN-7", "gone")])

        (entry,) = _entries(root)
        assert "re-staged" in entry

    def test_recovery_running_out_of_budget(self, tmp_path):
        from rite_ai.managers.recovery import REPORT, RecoveryAction

        root = _project(tmp_path)

        self._recovered(
            root, [RecoveryAction("alpha", REPORT, "KAN-7", "budget exhausted")]
        )

        (entry,) = _entries(root)
        assert "ran out of recovery budget" in entry

    def test_a_restart_that_FAILED_is_not_recorded_as_a_success(self, tmp_path):
        """⚠ **The journal stated the opposite of what happened.**
        `recover_stalled_workers` returned the PLANNED actions after
        executing them, so a restart that failed was recorded as "it was
        restarted in place" — measured 2026-10-07, with the terminal saying
        "could not restart stalled Worker 'alpha'" at the same moment. A
        journal that contradicts the run is worse than one that says
        nothing, which is this ticket's own subject."""
        from rite_ai.managers.recovery import RESTART, RecoveryAction

        root = _project(tmp_path)

        self._recovered(
            root,
            [
                RecoveryAction(
                    "alpha",
                    RESTART,
                    "KAN-7",
                    "session stalled; sandbox present",
                    attempted=False,
                    outcome="the sandbox refused to start",
                )
            ],
        )

        (entry,) = _entries(root)
        assert "FAILED" in entry
        assert "the sandbox refused to start" in entry
        assert "and it was restarted in place:" not in entry

    def test_the_real_recovery_reports_whether_it_worked(self, tmp_path):
        """⚠ Driven through `recover_stalled_workers` itself, because the
        defect was in what IT returned. A stand-in for it could not have
        caught this, and the first version of these tests only had one."""
        from rite_ai.managers.recovery import recover_stalled_workers
        from rite_ai.reporting.heartbeat import StallReport

        root = _project(tmp_path)
        stall = StallReport(
            worker="alpha", last_seen=0.0, seconds_silent=9999.0, ticket="KAN-7"
        )

        actions = recover_stalled_workers(
            root,
            "lead",
            lambda _line: None,
            stalls_of=lambda: [stall],
            classify=lambda _r: "present",
            do_restart=lambda _r: (False, "the sandbox refused to start"),
        )

        assert [a.attempted for a in actions] == [False]
        assert actions[0].outcome == "the sandbox refused to start"

    def test_a_restart_that_worked_still_reads_as_one(self, tmp_path):
        from rite_ai.managers.recovery import recover_stalled_workers
        from rite_ai.reporting.heartbeat import StallReport

        root = _project(tmp_path)
        stall = StallReport(
            worker="alpha", last_seen=0.0, seconds_silent=9999.0, ticket="KAN-7"
        )

        actions = recover_stalled_workers(
            root,
            "lead",
            lambda _line: None,
            stalls_of=lambda: [stall],
            classify=lambda _r: "present",
            do_restart=lambda _r: (True, "restarted"),
        )

        assert [a.attempted for a in actions] == [True]

    def test_a_cycle_with_nothing_stalled_records_nothing(self, tmp_path):
        root = _project(tmp_path)

        assert self._recovered(root, []) == []
        assert _entries(root) == []

    def test_the_recovery_actions_are_still_returned_to_the_caller(self, tmp_path):
        """⚠ The wrapper must not swallow what the supervisor relies on."""
        from rite_ai.managers.recovery import RESTART, RecoveryAction

        root = _project(tmp_path)
        actions = [RecoveryAction("alpha", RESTART, "KAN-7", "no heartbeat")]

        assert self._recovered(root, actions) == actions


class TestTheRelayRecordsItsOwnFailures:
    """The a9 failures by name: a post Slack refused, and an answer that
    went to the notes pile instead of the thread it answered."""

    def _listener(self, root: Path, slack, clock):
        from tests.test_an_answer_to_the_owner_goes_to_the_dm import _listener

        listener = _listener(root, slack, clock)
        listener.record = _recorder(root)
        return listener

    def _setup(self, tmp_path: Path):
        import time

        from tests.test_an_answer_to_the_owner_goes_to_the_dm import Clock, Slack
        from tests.test_an_answer_to_the_owner_goes_to_the_dm import (
            _project as slack_project,
        )

        root = slack_project(tmp_path)
        slack, clock = Slack(), Clock(time.time())
        return root, slack, clock, self._listener(root, slack, clock)

    def test_a_question_slack_refuses_to_post_is_recorded(self, tmp_path):
        """A QUESTION, which is what needs the person: it goes top-level into
        the DM, and a post Slack refuses leaves the person un-asked while
        every reply behind it waits."""
        from rite_ai.managers.mailbox import OUTBOX, QUESTION, send

        root, slack, clock, listener = self._setup(tmp_path)
        send(root, "lead", OUTBOX, "which board should RT-15 go on?", kind=QUESTION)

        def refuse(method, token, params=None, payload=None):
            if method == "chat.postMessage":
                return {"ok": False, "error": "channel_not_found"}
            return slack(method, token, params, payload)

        listener.post_replies(call=refuse)

        entries = _entries(root)
        assert entries, "a message that could not be posted recorded nothing"
        assert "could not be posted" in entries[0]
        assert "channel_not_found" in entries[0]

    def test_an_answer_that_fell_through_to_notes_is_recorded(self, tmp_path):
        """⚠ THE a9 failure. The Owner asked in their DM and the answer went
        to the ambient pile whose own root line says nothing in it needs
        them — and the journal said nothing about it."""
        from rite_ai.managers.mailbox import OUTBOX, REPLY, send
        from tests.test_an_answer_to_the_owner_goes_to_the_dm import (
            _heard,
            _owner_asks,
        )

        root, slack, clock, listener = self._setup(tmp_path)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        _heard(listener, slack, clock)
        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)

        def refuse_the_thread(method, token, params=None, payload=None):
            args = payload or params or {}
            if method == "chat.postMessage" and args.get("thread_ts") == asked:
                return {"ok": False, "error": "thread_not_found"}
            return slack(method, token, params, payload)

        listener.post_replies(call=refuse_the_thread)

        entries = _entries(root)
        assert entries, "a misrouted answer recorded nothing"
        # ⚠ It states that the answer did not appear where the Owner asked,
        # which is what is KNOWN at that point. The first version asserted
        # it "went to the day's notes instead" — before the notes post had
        # been attempted, and that post can fail too (found by review).
        assert "does not appear where the Owner asked" in entries[0]
        assert "went to the day's notes instead" not in entries[0]

    def test_an_ordinary_exchange_records_nothing(self, tmp_path):
        """The control: a reply that reaches the thread it answered is not a
        failure and must not fill the journal."""
        from rite_ai.managers.mailbox import OUTBOX, REPLY, send
        from tests.test_an_answer_to_the_owner_goes_to_the_dm import (
            _heard,
            _owner_asks,
        )

        root, slack, clock, listener = self._setup(tmp_path)
        _owner_asks(slack, clock, "is RT-14 merged?")
        _heard(listener, slack, clock)
        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)

        listener.post_replies(call=slack)

        assert _entries(root) == []

    def test_a_relay_with_no_recorder_still_works(self, tmp_path):
        """Off is off, and the relay must not need to know."""
        from rite_ai.managers.mailbox import OUTBOX, REPLY, send

        root, slack, clock, listener = self._setup(tmp_path)
        listener.record = None
        send(root, "lead", OUTBOX, "RT-15 merged", kind=REPLY)

        listener.post_replies(call=slack)

        assert _entries(root) == []
