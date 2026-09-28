"""What needs the Owner apart from what is for reading, in Slack (RP1 piece 3).

Robert, 2026-09-28: the Owner's DM carries only what needs him, so it is the
scan list; everything else goes in a thread. "Nothing between scheduled
reports" is enforced for the reading pile only; anything needing action is
never held back.

* A question (anything that needs the person) goes top-level into the DM.
* A reply goes into a thread: with check-in windows, held for the next
  check-in and posted in its thread; with none, posted at once under one
  "notes" root a day. A reply held longer than `HOLD_MAX_SECONDS` goes under
  the notes root rather than waiting for a check-in that may not come.
"""

from __future__ import annotations

import time
from pathlib import Path

from rite_ai.managers import checkins, pending
from rite_ai.managers.mailbox import OUTBOX, QUESTION, REPLY, send, unread
from rite_ai.managers.slack import HOLD_MAX_SECONDS, READER, Listener

OWNER = "U0WNER"
DAY = 86400.0
WINDOWS = 'checkins:\n  windows:\n    - {hours: "00:00-24:00"}\n'


class Slack:
    """Its `ts` values are real times, as Slack's are: the relay compares them
    with its clock (the thread horizon), and a 1970 `ts` is dropped at once."""

    def __init__(self):
        self.base = int(time.time())
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
            return {
                "ok": True,
                "channel": channel,
                "ts": f"{self.base + len(self.posts)}.0",
            }
        if method == "conversations.history":
            return {"ok": True, "messages": []}
        if method == "conversations.replies":
            oldest = float(args.get("oldest") or 0)
            got = self.replies.get((args["channel"], args["ts"]), [])
            return {"ok": True, "messages": [m for m in got if float(m["ts"]) > oldest]}
        if method == "reactions.get":
            return {"ok": True, "message": {}}
        raise AssertionError(method)

    def top_level(self) -> list[str]:
        return [p["text"] for p in self.posts if "thread_ts" not in p]

    def in_thread(self, ts: str) -> list[str]:
        return [p["text"] for p in self.posts if p.get("thread_ts") == ts]

    def ts_of(self, text_start: str) -> str:
        for n, p in enumerate(self.posts, start=1):
            if p["text"].startswith(text_start):
                return f"{self.base + n}.0"
        raise AssertionError(text_start)


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _project(tmp_path: Path, windows: str = "") -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n" + windows
    )
    return tmp_path


def _relay(root: Path, slack: Slack, clock: Clock) -> Listener:
    pending.sync(root, "lead")
    listener = Listener(
        token="t",
        manager="lead",
        owner=OWNER,
        broadcast="#all-rite",
        project=root,
        clock=clock,
    )
    listener.open(call=slack)
    listener.post_replies(call=slack)
    return listener


# --- no check-in windows: reading goes under one notes root a day ------------


def test_a_question_is_top_level_and_a_reply_is_in_the_days_notes_thread(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    send(root, "lead", OUTBOX, "RT-14 merged", kind=REPLY)
    send(root, "lead", OUTBOX, "RT-15 merged", kind=REPLY)
    listener.post_replies(call=slack)

    top = slack.top_level()
    assert "*lead* (needs your answer): which schema" in top
    assert not any("RT-14" in t or "RT-15" in t for t in top)
    notes = slack.ts_of("*lead*: notes for")
    assert slack.in_thread(notes) == ["*lead*: RT-14 merged", "*lead*: RT-15 merged"]
    assert sum(1 for t in top if "notes for" in t) == 1
    assert unread(root, "lead", OUTBOX, READER) == []


def test_the_next_day_gets_its_own_notes_root_and_a_restart_keeps_todays(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "one", kind=REPLY)
    listener.post_replies(call=slack)

    again = Listener(
        token="t",
        manager="lead",
        owner=OWNER,
        broadcast="#all-rite",
        project=root,
        clock=clock,
    )
    again.open(call=slack)
    send(root, "lead", OUTBOX, "two", kind=REPLY)
    again.post_replies(call=slack)
    assert sum(1 for t in slack.top_level() if "notes for" in t) == 1

    clock.now += DAY
    send(root, "lead", OUTBOX, "three", kind=REPLY)
    again.post_replies(call=slack)
    assert sum(1 for t in slack.top_level() if "notes for" in t) == 2


def test_an_answer_under_the_notes_root_reaches_the_manager(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "RT-14 merged", kind=REPLY)
    listener.post_replies(call=slack)
    notes = slack.ts_of("*lead*: notes for")
    slack.replies[("D1", notes)] = [
        {"user": OWNER, "text": "thanks, tag it", "ts": f"{float(notes) + 1}"}
    ]
    got = []
    for _ in range(20):
        got.extend(listener.poll(call=slack))
        clock.now += 31
    assert any("> thanks, tag it" in m for m in got)


# --- check-in windows: reading waits for the check-in; action never waits ----


def test_with_windows_a_reply_waits_for_the_checkin_and_goes_in_its_thread(tmp_path):
    root, slack = _project(tmp_path, WINDOWS), Slack()
    clock = Clock(time.time())
    listener = _relay(root, slack, clock)
    reply = send(root, "lead", OUTBOX, "RT-14 merged", kind=REPLY)
    listener.post_replies(call=slack)
    assert not any("RT-14" in p["text"] for p in slack.posts), "held"
    assert [m.path.name for m in unread(root, "lead", OUTBOX, READER)] == [reply.name]

    # A question after the held reply is NOT held behind it.
    send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
    listener.post_replies(call=slack)
    assert "*lead* (needs your answer): which schema" in slack.top_level()
    assert not any("RT-14" in p["text"] for p in slack.posts)

    checkins._deliver_checkin(root, "lead")
    listener.post_replies(call=slack)
    checkin = slack.ts_of("*lead*: Check-in")
    assert slack.in_thread(checkin) == ["*lead*: RT-14 merged"]
    assert unread(root, "lead", OUTBOX, READER) == []

    # The question was posted while the cursor waited behind the held reply,
    # so every tick since has seen it again: it is still posted once.
    question = "*lead* (needs your answer): which schema"
    assert slack.top_level().count(question) == 1

    # Nothing is posted twice, however often the relay ticks.
    before = len(slack.posts)
    for _ in range(3):
        listener.post_replies(call=slack)
    assert len(slack.posts) == before


def test_a_reply_held_past_the_limit_goes_under_the_notes_root(tmp_path):
    root, slack = _project(tmp_path, WINDOWS), Slack()
    clock = Clock(time.time())
    listener = _relay(root, slack, clock)
    send(root, "lead", OUTBOX, "RT-14 merged", kind=REPLY)
    listener.post_replies(call=slack)
    assert not any("RT-14" in p["text"] for p in slack.posts)

    clock.now += HOLD_MAX_SECONDS + 60
    listener.post_replies(call=slack)
    notes = slack.ts_of("*lead*: notes for")
    assert slack.in_thread(notes) == ["*lead*: RT-14 merged"]
