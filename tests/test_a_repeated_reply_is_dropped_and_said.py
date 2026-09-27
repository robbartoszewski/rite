"""A byte-identical repeat of a reply, for the same routed work, is dropped,
and that is SAID and COUNTED (W15 (c), Robert 2026-09-27).

Observed on Linux: a secondary sent "HELLO.txt written" three times, and each
started an Owner session. The constraints matter more than the feature:

- never silent: a drop is said, counted, and shown in the standup, because a
  Manager repeating itself is a signal about that Manager, not noise;
- scoped to the most recent route delivered to that secondary: the same words
  for later work are a legitimate reply;
- byte-identical only: anything cleverer is a judgement about meaning.

⚠ It is not a fix for honesty. When a false reply was followed by a
correction, the two differed, so both must be (and are) delivered.
"""

from __future__ import annotations

from rite_ai.managers import checkins, mailbox, routing, standup

OWNER, SECONDARY = "lead", "small"
NAMES = [OWNER, SECONDARY]


def _route(root, text="write HELLO.txt"):
    routing.request(root, OWNER, SECONDARY, text)
    routing.deliver_routes(root, OWNER, OWNER, NAMES, lambda _m: None)


def _collect(root):
    said: list[str] = []
    routing.collect_reports(root, OWNER, NAMES, said.append)
    return said


def _delivered(root):
    return [
        m.text
        for m in mailbox.read(root, OWNER, mailbox.INBOX)
        if m.text.startswith(routing.REPORT_HEADER_START)
    ]


def test_a_repeat_for_the_same_route_is_delivered_once_and_the_drop_is_said(
    tmp_path,
):
    _route(tmp_path)
    for _ in range(3):
        mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "HELLO.txt written")
    said = _collect(tmp_path)
    assert len(_delivered(tmp_path)) == 1
    drops = [line for line in said if line.startswith("dropped a duplicate reply")]
    assert len(drops) == 2, said


def test_a_repeat_arriving_later_is_dropped_too(tmp_path):
    _route(tmp_path)
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "HELLO.txt written")
    _collect(tmp_path)
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "HELLO.txt written")
    said = _collect(tmp_path)
    assert len(_delivered(tmp_path)) == 1
    assert any(line.startswith("dropped a duplicate reply") for line in said)


def test_a_false_reply_and_its_correction_are_both_delivered(tmp_path):
    """The A6 experiment 2 case. Dedup must NOT be read as a fix for it."""
    _route(tmp_path)
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "Created TOP.txt with 'top'")
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "failed to create TOP.txt")
    _collect(tmp_path)
    assert len(_delivered(tmp_path)) == 2


def test_the_same_words_for_new_work_are_a_new_reply(tmp_path):
    _route(tmp_path, "first task")
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "done")
    _collect(tmp_path)
    _route(tmp_path, "second task")
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "done")
    _collect(tmp_path)
    assert len(_delivered(tmp_path)) == 2


def test_only_byte_identical_counts(tmp_path):
    _route(tmp_path)
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "HELLO.txt written")
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "HELLO.txt written.")
    _collect(tmp_path)
    assert len(_delivered(tmp_path)) == 2


def test_the_standup_counts_replies_and_drops(tmp_path):
    _route(tmp_path)
    for _ in range(3):
        mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "HELLO.txt written")
    _collect(tmp_path)
    kinds = [e.get("event") for e in checkins.ledger(tmp_path, OWNER)]
    assert kinds.count("reply") == 1 and kinds.count("duplicate_reply") == 2
    text = "\n".join(standup.digest(tmp_path, OWNER, since=0.0))
    assert (
        "replies from 'small': 3 received, 2 byte-identical duplicate(s) dropped"
        in text
    ), text


class _Stopped(BaseException):
    """What stopping `rite start` raises into the verifier: not an Exception,
    so `_verified`'s fail-closed handler does not swallow it."""


def test_a_run_stopped_while_the_verifier_runs_does_not_lose_the_reply(tmp_path):
    """⚠ **A record must not assert what has not happened yet.** The digest
    that marks a reply "already delivered" was written when the reply was
    CHECKED, before the verifier (10–60 s) and before delivery. Stopping the
    Owner's `rite start` in that gap left a record of a delivery that never
    happened, and the next start dropped the reply for good, saying "already
    delivered". Reproduced with a probe; this is that probe."""
    from rite_ai.managers.verifier import CONFIRMED, Verdict

    _route(tmp_path)
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "HELLO.txt written")

    def stopped_mid_verification(_sender, _text):
        raise _Stopped

    try:
        routing.collect_reports(
            tmp_path, OWNER, NAMES, lambda _m: None, verify=stopped_mid_verification
        )
    except _Stopped:
        pass
    assert _delivered(tmp_path) == []

    said: list[str] = []
    routing.collect_reports(
        tmp_path,
        OWNER,
        NAMES,
        said.append,
        verify=lambda _s, _t: Verdict(CONFIRMED, "checked"),
    )
    assert len(_delivered(tmp_path)) == 1, said
    assert not any(line.startswith("dropped a duplicate") for line in said), said


def test_a_reply_delivered_before_a_stop_is_not_delivered_twice(tmp_path):
    """The other side of the same boundary: once the reply IS in the Owner's
    inbox, a stop before the cursor moves must not deliver it again."""
    _route(tmp_path)
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "HELLO.txt written")
    real_mark_read = routing.mark_read

    def stopped_before_the_cursor_moves(*_a, **_k):
        raise _Stopped

    routing.mark_read = stopped_before_the_cursor_moves
    try:
        try:
            _collect(tmp_path)
        except _Stopped:
            pass
    finally:
        routing.mark_read = real_mark_read
    assert len(_delivered(tmp_path)) == 1
    said = _collect(tmp_path)
    assert len(_delivered(tmp_path)) == 1
    assert any(line.startswith("dropped a duplicate reply") for line in said), said
