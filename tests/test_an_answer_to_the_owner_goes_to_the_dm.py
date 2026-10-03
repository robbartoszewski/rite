"""A Manager's answer goes where the Owner asked, not into the day's notes.

**Observed** in the dogfood of 2026-10-03 (SCRUM-56). The Owner asked a
Manager a question in their DM. The Manager answered. The answer was posted
into the day's notes thread — whose own root line says "Nothing in this
thread needs you; what does is posted on its own" — so the one place the
Owner had been told they could ignore is where their answer went.

**The cause was one line.** `Listener.post_replies` had
`reading = not _needs_action(message)`, and `_needs_action` asks "does this
need the person". An answer does not: nobody has to act on it. So every
answer was classed as reading, which meant two things, and both of them were
the defect:

* HELD until the next check-in, for up to `HOLD_MAX_SECONDS`, when check-in
  windows are configured — the Owner's question answered a day later;
* then posted under `_notes_root`, the ambient pile.

**What is pinned here:** an answer goes into the DM, in the thread of the
Owner's own message; ambient status still goes to notes; an answer is never
held; and the correlation is bounded, so a status line hours later is not
filed as an answer to a question nobody is still waiting on.

**Related open tickets, and what this does and does not touch.** SCRUM-21
(reply threading) is the general question of which thread a reply belongs in;
this fixes one case of it — the Owner's own message — and leaves the rest
under the notes root as before. SCRUM-52 (Owner answer not delivered) is the
same symptom seen from the Owner's side and should be re-checked against
this. SCRUM-23 (silent relay failure) is untouched: a post that FAILS is
reported by `_problem` and retried on the next tick, and nothing here
changes that path.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rite_ai.managers import pending
from rite_ai.managers.mailbox import OUTBOX, QUESTION, REPLY, send
from rite_ai.managers.slack import ANSWER_WINDOW_SECONDS, Listener

OWNER = "U0WNER"
SOMEBODY_ELSE = "U0THER"
WINDOWS = 'checkins:\n  windows:\n    - {hours: "00:00-24:00"}\n'


class Slack:
    """The fake from `test_two_destinations`, plus a DM the Owner typed.

    `history` is what `conversations.history` returns for the DM, which is
    how an Owner message reaches `Listener._relay` — the branch that decides
    a message is the Owner addressing this Manager, and so the branch that
    has to record what an answer would be answering.
    """

    def __init__(self):
        # ⚠ An HOUR AGO, so this run's start line — which is the DM cursor
        # `open` sets, and a message at or before it is one sent while no
        # Manager was listening — sits behind the messages these tests then
        # put in the DM. An outbox message's own timestamp is real wall
        # clock (`mailbox.send` does not take a clock), so the Owner's
        # message has to be placed in real time too, not in the fake's.
        self.base = int(time.time()) - 3600
        self.posts: list[dict] = []
        self.history: list[dict] = []
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
            if args.get("channel") != "D1":
                return {"ok": True, "messages": []}
            oldest = float(args.get("oldest") or 0)
            got = [m for m in self.history if float(m["ts"]) > oldest]
            return {"ok": True, "messages": got}
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

    def notes_made(self) -> int:
        return sum(1 for t in self.top_level() if "notes for" in t)


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


def _listener(root: Path, slack: Slack, clock: Clock) -> Listener:
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


def _owner_asks(
    slack: Slack,
    clock: Clock,
    text: str,
    user: str = OWNER,
    ago: float = 60.0,
) -> str:
    """One message typed in the Owner's DM, returning its `ts`.

    `ago` places it that many seconds before now, so a reply written next —
    at real wall clock — counts as having been written after it. A negative
    `ago` puts it in the future, which is how the "written before the
    question" case is set up.
    """
    ts = f"{clock.now - ago:.6f}"
    slack.history.append({"user": user, "text": text, "ts": ts})
    return ts


def _heard(listener: Listener, slack: Slack, clock: Clock) -> list[str]:
    """Poll until the DM has been read — `poll` reads one conversation a
    tick, and it alternates between the DM and the broadcast channel."""
    got: list[str] = []
    for _ in range(6):
        got.extend(listener.poll(call=slack))
        clock.now += 31
    return got


class TestTheAnswerGoesToTheDM:
    def test_an_answer_lands_under_the_owners_own_message(self, tmp_path):
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        assert any("is RT-14 merged?" in m for m in _heard(listener, slack, clock))

        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)
        listener.post_replies(call=slack)

        assert slack.in_thread(asked) == ["*lead*: yes, on a1b2c3d"], (
            "the Owner's answer did not reach the thread of the message they "
            "asked it in"
        )
        assert slack.notes_made() == 0, (
            "a notes root was posted, so the answer went to the ambient pile "
            "the Owner is told they can ignore"
        )

    def test_control_ambient_status_still_goes_to_the_days_notes(self, tmp_path):
        """⚠ Without this the test above proves nothing: it would pass on a
        relay that had simply stopped using the notes thread at all."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        send(root, "lead", OUTBOX, "RT-15 merged", kind=REPLY)
        listener.post_replies(call=slack)
        assert slack.notes_made() == 1
        notes = next(
            p for p in slack.posts if p["text"].startswith("*lead*: notes for")
        )
        ts = f"{slack.base + slack.posts.index(notes) + 1}.0"
        assert slack.in_thread(ts) == ["*lead*: RT-15 merged"]

    def test_a_question_is_still_top_level_in_the_dm(self, tmp_path):
        """RP1 piece 3 is unchanged: what needs the person is the scan list."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        _owner_asks(slack, clock, "anything blocking?")
        _heard(listener, slack, clock)
        send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
        listener.post_replies(call=slack)
        assert "*lead* (needs your answer): which schema" in slack.top_level()

    def test_a_two_part_answer_stays_together(self, tmp_path):
        """A Manager that answers in two `rite reply` calls must not have
        its second half filed as notes — half an answer is the same defect
        in a smaller place."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "status?")
        _heard(listener, slack, clock)
        send(root, "lead", OUTBOX, "RT-14 merged", kind=REPLY)
        send(root, "lead", OUTBOX, "RT-15 still in review", kind=REPLY)
        listener.post_replies(call=slack)
        assert slack.in_thread(asked) == [
            "*lead*: RT-14 merged",
            "*lead*: RT-15 still in review",
        ]
        assert slack.notes_made() == 0


