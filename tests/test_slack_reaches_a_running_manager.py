"""Slack messages reach a Manager through the mailbox (A3b).

⚠ **THE ONE HOOK.** A Slack message becomes an ordinary mailbox file through
the same validated writer `rite message` uses, so it reaches the Manager by
the instruction composed at the next cycle boundary. A second delivery path
was refused by name in 0.5.1 and this does not add one.

⚠ **AUTHORITY COMES FROM THE CHANNEL (SPEC §9.16.2, D-95).** Instructions
come only from the Owner's DM with the app, found from the Owner's user id —
never from a configured channel, which could be one the whole workspace posts
in.
"""

from __future__ import annotations

from rite_ai.managers.mailbox import INBOX, read
from rite_ai.managers.slack import Listener, _hear, _post
from rite_ai.managers.supervise import supervise


def _reply(messages, ok=True, error="channel_not_found"):
    """A fake Slack. Returns newest-first, as Slack does."""

    def call(method, token, params=None, payload=None):
        if method == "conversations.history":
            return {"ok": ok, "error": error, "messages": list(messages)}
        return {"ok": ok, "error": error, "channel": "C1", "ts": "1.0"}

    return call


def _msg(text, ts, bot=False):
    m = {"text": text, "ts": ts, "type": "message", "user": "U1"}
    if bot:
        m["bot_id"] = "B1"
    return m


class TestWhatCountsAsAnInstruction:
    def test_a_persons_message_is_heard(self):
        got = _hear("C1", "t", call=_reply([_msg("do RT-14 first", "20.0")]))
        assert got.texts == ("do RT-14 first",)

    def test_a_bot_message_is_not(self):
        """⚠ Not cosmetic. rite POSTS the Manager's replies to Slack; reading
        them back would feed a Manager its own words as a new instruction and
        loop for as long as the run lasts."""
        got = _hear(
            "C1", "t", call=_reply([_msg("I have done that", "20.0", bot=True)])
        )
        assert got.texts == ()

    def test_an_empty_message_is_not(self):
        got = _hear("C1", "t", call=_reply([_msg("   ", "20.0")]))
        assert got.texts == ()

    def test_messages_arrive_in_the_order_they_were_said(self):
        """Slack returns newest first; a Manager should be told things in the
        order a person said them."""
        got = _hear(
            "C1", "t", call=_reply([_msg("second", "21.0"), _msg("first", "20.0")])
        )
        assert got.texts == ("first", "second")


class TestTheCursorMovesAndNothingIsLost:
    def test_the_newest_timestamp_comes_back_for_the_next_poll(self):
        got = _hear("C1", "t", call=_reply([_msg("b", "21.0"), _msg("a", "20.0")]))
        assert got.newest == "21.0"

    def test_a_bot_message_still_advances_the_cursor(self):
        """⚠ Otherwise rite's own post is re-read on every poll for ever, and
        the cursor never passes it."""
        got = _hear("C1", "t", call=_reply([_msg("mine", "21.0", bot=True)]))
        assert got.texts == () and got.newest == "21.0"

    def test_the_listener_carries_the_cursor_between_polls(self):
        listener = Listener(token="t", manager="m", dm="D1")
        listener.poll(call=_reply([_msg("a", "20.0")]))
        assert listener.since == {"D1": "20.0"}

    def test_an_outage_does_not_move_the_cursor_or_raise(self):
        """A Slack outage must not end a Manager's run, and must not skip the
        messages it could not read."""
        listener = Listener(token="t", manager="m", dm="D1", since={"D1": "19.0"})
        assert listener.poll(call=_reply([], ok=False, error="ratelimited")) == ()
        assert listener.since == {"D1": "19.0"}
        assert listener.problems and "ratelimited" in listener.problems[0]

    def test_a_raising_transport_is_a_problem_not_a_crash(self):
        def explode(*a, **kw):
            raise TimeoutError("slack is slow")

        got = _hear("C1", "t", call=explode)
        assert not got.ok and "TimeoutError" in got.problem


class TestItDoesNothingWhenNotConfigured:
    def test_no_channel_means_no_call(self):
        called = []
        _hear("", "t", call=lambda *a, **kw: called.append(a) or {"ok": True})
        assert called == []

    def test_no_token_means_no_call(self):
        called = []
        _hear("C1", "", call=lambda *a, **kw: called.append(a) or {"ok": True})
        assert called == []

    def test_posting_nothing_posts_nothing(self):
        called = []
        assert _post("C1", "t", "   ", call=lambda *a, **kw: called.append(a)).ok
        assert called == []


