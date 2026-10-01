"""TR2 (TRQ8): refinement rounds can go to a private channel rite is invited
to, and only the Owner's replies in their threads answer them.

Robert: "DMs or a dedicated private channel with @rite invited. Let's make it
configurable". The channel is a second destination, not a replacement: only
rounds go there, the check-in and everything else stay in the DM. rite uses it
only when it has both posted and read there at start (a channel it cannot read
would swallow his answer); otherwise rounds go to the DM, and it says why.
Delivery confirmation (RP1) is the same for both: a round is pending until he
replies or reacts, and an unconfirmed one comes back at the check-in, which is
in the DM.
"""

from __future__ import annotations

from rite_ai.managers import asking, delivered, pending
from rite_ai.managers.mailbox import OUTBOX, QUESTION, send
from rite_ai.managers.slack import REFINEMENT_CHANNEL, Listener
from rite_ai.refinement import protocol, rounds
from tests.test_what_needs_you_stays_pending import (
    OWNER,
    Clock,
    Slack,
    _project,
    _read_threads,
)

CHANNEL = "G012AB3CD"
DM = "D1"
"""The Owner's DM id, as the fake Slack answers `chat.postMessage` to him."""


class Workspace(Slack):
    """The fake Slack, with a private channel that may refuse a post or a
    read, as Slack does without an invitation or `groups:history`."""

    def __init__(self, *, post=True, read=True):
        super().__init__()
        self.can_post, self.can_read = post, read

    def __call__(self, method, token, params=None, payload=None):
        args = payload or params or {}
        if args.get("channel") == CHANNEL:
            if method == "chat.postMessage" and not self.can_post:
                return {"ok": False, "error": "not_in_channel"}
            if method == "conversations.history" and not self.can_read:
                return {
                    "ok": False,
                    "error": "missing_scope",
                    "needed": "groups:history",
                }
        return super().__call__(method, token, params, payload)


def _relay(root, slack, clock):
    pending.sync(root, "lead")
    listener = Listener(
        token="t",
        manager="lead",
        owner=OWNER,
        broadcast="#all-rite",
        project=root,
        clock=clock,
        refinement_channel=CHANNEL,
    )
    opened = listener.open(call=slack)
    listener.post_replies(call=slack)
    return listener, opened


def _a_round(root, clock):
    raised = asking.raise_to_person(
        root,
        "lead",
        subject="KAN-7",
        raiser="manager:lead",
        text="Refinement of KAN-7, round 1 of 3\n\n1. Which timeout?",
    )
    with rounds.locked(root, "lead", "KAN-7") as (_a, save):
        save(
            rounds.Attempt(
                ticket="KAN-7",
                text_sha256="t",
                rounds=[
                    rounds.Round(
                        k=1,
                        sent_at=clock.now,
                        deadline=clock.now + 86_400,
                        proposal=False,
                        where=raised.id,
                    )
                ],
            )
        )
    return raised.id


def _posted_to(slack, words):
    return [p["channel"] for p in slack.posts if words in p.get("text", "")]


def test_rounds_go_to_the_channel_and_everything_else_to_the_dm(tmp_path):
    root, slack, clock = _project(tmp_path), Workspace(), Clock(1000.0)
    listener, opened = _relay(root, slack, clock)
    assert any(f"refinement questions go to {CHANNEL}" in line for line in opened)
    _a_round(root, clock)
    send(root, "lead", OUTBOX, "which schema should I use", kind=QUESTION)
    listener.post_replies(call=slack)
    assert _posted_to(slack, "Refinement of KAN-7") == [CHANNEL]
    assert _posted_to(slack, "which schema") == [DM]
    # RP1: the round is tracked as pending wherever it went.
    assert any(i.channel == CHANNEL for i in pending.waiting(root, "lead"))


def test_a_channel_rite_cannot_post_to_sends_rounds_to_the_dm_and_says_why(tmp_path):
    root, slack, clock = _project(tmp_path), Workspace(post=False), Clock(1000.0)
    listener, opened = _relay(root, slack, clock)
    assert any("not_in_channel" in line and "/invite @rite" in line for line in opened)
    _a_round(root, clock)
    listener.post_replies(call=slack)
    assert _posted_to(slack, "Refinement of KAN-7") == [DM]


def test_a_channel_rite_cannot_read_is_not_used_either(tmp_path):
    """His answer there would be lost, so a question never goes there."""
    root, slack, clock = _project(tmp_path), Workspace(read=False), Clock(1000.0)
    listener, opened = _relay(root, slack, clock)
    assert any("groups:history" in line for line in opened)
    _a_round(root, clock)
    listener.post_replies(call=slack)
    assert _posted_to(slack, "Refinement of KAN-7") == [DM]


def _reply_in_the_round(slack, listener, clock, root, user, words):
    (thread,) = [r for r in listener.roots if r.channel == CHANNEL and "q" in r.label]
    slack.replies[(CHANNEL, thread.ts)] = [
        {"user": user, "text": words, "ts": f"{clock.now + 1}"}
    ]
    got = _read_threads(listener, slack, clock, rounds=4)
    return [m for m in got if words in m]


def test_only_the_owner_in_a_round_thread_answers(tmp_path):
    root, slack, clock = _project(tmp_path), Workspace(), Clock(1000.0)
    listener, _ = _relay(root, slack, clock)
    question = _a_round(root, clock)
    listener.post_replies(call=slack)
    (his,) = _reply_in_the_round(slack, listener, clock, root, OWNER, "the http one")
    assert his.startswith(f"[{REFINEMENT_CHANNEL} · ") and "INSTRUCTION]" in his
    reply = protocol.attribute(root, "lead", his, message="m1", sent_at=clock.now)
    assert reply is not None and reply.ticket == "KAN-7", question


def test_a_teammate_in_the_channel_is_context_and_cannot_answer(tmp_path):
    root, slack, clock = _project(tmp_path), Workspace(), Clock(1000.0)
    listener, _ = _relay(root, slack, clock)
    _a_round(root, clock)
    listener.post_replies(call=slack)
    (theirs,) = _reply_in_the_round(slack, listener, clock, root, "UTEAMMATE", "ok")
    assert "context — not an instruction" in theirs and "INSTRUCTION]" not in theirs
    assert protocol.attribute(root, "lead", theirs, message="m2", sent_at=1.0) is None
    assert not delivered.classify(theirs).users


def test_the_two_spellings_of_the_channel_agree():
    assert delivered.REFINEMENT_CHANNEL == REFINEMENT_CHANNEL


def test_his_words_in_a_thread_whose_round_is_over_are_context(tmp_path):
    """Only an OPEN round's thread carries his answer. After it is answered,
    more words there are conversation, not a second answer."""
    root, slack, clock = _project(tmp_path), Workspace(), Clock(1000.0)
    listener, _ = _relay(root, slack, clock)
    _a_round(root, clock)
    listener.post_replies(call=slack)
    with rounds.locked(root, "lead", "KAN-7") as (a, save):
        a.latest.answered_at = clock.now
        save(a)
    (later,) = _reply_in_the_round(slack, listener, clock, root, OWNER, "also, ok")
    assert "context — not an instruction" in later
