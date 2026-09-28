"""TR1: a ticket is refined only by a signed record that matches it (the note's
part 3.2), and every other state fails closed and says why.

Each negative case asserts the SPECIFIC state, because "not refined" is not one
answer: an UNREADABLE ticket needs a person, a STALE one needs asking again, a
CONFLICT needs a decision. Collapsing them is how a stop stops saying why.
"""

from __future__ import annotations

import json
import os
import stat
from unittest.mock import patch

import pytest

from rite_ai.refinement import key as refinement_key
from rite_ai.refinement import record as rec
from rite_ai.refinement import status
from rite_ai.tickets import BackendError, Comment, Thread, Ticket
from rite_ai.tickets.github import GitHubBackend

BOARD = {"type": "github", "repo": "org/repo"}
KEY = b"k" * 32
OTHER_KEY = b"x" * 32


def _ticket(title="Timeout is too long", description="make it configurable"):
    return Ticket(id="7", title=title, description=description)


def _record(
    ticket=None,
    *,
    supersedes=None,
    dod=("the timeout is a flag",),
    key=KEY,
    provenance=None,
    board=BOARD,
):
    t = ticket or _ticket()
    return rec.build(
        ticket=t.id,
        board=board,
        title=t.title,
        description=t.description,
        definition_of_done=list(dod),
        verify=rec.NONE_AGREED,
        provenance=provenance or {"kind": rec.ACCEPTED, "message": "m1"},
        supersedes=supersedes,
        key=key,
    )


def _thread(*bodies, ticket=None, complete=True):
    return Thread(
        ticket or _ticket(),
        [Comment(id=str(i), body=b, author="rite") for i, b in enumerate(bodies)],
        complete,
    )


def _eval(thread, key=KEY):
    return status.evaluate("7", BOARD, thread, refinement_key.Loaded(key))


def test_a_signed_record_that_matches_the_ticket_is_refined():
    r = _record()
    result = _eval(_thread("an unrelated comment", rec.render(r)))
    assert result.state == status.REFINED, result.detail
    assert result.head.definition_of_done == ("the timeout is a flag",)


def test_no_record_is_not_refined():
    assert _eval(_thread("looks good", "ship it")).state == status.NOT_REFINED


def test_a_ticket_whose_description_changed_is_stale():
    r = _record()
    edited = _ticket(description="make it configurable, default 10s")
    result = _eval(_thread(rec.render(r), ticket=edited))
    assert result.state == status.STALE
    assert "description" in result.detail


def test_a_ticket_whose_title_changed_is_stale():
    r = _record()
    result = _eval(_thread(rec.render(r), ticket=_ticket(title="Other")))
    assert result.state == status.STALE and "title" in result.detail


def test_a_record_signed_with_another_key_is_unreadable_not_ignored():
    """A Manager holding a board token can post a record-shaped comment. It
    must not make the ticket refined, and it must not be skipped silently."""
    forged = _record(key=OTHER_KEY)
    result = _eval(_thread(rec.render(forged)))
    assert result.state == status.UNREADABLE
    assert "signature" in result.detail


def test_an_edited_record_is_unreadable_even_with_a_valid_older_one():
    """Skipping the edited head would promote the older record, and hand a
    Worker a superseded definition of done without saying so."""
    old = _record()
    new = _record(
        dod=("the timeout is a flag", "default unchanged"),
        supersedes=old.record_id,
        provenance={"kind": rec.ACCEPTED, "message": "m2"},
    )
    tampered = rec.render(new).replace("default unchanged", "default 1s")
    result = _eval(_thread(rec.render(old), tampered))
    assert result.state == status.UNREADABLE


def test_a_marker_with_no_readable_record_is_unreadable():
    result = _eval(_thread(f"```{rec.MARKER}\nnot json\n```"))
    assert result.state == status.UNREADABLE


def test_two_heads_are_a_conflict_never_latest_wins():
    a = _record(provenance={"kind": rec.ACCEPTED, "message": "a"})
    b = _record(provenance={"kind": rec.ATTESTED, "session": "b"})
    result = _eval(_thread(rec.render(a), rec.render(b)))
    assert result.state == status.CONFLICT


def test_a_superseding_record_is_the_head():
    old = _record()
    new = _record(
        dod=("the timeout is a --timeout flag",),
        supersedes=old.record_id,
        provenance={"kind": rec.ACCEPTED, "message": "m2"},
    )
    result = _eval(_thread(rec.render(old), rec.render(new)))
    assert result.state == status.REFINED
    assert result.head.record_id == new.record_id


def test_the_same_record_twice_is_one_record_not_two_heads():
    """A retried write after a lost response posts the same record again."""
    r = _record()
    assert _eval(_thread(rec.render(r), rec.render(r))).state == status.REFINED


def test_a_record_for_another_ticket_does_not_transfer():
    other = Ticket(
        id="8", title="Timeout is too long", description="make it configurable"
    )
    r = _record(ticket=other)
    result = _eval(_thread(rec.render(r)))
    assert result.state == status.UNREADABLE and "'8'" in result.detail


def test_a_record_for_another_board_does_not_transfer():
    r = _record(board={"type": "github", "repo": "someone/else"})
    assert _eval(_thread(rec.render(r))).state == status.UNREADABLE


def test_an_incomplete_comment_list_is_unreadable_not_unrefined():
    result = _eval(_thread("hello", complete=False))
    assert result.state == status.UNREADABLE


def test_a_board_that_cannot_be_read_is_unreadable():
    result = _eval(BackendError("gh exited 1"))
    assert result.state == status.UNREADABLE


