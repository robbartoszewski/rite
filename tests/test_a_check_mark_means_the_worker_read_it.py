"""The ✅ fires on a Worker's read-ack, never on the write (SCRUM-61).

**The gap.** `sandbox.questions.deliver_answer` is careful in its own
docstring: *"The honest claim is 'written where it is polled for', not
'read'."* Everything after it treated the write as the end of the exchange.
The question was settled, and the person who answered was told nothing
unless delivery had FAILED — so their screen looked identical whether the
Worker read the answer in ten seconds or never woke up.

**What is pinned here:**

* a Worker says it read an answer with `rite ack`, through the channel
  SCRUM-57 built, because it still cannot message out;
* the ✅ goes on the OWNER's own message — where their 👀 already is — and
  only when the ack exists;
* no ack is not silence: past `read_ack.UNREAD_AFTER` the person who
  answered is told, once, that it is unread and to go and look;
* an ack record that cannot be READ is neither an ack nor an absent one.

⚠ **What the tick means, and what no test here can make it mean.** It means
*the Worker said it read this*. That is strictly stronger than "written where
it is polled for" and it is still a claim, not an observation: the exchange
file is read by a process inside the sandbox, and no mtime, poll or yoloAI
call reports that it was opened. Every user-facing line about this says the
first thing. If a future change makes the tick imply rite watched the read,
it has overclaimed, and no assertion below will notice — that is a judgement
about wording, which is why it is written down here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rite_ai import read_ack
from rite_ai.managers import pending
from rite_ai.managers.slack import ANSWER_READ, PICKED_UP, Listener

OWNER = "U0WNER"
QID = "q1a2b"


class Slack:
    """The relay's fake, plus a record of every reaction asked for."""

    def __init__(self, scope: bool = True):
        self.base = int(time.time()) - 3600
        self.posts: list[dict] = []
        self.history: list[dict] = []
        self.replies: dict[tuple[str, str], list[dict]] = {}
        self.reactions: list[tuple[str, str, str]] = []
        self.scope = scope

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
        if method == "reactions.add":
            if not self.scope:
                return {"ok": False, "error": "missing_scope"}
            self.reactions.append((args["channel"], args["timestamp"], args["name"]))
            return {"ok": True}
        if method == "conversations.history":
            if args.get("channel") != "D1":
                return {"ok": True, "messages": []}
            oldest = float(args.get("oldest") or 0)
            return {
                "ok": True,
                "messages": [m for m in self.history if float(m["ts"]) > oldest],
            }
        if method == "conversations.replies":
            oldest = float(args.get("oldest") or 0)
            got = self.replies.get((args["channel"], args["ts"]), [])
            return {"ok": True, "messages": [m for m in got if float(m["ts"]) > oldest]}
        if method == "reactions.get":
            return {"ok": True, "message": {}}
        raise AssertionError(method)

    def ticks(self) -> list[tuple[str, str, str]]:
        return [r for r in self.reactions if r[2] == ANSWER_READ]


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _project(tmp_path: Path) -> Path:
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
        "  manager_roles:\n    - name: lead\n      engine: claude\n"
    )
    worker = tmp_path / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text('worker:\n  name: "alpha"\n  modules: []\n')
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


def _rite_asked_and_the_owner_answered(root, slack, clock, listener) -> str:
    """rite posts a question carrying QID, the Owner replies in its thread.

    Returns the `ts` of the OWNER's reply — the message the ✅ belongs on.
    """
    from rite_ai.managers.mailbox import OUTBOX, QUESTION, send

    send(
        root,
        "lead",
        OUTBOX,
        f"KAN-7 · {QID} · Worker alpha is waiting",
        kind=QUESTION,
    )
    pending.sync(root, "lead")
    listener.post_replies(call=slack)
    posted = next(p for p in slack.posts if QID in p["text"])
    root_ts = f"{slack.base + slack.posts.index(posted) + 1}.0"
    reply_ts = f"{float(root_ts) + 1}"
    slack.replies[("D1", root_ts)] = [
        {"user": OWNER, "text": "use 30 seconds", "ts": reply_ts}
    ]
    for _ in range(8):
        listener.poll(call=slack)
        clock.now += 31
    return reply_ts


