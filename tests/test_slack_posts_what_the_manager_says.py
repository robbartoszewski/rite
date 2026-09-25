"""The Manager's replies are posted to Slack, and each post's identity is kept
(A4).

⚠ **The `ts` Slack returns is kept in the relay's OWN state, keyed by outbox
filename — never in the message**, which stays identity-free (Decision 1a).
That `ts` is what a thread is rooted on, and what A3 reads replies under. A
post whose identity is thrown away cannot be answered in a thread.
"""

from __future__ import annotations

import re

from rite_ai.managers.mailbox import OUTBOX, send, unread
from rite_ai.managers.slack import READER, Listener

OWNER = "U0WNER"


def _bare(text: str) -> str:
    """The header without its send time, which is pinned on its own below."""
    return re.sub(r" · sent \w{3} \d\d:\d\d", "", text)


class Slack:
    def __init__(self, fail_posts: int = 0):
        self.posts: list[dict] = []
        self.fail_posts = fail_posts
        self.replies: dict[tuple[str, str], list[dict]] = {}

    def __call__(self, method, token, params=None, payload=None):
        args = payload or params or {}
        if method == "auth.test":
            return {"ok": True, "user_id": "UR1TE"}
        if method == "chat.postMessage":
            if self.fail_posts:
                self.fail_posts -= 1
                return {"ok": False, "error": "ratelimited"}
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


def _listener(tmp_path, slack, owner=OWNER, clock=lambda: 150.0):
    (tmp_path / ".rite").mkdir(exist_ok=True)
    listener = Listener(
        token="t",
        manager="lead",
        owner=owner,
        broadcast="#all-rite",
        project=tmp_path,
        clock=clock,
    )
    listener.open(call=slack)
    return listener


def _started(tmp_path, slack, **kw):
    """A relay past its first run, so replies from here on are posted."""
    listener = _listener(tmp_path, slack, **kw)
    listener.post_replies(call=slack)
    return listener


class TestAReplyIsPostedAndItsIdentityKept:
    def test_it_goes_to_the_owners_dm_and_the_ts_is_recorded(self, tmp_path):
        slack = Slack()
        listener = _started(tmp_path, slack)
        path = send(tmp_path, "lead", OUTBOX, "RT-14 is merged")
        listener.post_replies(call=slack)
        # To the id the start line resolved the Owner's user id to.
        assert slack.posts[-1] == {"channel": "D1", "text": "*lead*: RT-14 is merged"}
        record = listener.posted()[path.name]
        assert (
            record["channel"] == "D1" and record["ts"] == f"{len(slack.posts) + 100}.0"
        )

    def test_the_message_itself_is_not_touched(self, tmp_path):
        """Decision 1a: nothing is written into a message."""
        slack = Slack()
        listener = _started(tmp_path, slack)
        path = send(tmp_path, "lead", OUTBOX, "done")
        before = path.read_text()
        listener.post_replies(call=slack)
        assert path.read_text() == before

    def test_rite_connect_still_sees_it(self, tmp_path):
        """Two readers, two cursors (A1). Posting to Slack must not take the
        message from the User at the terminal."""
        slack = Slack()
        listener = _started(tmp_path, slack)
        send(tmp_path, "lead", OUTBOX, "done")
        listener.post_replies(call=slack)
        assert unread(tmp_path, "lead", OUTBOX, READER) == []
        assert [m.text for m in unread(tmp_path, "lead", OUTBOX, "connect")] == ["done"]

    def test_with_no_owner_it_goes_to_the_broadcast_channel(self, tmp_path):
        slack = Slack()
        listener = _started(tmp_path, slack, owner="")
        send(tmp_path, "lead", OUTBOX, "done")
        listener.post_replies(call=slack)
        assert slack.posts[-1]["channel"] == "C1"


class TestTheFirstRunDoesNotFloodTheDM:
    def test_earlier_replies_are_not_posted_and_it_says_so(self, tmp_path):
        slack = Slack()
        (tmp_path / ".rite").mkdir()
        for text in ("old one", "old two"):
            send(tmp_path, "lead", OUTBOX, text)
        listener = _listener(tmp_path, slack)
        starts = len(slack.posts)
        lines = listener.post_replies(call=slack)
        assert len(slack.posts) == starts
        assert "2 earlier" in lines[0] and "rite replies" in lines[0]
        send(tmp_path, "lead", OUTBOX, "new one")
        listener.post_replies(call=slack)
        assert slack.posts[-1]["text"] == "*lead*: new one"


class TestAFailedPostIsRetriedInOrder:
    def test_nothing_is_lost_and_nothing_overtakes(self, tmp_path):
        slack = Slack()
        listener = _started(tmp_path, slack)
        send(tmp_path, "lead", OUTBOX, "first")
        send(tmp_path, "lead", OUTBOX, "second")
        slack.fail_posts = 1
        listener.post_replies(call=slack)
        assert not any(
            "first" in p["text"] or "second" in p["text"] for p in slack.posts
        )
        assert any("ratelimited" in line for line in listener.news())
        listener.post_replies(call=slack)
        texts = [p["text"] for p in slack.posts]
        assert texts[-2:] == ["*lead*: first", "*lead*: second"]


