"""S31: a definition-of-done item the Worker cannot measure is the host's.

Found in the v0.7.0 dogfood (KAN-29): a Worker cannot run a sandbox
before/after test inside its own sandbox (a nested `sandbox_apply` is denied,
"Operation not permitted"), and found that out mid-task. Robert's design:

1. the item is marked at refinement, in the SIGNED record, not a flag beside it;
2. the Worker's TICKET.md says it is not the Worker's to run, and the run
   pauses for the host's result;
3. the host's result is itself a signed, attributed, audited record: who
   measured, when, the result and the output's content hash, bound to the
   record and the item, tamper-evident.

Real key, real git, real predicate; the board and yoloAI are in memory.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.config.parse import parse_config, parse_modules
from rite_ai.publishing import record as publish_record
from rite_ai.publishing.deliver import Outcome, _host_measurement_hold
from rite_ai.refinement import accept, ask, attribution, measurement
from rite_ai.refinement import key as refinement_key
from rite_ai.refinement import record as rec
from rite_ai.refinement import status as st
from rite_ai.tickets import Ticket
from tests.test_rite_delivers_a_finished_task import TICKET, Project
from tests.test_the_owner_refines_with_the_user import (
    HOUR,
    NOW,
    Board,
    kan7,
    key,
    reply_to_latest,
    send,
)

__all__ = ["key"]  # a fixture, used by name below

BOARD = {"type": "github", "repo": "org/repo"}
BY = attribution.answered_by("U0OWNER1", attribution.TERMINAL)
HOST_ITEM = "a sandbox run before and after the change shows the denial is gone"


def _record(key, items, host=(), ticket="KAN-29", **kw) -> rec.Record:
    return rec.build(
        ticket=ticket,
        board=BOARD,
        title="t",
        description="d",
        definition_of_done=list(items),
        verify=rec.NONE_AGREED,
        provenance={"kind": rec.ATTESTED, "at": "2026-09-30T10:00:00+00:00"},
        supersedes=None,
        key=key,
        host_measured=list(host),
        **kw,
    )


def _measure(key, record, item, result=measurement.PASS, output=b"denied: 0\n"):
    return measurement.build(
        record=record, item=item, result=result, output=output, by=BY, key=key
    )


# --- 1. in the signed record ----------------------------------------------------


class TestTheMarkIsSigned:
    def test_a_record_without_one_is_byte_for_byte_what_it_was(self, key):
        """Every record written before S31 keeps its id and its MAC."""
        plain = _record(key, ["a", "b"])
        assert "host_measured" not in plain.payload()
        assert rec.mac_verifies(plain.payload(), key)

    def test_the_mark_is_inside_the_mac(self, key):
        marked = _record(key, ["a", "b"], host=[1])
        payload = marked.payload()
        assert payload["host_measured"] == [1]
        stripped = {k: v for k, v in payload.items() if k != "host_measured"}
        assert not rec.mac_verifies(stripped, key), "removing the mark is detected"
        moved = dict(payload, host_measured=[0])
        assert not rec.mac_verifies(moved, key), "moving it is detected"

    def test_it_changes_the_record_id(self, key):
        assert (
            _record(key, ["a", "b"]).record_id
            != _record(key, ["a", "b"], host=[1]).record_id
        )

    @pytest.mark.parametrize("bad", [[2], [-1], [0, 0], [1, 0], ["0"], [True], "0"])
    def test_a_mark_that_is_not_an_item_is_refused(self, key, bad):
        payload = dict(_record(key, ["a", "b"]).payload(), host_measured=bad)
        assert rec.schema_problem(payload)

    def test_the_predicate_reads_it_back(self, key):
        ticket = Ticket(id="KAN-29", title="t", description="d")
        board = Board(ticket)
        record = rec.build(
            ticket="KAN-29",
            board=BOARD,
            title="t",
            description="d",
            definition_of_done=["a", HOST_ITEM],
            verify=rec.NONE_AGREED,
            provenance={"kind": rec.ATTESTED, "at": "x"},
            supersedes=None,
            key=key,
            host_measured=[1],
        )
        board.comments["KAN-29"].append(rec.render(record))
        got = st.status(board, "KAN-29")
        assert got.refined and got.record.host_measured == (1,)


# --- marked at refinement: the attest path and the round path ------------------


class TestMarkedAtRefinement:
    def test_rite_refine_accept_host_item(self, key, tmp_path):
        board = Board(Ticket(id="KAN-29", title="t", description="d"))
        with patch("rite_ai.refinement.status.board_for", return_value=board):
            done = accept.accept(
                tmp_path,
                None,
                "KAN-29",
                items=["the fix is in"],
                verify=[],
                host_items=[HOST_ITEM],
            )
        assert done.ok, done.message
        got = st.status(board, "KAN-29").record
        assert got.definition_of_done == ("the fix is in", HOST_ITEM)
        assert got.host_measured == (1,)
        assert (
            "(measured on the host, not by the Worker)" in board.comments["KAN-29"][-1]
        )

    def test_a_host_tag_in_a_round_is_parsed_named_and_needs_its_source(self):
        text = (
            "Proposal:\n"
            f'- {HOST_ITEM} [ticket: "sandbox"] [host]\n'
            "- the fix is in [proposed]\n"
        )
        checked = ask.check(text, k=2, ticket_text="the sandbox denial", answers=[])
        assert checked.ok, checked.problems
        first, second = checked.ask.proposal
        assert (first.host, second.host) == (True, False)
        assert first.text == HOST_ITEM
        shown = ask.render(
            checked.ask, ticket="KAN-29", k=2, manager="lead", accept_words=["ok"]
        )
        assert f'1. {HOST_ITEM} (from the ticket: "sandbox"){ask.HOST_LINE}' in shown
        assert "2. the fix is in (proposed by lead" in shown
        assert ask.HOST_LINE not in shown.split("2. the fix is in")[1].split("\n")[0]
        assert "Item(s) 1: the Worker cannot take this measurement" in shown
        alone = ask.check(
            f"Proposal:\n- {HOST_ITEM} [host]\n", k=2, ticket_text="", answers=[]
        )
        assert any("does not say where it came from" in p for p in alone.problems)

    def test_his_ok_to_a_round_with_a_host_item_signs_the_mark(self, tmp_path, key):
        board = Board(kan7())
        assert send(tmp_path, board).ok
        reply_to_latest(tmp_path, board, "the http one in main.py. just make it a flag")
        round_2 = (
            "Proposal:\n"
            "- The HTTP request timeout in main.py is set by a flag "
            '[answer: "the http one in main.py"]\n'
            "- A live run shows the request times out [proposed] [host]\n"
        )
        assert send(tmp_path, board, text=round_2, now=NOW + 2 * HOUR).ok
        reply_to_latest(tmp_path, board, "ok", sent_at=NOW + 3 * HOUR)
        got = st.status(board, "KAN-7")
        assert got.refined, got.detail
        assert got.record.host_measured == (1,)


# --- 2. TICKET.md: not the Worker's to run ---------------------------------------


class TestTheWorkerIsTold:
    def test_the_marked_item_is_not_its_to_run_and_the_rest_is_unchanged(self, key):
        marked = rec.render_for_worker(
            _record(key, ["the fix is in", HOST_ITEM], host=[1])
        )
        plain = rec.render_for_worker(_record(key, ["the fix is in", HOST_ITEM]))
        assert f"- [ ] {rec.HOST_TAG_FOR_WORKER}{HOST_ITEM}" in marked
        assert "- [ ] the fix is in" in marked
        assert "NOT YOURS TO RUN" in marked and "holds publishing" in marked
        assert rec.HOST_TAG_FOR_WORKER not in plain and "NOT YOURS" not in plain

    def test_the_file_the_worker_reads_carries_it(self, tmp_path, key):
        from rite_ai.sandbox.delivery import delivery_text, render_ticket

        record = _record(key, ["the fix is in", HOST_ITEM], host=[1])
        text = delivery_text(
            render_ticket(Ticket(id="KAN-29", title="t", description="d")),
            "2026-09-30T10:00:00Z",
            "the board",
            sections=(("Agreed definition of done", rec.render_for_worker(record)),),
        )
        assert "not yours to run: the host will measure this" in text


# --- 3. the host's result: signed, attributed, audited, bound ------------------


class TestTheHostResult:
    def test_it_says_who_when_what_and_the_outputs_hash(self, key):
        record = _record(key, ["a", HOST_ITEM], host=[1])
        m = _measure(key, record, 1, output=b"denied: 0\n")
        p = m.payload
        assert p["measured_by"] == BY
        assert p["measured_at"] and p["result"] == "pass"
        import hashlib

        assert p["output_sha256"] == hashlib.sha256(b"denied: 0\n").hexdigest()
        assert p["record_id"] == record.record_id and p["item"] == 1
        assert measurement.verifies(p, key, record, 1)

    def test_only_a_host_item_takes_a_result(self, key):
        record = _record(key, ["a", HOST_ITEM], host=[1])
        with pytest.raises(ValueError, match="not one the host measures"):
            _measure(key, record, 0)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("result", "fail"),
            ("output_sha256", "0" * 64),
            ("measured_by", {"owner_user": "U0SOMEONE", "via": "slack"}),
            ("measured_at", "2020-01-01T00:00:00+00:00"),
            ("item", 0),
            ("record_id", "f" * 24),
            ("ticket", "KAN-30"),
            ("board", {"type": "github", "repo": "other/repo"}),
        ],
    )
    def test_any_edit_is_evident(self, key, field, value):
        record = _record(key, ["a", HOST_ITEM], host=[1])
        edited = dict(_measure(key, record, 1).payload, **{field: value})
        assert not measurement.verifies(edited, key, record, 1)

    def test_it_does_not_transfer_to_another_record_or_item_text(self, key):
        record = _record(key, ["a", HOST_ITEM], host=[1])
        p = _measure(key, record, 1).payload
        reworded = _record(key, ["a", HOST_ITEM + " twice"], host=[1])
        assert not measurement.verifies(p, key, reworded, 1)

    def test_the_item_text_is_bound_even_under_a_valid_signature(self, key):
        """Defence in depth under the record id: a result signed with the
        right key, for the right record and index, but for other item text,
        is no result for this item."""
        record = _record(key, ["a", HOST_ITEM], host=[1])
        p = dict(_measure(key, record, 1).payload)
        p["item_sha256"] = rec.text_sha256("a different item")
        p.pop("mac")
        p["mac"] = rec.mac_of(p, key)
        assert rec.mac_verifies(p, key)
        assert not measurement.verifies(p, key, record, 1)

    def test_one_signed_with_another_key_is_no_result(self, key, tmp_path):
        record = _record(key, ["a", HOST_ITEM], host=[1])
        other = b"k" * 32
        forged = _measure(other, record, 1).payload
        assert not measurement.verifies(forged, key, record, 1)

    def test_its_comment_is_never_read_as_a_record_claim(self, key):
        record = _record(key, ["a", HOST_ITEM], host=[1])
        body = measurement.render(_measure(key, record, 1), record)
        assert not rec.carries_marker(body)
        assert "PASS" in body and "terminal" in body

    def test_the_audit_log_is_append_only_and_outside_every_managers_grant(
        self, key, tmp_path
    ):
        from rite_ai.managers.mailbox import _mail_home  # noqa: PLC2701

        record = _record(key, ["a", HOST_ITEM], host=[1])
        measurement.append(tmp_path, _measure(key, record, 1, measurement.FAIL))
        measurement.append(tmp_path, _measure(key, record, 1, measurement.PASS))
        lines = measurement.log_path(tmp_path).read_text().splitlines()
        assert len(lines) == 2
        assert measurement.log_path(tmp_path).is_relative_to(_mail_home().parent)
        assert not measurement.log_path(tmp_path).is_relative_to(tmp_path)


class TestRiteRefineMeasured:
    def _project(self, tmp_path, key, board):
        (tmp_path / ".rite").mkdir()
        (tmp_path / ".rite" / "config.yaml").write_text(
            "slack:\n  owner_user: U0OWNER1\n"
        )
        (tmp_path / ".rite" / "modules.yaml").write_text("modules: []\n")
        record = rec.build(
            ticket="KAN-29",
            board=BOARD,
            title="t",
            description="d",
            definition_of_done=["a", HOST_ITEM],
            verify=rec.NONE_AGREED,
            provenance={"kind": rec.ATTESTED, "at": "x"},
            supersedes=None,
            key=key,
            host_measured=[1],
        )
        board.comments["KAN-29"].append(rec.render(record))
        return record

    def _run(self, tmp_path, board, *args):
        from rite_ai.cli.main import cli

        with patch("rite_ai.refinement.status.board_for", return_value=board):
            return CliRunner().invoke(
                cli,
                ["refine", "measured", "KAN-29", *args],
                input="denied: 0\n",
                env={"RITE_PROJECT_ROOT": str(tmp_path)},
            )

    def test_it_posts_reads_back_and_logs_a_signed_result(self, key, tmp_path):
        board = Board(Ticket(id="KAN-29", title="t", description="d"))
        record = self._project(tmp_path, key, board)
        got = self._run(
            tmp_path, board, "--item", "2", "--result", "pass", "--output", "-"
        )
        assert got.exit_code == 0, got.output
        (logged,) = measurement.logged(tmp_path)
        assert measurement.verifies(logged, key, record, 1)
        assert logged["measured_by"] == {"owner_user": "U0OWNER1", "via": "terminal"}
        assert logged["measurement_id"] in board.comments["KAN-29"][-1]
        assert measurement.holds(tmp_path, record, key) == []

    def test_an_item_the_host_does_not_measure_is_refused(self, key, tmp_path):
        board = Board(Ticket(id="KAN-29", title="t", description="d"))
        self._project(tmp_path, key, board)
        got = self._run(
            tmp_path, board, "--item", "1", "--result", "pass", "--output", "-"
        )
        assert got.exit_code == 1 and "not one the host measures" in got.output
        assert measurement.logged(tmp_path) == []

    def test_a_result_the_board_did_not_keep_is_not_logged(self, key, tmp_path):
        board = Board(Ticket(id="KAN-29", title="t", description="d"))
        self._project(tmp_path, key, board)
        real = board.comment

        def garbling(ticket, text):
            return real(ticket, text.replace('"pass"', '"PASS"'))

        board.comment = garbling
        got = self._run(
            tmp_path, board, "--item", "2", "--result", "pass", "--output", "-"
        )
        assert got.exit_code == 1 and "NOT recorded" in got.output
        assert measurement.logged(tmp_path) == []

    def test_it_is_refused_in_a_managers_session(self, key, tmp_path, monkeypatch):
        board = Board(Ticket(id="KAN-29", title="t", description="d"))
        self._project(tmp_path, key, board)
        monkeypatch.setattr("rite_ai.managers.current_manager", lambda: "lead")
        got = self._run(
            tmp_path, board, "--item", "2", "--result", "pass", "--output", "-"
        )
        assert got.exit_code == 1 and "Manager's session" in got.output


# --- the pause: publishing waits for the host ---------------------------------


class TestPublishingWaits:
    def _started(self, tmp_path, key, host=(1,)):
        """A Worker started, as `rite sandbox start` records it, on a record
        whose item 2 is the host's, then its work committed in the sandbox."""
        p = Project(tmp_path, "pull_request")
        record = _record(key, ["the fix is in", HOST_ITEM], host=host, ticket=TICKET)
        publish_record.write(
            p.root,
            "alpha",
            TICKET,
            parse_config(p.root / ".rite" / "config.yaml"),
            parse_modules(p.root / ".rite" / "modules.yaml"),
            refinement=record.payload(),
        )
        p.work()
        return p, record

    def _deliver(self, p):
        opened = Outcome("svc", TICKET, True, "pushed; PR https://x/pull/1")
        with patch(
            "rite_ai.publishing.deliver._publish", return_value=opened
        ) as published:
            got = p.deliver()
        return got, published.called

    def test_no_result_collects_and_does_not_publish(self, tmp_path, key):
        p, _ = self._started(tmp_path, key)
        got, published = self._deliver(p)
        assert not published
        (o,) = got.outcomes
        assert not o.ok and "held for the host measurement" in o.why
        assert "item 2 has no host measurement" in o.why
        assert p.branch(TICKET), "collected: the host has the work to measure"
        assert "sandbox kept" in got.sandbox

    def test_a_pass_releases_it(self, tmp_path, key):
        p, record = self._started(tmp_path, key)
        measurement.append(p.root, _measure(key, record, 1))
        got, published = self._deliver(p)
        assert published and got.ok

    def test_a_fail_holds(self, tmp_path, key):
        p, record = self._started(tmp_path, key)
        measurement.append(p.root, _measure(key, record, 1, measurement.FAIL))
        got, published = self._deliver(p)
        assert not published and "FAILED" in got.outcomes[0].why

    def test_no_host_item_publishes_as_before(self, tmp_path, key):
        p, _ = self._started(tmp_path, key, host=())
        got, published = self._deliver(p)
        assert published and got.ok

    def test_a_snapshot_that_does_not_verify_holds(self, tmp_path, key):
        p, record = self._started(tmp_path, key)
        started = publish_record.read(p.root, "alpha")
        tampered = replace(
            started, refinement=dict(started.refinement, host_measured=[0])
        )
        assert _host_measurement_hold(p.root, tampered)
        assert "does not verify" in _host_measurement_hold(p.root, tampered)[0]

    def test_no_key_holds(self, tmp_path, key, monkeypatch):
        p, record = self._started(tmp_path, key)
        measurement.append(p.root, _measure(key, record, 1))
        monkeypatch.setenv(refinement_key.KEY_DIR_ENV, str(tmp_path / "nokey"))
        started = publish_record.read(p.root, "alpha")
        assert (
            "cannot read its refinement key"
            in _host_measurement_hold(p.root, started)[0]
        )

    def test_an_older_start_with_no_snapshot_holds_nothing(self, tmp_path, key):
        p = Project(tmp_path, "pull_request")
        p.start()
        assert (
            _host_measurement_hold(p.root, publish_record.read(p.root, "alpha")) == []
        )


