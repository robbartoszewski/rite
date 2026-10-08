"""What happens to a Slack message when no Manager is running (A5).

There is no daemon (Decision 2), so a message sent while nothing runs stays in
Slack. That is HANDLED: the relay remembers where it stopped reading, so the
next `rite start` delivers it. And it is STATED: the last thing a run says in
each conversation is that it has stopped and what happens to a message now.
A user who is not told reads the silence as "it is broken".
"""

from __future__ import annotations

import re

from rite_ai.managers.slack import HISTORY_PAGES, READ_LIMIT, Listener, _hear

OWNER = "U0WNER"


def _bare(text: str) -> str:
    """The header without its send time, which is pinned on its own below."""
    return re.sub(r" · sent \w{3} \d\d:\d\d", "", text)


# Each configured target resolves to a distinct id, so a test can tell the
# DM, the broadcast channel and the status channel apart in `posts`.
_CHANNELS = {"#all-rite": "CB", "#rite-status": "CS"}


class Slack:
    def __init__(self, *, im_write: bool = False, status_ok: bool = True):
        self.history: dict[str, list[dict]] = {"D1": [], "CB": []}
        self.posts: list[dict] = []
        # Whether the app has `im:write`: with it, `conversations.open` learns
        # the DM id without posting; without it, the id is learned by posting
        # once (RS1).
        self.im_write = im_write
        # Whether the status channel can be posted to; False is "the app is not
        # in it", so the line must stay on the terminal and never fall back.
        self.status_ok = status_ok

    def __call__(self, method, token, params=None, payload=None):
        args = payload or params or {}
        if method == "auth.test":
            return {"ok": True, "user_id": "UR1TE"}
        if method == "conversations.open":
            return (
                {"ok": True, "channel": {"id": "D1"}}
                if self.im_write
                else {"ok": False}
            )
        if method == "chat.postMessage":
            target = args["channel"]
            if target == "#rite-status" and not self.status_ok:
                return {"ok": False, "error": "channel_not_found"}
            channel = "D1" if target.startswith("U") else _CHANNELS.get(target, target)
            # Record the RESOLVED id, so a test tells the conversations apart by
            # id whether the caller named a user, a channel name, or an id.
            self.posts.append({**args, "channel": channel})
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
        status="#rite-status",
        project_name="acme",
        project=tmp_path,
        clock=lambda: 600.0,
    )
    listener.open(call=slack)
    return listener


def _heard(listener, slack, polls=4):
    return [_bare(m) for _ in range(polls) for m in listener.poll(call=slack)]


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
            "[Owner's DM · addressed · message 504.5 · INSTRUCTION]\n"
            "> while you were stopped"
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


class TestStartsAndStopsLeaveTheDM:
    """RS1 (Robert, 2026-09-29): "it must go to a separate #rite-status
    channel or something. It's a spam anywhere else." Starts and stops are
    said in the status channel, naming the project; never in the DM or the
    broadcast channel. The DM is for what needs the person."""

    def test_the_start_line_is_said_only_in_the_status_channel(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        slack = Slack()
        _run(tmp_path, slack)
        running = [p for p in slack.posts if "is running" in p["text"]]
        assert [p["channel"] for p in running] == ["CS"]
        assert "`acme`" in running[0]["text"]
        # Neither the DM nor the broadcast channel hears a start line.
        assert not any(
            "is running" in p["text"]
            for p in slack.posts
            if p["channel"] in ("D1", "CB")
        )

    def test_the_stop_line_is_said_only_in_the_status_channel(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        slack = Slack()
        listener = _run(tmp_path, slack)
        slack.posts.clear()
        listener.close(call=slack)
        stops = [p for p in slack.posts if "has stopped" in p["text"]]
        assert [p["channel"] for p in stops] == ["CS"]
        assert "rite start lead" in stops[0]["text"]

    def test_a_second_start_posts_nothing_to_the_dm_or_broadcast(self, tmp_path):
        """The DM and broadcast ids are learned once and remembered, so a
        later start posts only the status line."""
        (tmp_path / ".rite").mkdir()
        slack = Slack()
        _run(tmp_path, slack)
        slack.posts.clear()
        _run(tmp_path, slack)
        assert [p["channel"] for p in slack.posts] == ["CS"]

    def test_with_im_write_the_dm_is_never_posted_to(self, tmp_path):
        """`conversations.open` learns the DM id without posting, so even the
        first run posts nothing to the DM."""
        (tmp_path / ".rite").mkdir()
        slack = Slack(im_write=True)
        listener = _run(tmp_path, slack)
        assert listener.dm == "D1"
        assert not any(p["channel"] == "D1" for p in slack.posts)

    def test_the_undelivered_warning_stays_in_the_dm(self, tmp_path):
        """A message rite took from Slack but never delivered needs the
        person, where they typed it — and that is the one stop line in the DM.
        The plain stop still goes only to the status channel."""
        (tmp_path / ".rite").mkdir()
        slack = Slack()
        listener = _run(tmp_path, slack)
        slack.posts.clear()
        listener.close(call=slack, undelivered='your "ship it" was not delivered')
        dm = [p for p in slack.posts if p["channel"] == "D1"]
        assert len(dm) == 1
        assert "was not delivered" in dm[0]["text"] and "has stopped" in dm[0]["text"]
        assert any(
            p["channel"] == "CS" and "has stopped" in p["text"] for p in slack.posts
        )

    def test_a_status_channel_rite_cannot_post_to_never_falls_back(self, tmp_path):
        """The fallback that would post to the DM IS the spam this removes. A
        status channel rite cannot reach leaves the line on the terminal."""
        (tmp_path / ".rite").mkdir()
        slack = Slack(im_write=True, status_ok=False)
        listener = _run(tmp_path, slack)
        # The failed status line is not retried in the DM or the broadcast
        # channel: no lifecycle line reaches either, and im:write means the DM
        # gets no post at all.
        assert not any(
            ("is running" in p["text"] or "has stopped" in p["text"])
            for p in slack.posts
            if p["channel"] in ("D1", "CB")
        )
        assert not any(p["channel"] == "D1" for p in slack.posts)
        lines = listener.close(call=slack)
        assert any("cannot post to the status channel" in line for line in lines)
        assert not any(p["channel"] == "D1" for p in slack.posts)

    def test_rite_start_closes_the_relay_even_on_ctrl_c(self):
        """In a `finally`, so an interrupted run still says it stopped."""
        import inspect

        import rite_ai.cli.main as cli

        source = inspect.getsource(cli)
        body = source[source.index("listener = _slack_listener(root, role.name)") :]
        assert body.index("finally:") < body.index("listener.close(")
        # And the undelivered count goes with it, inside the same `finally`.
        assert body.index("finally:") < body.index("undelivered_line(")


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