class TestAnAnswerIsNeverHeld:
    def test_with_checkin_windows_an_answer_is_not_held_for_one(self, tmp_path):
        """The other half of the defect. With windows configured, reading is
        held for the next check-in — and an answer was reading."""
        root = _project(tmp_path, WINDOWS)
        slack, clock = Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        _heard(listener, slack, clock)
        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)
        listener.post_replies(call=slack)
        assert slack.in_thread(asked) == ["*lead*: yes, on a1b2c3d"]

    def test_control_ambient_status_is_still_held_with_windows(self, tmp_path):
        """Proves the hold still works, so the test above is about answers
        and not about the hold having been removed."""
        root = _project(tmp_path, WINDOWS)
        slack, clock = Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        send(root, "lead", OUTBOX, "RT-15 merged", kind=REPLY)
        listener.post_replies(call=slack)
        assert slack.notes_made() == 0
        assert not any("RT-15" in p["text"] for p in slack.posts)


class TestTheCorrelationIsBounded:
    def test_a_reply_after_the_window_goes_to_notes(self, tmp_path):
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        _heard(listener, slack, clock)
        clock.now += ANSWER_WINDOW_SECONDS + 60
        send(root, "lead", OUTBOX, "unrelated: RT-20 opened", kind=REPLY)
        listener.post_replies(call=slack)
        assert slack.in_thread(asked) == []
        assert slack.notes_made() == 1

    def test_a_newer_owner_message_supersedes_the_older_one(self, tmp_path):
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        # Both typed before rite polls, which is the ordinary case: the
        # relay reads a conversation's history in one call and relays the
        # messages oldest first, so the newest is the one left outstanding.
        first = _owner_asks(slack, clock, "is RT-14 merged?", ago=120)
        second = _owner_asks(slack, clock, "and RT-15?", ago=60)
        heard = _heard(listener, slack, clock)
        assert any("RT-14" in m for m in heard) and any("RT-15" in m for m in heard)
        send(root, "lead", OUTBOX, "both merged", kind=REPLY)
        listener.post_replies(call=slack)
        assert slack.in_thread(second) == ["*lead*: both merged"]
        assert slack.in_thread(first) == []

    def test_a_dm_from_somebody_who_is_not_the_owner_is_not_answered(self, tmp_path):
        """That branch relays the message as context, not an instruction, so
        no reply is owed to it — and the header already says so."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "hello", user=SOMEBODY_ELSE)
        heard = _heard(listener, slack, clock)
        assert any("not the Owner" in m for m in heard)
        send(root, "lead", OUTBOX, "RT-14 merged", kind=REPLY)
        listener.post_replies(call=slack)
        assert slack.in_thread(asked) == []
        assert slack.notes_made() == 1

    def test_a_reply_written_before_the_question_is_not_an_answer_to_it(self, tmp_path):
        """The outbox is filed by when a message was written, and the relay
        files an inbox message by when it was SENT in Slack — so "after" has
        to be checked rather than assumed from the order rite saw them."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        send(root, "lead", OUTBOX, "written earlier", kind=REPLY)
        asked = _owner_asks(slack, clock, "is RT-14 merged?", ago=-120)
        _heard(listener, slack, clock)
        listener.post_replies(call=slack)
        assert slack.in_thread(asked) == []
        assert slack.notes_made() == 1


