"""S22b: a refinement round can be answered at the terminal, and every answer,
whichever channel it came by, is attributed to the one owner identity in one
shape.

The channels: a reply in the round's thread in the Owner's DM, the same in the
refinement channel (both relayed as INSTRUCTION), a message written on this
machine (`rite message`: no rite header), and `rite refine answer`. Each must
leave the same `{"id", "words", "at", "by"}` answer, `by` naming
`slack.owner_user` and the channel; an accept word must carry the same `by`
into the signed record's provenance, and the record must say a terminal answer
is one rite cannot tell from a session running as the person.

Real key, real predicate; only the board is in memory.
"""

from __future__ import annotations

import itertools

import pytest
from click.testing import CliRunner

from rite_ai.managers import mailbox, slack
from rite_ai.refinement import attribution, protocol, rounds
from rite_ai.refinement import record as rec
from rite_ai.refinement import status as st
from tests.test_the_owner_refines_with_the_user import (
    HOUR,
    LIMITS,
    NOW,
    OWNER,
    ROUND_2,
    Board,
    kan7,
    key,
    outbox,
    relayed_in_thread,
    send,
)

__all__ = ["key"]  # a fixture, used by name below

OWNER_ID = "U0OWNER1"


def _config(root, owner_user):
    (root / ".rite").mkdir(exist_ok=True)
    lines = ["coordination:", f"  managers: [{OWNER}]"]
    if owner_user:
        lines += ["slack:", f"  owner_user: {owner_user}"]
    (root / ".rite" / "config.yaml").write_text("\n".join(lines) + "\n")
    (root / ".rite" / "modules.yaml").write_text("modules: []\n")


def _to_round_two(root, board):
    """Round 1, answered in Slack, then round 2's proposal."""
    assert send(root, board).ok
    answered = _reply(root, board, "dm", "the http one in main.py. just make it a flag")
    assert answered is not None
    assert send(root, board, text=ROUND_2, now=NOW + 2 * HOUR).ok


def _question_line(root) -> str:
    (q,) = [m for m in outbox(root) if m.kind == mailbox.QUESTION][-1:]
    return q.text.splitlines()[0]


