"""rite acknowledges what it picks up, and links what it refers back to.

**SCRUM-35.** A message the Owner sent reached the Manager, but the person saw
nothing until a reply came, minutes later. Now the moment the relay picks up a
message addressed to the Manager, it puts 👀 on it. Only on an addressed
message: an eyes on a bystander's line in the broadcast channel would say rite
is acting on it.

**SCRUM-47.** rite referred to things it had already posted ("waiting on an
answer") in prose, so the person had to go and find them. A question rite
posted carries an id (`q3f9a`); a later message naming that id now reaches
Slack with the id as a link to the post. The outbox keeps the bare id, because
it is read in a terminal too (`rite replies`).
"""

from __future__ import annotations

import time

from rite_ai.managers import supervise, worker_questions
from rite_ai.managers.asking import raise_to_person
from rite_ai.managers.mailbox import OUTBOX, REPLY, send
from tests import test_slack_says_what_a_message_counts_as as counts
from tests import test_two_destinations as dest

OWNER, OTHER, ME = counts.OWNER, counts.OTHER, counts.ME


class Reacting(counts.Slack):
    """The DM/broadcast fake, plus `reactions.add` (recorded)."""

    def __init__(self, error: str = ""):
        super().__init__()
        self.error = error
        self.reactions: list[dict] = []

    def __call__(self, method, token, params=None, payload=None):
        if method == "reactions.add":
            args = payload or params or {}
            self.calls.append((method, dict(args)))
            if self.error:
                return {"ok": False, "error": self.error}
            self.reactions.append(dict(args))
            return {"ok": True}
        return super().__call__(method, token, params, payload)


def _picked(slack: Reacting, polls: int = 4) -> list[str]:
    listener = counts._opened(slack)
    out = counts._drain(listener, slack, polls)
    return out, listener


class TestPickedUpIsAcknowledged:
    def test_the_owners_dm_gets_exactly_one_eyes(self):
        slack = Reacting()
        slack.history["D1"].append(counts._said("do RT-14 first", "101.0"))

        got, _ = _picked(slack)

        assert len(got) == 1
        assert slack.reactions == [
            {"channel": "D1", "timestamp": "101.0", "name": "eyes"}
        ]

    def test_reading_again_does_not_react_again(self):
        slack = Reacting()
        listener = counts._opened(slack)
        slack.history["D1"].append(counts._said("do RT-14 first", "101.0"))
        counts._drain(listener, slack, 8)
        counts._drain(listener, slack, 8)

        assert len(slack.reactions) == 1

    def test_an_at_rite_mention_is_acknowledged(self):
        slack = Reacting()
        slack.history["C1"].append(
            counts._said(f"<@{ME}> what is left on KAN-9?", "101.0", OTHER)
        )

        _picked(slack)

        assert slack.reactions == [
            {"channel": "C1", "timestamp": "101.0", "name": "eyes"}
        ]

    def test_an_unaddressed_broadcast_line_is_not(self):
        """Relayed as context, but nobody asked rite anything."""
        slack = Reacting()
        slack.history["C1"].append(counts._said("standup looks late", "101.0", OTHER))

        got, _ = _picked(slack)

        assert len(got) == 1, "the message must still be relayed"
        assert slack.reactions == []

    def test_without_reactions_write_it_is_said_once_and_the_message_still_goes(
        self,
    ):
        slack = Reacting(error="missing_scope")
        listener = counts._opened(slack)
        slack.history["D1"].append(counts._said("first", "101.0"))
        slack.history["D1"].append(counts._said("second", "102.0"))

        got = counts._drain(listener, slack, 8)

        assert len(got) == 2, "a reaction that cannot be added must not stop delivery"
        said = [line for line in listener.news() if "reactions:write" in line]
        assert len(said) == 1, listener.news()
        tried = [c for c in slack.calls if c[0] == "reactions.add"]
        assert len(tried) == 1, "it kept trying after Slack said the scope is missing"


class Linking(dest.Slack):
    """The posting fake, plus `chat.getPermalink`: a link per message, or an
    error when asked to refuse."""

    def __init__(self, refuse: str = ""):
        super().__init__()
        self.refuse = refuse
        self.asked: list[dict] = []

    def __call__(self, method, token, params=None, payload=None):
        if method == "chat.getPermalink":
            args = payload or params or {}
            self.asked.append(dict(args))
            if self.refuse:
                return {"ok": False, "error": self.refuse}
            ts = args["message_ts"].replace(".", "")
            return {
                "ok": True,
                "permalink": f"https://acme.slack.com/archives/{args['channel']}/p{ts}",
            }
        if method == "reactions.add":
            return {"ok": True}
        return super().__call__(method, token, params, payload)