def test_records_but_no_key_is_unreadable():
    r = _record()
    result = status.evaluate(
        "7", BOARD, _thread(rec.render(r)), refinement_key.Loaded(None)
    )
    assert result.state == status.UNREADABLE


def test_no_records_and_no_key_is_simply_not_refined():
    """A machine that has signed nothing still answers from the board."""
    result = status.evaluate("7", BOARD, _thread("hi"), refinement_key.Loaded(None))
    assert result.state == status.NOT_REFINED


@pytest.mark.parametrize(
    "dod, verify, provenance",
    [
        ([], rec.NONE_AGREED, {"kind": rec.ACCEPTED}),
        (["  "], rec.NONE_AGREED, {"kind": rec.ACCEPTED}),
        (["x"], [], {"kind": rec.ACCEPTED}),
        (["x"], rec.NONE_AGREED, {"kind": "the model said so"}),
    ],
)
def test_a_record_that_could_not_be_valid_is_refused_at_build(dod, verify, provenance):
    with pytest.raises(ValueError):
        rec.build(
            ticket="7",
            board=BOARD,
            title="t",
            description="d",
            definition_of_done=dod,
            verify=verify,
            provenance=provenance,
            supersedes=None,
            key=KEY,
        )


def test_the_record_survives_whitespace_being_rewritten():
    """Jira flattens a comment on read (unmeasured, TR0); the MAC is over the
    parsed payload, so reflowed whitespace still verifies."""
    r = _record()
    body = rec.render(r)
    block = body[body.index("{") : body.rindex("}") + 1]
    reflowed = body.replace(block, json.dumps(json.loads(block)))
    assert _eval(_thread(reflowed)).state == status.REFINED


# --- the key -------------------------------------------------------------


@pytest.fixture
def key_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(refinement_key.KEY_DIR_ENV, str(tmp_path / "refinement"))
    return tmp_path / "refinement"


def test_reading_never_creates_the_key(key_dir):
    assert refinement_key.load().key is None
    assert not key_dir.exists()


def test_the_key_is_created_once_private_and_whole(key_dir):
    first = refinement_key.ensure()
    assert len(first) == refinement_key.KEY_BYTES
    assert refinement_key.ensure() == first
    mode = stat.S_IMODE(os.stat(refinement_key.key_path()).st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(os.stat(key_dir).st_mode) == 0o700
    assert [p.name for p in key_dir.iterdir()] == ["key"], "a temporary file was left"


def test_a_writer_losing_the_race_uses_the_winners_key(key_dir, monkeypatch):
    """The link that publishes the key refuses when one already exists."""
    key_dir.mkdir(mode=0o700)
    winner = os.urandom(refinement_key.KEY_BYTES)
    real_link = os.link

    def racing_link(src, dst):
        refinement_key.key_path().write_bytes(winner)
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", racing_link)
    assert refinement_key.ensure() == winner


def test_a_damaged_key_is_a_problem_not_a_new_key(key_dir):
    key_dir.mkdir(mode=0o700)
    refinement_key.key_path().write_bytes(b"short")
    loaded = refinement_key.load()
    assert loaded.key is None and "damaged" in loaded.problem
    with pytest.raises(OSError):
        refinement_key.ensure()


# --- reading a GitHub issue's whole thread -------------------------------


def _page(nodes, total, next_cursor=""):
    return json.dumps(
        {
            "data": {
                "repository": {
                    "issue": {
                        "number": 7,
                        "title": "Timeout",
                        "state": "OPEN",
                        "body": "b",
                        "url": "u",
                        "createdAt": "2026-09-28T10:00:00Z",
                        "updatedAt": "2026-09-28T10:00:00Z",
                        "labels": {"nodes": [{"name": "scheduled"}]},
                        "comments": {
                            "totalCount": total,
                            "pageInfo": {
                                "hasNextPage": bool(next_cursor),
                                "endCursor": next_cursor,
                            },
                            "nodes": nodes,
                        },
                    }
                }
            }
        }
    )


def _nodes(start, n):
    return [
        {"databaseId": i, "body": f"c{i}", "createdAt": None, "author": {"login": "p"}}
        for i in range(start, start + n)
    ]


@patch("rite_ai.tickets.github._run_gh")
def test_github_pages_until_the_count_matches(mock_gh):
    mock_gh.side_effect = [
        _page(_nodes(0, 100), 148, "c1"),
        _page(_nodes(100, 48), 148),
    ]
    thread = GitHubBackend("org/repo").read_thread("7")
    assert thread.complete and len(thread.comments) == 148
    assert thread.ticket.labels == ["scheduled"]
    second = mock_gh.call_args_list[1].args[0]
    assert "after=c1" in second


@patch("rite_ai.tickets.github._run_gh")
def test_github_counts_that_disagree_are_not_complete(mock_gh):
    mock_gh.side_effect = [_page(_nodes(0, 99), 100)]
    thread = GitHubBackend("org/repo").read_thread("7")
    assert not thread.complete and "100" in thread.note


@patch("rite_ai.tickets.github._run_gh")
def test_github_repository_names_are_sent_as_strings(mock_gh):
    mock_gh.side_effect = [_page([], 0)]
    GitHubBackend("org/2024").read_thread("7")
    args = mock_gh.call_args.args[0]
    assert args[args.index("name=2024") - 1] == "-f"


@patch("rite_ai.tickets.github._run_gh")
def test_github_error_propagates(mock_gh):
    mock_gh.return_value = BackendError("gh exited 1: auth")
    assert isinstance(GitHubBackend("org/repo").read_thread("7"), BackendError)


def test_github_refuses_a_ticket_that_is_not_an_issue_number():
    assert isinstance(GitHubBackend("org/repo").read_thread("KAN-7"), BackendError)