def _reply(root, board, route, words, *, sent_at=NOW + HOUR):
    """Answer the latest round by `route`, through rite's own path for it."""
    if route == "cli":
        from rite_ai.cli.main import cli

        result = CliRunner().invoke(
            cli,
            ["refine", "answer", "KAN-7", *words.split()],
            env={"RITE_PROJECT_ROOT": str(root)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        return result
    if route == "dm":
        text = relayed_in_thread(_question_line(root), words)
    elif route == "channel":
        text = _in_the_refinement_channel(_question_line(root), words)
    elif route == "machine":
        text = f"KAN-7 {words}"
    else:
        raise AssertionError(route)
    reply = protocol.attribute(root, OWNER, text, message=f"m-{route}", sent_at=sent_at)
    assert reply is not None, (route, text)
    return protocol.handle(root, OWNER, board, reply, limits=LIMITS)


def _in_the_refinement_channel(question_first_line: str, words: str) -> str:
    """What the relay delivers for the Owner's reply in a refinement thread
    in the private channel (TRQ8): the DM's shape, under the channel's name."""
    label = f'rite\'s reply "{" ".join(question_first_line.split())[:40]}" at 10:00'
    return (
        slack._header(
            "refinement channel",
            "sent Tue 10:05",
            f"reply in the thread under {label}",
            "addressed",
            "INSTRUCTION",
        )
        + "\n"
        + slack._quoted(words)
    )


VIA = {
    "dm": attribution.SLACK,
    "channel": attribution.SLACK,
    "machine": attribution.TERMINAL,
    "cli": attribution.TERMINAL,
}


@pytest.fixture
def cli_board(monkeypatch):
    """The board `rite refine answer` builds is the test's own."""
    holder = {}

    def board_for(root, config, role="workers"):
        return holder["board"]

    monkeypatch.setattr(st, "board_for", board_for)
    return holder


class TestTheAttribution:
    def test_one_shape_whatever_the_channel(self):
        assert attribution.answered_by("U1", "slack") == {
            "owner_user": "U1",
            "via": "slack",
        }
        assert attribution.answered_by("", "terminal") == {
            "owner_user": "",
            "via": "terminal",
        }
        with pytest.raises(ValueError):
            attribution.answered_by("U1", "email")

    def test_a_terminal_answer_never_reads_as_slack(self):
        said = attribution.describe(attribution.answered_by("U1", "terminal"))
        assert "terminal" in said and "cannot tell the person" in said
        assert "Slack user U1" in said and "in Slack" not in said
        assert attribution.describe(attribution.answered_by("U1", "slack")) == (
            "the owner (Slack user U1) in Slack"
        )

    def test_a_record_from_before_s22b_keeps_its_wording(self):
        assert rec.how_agreed({"kind": rec.ACCEPTED, "message": "m"}) == (
            "accepted by the User in their channel"
        )


class TestRiteRefineAnswer:
    def test_an_answer_at_the_terminal_is_kept_like_a_slack_answer(
        self, tmp_path, key, cli_board
    ):
        _config(tmp_path, OWNER_ID)
        board = cli_board["board"] = Board(kan7())
        assert send(tmp_path, board).ok
        _reply(tmp_path, board, "cli", "the http one in main.py")
        (answer,) = rounds.load(tmp_path, OWNER, "KAN-7").answers
        assert set(answer) == {"id", "words", "at", "by"}
        assert answer["words"] == "the http one in main.py"
        assert answer["by"] == {"owner_user": OWNER_ID, "via": "terminal"}
        assert rounds.load(tmp_path, OWNER, "KAN-7").latest.answered

    def test_ok_at_the_terminal_writes_the_record_as_the_owners(
        self, tmp_path, key, cli_board
    ):
        _config(tmp_path, OWNER_ID)
        board = cli_board["board"] = Board(kan7())
        _to_round_two(tmp_path, board)
        _reply(tmp_path, board, "cli", "ok")
        got = st.status(board, "KAN-7")
        assert got.state == st.REFINED
        assert got.record.provenance["by"] == {
            "owner_user": OWNER_ID,
            "via": "terminal",
        }
        shown = board.comments["KAN-7"][-1]
        assert "at this machine's terminal" in shown and "cannot tell" in shown

    def test_it_is_refused_in_a_managers_session(self, tmp_path, key, monkeypatch):
        from rite_ai.cli.main import cli

        _config(tmp_path, OWNER_ID)
        monkeypatch.setattr("rite_ai.managers.current_manager", lambda: "lead")
        result = CliRunner().invoke(
            cli,
            ["refine", "answer", "KAN-7", "ok"],
            env={"RITE_PROJECT_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1 and "Manager's session" in result.output
        assert rounds.load(tmp_path, OWNER, "KAN-7") is None

    def test_a_ticket_with_no_round_is_said(self, tmp_path, key, cli_board):
        from rite_ai.cli.main import cli

        _config(tmp_path, OWNER_ID)
        cli_board["board"] = Board(kan7())
        result = CliRunner().invoke(
            cli,
            ["refine", "answer", "KAN-7", "ok"],
            env={"RITE_PROJECT_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 1 and "no refinement round" in result.output


class TestAMessageFromThisMachine:
    def test_its_ok_is_recorded_as_the_terminal_not_their_channel(self, tmp_path, key):
        """Before S22b, `rite message lead "KAN-7 ok"` wrote a record saying
        "accepted by the User in their channel"."""
        _config(tmp_path, OWNER_ID)
        board = Board(kan7())
        _to_round_two(tmp_path, board)
        _reply(tmp_path, board, "machine", "ok", sent_at=NOW + 3 * HOUR)
        got = st.status(board, "KAN-7")
        assert got.record.provenance["by"]["via"] == "terminal"
        assert "in their channel" not in board.comments["KAN-7"][-1]


# --- the invariant, over every route, both kinds of answer, owner set or not ---


@pytest.mark.parametrize(
    "route,owner_user",
    list(itertools.product(("dm", "channel", "machine", "cli"), (OWNER_ID, ""))),
)
def test_every_answer_and_every_accept_is_the_owners_in_one_shape(
    tmp_path, key, cli_board, route, owner_user
):
    _config(tmp_path, owner_user)
    board = cli_board["board"] = Board(kan7())
    expected = attribution.answered_by(owner_user, VIA[route])

    # An answer.
    assert send(tmp_path, board).ok
    _reply(tmp_path, board, route, "the http one in main.py. just make it a flag")
    (answer,) = rounds.load(tmp_path, OWNER, "KAN-7").answers
    assert set(answer) == {"id", "words", "at", "by"}
    assert answer["by"] == expected

    # An accept, under the next round's proposal.
    assert send(tmp_path, board, text=ROUND_2, now=NOW + 2 * HOUR).ok
    _reply(tmp_path, board, route, "ok", sent_at=NOW + 3 * HOUR)
    got = st.status(board, "KAN-7")
    assert got.state == st.REFINED, got.detail
    assert got.record.provenance["kind"] == rec.ACCEPTED
    assert got.record.provenance["by"] == expected
    assert got.record.provenance["by"]["owner_user"] == owner_user
    said = rec.how_agreed(got.record.provenance)
    assert ("terminal" in said) == (VIA[route] == attribution.TERMINAL)
    assert ("in Slack" in said) == (VIA[route] == attribution.SLACK)
    assert "in their channel" not in said