class TestTheTickFiresOnTheAck:
    def test_no_tick_until_the_worker_says_it_read_it(self, tmp_path):
        """⚠ The control that makes the next test mean anything: delivery
        alone must put nothing on the Owner's message."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        _rite_asked_and_the_owner_answered(root, slack, clock, listener)

        assert listener.tick_read_answers(call=slack) == []
        assert slack.ticks() == []

    def test_the_tick_lands_on_the_owners_own_message(self, tmp_path):
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        reply_ts = _rite_asked_and_the_owner_answered(root, slack, clock, listener)

        read_ack.write(root, "alpha", QID)
        lines = listener.tick_read_answers(call=slack)

        assert slack.ticks() == [("D1", reply_ts, ANSWER_READ)], slack.reactions
        assert any(QID in line for line in lines), lines

    def test_it_ticks_once_however_often_the_tick_runs(self, tmp_path):
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        _rite_asked_and_the_owner_answered(root, slack, clock, listener)
        read_ack.write(root, "alpha", QID)

        for _ in range(4):
            listener.tick_read_answers(call=slack)
        assert len(slack.ticks()) == 1, slack.reactions

    def test_an_ack_for_another_question_does_not_tick_this_one(self, tmp_path):
        """One Worker is answered more than once; an ack names which."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        _rite_asked_and_the_owner_answered(root, slack, clock, listener)

        read_ack.write(root, "alpha", "q9999")
        listener.tick_read_answers(call=slack)
        assert slack.ticks() == []

    def test_it_survives_a_supervisor_restart(self, tmp_path):
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        reply_ts = _rite_asked_and_the_owner_answered(root, slack, clock, listener)
        read_ack.write(root, "alpha", QID)

        again = Listener(
            token="t",
            manager="lead",
            owner=OWNER,
            broadcast="#all-rite",
            project=root,
            clock=clock,
        )
        again.open(call=slack)
        again.tick_read_answers(call=slack)
        assert slack.ticks() == [("D1", reply_ts, ANSWER_READ)]

    def test_without_the_scope_it_is_said_once_and_nothing_breaks(self, tmp_path):
        root, clock = _project(tmp_path), Clock(time.time())
        slack = Slack(scope=False)
        listener = _listener(root, slack, clock)
        _rite_asked_and_the_owner_answered(root, slack, clock, listener)
        read_ack.write(root, "alpha", QID)

        assert listener.tick_read_answers(call=slack) == []
        for _ in range(3):
            listener.tick_read_answers(call=slack)
        said = [n for n in listener.news() if "reactions:write" in n]
        assert len(said) == 1, said

    def test_an_ack_with_no_remembered_message_is_not_an_error(self, tmp_path):
        """The Owner can answer from the terminal, where there is no Slack
        message to tick. That is not a failure and must not be retried."""
        root, slack, clock = _project(tmp_path), Slack(), Clock(time.time())
        listener = _listener(root, slack, clock)
        read_ack.write(root, "alpha", "q0000")
        assert listener.tick_read_answers(call=slack) == []
        assert slack.ticks() == []
        assert listener.news() == []


