"""A check-in goes to Slack, and answers to it come back labelled (K5).

The digest and the surviving questions are ONE outbox message, so `rite
replies` and the Slack relay carry the same thing. In Slack:

* it is posted to the **Owner's DM**, the command channel, because an answer
  to a queued question is an instruction and must come from the channel that
  carries authority (D-95). A reply in that thread reaches the Manager
  labelled INSTRUCTION, naming the check-in it answers;
* it is **mirrored** to the broadcast channel, where a reply reaches the
  Manager as context, whoever typed it (D-94).

No new mailbox shape and no sender field: which outbox file is a check-in is
recorded in the check-in ledger, and the relay reads it from there.
"""

from __future__ import annotations

from rite_ai.managers import checkins
from rite_ai.managers.mailbox import OUTBOX, send, unread
from rite_ai.managers.slack import Listener

OWNER = "U0WNER"


class Slack:
    def __init__(self):
        self.posts: list[dict] = []
        self.replies: dict[tuple[str, str], list[dict]] = {}

    def __call__(self, method, token, params=None, payload=None):
        args = payload or params or {}
        if method == "auth.test":
            return {"ok": True, "user_id": "UR1TE"}
        if method == "chat.postMessage":
            self.posts.append(args)
            target = args["channel"]
            channel = (
                "D1" if target.startswith("U") else "C1" if target[0] == "#" else target
            )
            return {"ok": True, "channel": channel, "ts": f"{100 + len(self.posts)}.0"}
        if method == "conversations.history":
            return {"ok": True, "messages": []}
        if method == "conversations.replies":
            oldest = float(args.get("oldest") or 0)
            got = self.replies.get((args["channel"], args["ts"]), [])
            return {"ok": True, "messages": [m for m in got if float(m["ts"]) > oldest]}
        raise AssertionError(method)


def _started(tmp_path, slack, owner=OWNER):
    (tmp_path / ".rite").mkdir(exist_ok=True)
    listener = Listener(
        token="t",
        manager="lead",
        owner=owner,
        broadcast="#all-rite",
        project=tmp_path,
        clock=lambda: 150.0,
    )
    listener.open(call=slack)
    listener.post_replies(call=slack)  # past the first run: from here on, posted
    return listener


def _a_check_in(root) -> str:
    """Deliver a real check-in through the check-in module."""
    checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    checkins._deliver_checkin(root, "lead")
    [name] = [
        e["outbox"] for e in checkins.ledger(root, "lead") if e["event"] == "checkin"
    ]
    return name


def _poll_all(listener, slack, times: int = 6):
    heard: list[str] = []
    for _ in range(times):
        listener.clock = lambda: 10_000.0  # every thread is due
        heard.extend(listener.poll(call=slack))
    return heard


def test_a_check_in_is_posted_to_the_dm_and_mirrored_to_broadcast(tmp_path):
    slack = Slack()
    listener = _started(tmp_path, slack)
    before = len(slack.posts)
    name = _a_check_in(tmp_path)
    listener.post_replies(call=slack)
    dm, mirror = slack.posts[before:]
    assert dm["channel"] == "D1" and "Check-in — lead" in dm["text"]
    assert mirror["channel"] == "C1" and "Check-in — lead" in mirror["text"]
    assert "replies here are read as context" in mirror["text"]
    record = listener.posted()[name]
    assert record["channel"] == "D1" and record["mirror"]["channel"] == "C1"


def test_an_ordinary_reply_is_not_mirrored(tmp_path):
    slack = Slack()
    listener = _started(tmp_path, slack)
    before = len(slack.posts)
    send(tmp_path, "lead", OUTBOX, "RT-14 is merged")
    listener.post_replies(call=slack)
    assert [p["channel"] for p in slack.posts[before:]] == ["D1"]


def test_the_owners_reply_in_the_dm_thread_is_an_instruction_answering_it(tmp_path):
    slack = Slack()
    listener = _started(tmp_path, slack)
    name = _a_check_in(tmp_path)
    listener.post_replies(call=slack)
    ts = listener.posted()[name]["ts"]
    slack.replies[("D1", ts)] = [
        {"ts": ts, "user": "UR1TE", "text": "root"},
        {"ts": "900.0", "user": OWNER, "text": "yes, rename it"},
    ]
    [heard] = [h for h in _poll_all(listener, slack) if "yes, rename it" in h]
    head = heard.splitlines()[0]
    assert head.startswith("[Owner's DM")
    assert "check-in" in head and "INSTRUCTION" in head


def test_a_reply_under_the_broadcast_mirror_is_context(tmp_path):
    slack = Slack()
    listener = _started(tmp_path, slack)
    name = _a_check_in(tmp_path)
    listener.post_replies(call=slack)
    ts = listener.posted()[name]["mirror"]["ts"]
    slack.replies[("C1", ts)] = [
        {"ts": ts, "user": "UR1TE", "text": "root"},
        {"ts": "901.0", "user": "U0THER", "text": "I'd keep --out"},
    ]
    [heard] = [h for h in _poll_all(listener, slack) if "keep --out" in h]
    head = heard.splitlines()[0]
    assert "check-in (broadcast mirror)" in head
    assert "context" in head and "INSTRUCTION" not in head


def test_broadcast_only_says_answers_there_cannot_instruct(tmp_path):
    """No Owner, no command channel: the check-in says so where its
    questions are, and names the way to answer that does carry authority."""
    slack = Slack()
    listener = _started(tmp_path, slack, owner="")
    before = len(slack.posts)
    _a_check_in(tmp_path)
    listener.post_replies(call=slack)
    [post] = slack.posts[before:]
    assert post["channel"] == "C1"
    assert "reaches the Manager as context" in post["text"]
    assert "rite message lead" in post["text"]


def test_rite_replies_carries_the_same_check_in(tmp_path):
    """One message, every reader: the local reader sees what Slack got."""
    slack = Slack()
    listener = _started(tmp_path, slack)
    _a_check_in(tmp_path)
    listener.post_replies(call=slack)
    [local] = unread(tmp_path, "lead", OUTBOX, "connect")
    assert "Check-in — lead" in local.text and "rename the flag?" in local.text