# --- the invariant, over every definition of done up to four items ------------


STATES = ("none", "pass", "fail", "forged", "other-record", "fail-then-pass")


def _cases():
    for n in range(1, 5):
        for size in range(n + 1):
            for host in itertools.combinations(range(n), size):
                yield n, host


@pytest.mark.parametrize("n,host", list(_cases()))
def test_the_mark_the_worker_and_the_hold_agree_for_every_shape(tmp_path, key, n, host):
    """For every definition of done of 1-4 items and every set of marks:
    the record verifies and carries exactly those marks, TICKET.md and the
    board mark exactly those lines, and publishing is held unless every
    marked item has a genuine latest PASS, for every combination of results."""
    items = [f"item {i} is true" for i in range(n)]
    record = _record(key, items, host=host)
    assert rec.mac_verifies(record.payload(), key)
    assert record.host_measured == tuple(host)
    for_worker = rec.render_for_worker(record).splitlines()
    on_board = rec.render(record).splitlines()
    for i, item in enumerate(items):
        (worker_line,) = [line for line in for_worker if line.endswith(item)]
        (board_line,) = [line for line in on_board if line.endswith(item)]
        assert (rec.HOST_TAG_FOR_WORKER in worker_line) == (i in host)
        assert (rec.HOST_TAG_ON_BOARD in board_line) == (i in host)
    assert ("NOT YOURS TO RUN" in "\n".join(for_worker)) == bool(host)

    other = _record(key, items + ["x"], host=host)
    for states in itertools.product(STATES, repeat=len(host)):
        root = tmp_path / "-".join(states or ("empty",))
        root.mkdir()
        for item, state in zip(host, states):
            if state == "pass":
                measurement.append(root, _measure(key, record, item))
            elif state == "fail":
                measurement.append(root, _measure(key, record, item, measurement.FAIL))
            elif state == "forged":
                measurement.append(root, _measure(b"x" * 32, record, item))
            elif state == "other-record":
                measurement.append(root, _measure(key, other, item))
            elif state == "fail-then-pass":
                measurement.append(root, _measure(key, record, item, measurement.FAIL))
                measurement.append(root, _measure(key, record, item))
        genuine_pass = all(s in ("pass", "fail-then-pass") for s in states)
        assert (measurement.holds(root, record, key) == []) == genuine_pass, states