class TestAPostedReplyCanBeAnsweredInItsThread:
    def test_a_reply_under_it_reaches_the_manager_naming_it(self, tmp_path):
        slack = Slack()
        listener = _started(tmp_path, slack, clock=lambda: 150.0)
        send(tmp_path, "lead", OUTBOX, "shall I merge RT-14?")
        listener.post_replies(call=slack)
        root = listener.roots[-1]
        slack.replies[("D1", root.ts)] = [
            {"user": OWNER, "text": "yes", "ts": f"{float(root.ts) + 1}"}
        ]
        got = []
        for _ in range(10):
            got.extend(_bare(m) for m in listener.poll(call=slack))
        assert got == [
            "[Owner's DM · reply in the thread under rite's reply "
            f'"shall I merge RT-14?" at {root.label.split(" at ")[-1]} · '
            "addressed · INSTRUCTION]\n> yes"
        ]

    def test_a_restart_keeps_reading_the_thread_without_redelivering(self, tmp_path):
        slack = Slack()
        listener = _started(tmp_path, slack)
        send(tmp_path, "lead", OUTBOX, "shall I merge?")
        listener.post_replies(call=slack)
        root = listener.roots[-1]
        slack.replies[("D1", root.ts)] = [
            {"user": OWNER, "text": "yes", "ts": f"{float(root.ts) + 1}"}
        ]
        first = [m for _ in range(10) for m in listener.poll(call=slack)]
        assert len(first) == 1

        again = _listener(tmp_path, slack)
        assert any(r.ts == root.ts for r in again.roots), "the thread was forgotten"
        slack.replies[("D1", root.ts)].append(
            {"user": OWNER, "text": "and tag it", "ts": f"{float(root.ts) + 2}"}
        )
        later = [_bare(m) for _ in range(10) for m in again.poll(call=slack)]
        assert [m.splitlines()[1] for m in later] == ["> and tag it"]


class TestTheLastReplyOfARunIsPosted:
    def test_close_posts_what_the_last_cycle_said(self, tmp_path):
        """The wait loop only runs while a cycle is alive, and the last reply
        is usually written just before the engine exits."""
        slack = Slack()
        listener = _started(tmp_path, slack)
        send(tmp_path, "lead", OUTBOX, "signing off")
        listener.close(call=slack)
        texts = [p["text"] for p in slack.posts]
        assert "*lead*: signing off" in texts
        # Before the stop line, so the last thing said is that it stopped.
        assert "has stopped" in texts[-1]

    def test_the_supervisor_posts_while_the_manager_works(self):
        import inspect

        import rite_ai.managers.supervise as sup

        assert '"post_replies"' in inspect.getsource(sup.supervise)


class TestWhatIsPostedIsRedacted:
    """A Manager pastes command output into replies, and this posts them where
    people read — with no Owner, into a channel the whole workspace reads."""

    def test_a_credential_shaped_assignment_is_redacted_in_slack(self, tmp_path):
        slack = Slack()
        listener = _started(tmp_path, slack, owner="")
        path = send(
            tmp_path, "lead", OUTBOX, "env says GITHUB_TOKEN=ghp_SENTINEL12345 ok"
        )
        listener.post_replies(call=slack)
        posted = slack.posts[-1]["text"]
        assert "ghp_SENTINEL12345" not in posted
        assert "GITHUB_TOKEN=[redacted]" in posted
        # The outbox keeps what was said; only what leaves the machine changes.
        assert "ghp_SENTINEL12345" in path.read_text()

    def test_the_relays_own_token_is_redacted_wherever_it_appears(self, tmp_path):
        slack = Slack()
        listener = _started(tmp_path, slack, owner="")
        listener.token = "xoxb-not-a-real-token-0000"
        send(tmp_path, "lead", OUTBOX, "the token is xoxb-not-a-real-token-0000")
        listener.post_replies(call=slack)
        assert "xoxb-not-a-real-token-0000" not in slack.posts[-1]["text"]

    def test_an_ordinary_reply_is_untouched(self, tmp_path):
        slack = Slack()
        listener = _started(tmp_path, slack, owner="")
        send(tmp_path, "lead", OUTBOX, "ran with --sessions=3 and PYTHONPATH=src")
        listener.post_replies(call=slack)
        assert slack.posts[-1]["text"].endswith("--sessions=3 and PYTHONPATH=src")

    def test_the_token_is_not_in_the_listeners_repr(self):
        from rite_ai.managers.slack import Listener

        assert "xoxb-secret-value" not in repr(
            Listener(token="xoxb-secret-value", manager="m")
        )
