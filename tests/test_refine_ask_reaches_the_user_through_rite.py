"""TR2: `rite refine ask` is how the Owner asks the User, and its supervisor,
outside the boundary, is what sends it and what reads his reply.

The command only ASKS, like `rite route` and `rite chore`: it writes a request
into the Manager's own directory. The Owner's supervisor checks the round
against the ticket and the User's answers, sends it, and tells the Owner in
its next instruction. When the User's reply is delivered, what it did (an
answer recorded, a definition of done accepted and written) is in the same
instruction as the reply, in rite's words.
"""

from __future__ import annotations

from click.testing import CliRunner

import rite_ai.managers.supervise as supervise_mod
from rite_ai.cli.main import cli
from rite_ai.managers import MANAGER_ENV, mailbox, slack
from rite_ai.managers.session import FINISHED, Ending, Liveness, StartResult
from rite_ai.managers.supervise import supervise
from rite_ai.refinement import protocol, rounds
from rite_ai.refinement import status as st
from tests.test_the_owner_refines_with_the_user import (
    ROUND_1,
    ROUND_2,
    Board,
    kan7,
    key,
)
from tests.test_the_owner_routes_to_other_managers import CONFIG, project

__all__ = ["key", "project"]  # fixtures, used by name below


def _ask(monkeypatch, as_manager, ticket, text, *, on_stdin=True):
    if as_manager:
        monkeypatch.setenv(MANAGER_ENV, as_manager)
    else:
        monkeypatch.delenv(MANAGER_ENV, raising=False)
    if on_stdin:
        return CliRunner().invoke(cli, ["refine", "ask", ticket, "-"], input=text)
    return CliRunner().invoke(cli, ["refine", "ask", ticket, text])


class TestTheCommandOnlyAsks:
    def test_the_owner_queues_a_round(self, project, monkeypatch):
        got = _ask(monkeypatch, "lead", "KAN-7", ROUND_1)
        assert got.exit_code == 0, got.output
        assert "round queued for KAN-7" in got.output
        assert protocol.take(project, "lead") == [
            {"ticket": "KAN-7", "text": ROUND_1.removesuffix("\n")}
        ]

    def test_a_secondary_does_not_refine(self, project, monkeypatch):
        got = _ask(monkeypatch, "helper", "KAN-7", ROUND_1)
        assert got.exit_code == 1 and "refinement is the Owner's" in got.output
        assert protocol.take(project, "helper") == []

    def test_from_a_persons_shell_it_points_at_accept(self, project, monkeypatch):
        got = _ask(monkeypatch, "", "KAN-7", ROUND_1)
        assert got.exit_code == 1 and "rite refine accept KAN-7" in got.output

    def test_text_on_the_command_line_is_refused(self, project, monkeypatch):
        """F14: in double quotes the shell runs backticks before rite sees
        them, and a round quotes tickets."""
        got = _ask(monkeypatch, "lead", "KAN-7", ROUND_1, on_stdin=False)
        assert got.exit_code == 1
        assert "reads its text from a file or stdin" in got.output

    def test_a_round_in_the_wrong_shape_costs_no_turn(self, project, monkeypatch):
        got = _ask(monkeypatch, "lead", "KAN-7", "Which timeout?\n")
        assert got.exit_code == 1 and "outside Questions" in got.output
        assert protocol.take(project, "lead") == []


class TestTheSupervisorSendsAndReads:
    def test_it_sends_what_was_asked_and_tells_the_owner(self, project, key):
        board = Board(kan7())
        protocol.request(project, "lead", "KAN-7", ROUND_1)
        said: list[str] = []
        assert protocol.step(project, "lead", board, said.append) == []
        (question,) = mailbox.read(project, "lead", mailbox.OUTBOX)
        assert question.kind == mailbox.QUESTION
        (note,) = mailbox.read(project, "lead", mailbox.INBOX)
        assert "round 1 is in front of the User" in note.text
        assert any("refinement:" in line for line in said)

    def test_a_refused_round_is_said_to_the_owner(self, project, key):
        board = Board(kan7())
        protocol.request(project, "lead", "KAN-7", ROUND_2)
        protocol.step(project, "lead", board, lambda _m: None)
        assert mailbox.read(project, "lead", mailbox.OUTBOX) == []
        (note,) = mailbox.read(project, "lead", mailbox.INBOX)
        assert "was not sent" in note.text and "Quote exactly" in note.text

    def test_a_secondarys_request_is_discarded_and_said(self, project, key):
        protocol.request(project, "helper", "KAN-7", ROUND_1)
        said: list[str] = []
        protocol.step(project, "helper", Board(kan7()), said.append)
        assert mailbox.read(project, "lead", mailbox.OUTBOX) == []
        assert any("does not refine" in line for line in said)

    def test_his_reply_is_read_when_it_is_delivered(self, project, key):
        board = Board(kan7())
        protocol.request(project, "lead", "KAN-7", ROUND_1)
        protocol.step(project, "lead", board, lambda _m: None)
        (question,) = mailbox.read(project, "lead", mailbox.OUTBOX)
        first = " ".join(question.text.splitlines()[0].split())
        label = f'rite\'s reply "{first[:40]}" at 10:00'
        mailbox.send(
            project,
            "lead",
            mailbox.INBOX,
            slack._header(
                "Owner's DM",
                "sent Tue 10:05",
                f"reply in the thread under {label}",
                "addressed",
                "INSTRUCTION",
            )
            + "\n"
            + slack._quoted("the http one in main.py. just make it a flag"),
        )
        delivered = mailbox.take(project, "lead", mailbox.INBOX)
        lines = protocol.step(
            project, "lead", board, lambda _m: None, messages=delivered
        )
        assert any("the User answered round 1" in line for line in lines), lines
        assert rounds.load(project, "lead", "KAN-7").answers


