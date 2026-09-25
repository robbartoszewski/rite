"""Slack's two targets are configuration, with different authority (A6).

⚠ **AUTHORITY COMES FROM THE CHANNEL (SPEC §9.16.2, D-95).** The command
channel is the Owner's DM with the app, named by the Owner's user id. It is
NOT a configurable channel id, because a configurable one can be pointed at a
channel the whole workspace posts in. The broadcast channel is configurable,
defaults to `#all-rite`, and never carries authority.
"""

from __future__ import annotations

from rite_ai.cli.init.scaffold import config_to_yaml
from rite_ai.config.models import SlackConfig
from rite_ai.config.parse import ParseError, parse_config
from rite_ai.managers.slack import Listener, probe


def _config(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return parse_config(path)


class TestTheCommandChannelIsNotConfigurable:
    def test_a_command_channel_is_refused_and_says_why(self, tmp_path):
        """Refused rather than ignored: ignored, a Manager that used to listen
        would go quietly deaf; read, authority stays configurable."""
        got = _config(tmp_path, "slack:\n  command_channel: C0C4KB709T6\n")
        assert isinstance(got, ParseError)
        assert "Owner's DM" in got.message and "owner_user" in got.message

    def test_the_owner_is_a_user_id_not_a_name(self, tmp_path):
        got = _config(tmp_path, "slack:\n  owner_user: robert\n")
        assert isinstance(got, ParseError) and "user id" in got.message

    def test_a_broadcast_channel_is_a_name_or_an_id(self, tmp_path):
        got = _config(tmp_path, "slack:\n  broadcast_channel: all-rite\n")
        assert isinstance(got, ParseError) and "broadcast_channel" in got.message
        assert not isinstance(
            _config(tmp_path, "slack:\n  broadcast_channel: '#team-x'\n"), ParseError
        )
        assert not isinstance(
            _config(tmp_path, "slack:\n  broadcast_channel: C0C4KB709T6\n"),
            ParseError,
        )


class TestTheBroadcastDefault:
    def test_nothing_configured_means_slack_is_off(self):
        assert not SlackConfig().enabled and SlackConfig().broadcast == ""

    def test_an_owner_alone_broadcasts_to_all_rite(self, tmp_path):
        got = _config(tmp_path, "slack:\n  owner_user: U0C4HK552HF\n")
        assert got.slack.enabled and got.slack.broadcast == "#all-rite"

    def test_a_configured_broadcast_wins(self):
        assert SlackConfig(broadcast_channel="#ops").broadcast == "#ops"

    def test_the_section_round_trips_through_the_writer(self, tmp_path):
        """⚠ A key the writer drops is one `rite schedule set` deletes from a
        real project the next time it rewrites the file."""
        got = _config(
            tmp_path,
            "slack:\n  owner_user: U0C4HK552HF\n  broadcast_channel: '#ops'\n",
        )
        again = _config(tmp_path, config_to_yaml(got))
        assert again.slack == got.slack


def _slack(errors: dict | None = None, *, dm="D1", channel="C1"):
    """A fake Slack: `errors` maps a method, or `method:target`, to an error."""
    errors = errors or {}
    calls: list[tuple] = []

    def call(method, token, params=None, payload=None):
        target = (payload or params or {}).get("channel", "")
        calls.append((method, target))
        error = errors.get(f"{method}:{target}") or errors.get(method)
        if error:
            return {"ok": False, "error": error, "needed": "im:history"}
        resolved = dm if str(target).startswith("U") else channel
        return {"ok": True, "channel": resolved, "ts": "100.5", "messages": []}

    return call, calls


class TestDoctorNamesSlacksOwnError:
    def test_both_targets_are_probed_for_posting_and_reading(self):
        call, calls = _slack()
        got = probe("U1", "#all-rite", "t", call=call)
        assert [p.ok for p in got] == [True, True]
        # Reading is probed with the id the post RESOLVED, not the user id or
        # the name — `conversations.history` takes neither (measured).
        assert ("conversations.history", "D1") in calls
        assert ("conversations.history", "C1") in calls

    def test_a_dm_that_cannot_be_read_names_the_missing_scope(self):
        """The case that matters: posting to the DM works on `chat:write`
        alone, so a probe that only posted would pass a relay that can never
        hear the Owner."""
        call, _ = _slack({"conversations.history:D1": "missing_scope"})
        owner, _broadcast = probe("U1", "#all-rite", "t", call=call)
        assert not owner.ok
        assert "missing_scope" in owner.detail and "im:history" in owner.detail

    def test_not_in_channel_is_named_with_the_fix(self):
        call, _ = _slack({"chat.postMessage:#all-rite": "not_in_channel"})
        _owner, broadcast = probe("U1", "#all-rite", "t", call=call)
        assert not broadcast.ok
        assert "not_in_channel" in broadcast.detail and "/invite" in broadcast.detail

    def test_broadcast_only_probes_one_target(self):
        call, _ = _slack()
        assert [p.target for p in probe("", "#all-rite", "t", call=call)] == [
            "broadcast channel #all-rite"
        ]


class TestTheListenerStartsWhereItSaidItWasListening:
    def test_no_owner_is_said_to_be_broadcast_only(self):
        call, calls = _slack()
        listener = Listener(token="t", manager="m", broadcast="#all-rite")
        lines = listener.open(call=call)
        assert any("broadcast-only" in line for line in lines)
        listener.poll(call=call)
        read = [t for m, t in calls if m == "conversations.history"]
        assert read == ["C1"], "with no Owner, only the broadcast channel is read"

    def test_opening_learns_both_ids_and_starts_each_cursor_at_its_start_line(self):
        """So a restart does not re-read the last fifty messages as new."""
        call, _ = _slack()
        listener = Listener(token="t", manager="m", owner="U1", broadcast="#b")
        listener.open(call=call)
        assert listener.dm == "D1" and listener.broadcast_id == "C1"
        assert listener.since == {"D1": "100.5", "C1": "100.5"}

    def test_a_failing_poll_is_said_once_not_every_tick(self):
        call, _ = _slack({"conversations.history": "missing_scope"})
        listener = Listener(token="t", manager="m", owner="U1")
        listener.open(call=call)
        for _ in range(5):
            listener.poll(call=call)
        news = listener.news()
        assert len(news) == 1 and "missing_scope" in news[0]
        listener.poll(call=call)
        assert listener.news() == []
