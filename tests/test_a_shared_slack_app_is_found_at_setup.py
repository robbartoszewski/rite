"""A Slack app another project already uses is found when the token is set.

v0.7.0 dogfood S22a. `slack_app` bound an app to one project and refused the
second — but only where a LISTENER opens, inside `rite start`. So a token
belonging to another project was accepted by `rite credential set slack`,
stored, and configured, and the collision surfaced at the first run: after the
app had been made, the token stored and the project set up. The only guidance
was "give this project its own Slack app", which is the heaviest step in the
whole setup, offered last and with no mention of the scopes it needs.

Asked now where the token ARRIVES and where the project is CHECKED.

⚠ **Read only.** `bind` answers by binding, which neither of those may do:
`rite doctor` on a project that has never run would take the app for itself
and make the project that really uses it the second one. `holder_of` reads the
record and writes nothing, and that is asserted over the whole state range.

⚠ **Three answers, not two.** "Could not ask Slack" is neither "shared" nor
"clear". `bind` collapses it into a refusal, which is right when the choice is
whether to open a listener; in a report it would call a project faulty because
a network was unreachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rite_ai.managers.slack as slack_mod
import rite_ai.managers.slack_app as slack_app_mod
from rite_ai.cli.main import _doctor_slack
from rite_ai.managers.slack_app import Identity, bind, sharing

APP = Identity(team="T0TEAM", user="U0RITEBOT")
OWNER = "U0WNER"
TOKEN = "xoxb-shared"


class Answers:
    """`auth.test` only — the sharing check asks nothing else."""

    def __init__(self, who=None):
        self.who = who or {"ok": True, "team_id": APP.team, "user_id": APP.user}
        self.asked = 0

    def __call__(self, method, token, params=None, payload=None):
        if method == "auth.test":
            self.asked += 1
            if isinstance(self.who, Exception):
                raise self.who
            return self.who
        if method == "chat.postMessage":
            return {"ok": True, "channel": "D1", "ts": "1.0"}
        if method == "conversations.history":
            return {"ok": True, "messages": []}
        raise AssertionError(method)


@pytest.fixture
def answers(monkeypatch):
    a = Answers()
    monkeypatch.setattr(slack_mod, "_call", a)
    monkeypatch.setenv("RITE_SLACK_BOT_TOKEN", TOKEN)
    return a


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "config.yaml").write_text(
        f"ticket_backend:\n  type: none\nslack:\n  owner_user: {OWNER}\n"
        "  broadcast_channel: '#all-rite'\n"
    )
    return root


def _apps_dir() -> Path:
    import os

    return Path(os.environ["RITE_SLACK_APPS_DIR"])


def _bindings() -> set[str]:
    d = _apps_dir()
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


# --- the invariant, over every state the binding can be in -------------------

STATES = ["none", "this project", "another project", "unreadable", "cannot ask"]


def _put_state(state: str, mine: Path, theirs: Path, answers: Answers) -> None:
    if state == "this project":
        bind(APP, mine)
    elif state == "another project":
        bind(APP, theirs)
    elif state == "unreadable":
        bind(APP, theirs)
        next(iter(_apps_dir().iterdir())).write_text("{not json")
    elif state == "cannot ask":
        answers.who = {"ok": False}


EXPECTED = {
    "none": "ok",
    "this project": "ok",
    "another project": "shared",
    "unreadable": "ok",
    "cannot ask": "unknown",
}


@pytest.mark.parametrize("state", STATES)
def test_the_check_never_writes_a_binding(tmp_path, answers, state):
    """THE invariant that makes this safe to call from a report: asking never
    changes who the app belongs to, in any state. `bind` would."""
    mine, theirs = _project(tmp_path, "mine"), _project(tmp_path, "theirs")
    _put_state(state, mine, theirs, answers)
    before = _bindings()

    sharing(TOKEN, mine)
    slack_app_mod._holder_of(APP)

    assert _bindings() == before, f"{state}: the check changed the bindings"


@pytest.mark.parametrize("state", STATES)
def test_both_entry_points_give_the_same_verdict(tmp_path, answers, state):
    """Setup and doctor cannot disagree about whether an app is shared — one
    function answers both, over the whole range."""
    mine, theirs = _project(tmp_path, "mine"), _project(tmp_path, "theirs")
    _put_state(state, mine, theirs, answers)

    verdict = sharing(TOKEN, mine)

    assert verdict.kind == EXPECTED[state], f"{state}: {verdict}"
    assert verdict.is_a_problem == (verdict.kind == "shared")
    problems: list[str] = []
    _doctor_slack(mine, problems)
    # Only a KNOWN collision is counted against the project.
    assert bool(problems and "shared" in problems[0]) == (verdict.kind == "shared")


# --- what each reader is told -------------------------------------------------


def test_doctor_names_the_holder_a_dedicated_app_and_the_scope(tmp_path, answers):
    mine, theirs = _project(tmp_path, "mine"), _project(tmp_path, "theirs")
    bind(APP, theirs)
    problems: list[str] = []

    _doctor_slack(mine, problems)

    assert problems and "shared" in problems[0]
    said = problems[0]
    assert str(theirs) in said, "it names the project that holds the app"
    assert "own Slack app" in said and "api.slack.com/apps" in said
    assert "reactions:read" in said, "the scope a dedicated app also needs"


def test_an_unreachable_slack_is_said_but_not_counted(tmp_path, answers, capsys):
    """The S28 distinction, applied here: 'could not ask' is a gap in the
    report, not a fault in the project."""
    mine = _project(tmp_path, "mine")
    answers.who = {"ok": False}
    problems: list[str] = []

    _doctor_slack(mine, problems)
    out = capsys.readouterr().out

    assert "could not check" in out
    assert not [p for p in problems if "shared" in p]


def test_the_projects_own_app_is_silent(tmp_path, answers, capsys):
    mine = _project(tmp_path, "mine")
    bind(APP, mine)
    problems: list[str] = []

    _doctor_slack(mine, problems)

    assert "SHARED APP" not in capsys.readouterr().out
    assert not [p for p in problems if "shared" in p]


def test_credential_set_warns_after_storing_and_still_stores(tmp_path, monkeypatch):
    """Not a refusal. The token is real, the person may be moving a project
    onto its own app in either order, and refusing to store it would leave
    them unable to record the app they just made. `rite start` still refuses
    to OPEN a listener on it, which is where refusing belongs."""
    from click.testing import CliRunner

    import rite_ai.sandbox as sb
    from rite_ai.cli.init import run_init
    from rite_ai.cli.main import cli

    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setattr("keyring.set_password", lambda *a: None)
    monkeypatch.setenv("RITE_CREDENTIAL_DIR", str(tmp_path / "creds"))
    monkeypatch.setattr(slack_mod, "_call", Answers())
    theirs = _project(tmp_path, "theirs")
    bind(APP, theirs)
    mine = tmp_path / "mine"
    mine.mkdir()
    run_init(mine, yes=True)
    monkeypatch.chdir(mine)

    result = CliRunner().invoke(
        cli, ["credential", "set", "slack"], input=f"{TOKEN}\n{TOKEN}\n\n\n"
    )

    assert result.exit_code == 0, result.output
    assert "already used by another project" in result.output
    assert str(theirs) in result.output
    assert "reactions:read" in result.output
    assert "slack_bot_token" in result.output, "and it is still stored"