def test_a_supervisor_puts_what_his_replies_did_in_the_same_instruction(
    project, monkeypatch
):
    """Not the next one: the reply and what rite made of it arrive together,
    so the Owner never works from a reply rite has already acted on."""
    prompts: list[str] = []

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
        prompts.append(prompt)
        return StartResult(True, "ok", session="fake-1", attach="")

    monkeypatch.setattr(
        supervise_mod, "liveness", lambda _n: Liveness(False, known=True)
    )
    monkeypatch.setattr(supervise_mod, "was_attached", lambda _n: False)
    monkeypatch.setattr(
        supervise_mod, "ending", lambda _n, human_was_present, pane="": Ending(FINISHED)
    )
    mailbox.send(project, "lead", mailbox.INBOX, "KAN-7 ok")
    seen: list = []

    def refine(say, messages=()):
        seen.append(len(messages))
        return ["KAN-7 refined: record abc"] if messages else []

    supervise(
        project,
        "lead",
        engine="claude",
        max_sessions=1,
        window_seconds=0,
        starter=starter,
        resume_id_for=lambda _r, _m, _s=0.0: "",
        poll=0,
        refine=refine,
    )
    assert 1 in seen, "the delivered message reached the refinement step"
    assert "## Refinement: what the User's replies did (rite)" in prompts[0]
    assert "- KAN-7 refined: record abc" in prompts[0]
    assert 0 in seen, "and the boundary pass ran too"


def test_a_refinement_step_that_raises_does_not_end_the_run(project, monkeypatch):
    said: list[str] = []

    def refine(say, messages=()):
        raise RuntimeError("board down")

    supervise_mod._refinement_step(refine, said.append)
    assert supervise_mod._refinement_heard(refine, ["m"], said.append) == ""
    assert len(said) == 2 and all("board down" in s for s in said)


def test_the_status_of_a_ticket_in_refinement_is_the_boards(project, key):
    """Nothing here makes a ticket REFINED except the record on the board."""
    board = Board(kan7())
    protocol.request(project, "lead", "KAN-7", ROUND_1)
    protocol.step(project, "lead", board, lambda _m: None)
    assert st.status(board, "KAN-7").state == st.NOT_REFINED
    assert CONFIG  # the project fixture's two-Manager root


class TestAPersonReopens:
    def _reopen(self, monkeypatch, *args, as_manager=""):
        if as_manager:
            monkeypatch.setenv(MANAGER_ENV, as_manager)
        else:
            monkeypatch.delenv(MANAGER_ENV, raising=False)
        return CliRunner().invoke(cli, ["refine", "reopen", *args])

    def test_a_parked_ticket_starts_again_from_round_one_keeping_answers(
        self, project, monkeypatch
    ):
        with rounds.locked(project, "lead", "KAN-7") as (_a, save):
            save(
                rounds.Attempt(
                    ticket="KAN-7",
                    text_sha256="t",
                    rounds=[
                        rounds.Round(k=1, sent_at=1.0, deadline=2.0, proposal=False)
                    ],
                    parked=rounds.NOT_ANSWERED,
                    misses=1,
                    answers=[{"id": "m1", "words": "the http one", "at": 1.5}],
                )
            )
        got = self._reopen(monkeypatch, "KAN-7")
        assert got.exit_code == 0, got.output
        assert "reopened (it was not answered after N messages)" in got.output
        attempt = rounds.load(project, "lead", "KAN-7")
        assert (attempt.parked, attempt.misses, attempt.rounds) == ("", 0, [])
        assert attempt.answers[0]["words"] == "the http one"

    def test_a_manager_cannot_reopen(self, project, monkeypatch):
        got = self._reopen(monkeypatch, "KAN-7", as_manager="lead")
        assert got.exit_code == 1 and "a person's decision" in got.output

    def test_nothing_to_reopen_is_said(self, project, monkeypatch):
        got = self._reopen(monkeypatch, "KAN-8")
        assert got.exit_code == 0 and "no refinement to reopen" in got.output


def test_a_round_about_his_message_is_queued_under_the_message(project, monkeypatch):
    monkeypatch.setenv(MANAGER_ENV, "lead")
    got = CliRunner().invoke(
        cli,
        ["refine", "ask", "--message", "1790000000000_1_000000000000", "-"],
        input=ROUND_1,
    )
    assert got.exit_code == 0, got.output
    (queued,) = protocol.take(project, "lead")
    assert queued["ticket"] == "message-1790000000000_1_000000000000"


def test_refinement_runs_wherever_routing_runs():
    """`rite start` composes the two, so a round goes out mid-cycle and in
    the waits, not only at a cycle's end."""
    from rite_ai.cli.main import _with_refinement

    calls: list[str] = []
    step = _with_refinement(
        lambda say: calls.append("route"), lambda say: calls.append("refine")
    )
    step(lambda _m: None)
    assert calls == ["route", "refine"]
    alone = _with_refinement(None, lambda say: calls.append("refine only"))
    alone(lambda _m: None)
    assert calls[-1] == "refine only"
    assert _with_refinement(None, None) is None