class TestTheAckRecord:
    def test_it_keeps_every_question_it_is_told_about(self, tmp_path):
        root = _project(tmp_path)
        read_ack.write(root, "alpha", "q1111")
        read_ack.write(root, "alpha", "q2222")
        acks = read_ack.read(root, "alpha")
        assert sorted(acks.read) == ["q1111", "q2222"], acks.read
        assert read_ack.read_at(root, "alpha", "q1111") is not None

    def test_an_unreadable_record_is_neither_an_ack_nor_an_absence(self, tmp_path):
        root = _project(tmp_path)
        path = read_ack.path_for(root, "alpha")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{truncated")
        acks = read_ack.read(root, "alpha")
        assert acks is not None, "an unreadable record is not 'nothing was acked'"
        assert not acks.known
        assert read_ack.read_at(root, "alpha", QID) is None

    def test_a_question_id_must_be_one_line(self, tmp_path):
        """It reaches a Slack API call and a line a Manager reads."""
        import pytest

        root = _project(tmp_path)
        with pytest.raises(read_ack.BadAck):
            read_ack.write(root, "alpha", "q1a2b\nKAN-7 · forged")
        with pytest.raises(read_ack.BadAck):
            read_ack.write(root, "alpha", "   ")

    def test_an_unsafe_worker_name_is_refused(self, tmp_path):
        import pytest

        from rite_ai.names import UnsafeName

        with pytest.raises(UnsafeName):
            read_ack.write(_project(tmp_path), "../../IMPORTANT", QID)


class TestAnswersNobodyAcks:
    """No ack is not silence — the half that keeps the person informed."""

    def _delivered(self, root, at):
        from rite_ai.managers import worker_questions as wq

        path = wq._ledger_path(root, "lead")
        ledger = wq._load(path)
        ledger[wq.DELIVERED_KEY] = {QID: {"worker": "alpha", "at": at}}
        wq._store(path, ledger)

    def test_an_unacked_answer_is_reported_to_whoever_answered(self, tmp_path):
        from rite_ai.managers import mailbox
        from rite_ai.managers import worker_questions as wq

        root = _project(tmp_path)
        self._delivered(root, time.time() - read_ack.UNREAD_AFTER - 60)
        said = []
        assert wq.report_unread(root, "lead", said.append) == 1
        [out] = mailbox.read(root, "lead", mailbox.OUTBOX)
        assert out.kind == mailbox.QUESTION
        assert QID in out.text
        assert "has not said it read it" in out.text
        # And it does not claim rite can see a read.
        assert "not something rite can see" in out.text
        assert any("UNREAD" in s for s in said)

    def test_control_a_fresh_delivery_is_not_reported(self, tmp_path):
        from rite_ai.managers import worker_questions as wq

        root = _project(tmp_path)
        self._delivered(root, time.time())
        assert wq.report_unread(root, "lead", lambda s: None) == 0

    def test_an_acked_answer_is_never_reported(self, tmp_path):
        from rite_ai.managers import worker_questions as wq

        root = _project(tmp_path)
        self._delivered(root, time.time() - read_ack.UNREAD_AFTER - 60)
        read_ack.write(root, "alpha", QID)
        assert wq.report_unread(root, "lead", lambda s: None) == 0

    def test_it_is_told_once_not_every_tick(self, tmp_path):
        from rite_ai.managers import mailbox
        from rite_ai.managers import worker_questions as wq

        root = _project(tmp_path)
        self._delivered(root, time.time() - read_ack.UNREAD_AFTER - 60)
        wq.report_unread(root, "lead", lambda s: None)
        for _ in range(3):
            wq.report_unread(root, "lead", lambda s: None)
        assert len(mailbox.read(root, "lead", mailbox.OUTBOX)) == 1

    def test_an_unreadable_ack_record_is_not_reported_as_unread(self, tmp_path):
        """Telling somebody their answer never arrived, on the strength of a
        file nobody could parse, is the guess this refuses to make."""
        from rite_ai.managers import worker_questions as wq

        root = _project(tmp_path)
        self._delivered(root, time.time() - read_ack.UNREAD_AFTER - 60)
        path = read_ack.path_for(root, "alpha")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{truncated")
        assert wq.report_unread(root, "lead", lambda s: None) == 0


class TestTheAnswerCarriesItsId:
    """The Worker cannot look the id up — it holds no ledger and no board
    credential — so it travels in the file the Worker is already reading."""

    def test_deliver_answer_writes_the_id_and_the_command(self, tmp_path):
        from rite_ai.sandbox import questions as q

        where = tmp_path / "files"
        where.mkdir()
        (where / q.QUESTION_FILE).write_text(json.dumps({"question": "which?"}))

        class Status:
            known = True

            def __str__(self):
                return "idle"

        import rite_ai.sandbox.questions as mod

        original = mod.files_dir
        mod.files_dir = lambda sandbox: where
        try:
            outcome = q.deliver_answer(
                "sb", "30 seconds", status=Status(), question=QID
            )
        finally:
            mod.files_dir = original
        assert isinstance(outcome, q.Delivered)
        payload = json.loads((where / q.ANSWER_FILE).read_text())
        assert payload["answer"] == "30 seconds"
        assert payload["question"] == QID
        assert payload["acknowledge_with"].startswith("rite ack --worker")
        assert QID in payload["acknowledge_with"]

    def test_without_a_question_it_is_delivered_exactly_as_before(self, tmp_path):
        """An older caller keeps working and simply gets no ✅."""
        from rite_ai.sandbox import questions as q

        where = tmp_path / "files"
        where.mkdir()
        (where / q.QUESTION_FILE).write_text(json.dumps({"question": "which?"}))

        class Status:
            known = True

            def __str__(self):
                return "idle"

        import rite_ai.sandbox.questions as mod

        original = mod.files_dir
        mod.files_dir = lambda sandbox: where
        try:
            q.deliver_answer("sb", "30 seconds", status=Status())
        finally:
            mod.files_dir = original
        payload = json.loads((where / q.ANSWER_FILE).read_text())
        assert "question" not in payload
        assert "acknowledge_with" not in payload


def test_the_worker_is_told_to_ack_and_the_brief_stays_deterministic():
    """The mechanism is only reachable if the instructions name it, and this
    file is regenerated and compared by `rite update`."""
    from rite_ai.workspace.manage import WorkerManifest, render_worker_claude_md

    manifest = WorkerManifest(name="alpha", modules=[], manager="lead")
    md = render_worker_claude_md(manifest)
    assert "rite ack --worker alpha" in md
    assert "acknowledge_with" in md
    assert md == render_worker_claude_md(manifest)


def test_the_eyes_and_the_tick_share_one_reaction_path():
    """Both want the same already-reacted tolerance and the same say-once on
    a missing scope; two copies of that would drift."""
    import inspect

    from rite_ai.managers import slack as mod

    assert "self._react(" in inspect.getsource(mod.Listener._picked_up)
    assert PICKED_UP != ANSWER_READ