def _question_then(root, slack, clock, later: str):
    """Open the relay, raise a question and let the relay post it, then queue
    `later` (with `QID` replaced by the question's id) and post that. Returns
    (the question's id, its ts, what Slack was sent for `later`).

    The relay is opened FIRST, as in production: what was in the outbox
    before it opened predates its tracking and is not posted."""
    listener = dest._relay(root, slack, clock)
    raised = raise_to_person(
        root, "lead", subject="KAN-28", raiser="worker:alpha", text="> which rules?"
    )
    clock.now += 1
    listener.post_replies(call=slack)
    (asked,) = [p for p in slack.posts if raised.id in p["text"]]
    asked_ts = slack.ts_of(asked["text"])
    send(root, "lead", OUTBOX, later.replace("QID", raised.id), kind=REPLY)
    clock.now += 1
    listener.post_replies(call=slack)
    marker = later.split("QID")[0].strip()[:15]
    posted = [p for p in slack.posts if marker in p["text"] and p is not asked]
    assert posted, f"the later message was not posted: {slack.posts}"
    return raised.id, asked_ts, posted[-1]["text"]


class TestABackReferenceIsALink:
    def test_a_question_rite_posted_is_linked_where_it_is_named_again(self, tmp_path):
        root, slack, clock = dest._project(tmp_path), Linking(), dest.Clock(time.time())

        qid, ts, text = _question_then(
            root, slack, clock, "Status: alpha is still waiting on QID."
        )

        url = f"https://acme.slack.com/archives/D1/p{ts.replace('.', '')}"
        assert f"<{url}|{qid}>" in text, text
        assert f"waiting on {qid}." not in text, "the id was left as bare prose"

    def test_the_outbox_keeps_the_bare_id(self, tmp_path):
        """The terminal reads the outbox too, where a Slack link means nothing."""
        root, slack, clock = dest._project(tmp_path), Linking(), dest.Clock(time.time())
        qid, _, _ = _question_then(root, slack, clock, "Still waiting on QID.")

        from rite_ai.managers.mailbox import read

        texts = [m.text for m in read(root, "lead", OUTBOX)]
        assert any(t == f"Still waiting on {qid}." for t in texts), texts

    def test_an_id_rite_never_posted_stays_as_written(self, tmp_path):
        root, slack, clock = dest._project(tmp_path), Linking(), dest.Clock(time.time())
        _, _, text = _question_then(root, slack, clock, "Unrelated to q0bad, really.")
        assert "q0bad" in text and "|q0bad>" not in text

    def test_a_link_slack_will_not_give_leaves_the_id(self, tmp_path):
        root, slack, clock = (
            dest._project(tmp_path),
            Linking(refuse="channel_not_found"),
            dest.Clock(time.time()),
        )
        qid, _, text = _question_then(root, slack, clock, "Still waiting on QID.")
        assert f"Still waiting on {qid}." in text
        assert "<https://" not in text


class TestTheStoppedSummaryNamesTheQuestion:
    def test_a_waiting_worker_is_named_with_its_question_id(
        self, tmp_path, monkeypatch
    ):
        """The SCRUM-47 starting point: "waiting on an answer" now says which
        question, by the id the relay links."""
        root = dest._project(tmp_path)
        monkeypatch.setattr(
            supervise,
            "_workers_left_mid_flight",
            lambda r: [
                {
                    "worker": "alpha",
                    "ticket": "KAN-28",
                    "paths": ["yoloai/internal"],
                    "question": "which parsing rules?",
                    "qid": "q3f9a",
                }
            ],
        )
        said: list[str] = []

        supervise._hand_over_on_stop(root, "lead", "ceiling reached", said.append)

        assert any("alpha on KAN-28 (waiting on an answer, q3f9a)" in s for s in said)

    def test_the_id_comes_from_the_ledger_rite_raised_it_in(self, tmp_path):
        root = dest._project(tmp_path)
        worker_questions._store(
            worker_questions._ledger_path(root, "lead"),
            {"rite-acme-alpha": "q3f9a"},
        )
        assert worker_questions.raised_id(root, "rite-acme-alpha") == "q3f9a"
        assert worker_questions.raised_id(root, "rite-acme-beta") == ""


def test_a_question_posted_again_does_not_link_its_own_header(tmp_path):
    """A question's own id, in the header `asking` writes, introduces it. When
    the same question is posted a second time its id is already in the relay's
    record, and only the guard keeps the header from linking to itself."""
    from rite_ai.managers.mailbox import QUESTION

    root, slack, clock = dest._project(tmp_path), Linking(), dest.Clock(time.time())
    listener = dest._relay(root, slack, clock)
    first = raise_to_person(
        root, "lead", subject="KAN-28", raiser="worker:alpha", text="> which rules?"
    )
    clock.now += 1
    listener.post_replies(call=slack)
    header = f"KAN-28 · {first.id} · Worker alpha is waiting · reply in this thread"
    send(
        root,
        "lead",
        OUTBOX,
        f"{header}\n> still waiting",
        kind=QUESTION,
    )
    clock.now += 1
    listener.post_replies(call=slack)
    again = [p["text"] for p in slack.posts if "> still waiting" in p["text"]]
    (posted,) = again

    assert f"· {first.id} ·" in posted, f"the header's own id was linked: {posted}"
    assert f"|{first.id}>" not in posted, posted
