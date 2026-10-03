"""`rite doctor` checks Slack without posting where the person reads (RS3).

Robert, 2026-09-29: "rite doctor: checking that rite can reach this
conversation" landed in his DM, a test artefact in a production conversation,
every time doctor ran. Now a plain `rite doctor` posts nothing at all. It reads
the app's scopes from what Slack reports, reads the DM and the broadcast
channel under the ids the Owner's relay remembered (or opens the DM where the
app has `im:write`), and says "not checked" for what it cannot settle without
a post. `rite doctor --network`, where the person asks for a message that
arrives, posts one line to the status channel, never to the DM.
"""

from __future__ import annotations

import json

import pytest

import rite_ai.managers.slack as slack_mod
from rite_ai.cli.main import _doctor_slack
from rite_ai.managers import manager_dir
from rite_ai.managers.slack import SCOPES_HEADER, probe

OWNER = "U0C4HK552HF"
ALL = "chat:write,channels:history,im:history"
KNOWN = {
    "dm": {"user": OWNER, "channel": "D1"},
    "broadcast": {"name": "#all-rite", "channel": "C1"},
}


class Slack:
    """A fake Slack that records every call. `scopes` is what the scopes
    header says (None: Slack sent none); `errors` maps `method` or
    `method:channel` to an error; `im_write` makes `conversations.open` work."""

    def __init__(self, *, scopes=ALL, errors=None, im_write=False):
        self.scopes, self.errors, self.im_write = scopes, errors or {}, im_write
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, token, params=None, payload=None):
        args = payload or params or {}
        target = str(args.get("channel") or args.get("users") or "")
        self.calls.append((method, target))
        error = self.errors.get(f"{method}:{target}") or self.errors.get(method)
        if error:
            return {"ok": False, "error": error, "needed": "im:history"}
        if method == "auth.test":
            got = {"ok": True, "user_id": "UBOT", "team_id": "T1"}
            if self.scopes is not None:
                got[SCOPES_HEADER] = self.scopes
            return got
        if method == "conversations.open":
            if not self.im_write:
                return {"ok": False, "error": "missing_scope", "needed": "im:write"}
            return {"ok": True, "channel": {"id": "D9"}}
        return {"ok": True, "channel": "C1", "ts": "100.5", "messages": []}

    def posted(self) -> list[str]:
        return [t for m, t in self.calls if m == "chat.postMessage"]


def _probe(slack, *, known=KNOWN, owner=OWNER, status="#rite-status"):
    return probe(owner, "#all-rite", "t", status=status, known=known, call=slack)


def _project(tmp_path, *, known: dict | None = None, manager="lead"):
    root = tmp_path / "p"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
    )
    (root / ".rite" / "config.yaml").write_text(
        f"ticket_backend:\n  type: none\ncoordination:\n  managers:\n    - {manager}\n"
        f"slack:\n  owner_user: {OWNER}\n  broadcast_channel: '#all-rite'\n"
    )
    if known is not None:
        state = manager_dir(root, manager) / "slack.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"known": known}) + "\n")
    return root


@pytest.fixture
def slack(monkeypatch):
    """The fake Slack as the transport every Slack call in doctor goes
    through, including the shared-app check."""
    fake = Slack()
    monkeypatch.setattr(slack_mod, "_call", fake)
    monkeypatch.setenv("RITE_SLACK_BOT_TOKEN", "xoxb-1")
    return fake


class TestDoctorPostsNothing:
    @pytest.mark.parametrize(
        "known", [KNOWN, None], ids=["after-a-start", "before-the-first-start"]
    )
    def test_doctor_posts_nothing_to_the_dm_or_anywhere(
        self, tmp_path, slack, capsys, known
    ):
        """THE property, through the real `_doctor_slack` and the real
        `probe`. Before the first start is the case main got wrong most
        visibly: with no DM id known, it posted to the Owner's user id to
        find one."""
        root = _project(tmp_path, known=known)

        _doctor_slack(root, [])
        out = capsys.readouterr().out

        assert slack.posted() == [], f"doctor posted: {slack.calls}"
        assert "slack command channel (the Owner's DM)" in out, out

    def test_after_a_start_both_conversations_are_read_by_their_ids(
        self, tmp_path, slack, capsys
    ):
        root = _project(tmp_path, known=KNOWN)
        problems: list[str] = []

        _doctor_slack(root, problems)
        out = capsys.readouterr().out

        # `conversations.history` takes neither a user id nor a name
        # (measured), so reading needs the ids the relay learned.
        assert ("conversations.history", "D1") in slack.calls
        assert ("conversations.history", "C1") in slack.calls
        assert "app scopes: ok" in out
        assert problems == []

    def test_network_posts_its_one_line_to_the_status_channel(
        self, tmp_path, slack, capsys
    ):
        root = _project(tmp_path, known=KNOWN)

        _doctor_slack(root, [], network=True)

        assert slack.posted() == ["#rite-status"]
        assert "slack delivery: ok — posted to #rite-status" in capsys.readouterr().out


