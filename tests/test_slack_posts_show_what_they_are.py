"""S29: every Slack post says what it is, and none runs into the next.

Live run (Robert): a Manager's question needing his answer, status
paragraphs and a system delivery warning stacked under one bot avatar and
timestamp, and read as one wall; the question looked like status. So each
post opens with a type tag naming its author, needs-answer posts are shown
loud (a section) and everything else muted (a context block), and every post
ends in a divider, so two consecutive posts are always separated.

⚠ **The tag is not decided from the text.** It is `pending.kind_of`, the same
decision as what rite tracks as waiting on the person, so a post that looks
like it needs an answer is exactly one that stays pending until it gets one.
The invariant below holds that across every sequence of up to three posts of
six kinds, by one author and by two.
"""

from __future__ import annotations

import itertools

import pytest

from rite_ai.managers import checkins, pending
from rite_ai.managers.mailbox import CHECKIN, OUTBOX, QUESTION, REPLY, send
from rite_ai.managers.slack import (
    BLOCKS_MAX,
    DELIVERY,
    LOUD,
    NEEDS_ANSWER,
    NEEDS_YOU,
    SECTION_CHARS,
    STATUS,
    SYSTEM,
    TAGS,
    Listener,
    _present,
    _ticket_in,
)
from tests.test_slack_posts_what_the_manager_says import Slack

OWNER = "U0WNER"
ASKING_LINE = "KAN-7 · q1a2b · Manager lead is waiting · reply in this thread"


def _text(block: dict) -> str:
    if block["type"] == "section":
        return block["text"]["text"]
    if block["type"] == "context":
        return block["elements"][0]["text"]
    return ""


def _tag_of(post: dict) -> str:
    """The one kind whose tag the post's first block carries."""
    first = _text(post["blocks"][0])
    found = [k for k, tag in TAGS.items() if first.startswith(tag)]
    assert len(found) == 1, f"not exactly one tag: {first!r}"
    return found[0]


# --- the pieces ----------------------------------------------------------------


class TestPresent:
    @pytest.mark.parametrize("kind", sorted(TAGS))
    def test_each_kind_has_its_tag_its_author_and_a_divider(self, kind):
        blocks = _present(kind, "the body", author="lead")
        assert _text(blocks[0]) == f"{TAGS[kind]} · lead"
        assert _text(blocks[1]) == "the body"
        assert blocks[-1] == {"type": "divider"}
        loud = {b["type"] for b in blocks[:-1]} == {"section"}
        muted = {b["type"] for b in blocks[:-1]} == {"context"}
        assert loud == (kind in LOUD) and muted == (kind not in LOUD)

    def test_a_needs_answer_post_names_its_ticket(self):
        blocks = _present(NEEDS_ANSWER, "which timeout?", author="lead", ticket="KAN-7")
        assert _text(blocks[0]) == "❓ *Needs your answer* · `KAN-7` · lead"

    def test_the_loud_kinds_are_exactly_the_ones_needing_the_person(self):
        assert LOUD == {NEEDS_ANSWER, NEEDS_YOU}
        assert {STATUS, SYSTEM, DELIVERY}.isdisjoint(LOUD)

    def test_a_long_body_is_split_under_slacks_limits_and_capped(self):
        body = ("x" * 100 + "\n") * 2000
        blocks = _present(STATUS, body, author="lead")
        assert len(blocks) <= BLOCKS_MAX
        assert all(len(_text(b)) <= SECTION_CHARS for b in blocks)
        assert "`rite replies` has all of it" in _text(blocks[-2])

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValueError):
            _present("urgent", "x")

    def test_the_ticket_is_read_from_rites_own_first_line(self):
        assert _ticket_in(f"{ASKING_LINE}\nwhich one?") == "KAN-7"
        assert _ticket_in("KAN-7 is merged") == ""
        assert _ticket_in("") == ""


# --- through the relay ----------------------------------------------------------


def _listener(root, slack, manager="lead", status=""):
    (root / ".rite").mkdir(exist_ok=True)
    listener = Listener(
        token="t",
        manager=manager,
        owner=OWNER,
        broadcast="#all-rite",
        status=status,
        project=root,
        clock=lambda: 150.0,
    )
    listener.open(call=slack)
    listener.post_replies(call=slack)
    return listener


KINDS = {
    # name: (what the Manager writes, the tag the post must carry)
    "question": ("question", NEEDS_ANSWER),
    "question-no-ticket": ("question-no-ticket", NEEDS_ANSWER),
    "unknown": ("unknown", NEEDS_YOU),
    "reply": ("reply", STATUS),
    "checkin-asking": ("checkin-asking", NEEDS_ANSWER),
    "checkin-quiet": ("checkin-quiet", STATUS),
}


