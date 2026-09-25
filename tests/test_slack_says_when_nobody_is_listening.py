"""What happens to a Slack message when no Manager is running (A5).

There is no daemon (Decision 2), so a message sent while nothing runs stays in
Slack. That is HANDLED: the relay remembers where it stopped reading, so the
next `rite start` delivers it. And it is STATED: the last thing a run says in
each conversation is that it has stopped and what happens to a message now.
A user who is not told reads the silence as "it is broken".
"""

from __future__ import annotations

from rite_ai.managers.slack import HISTORY_PAGES, READ_LIMIT, Listener, _hear

OWNER = "U0WNER"


class Slack:
    def __init__(self):
        self.history: dict[str, list[dict]] = {"D1": [], "C1": []}
        self.posts: list[dict] = []

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
            return {"ok": True, "channel": channel, "ts": f"{500 + len(self.posts)}.0"}
        if method == "conversations.history":
            return {"ok": True, "messages": self._newest_first(args)}
        if method == "conversations.replies":
            return {"ok": True, "messages": []}
        raise AssertionError(method)

    def _newest_first(self, args):
        oldest = float(args.get("oldest") or 0)
        got = [m for m in self.history[args["channel"]] if float(m["ts"]) > oldest]
        return list(reversed(got))

    def say(self, channel, text, ts):
        self.history[channel].append({"user": OWNER, "text": text, "ts": ts})


def _run(tmp_path, slack):
    listener = Listener(
        token="t",
        manager="lead",
        owner=OWNER,
        broadcast="#all-rite",
        project=tmp_path,
        clock=lambda: 600.0,
    )
    listener.open(call=slack)
    return listener


def _heard(listener, slack, polls=4):
    return [m for _ in range(polls) for m in listener.poll(call=slack)]


class TestAMessageSentWhileStoppedIsDeliveredAtTheNextStart:
    def test_it_arrives_labelled_as_before_and_the_gap_is_said(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        slack = Slack()
        first = _run(tmp_path, slack)
        slack.say("D1", "before stopping", "501.5")
        assert len(_heard(first, slack)) == 1
        first.close(call=slack)

        # Between the stop line (504) and the next start line (505).
        slack.say("D1", "while you were stopped", "504.5")
        again = _run(tmp_path, slack)
        got = _heard(again, slack)
        assert got == [
            "[Owner's DM · addressed · INSTRUCTION]\n> while you were stopped"
        ]
        news = again.news()
        assert any("1 message(s)" in n and "not running" in n for n in news), news

    def test_the_first_run_ever_does_not_replay_the_history(self, tmp_path):
        """Switching Slack on must not turn a DM's history into instructions."""
        (tmp_path / ".rite").mkdir()
        slack = Slack()
        slack.say("D1", "an old conversation", "100.0")
        assert _heard(_run(tmp_path, slack), slack) == []

    def test_what_was_delivered_is_not_delivered_again(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        slack = Slack()
        first = _run(tmp_path, slack)
        slack.say("D1", "once", "501.5")
        _heard(first, slack)
        first.close(call=slack)
        assert _heard(_run(tmp_path, slack), slack) == []


class TestTheStopIsSaidWhereThePersonIs:
    def test_both_conversations_are_told(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        slack = Slack()
        listener = _run(tmp_path, slack)
        lines = listener.close(call=slack)
        stops = [p for p in slack.posts if "has stopped" in p["text"]]
        assert {p["channel"] for p in stops} == {"D1", "C1"}
        assert "rite start lead" in stops[0]["text"]
        assert any("delivered when it next starts" in line for line in lines)

    def test_rite_start_closes_the_relay_even_on_ctrl_c(self):
        """In a `finally`, so an interrupted run still says it stopped."""
        import inspect

        import rite_ai.cli.main as cli

        source = inspect.getsource(cli)
        body = source[source.index("listener = _slack_listener(root, role.name)") :]
        assert body.index("finally:") < body.index("listener.close()")


class TestALongGapIsReadWhole:
    def test_more_than_one_page_is_read_oldest_first(self):
        """History comes newest first, fifty at a time. Unpaged, a gap of
        sixty would deliver the newest fifty and move the cursor past ten."""
        messages = [
            {"user": OWNER, "text": f"m{i}", "ts": f"{100 + i}.0"} for i in range(60)
        ]

        def call(method, token, params=None, payload=None):
            newest_first = list(reversed(messages))
            start = int(params.get("cursor") or 0)
            page = newest_first[start : start + READ_LIMIT]
            more = start + READ_LIMIT < len(newest_first)
            return {
                "ok": True,
                "messages": page,
                "has_more": more,
                "response_metadata": {
                    "next_cursor": str(start + READ_LIMIT) if more else ""
                },
            }

        got = _hear("D1", "t", since="99.0", call=call)
        assert got.texts == tuple(f"m{i}" for i in range(60))
        assert got.newest == "159.0" and not got.skipped

    def test_a_gap_past_the_page_limit_is_reported_not_hidden(self):
        def call(method, token, params=None, payload=None):
            n = int(params.get("cursor") or 0)
            return {
                "ok": True,
                "messages": [{"user": OWNER, "text": "x", "ts": f"{1000 - n}.0"}],
                "has_more": True,
                "response_metadata": {"next_cursor": str(n + 1)},
            }

        got = _hear("D1", "t", since="1.0", call=call)
        assert got.skipped and len(got.messages) == HISTORY_PAGES
