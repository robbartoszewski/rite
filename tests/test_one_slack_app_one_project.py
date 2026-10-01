"""One Slack app, one project: a second project is refused, not advised.

SPEC §9.16.6 (D-101) said each project uses its own app, and nothing checked.
Two projects on one app with one Owner share the Owner's DM, so each took the
other's messages as INSTRUCTIONS. Measured through the production path
(`_slack_listener` → `Listener.open` → `poll`) on main `f9c98a8`: with both
running, both Managers took 10 of 10 DMs as INSTRUCTION; with one stopped,
the other took all 10 of the stopped project's DMs at its next start. Seen for
real in the v0.6.0 dogfood run, when a second project reused the first's bot
token. See `rite_ai.managers.slack_app`.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

import pytest

import rite_ai.managers.slack as slack_mod
from rite_ai.cli.main import _slack_listener
from rite_ai.managers import slack_app
from rite_ai.managers.slack_app import Bound, Identity, Refused, bind, identity_of

APP = Identity(team="T0TEAM", user="U0RITEBOT")
OWNER = "U0WNER"


class Workspace:
    """One app, one Owner, so one DM, `D1`."""

    def __init__(self, who: dict | None = None):
        self.dm: list[dict] = []
        self.posted = 0
        self.who = who or {"ok": True, "team_id": APP.team, "user_id": APP.user}

    def __call__(self, method, token, params=None, payload=None):
        args = payload or params or {}
        if method == "auth.test":
            if isinstance(self.who, Exception):
                raise self.who
            return self.who
        if method == "chat.postMessage":
            self.posted += 1
            channel = "D1" if args["channel"].startswith("U") else "C1"
            return {"ok": True, "channel": channel, "ts": f"100.{self.posted:04d}"}
        if method == "conversations.history":
            oldest = float(args.get("oldest") or 0)
            dm = self.dm if args["channel"] == "D1" else []
            got = [m for m in dm if float(m["ts"]) > oldest]
            return {"ok": True, "messages": list(reversed(got))}
        if method == "conversations.replies":
            return {"ok": True, "messages": [{"ts": args["ts"], "bot_id": "B0"}]}
        raise AssertionError(method)


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "config.yaml").write_text(
        f"ticket_backend:\n  type: none\nslack:\n  owner_user: {OWNER}\n"
        "  broadcast_channel: '#all-rite'\n"
    )
    return root


@pytest.fixture
def workspace(monkeypatch):
    ws = Workspace()
    monkeypatch.setattr(slack_mod, "_call", ws)
    monkeypatch.setenv("RITE_SLACK_BOT_TOKEN", "xoxb-shared")
    return ws


def _instructions(listener, ws: Workspace) -> int:
    got: list[str] = []
    for _ in range(6):
        got.extend(listener.poll(call=ws))
    return sum("INSTRUCTION" in text for text in got)


def _dm(ws: Workspace, n: int, at: int) -> None:
    for i in range(n):
        ws.dm.append(
            {"type": "message", "user": OWNER, "text": f"do {i}", "ts": f"{at}.{i:04d}"}
        )


class TestASecondProjectIsRefused:
    def test_while_both_run_only_the_bound_project_hears(self, tmp_path, workspace):
        first = _slack_listener(_project(tmp_path, "a"), "lead")
        second = _slack_listener(_project(tmp_path, "b"), "lead")
        _dm(workspace, 10, 200)

        assert first is not None
        assert second is None, "a second project on one app opened a listener"
        assert _instructions(first, workspace) == 10

    def test_nor_when_the_first_is_stopped(self, tmp_path, workspace):
        """A5: a DM sent while a project is down waits in Slack for ITS next
        start. Another project on the same app must not take it instead."""
        first = _slack_listener(_project(tmp_path, "a"), "lead")
        first.close(call=workspace)
        _dm(workspace, 10, 300)

        assert _slack_listener(_project(tmp_path, "b"), "lead") is None

    def test_refused_before_anything_is_posted_and_it_says_why(
        self, tmp_path, workspace, capsys
    ):
        _slack_listener(_project(tmp_path, "a"), "lead")
        before = workspace.posted
        capsys.readouterr()

        _slack_listener(_project(tmp_path, "b"), "lead")

        assert workspace.posted == before, "the refused project posted to Slack"
        err = capsys.readouterr().err
        assert "NOT reading or posting" in err
        assert str((tmp_path / "a").resolve()) in err
        assert "own Slack app" in err

    def test_the_bound_project_opens_again_on_its_next_run(self, tmp_path, workspace):
        root = _project(tmp_path, "a")
        _slack_listener(root, "lead").close(call=workspace)

        assert _slack_listener(root, "lead") is not None

    def test_it_is_keyed_on_the_app_not_on_where_the_token_came_from(
        self, tmp_path, workspace, monkeypatch
    ):
        """Two DIFFERENT tokens for one bot are one app: `auth.test` names
        the same workspace and bot user for both."""
        _slack_listener(_project(tmp_path, "a"), "lead")
        monkeypatch.setenv("RITE_SLACK_BOT_TOKEN", "xoxb-another-token-same-bot")

        assert _slack_listener(_project(tmp_path, "b"), "lead") is None

    def test_a_different_app_is_its_own(self, tmp_path, workspace):
        _slack_listener(_project(tmp_path, "a"), "lead")
        workspace.who = {"ok": True, "team_id": APP.team, "user_id": "U0OTHERBOT"}

        assert _slack_listener(_project(tmp_path, "b"), "lead") is not None


class TestWhereItCannotTellItRefuses:
    @pytest.mark.parametrize(
        "who",
        [
            ConnectionError("no network"),
            {"ok": False, "error": "invalid_auth"},
            {"ok": True, "user_id": APP.user},
            {"ok": True, "team_id": APP.team, "user_id": ""},
            {"ok": True, "team_id": "../../etc", "user_id": APP.user},
        ],
    )
    def test_no_identity_no_relay(self, tmp_path, workspace, who, capsys):
        workspace.who = who

        assert _slack_listener(_project(tmp_path, "a"), "lead") is None
        assert "cannot tell which Slack app" in capsys.readouterr().err

    def test_an_unreadable_binding_refuses(self, tmp_path):
        target = slack_app._binding_path(APP)
        target.parent.mkdir(parents=True)
        target.write_text("{ not json")

        out = bind(APP, _project(tmp_path, "a"))

        assert isinstance(out, Refused)
        assert "cannot be read" in out.reason


def test_identity_is_the_workspace_and_the_bot_user():
    ws = Workspace()
    assert identity_of("t", call=ws) == APP


def test_a_binding_is_written_whole(tmp_path):
    root = _project(tmp_path, "a")
    assert bind(APP, root) == Bound(identity=APP, newly=True)
    record = json.loads(slack_app._binding_path(APP).read_text())
    assert record["project"] == str(root.resolve())
    assert (record["team"], record["bot_user"]) == (APP.team, APP.user)
    assert not list(slack_app._apps_dir().glob(".bind-*")), "a temp file was left"


def _race(args) -> str:
    apps, root, barrier = args
    import os

    os.environ["RITE_SLACK_APPS_DIR"] = apps
    barrier.wait()
    out = bind(APP, Path(root))
    return root if isinstance(out, Bound) else ""


def test_projects_binding_one_app_at_once_get_one_winner(tmp_path):
    """Released together by a barrier, 20 rounds of 6. Measured against a
    check-then-write binding, the shape a narrower fix would take, which let
    several projects bind one app in the same round."""
    ctx = mp.get_context("spawn")
    with ctx.Manager() as manager, ctx.Pool(6) as pool:
        for n in range(20):
            base = tmp_path / f"r{n}"
            roots = [str(_project(base, f"p{i}")) for i in range(6)]
            barrier = manager.Barrier(6)  # ONE for the round, or each waits alone
            won = pool.map(_race, [(str(base / "apps"), r, barrier) for r in roots])
            assert len([w for w in won if w]) == 1, (n, won)