class TestItSurvivesARestart:
    def test_a_supervisor_restart_still_answers_under_the_question(self, tmp_path):
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        _heard(listener, slack, clock)

        again = Listener(
            token="t",
            manager="lead",
            owner=OWNER,
            broadcast="#all-rite",
            project=root,
            clock=clock,
        )
        again.open(call=slack)
        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)
        again.post_replies(call=slack)
        assert slack.in_thread(asked) == ["*lead*: yes, on a1b2c3d"]


class TestWhatRoundOneFound:
    """Each of these is a defect round 1 measured on the first version, and
    each test fails if its fix is reverted."""

    def test_a_dead_answer_thread_does_not_stall_everything_else(self, tmp_path):
        """The answer root is the Owner's own message: a ts rite never
        posted, cannot verify, and the Owner can delete.

        The first version broke the posting loop on it, so nothing was
        marked read, `_awaiting` stayed set, and every tick retried the same
        dead thread for the rest of the window — with questions and
        check-ins queued behind it. The Owner saw silence.
        """
        root, clock = _project(tmp_path), Clock(time.time())

        class Refuses(Slack):
            def __init__(self, dead):
                super().__init__()
                self.dead = dead

            def __call__(self, method, token, params=None, payload=None):
                args = payload or params or {}
                if method == "chat.postMessage" and args.get("thread_ts") == self.dead:
                    return {"ok": False, "error": "thread_not_found"}
                return super().__call__(method, token, params, payload)

        slack = Refuses("")
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        slack.dead = asked
        _heard(listener, slack, clock)

        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)
        send(root, "lead", OUTBOX, "which schema", kind=QUESTION)
        listener.post_replies(call=slack)

        # The answer still reaches the Owner, in the pile that always can.
        assert slack.notes_made() == 1
        notes = next(
            p for p in slack.posts if p["text"].startswith("*lead*: notes for")
        )
        notes_ts = f"{slack.base + slack.posts.index(notes) + 1}.0"
        assert slack.in_thread(notes_ts) == ["*lead*: yes, on a1b2c3d"]
        # And what was queued behind it is not held hostage.
        assert "*lead* (needs your answer): which schema" in slack.top_level()

    def test_the_answered_thread_is_read_back(self, tmp_path):
        """Routing the answer there creates the obvious place for the Owner
        to follow up, so it has to be a thread rite reads."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        _heard(listener, slack, clock)
        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)
        listener.post_replies(call=slack)
        assert asked in [r.ts for r in listener.roots], (
            "rite answered in a thread it is not reading, so the Owner's "
            "follow-up under it would reach nobody"
        )

        slack.replies[("D1", asked)] = [
            {"user": OWNER, "text": "then deploy it", "ts": f"{float(asked) + 1}"}
        ]
        assert any("> then deploy it" in m for m in _heard(listener, slack, clock))

    def test_rites_own_narration_is_not_an_answer(self, tmp_path):
        """`kind` records the command, not the speaker: rite's refinement
        narration is written with `rite reply`'s kind and answers nobody."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "start work on RT-20")
        _heard(listener, slack, clock)
        send(
            root,
            "lead",
            OUTBOX,
            "RT-9 changed after round 2 was proposed, so your accept was not recorded.",
            kind=REPLY,
            by_rite=True,
        )
        listener.post_replies(call=slack)
        assert slack.in_thread(asked) == []
        assert slack.notes_made() == 1

    def test_an_answer_is_not_tagged_as_status(self, tmp_path):
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        _heard(listener, slack, clock)
        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)
        listener.post_replies(call=slack)
        [answer] = [p for p in slack.posts if p.get("thread_ts") == asked]
        tags = json.dumps(answer.get("blocks") or [])
        assert "Answer" in tags, tags
        assert "Status" not in tags, (
            "the premise of moving an answer to the DM is that it is not "
            "ambient status, and the post still said Status"
        )

    def test_a_changed_owner_dm_is_not_answered_into(self, tmp_path):
        """A state file written before `slack.owner_user` changed must not
        route the next answer into the former Owner's DM."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        asked = _owner_asks(slack, clock, "is RT-14 merged?")
        _heard(listener, slack, clock)
        listener.dm = "D-SOMEONE-ELSE"
        send(root, "lead", OUTBOX, "yes, on a1b2c3d", kind=REPLY)
        listener.post_replies(call=slack)
        assert slack.in_thread(asked) == []