def _write(root, manager, kind, n):
    if kind == "question":
        return send(root, manager, OUTBOX, f"{ASKING_LINE}\nq{n}?", kind=QUESTION)
    if kind == "question-no-ticket":
        return send(root, manager, OUTBOX, f"which way, {n}?", kind=QUESTION)
    if kind == "unknown":
        return send(root, manager, OUTBOX, f"something {n}")
    if kind == "reply":
        return send(root, manager, OUTBOX, f"status {n}", kind=REPLY)
    path = send(root, manager, OUTBOX, f"*Check-in* {n}", kind=CHECKIN)
    checkins.record(
        root,
        manager,
        {
            "event": "checkin",
            "at": 1.0,
            "outbox": path.name,
            "questions": 2 if kind == "checkin-asking" else 0,
        },
    )
    return path


def test_start_and_stop_lines_are_rites_own_and_muted(tmp_path):
    """RS1: starts and stops are rite's own SYSTEM posts (muted), said in the
    status channel, never the DM. The one stop line kept in the DM is the
    undelivered warning, and it is a DELIVERY post."""
    slack = Slack()
    listener = _listener(tmp_path, slack, status="#rite-status")
    listener.close(call=slack)
    listener.close(call=slack, undelivered="a message you sent was not delivered")
    assert SYSTEM not in LOUD, "start and stop lines are muted"
    status_lines = [
        p
        for p in slack.posts
        if "is running" in p["text"]
        or ("has stopped" in p["text"] and "not delivered" not in p["text"])
    ]
    assert status_lines, "start and stop lines are posted to the status channel"
    assert all(_tag_of(p) == SYSTEM for p in status_lines)
    assert all(p["channel"].startswith("#") for p in status_lines), (
        "in the status channel, not the DM"
    )
    dm = [p for p in slack.posts if "not delivered" in p["text"]]
    assert len(dm) == 1 and _tag_of(dm[0]) == DELIVERY


def test_a_check_in_mirror_is_muted_whatever_the_dm_copy_is(tmp_path):
    """An answer under the broadcast mirror is context, not the answer the DM
    copy waits for, so the mirror must not look like it needs one."""
    slack = Slack()
    listener = _listener(tmp_path, slack)
    _write(tmp_path, "lead", "checkin-asking", 1)
    listener.post_replies(call=slack)
    dm, mirror = slack.posts[-2], slack.posts[-1]
    assert _tag_of(dm) == NEEDS_ANSWER
    assert _tag_of(mirror) == STATUS and mirror["channel"] == "C1"


SEQUENCES = [
    seq for n in (1, 2, 3) for seq in itertools.product(sorted(KINDS), repeat=n)
]


@pytest.mark.parametrize("authors", ["one author", "two authors, alternating"])
@pytest.mark.parametrize("sequence", SEQUENCES, ids="+".join)
def test_every_post_says_what_it_is_and_ends_in_a_divider(tmp_path, sequence, authors):
    """The invariant, over a single post, runs of one author's posts, and two
    authors interleaved in one conversation:

    * every post carries exactly one tag, naming its author, and ends in a
      divider, so no two consecutive posts run together;
    * it is loud exactly when it needs the person;
    * the tag is what that message IS (`KINDS`), and a post carries a
      needs-answer tag exactly when `pending` is waiting on its answer.
    """
    slack = Slack()
    names = ["lead", "planner"] if authors.startswith("two") else ["lead"]
    listeners = {m: _listener(tmp_path, slack, m) for m in names}
    started = len(slack.posts)
    expected: dict[str, tuple[str, str]] = {}
    for n, kind in enumerate(sequence):
        manager = names[n % len(names)]
        path = _write(tmp_path, manager, kind, n)
        expected[path.name] = (manager, KINDS[kind][1])
        listeners[manager].post_replies(call=slack)

    for post in slack.posts:
        tag = _tag_of(post)
        first = _text(post["blocks"][0])
        assert first.endswith(tuple(names)), f"no author: {first!r}"
        assert post["blocks"][-1] == {"type": "divider"}
        body_types = {b["type"] for b in post["blocks"][:-1]}
        assert body_types == ({"section"} if tag in LOUD else {"context"})

    # The fake Slack answers each post with ts "<100 + its position>.0".
    for manager, listener in listeners.items():
        waiting = {i.name for i in pending.waiting(tmp_path, manager)}
        for name, record in listener.posted().items():
            if name not in expected:
                continue
            post = slack.posts[int(float(record["ts"])) - 101]
            want_manager, want_tag = expected[name]
            assert want_manager == manager
            assert _tag_of(post) == want_tag, (name, sequence)
            assert (want_tag in LOUD) == (name in waiting), (
                "a post looks like it needs the person exactly when rite waits on it"
            )
            named = "`KAN-7`" in _text(post["blocks"][0])
            assert named == (
                want_tag == NEEDS_ANSWER and ASKING_LINE in post["text"]
            ), "the ticket is named exactly on a question that names one"
    assert slack.posts[started:], "something was posted"