class TestWhatTheProbeReports:
    def test_a_dm_that_cannot_be_read_names_the_missing_scope(self):
        """Posting to the DM works on `chat:write` alone, so a check that only
        posted would pass a relay that can never hear the Owner."""
        dm = _probe(Slack(errors={"conversations.history:D1": "missing_scope"}))[1]
        assert dm.ok is False
        assert "missing_scope" in dm.detail and "im:history" in dm.detail

    def test_a_broadcast_channel_rite_is_not_in_names_the_fix(self):
        got = _probe(Slack(errors={"conversations.history:C1": "not_in_channel"}))
        broadcast = got[2]
        assert broadcast.ok is False
        assert "not_in_channel" in broadcast.detail and "/invite" in broadcast.detail

    def test_a_missing_scope_is_named_from_what_slack_reports(self):
        scopes = _probe(Slack(scopes="chat:write"))[0]
        assert scopes.ok is False
        assert "channels:history" in scopes.detail and "im:history" in scopes.detail

    def test_scopes_slack_did_not_report_are_not_checked_not_passed(self):
        scopes = _probe(Slack(scopes=None))[0]
        assert scopes.ok is None and "did not report" in scopes.detail

    def test_before_the_first_start_the_targets_are_not_checked_rather_than_ok(
        self,
    ):
        slack = Slack()
        _, dm, broadcast, status = _probe(slack, known={})
        assert dm.ok is None and "first `rite start`" in dm.detail
        assert broadcast.ok is None and "/invite @rite" in broadcast.detail
        assert status.ok is None and "--network" in status.detail
        assert slack.posted() == []

    def test_with_im_write_the_dm_is_opened_and_read_without_a_post(self):
        slack = Slack(im_write=True)
        dm = _probe(slack, known={})[1]
        assert dm.ok is True and "D9" in dm.detail
        assert slack.posted() == []

    def test_a_dm_remembered_for_another_owner_is_not_used(self):
        """The relay keeps the id for the Owner it learned it for; a changed
        `owner_user` must not be checked against the old Owner's DM."""
        slack = Slack()
        dm = _probe(slack, known={"dm": {"user": "UOTHER", "channel": "D1"}})[1]
        assert dm.ok is None
        assert ("conversations.history", "D1") not in slack.calls

    def test_broadcast_only_probes_no_dm_and_needs_no_dm_scope(self):
        got = _probe(Slack(scopes="chat:write,channels:history"), owner="")
        assert not any("Owner's DM" in p.target for p in got)
        assert got[0].ok is True


class TestNotCheckedIsNotAProblem:
    def test_before_the_first_start_doctor_counts_nothing(
        self, tmp_path, monkeypatch, capsys
    ):
        """S28's rule: "could not check" is a gap in the report, not a fault in
        the project. A new project must not fail doctor for not having run."""
        fake = Slack(scopes=None)
        monkeypatch.setattr(slack_mod, "_call", fake)
        monkeypatch.setenv("RITE_SLACK_BOT_TOKEN", "xoxb-1")
        root = _project(tmp_path, known=None)
        problems: list[str] = []

        _doctor_slack(root, problems)
        out = capsys.readouterr().out

        assert [p for p in problems if p.startswith("slack ")] == [], problems
        assert out.count(": not checked") >= 3, out


def test_doctor_reads_the_relays_state_and_never_writes_it(tmp_path, slack):
    """A running `rite start` owns the relay's state and rewrites it whole;
    doctor writing it would race that. So doctor reads the remembered ids and
    leaves the file byte for byte as it was."""
    root = _project(tmp_path, known=KNOWN)
    state = manager_dir(root, "lead") / "slack.json"
    before = state.read_bytes()

    _doctor_slack(root, [])

    assert ("conversations.history", "D1") in slack.calls, "the state was not read"
    assert state.read_bytes() == before


class _Response:
    def __init__(self, body: dict, headers: dict):
        self._body, self.headers = json.dumps(body).encode(), headers

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (
            {SCOPES_HEADER: "chat:write, channels:history"},
            {"chat:write", "channels:history"},
        ),
        ({}, None),
    ],
    ids=["header-sent", "no-header"],
)
def test_the_scopes_come_from_the_header_of_a_real_response(
    monkeypatch, headers, expected
):
    """The fakes above hand `probe` the header directly; this is the one place
    the real transport is exercised: `_call` keeps the header Slack sent, and
    a response without one is "Slack did not say", never an empty list."""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: _Response({"ok": True}, headers),
    )
    got = slack_mod._scopes_of("xoxb-1", call=slack_mod._call)
    assert got == (frozenset(expected) if expected is not None else None)