class TestItReachesTheManagerThroughTheMailbox:
    def test_a_slack_message_becomes_a_mailbox_file_while_a_cycle_runs(self, tmp_path):
        """⚠ This asserts the half A3b ADDS — Slack into the inbox, during a
        running cycle. The other half, inbox into the next instruction, is
        already covered by the mailbox tests and by the live observation in
        the commit message; asserting it here would need the real `ending`
        classifier and a real tmux pane.

        An earlier version of this test asserted the inbox and then `or
        True`, which cannot fail. That is the vacuous shape this project
        keeps finding, and it was removed rather than left passing.
        """
        (tmp_path / ".rite").mkdir()

        class OneMessage:
            def __init__(self):
                self.polls = 0

            def poll(self):
                self.polls += 1
                return ("stop after this ticket",) if self.polls == 1 else ()

        def starter(root, manager, **kw):
            from rite_ai.managers.session import StartResult

            return StartResult(True, "ok", session="s1", attach="a", pane="%1")

        import rite_ai.managers.supervise as sup

        seen = {"n": 0}

        def liveness(name):
            seen["n"] += 1
            return type("L", (), {"alive": seen["n"] == 1})()

        original = sup.liveness
        sup.liveness = liveness
        try:
            supervise(
                tmp_path,
                "lead",
                engine="claude",
                max_sessions=1,
                window_seconds=0,
                verdict=lambda _r: "ready",
                starter=starter,
                slack=OneMessage(),
                note=lambda _m: None,
                poll=0.0,
            )
        finally:
            sup.liveness = original

        # The cycle that was running does NOT get it — that would be a second
        # delivery path. It waits in the inbox for the next instruction.
        waiting = [m.text for m in read(tmp_path, "lead", INBOX)]
        assert waiting == ["stop after this ticket"], (
            f"the Slack message did not land in the inbox: {waiting}"
        )

    def test_the_writer_is_the_validated_one_not_a_hand_written_file(self):
        """The Slack path calls `mailbox.send`, which is why an LLM emitting a
        malformed value cannot end a supervised run the way it once did."""
        import inspect

        import rite_ai.managers.supervise as sup

        source = inspect.getsource(sup.supervise)
        assert "send(\n" in source and "INBOX,\n" in source and "heard," in source


class TestASystemEventIsNotAnInstruction:
    """⚠ FOUND BY RUNNING IT, not by reading. The first live run delivered two
    messages to a Manager and both were `"<@U…> has joined the channel"` —
    `channel_join` events, authored by a real user, which the bot filter had
    no reason to drop. A Manager was being instructed by somebody joining a
    channel.
    """

    def test_a_join_event_is_not_delivered(self):
        joined = {
            "text": "<@U0C4HK552HF> has joined the channel",
            "ts": "20.0",
            "type": "message",
            "user": "U0C4HK552HF",
            "subtype": "channel_join",
        }
        got = _hear("C1", "t", call=_reply([joined]))
        assert got.texts == (), (
            "a channel-join event was handed to the Manager as an instruction"
        )

    def test_it_still_advances_the_cursor_past_one(self):
        joined = {"text": "x", "ts": "20.0", "subtype": "channel_join", "user": "U1"}
        assert _hear("C1", "t", call=_reply([joined])).newest == "20.0"

    def test_the_rule_is_an_allowlist_so_a_future_subtype_is_covered(self):
        """A list of subtypes to REFUSE would miss the next one Slack adds.
        A plain human message has no subtype at all, so that is the test."""
        invented = {
            "text": "pinned a message",
            "ts": "21.0",
            "user": "U1",
            "subtype": "a_subtype_that_does_not_exist_yet",
        }
        assert _hear("C1", "t", call=_reply([invented])).texts == ()

    def test_a_plain_human_message_still_gets_through(self):
        """The control: a filter that dropped everything would pass the three
        tests above."""
        plain = {
            "text": "do RT-14 first",
            "ts": "22.0",
            "type": "message",
            "user": "U1",
        }
        assert _hear("C1", "t", call=_reply([plain])).texts == ("do RT-14 first",)


class TestAReplyCanCarryAThreadRoot:
    """⚠ SHAPE, not a feature — nothing passes a thread yet. Measured
    2026-09-25: posting into a thread needs no scope beyond `chat:write`, only
    a `thread_ts`. The parameter exists so A4 can record roots without this
    signature changing under a caller."""

    def test_a_thread_root_becomes_thread_ts(self):
        sent = []
        _post(
            "C1",
            "t",
            "hello",
            thread="123.456",
            call=lambda m, tok, p, pay: sent.append(pay) or {"ok": True},
        )
        assert sent[0]["thread_ts"] == "123.456"

    def test_no_thread_root_sends_no_thread_ts(self):
        """A key Slack does not expect must not appear at all — an empty
        `thread_ts` is not the same as its absence."""
        sent = []
        _post(
            "C1",
            "t",
            "hello",
            call=lambda m, tok, p, pay: sent.append(pay) or {"ok": True},
        )
        assert "thread_ts" not in sent[0]
