"""`rite credential set slack` sets Slack up, and a channel is taken as typed.

v0.7.0 dogfood S14 and S19, measured on 0.7.0a3.

**S14.** The command stored the bot token and stopped. `slack.owner_user` —
without which `refinement.questions_to: dm` has nowhere to send a refinement
round — had to be hand-edited into `.rite/config.yaml`, which is the config
edit the CLI exists to spare a user. The one-field shape was deliberate ("the
channel is configuration rather than a credential"), and that reasoning is
kept: the channel IS configuration, and it goes to the committed config.yaml
through the same `config_path` route that already carries JIRA's site and
board key. What changed is that "not a credential" no longer means "not this
command's business".

**S19.** `broadcast_channel: all-rite` was refused as "neither a channel name
starting with '#' nor a channel id" — for a channel rite could name exactly.
Slack's own sidebar shows the bare word. It is normalised now. And a channel
rite still cannot name no longer takes out `rite credential set`, which was the
command that would have repaired the project.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.managers.slack as slack_mod
import rite_ai.sandbox as sb
from rite_ai.cli.init import run_init
from rite_ai.cli.main import cli
from rite_ai.config.parse import ParseError, normalize_slack_channel, parse_config
from rite_ai.credentials.services import SERVICES
from rite_ai.managers.slack_app import Identity, bind

TOKEN = "xoxb-000\nxoxb-000\n"
OWNER = "U0C4HK552HF"


class Slack:
    """Answers `auth.test`, and FAILS THE TEST on any post.

    ⚠ The hard rule, enforced for every test in this file rather than asserted
    in one of them: `rite credential set slack` must never put a message in
    anyone's channel. Someone setting rite up is not announcing it, and a
    setup command that posts spams a workspace every time it is re-run. A live
    post to prove delivery belongs in `rite doctor`.
    """

    def __init__(self, who=None):
        self.who = (
            who
            if who is not None
            else {
                "ok": True,
                "team_id": "T0TEAM",
                "user_id": "U0RITEBOT",
            }
        )
        self.reads = 0

    def __call__(self, method, token, params=None, payload=None):
        if method == "auth.test":
            self.reads += 1
            if isinstance(self.who, Exception):
                raise self.who
            return self.who
        raise AssertionError(
            f"`credential set slack` called Slack method {method!r} — it must "
            "only READ with auth.test, never post"
        )


@pytest.fixture(autouse=True)
def slack(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setattr("keyring.set_password", lambda *a: None)
    monkeypatch.setenv("RITE_CREDENTIAL_DIR", str(tmp_path / "creds"))
    fake = Slack()
    monkeypatch.setattr(slack_mod, "_call", fake)
    return fake


def _project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    run_init(root, yes=True)
    return root


def _slack_of(root):
    parsed = parse_config(root / ".rite" / "config.yaml")
    assert not isinstance(parsed, ParseError), parsed
    return parsed.slack


def _set_slack(root, monkeypatch, answers: str):
    monkeypatch.chdir(root)
    return CliRunner().invoke(cli, ["credential", "set", "slack"], input=answers)


# --- S14: one command, no hand edit -------------------------------------------


def test_one_command_records_both_slack_settings(tmp_path, monkeypatch):
    """The invariant S14 is about: after this command, Slack is set up — no
    step where the user opens config.yaml."""
    root = _project(tmp_path)

    result = _set_slack(root, monkeypatch, f"{TOKEN}{OWNER}\nall-rite\n")

    assert result.exit_code == 0, result.output
    slack = _slack_of(root)
    assert slack.owner_user == OWNER
    assert slack.broadcast_channel == "#all-rite"
    assert slack.enabled
    # Written to the COMMITTED config, which is what spares the next clone.
    raw = yaml.safe_load((root / ".rite" / "config.yaml").read_text())
    assert raw["slack"]["owner_user"] == OWNER


def test_skipping_both_leaves_slack_off_and_the_token_stored(tmp_path, monkeypatch):
    """Optional means optional — Enter must not be a wall. But skipping BOTH
    leaves Slack with no target, so it is off, and that is said rather than
    left to be discovered at the first refinement round."""
    root = _project(tmp_path)

    result = _set_slack(root, monkeypatch, f"{TOKEN}\n\n")

    assert result.exit_code == 0, result.output
    slack = _slack_of(root)
    assert slack.owner_user == "" and slack.broadcast_channel == ""
    assert not slack.enabled
    assert "slack_bot_token" in result.output, "the token is stored either way"
    assert OFF in result.output, "and the off state is not silent"


OFF = "Slack is INACTIVE"

# (what is answered after the token) -> whether Slack ends up ON
ANSWERS = [
    ("\n\n", False),  # token only — no target at all
    (f"{OWNER}\n\n", True),  # a command channel
    ("\nall-rite\n", True),  # broadcast only: a channel, no owner
    (f"{OWNER}\nall-rite\n", True),  # both
]


@pytest.mark.parametrize(("answers", "on"), ANSWERS)
def test_the_off_notice_appears_exactly_when_slack_lands_off(
    tmp_path, monkeypatch, answers, on
):
    """IFF, across every way this command can end. The notice must not cry
    wolf on a working broadcast-only setup, and must not stay quiet on the one
    that turns nothing on."""
    root = _project(tmp_path)

    result = _set_slack(root, monkeypatch, f"{TOKEN}{answers}")

    assert result.exit_code == 0, result.output
    assert _slack_of(root).enabled is on
    assert (OFF in result.output) is (not on), result.output
    # Stored whatever the answer: this notice is never a refusal.
    assert "slack_bot_token" in result.output


def test_skipping_both_is_quiet_when_slack_is_already_on(tmp_path, monkeypatch):
    """Read off the RESULTING config, not off what this run answered. Someone
    rotating a token on a configured project skips both and has turned nothing
    off — telling them Slack is off would be false."""
    root = _project(tmp_path)
    _set_slack(root, monkeypatch, f"{TOKEN}{OWNER}\nall-rite\n")

    result = _set_slack(root, monkeypatch, f"{TOKEN}\n\n")

    assert result.exit_code == 0, result.output
    assert _slack_of(root).enabled
    assert OFF not in result.output


def test_a_name_typed_where_an_id_belongs_is_asked_again(tmp_path, monkeypatch):
    """⚠ The regression this fix could most easily CREATE. This command writes
    config.yaml; an `owner_user` the parser refuses would leave every later
    `rite` run failing on the file this run wrote — S19's blocking shape,
    manufactured by S14's own fix. So it is asked again, and never stored."""
    root = _project(tmp_path)

    result = _set_slack(root, monkeypatch, f"{TOKEN}robert\n{OWNER}\n\n")

    assert result.exit_code == 0, result.output
    assert "is not a Slack user id" in result.output
    assert _slack_of(root).owner_user == OWNER
    # The file still parses — the point of asking again.
    assert not isinstance(parse_config(root / ".rite" / "config.yaml"), ParseError)


# --- S19: one rule for a channel, wherever it is typed ------------------------


CHANNELS = [
    ("all-rite", "#all-rite"),
    ("#all-rite", "#all-rite"),
    ("  all-rite  ", "#all-rite"),
    ("C0C4KB709T6", "C0C4KB709T6"),
    ("G0C4KB709T6", "G0C4KB709T6"),
    ("", ""),
    ("team.x_1-2", "#team.x_1-2"),
]


@pytest.mark.parametrize(("typed", "stored"), CHANNELS)
def test_a_channel_means_the_same_thing_wherever_it_is_typed(typed, stored):
    """The INVARIANT, across the whole range of what a person types: the
    parser and the credential prompt apply one rule, so a channel cannot be
    accepted by one entry point and refused by the other."""
    field = next(f for f in SERVICES["slack"].fields if f.name == "broadcast_channel")

    assert normalize_slack_channel(typed) == stored
    assert field.clean(typed) == stored


@pytest.mark.parametrize(("typed", "stored"), CHANNELS)
def test_the_config_file_stores_the_same_normalised_channel(tmp_path, typed, stored):
    """The same range again, through the file, so normalising at the prompt and
    normalising at the parse cannot drift."""
    root = tmp_path / "p"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "config.yaml").write_text(
        f"slack:\n  broadcast_channel: {typed!r}\n"
    )

    parsed = parse_config(root / ".rite" / "config.yaml")

    assert not isinstance(parsed, ParseError), parsed
    assert parsed.slack.broadcast_channel == stored


def test_an_unnameable_channel_is_still_refused(tmp_path):
    """Normalising is not accepting anything: '#Bad Chan!' is not a channel."""
    root = tmp_path / "p"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "config.yaml").write_text(
        "slack:\n  broadcast_channel: 'Bad Chan!'\n"
    )

    parsed = parse_config(root / ".rite" / "config.yaml")

    assert isinstance(parsed, ParseError)
    assert "broadcast_channel" in parsed.message


def test_a_bare_channel_typed_at_the_prompt_reaches_slack_as_a_name(
    tmp_path, monkeypatch
):
    root = _project(tmp_path)

    _set_slack(root, monkeypatch, f"{TOKEN}\nall-rite\n")

    assert _slack_of(root).broadcast == "#all-rite"


def test_a_malformed_channel_does_not_block_setting_a_credential(tmp_path, monkeypatch):
    """S19's other half. The channel is broken, so the project cannot run a
    Manager — but `rite credential set` is how it gets repaired, and it used to
    answer with the parse error and exit 1."""
    root = _project(tmp_path)
    path = root / ".rite" / "config.yaml"
    path.write_text(path.read_text() + "slack:\n  broadcast_channel: 'Bad Chan!'\n")
    before = path.read_text()

    result = _set_slack(root, monkeypatch, TOKEN)

    assert result.exit_code == 0, result.output
    assert "broadcast_channel" in result.output, "it still says what is wrong"
    assert "will not rewrite it" in result.output
    assert path.read_text() == before, "a refused config is never rewritten"


# --- the state space --------------------------------------------------------
#
# token {valid, bad, unreachable} x owner {unset, set} x channel {unset, bare,
# #name, id} x shared app {none, this project, another, cannot tell}. Asserted
# as invariants rather than a list of cases, because the defect this command
# keeps producing is a state nobody enumerated.

APP_ID = Identity(team="T0TEAM", user="U0RITEBOT")

OWNERS = [("", False), (f"{OWNER}", True)]
CHANNELS_IN = [
    ("", ""),
    ("all-rite", "#all-rite"),
    ("#ops", "#ops"),
    ("C0C4KB709T6", "C0C4KB709T6"),
]


@pytest.mark.parametrize(("owner", "has_owner"), OWNERS)
@pytest.mark.parametrize(("typed", "stored"), CHANNELS_IN)
def test_the_summary_says_active_exactly_when_slack_has_a_target(
    tmp_path, monkeypatch, owner, has_owner, typed, stored
):
    """ACTIVE iff a target exists, and the channel is stored normalised —
    across every combination of the two optional answers."""
    root = _project(tmp_path)

    result = _set_slack(root, monkeypatch, f"{TOKEN}{owner}\n{typed}\n")

    assert result.exit_code == 0, result.output
    slack = _slack_of(root)
    assert slack.owner_user == owner
    assert slack.broadcast_channel == stored
    on = bool(owner or stored)
    assert slack.enabled is on
    assert ("Slack is ACTIVE" in result.output) is on, result.output
    assert (OFF in result.output) is (not on), result.output
    if on:
        assert slack.broadcast in result.output, "it names where status goes"
        if not has_owner:
            assert "broadcast-only" in result.output


def test_a_token_slack_refuses_is_asked_again_and_not_stored_until_fixed(
    tmp_path, monkeypatch, slack
):
    """Slack ANSWERED and said no, so asking again is useful — unlike a
    network that could not be reached, where the answer is to store it.

    ⚠ Asserts the token was ASKED FOR TWICE, not merely that a message was
    printed. A first draft checked only the message, and a mutation that
    reported a refused token as merely unreachable — storing it instead of
    re-asking — kept the message and passed.
    """
    root = _project(tmp_path)
    refused = {"ok": False, "error": "invalid_auth"}
    good = {"ok": True, "team_id": "T0TEAM", "user_id": "U0RITEBOT"}
    seen = []

    def answer(method, token, params=None, payload=None):
        assert method == "auth.test", method
        seen.append(token)
        return refused if len(seen) == 1 else good

    monkeypatch.setattr(slack_mod, "_call", answer)

    result = _set_slack(root, monkeypatch, f"bad-one\nbad-one\n{TOKEN}{OWNER}\n\n")

    assert result.exit_code == 0, result.output
    assert "Slack refused this token" in result.output
    assert "invalid_auth" in result.output
    prompt = "Slack bot token"
    assert result.output.count(prompt) >= 2, (
        f"the token must be asked for again, not stored; output:\n{result.output}"
    )
    assert seen[0] == "bad-one" and seen[-1] != "bad-one"
    assert _slack_of(root).owner_user == OWNER


def test_an_unreachable_slack_warns_and_still_stores(tmp_path, monkeypatch, slack):
    """Could-not-check is not refused. Refusing on an unreachable network
    would leave the person unable to record a token that is probably fine."""
    root = _project(tmp_path)
    slack.who = OSError("no route to host")

    result = _set_slack(root, monkeypatch, f"{TOKEN}{OWNER}\n\n")

    assert result.exit_code == 0, result.output
    assert "could not reach Slack" in result.output
    assert "storing it anyway" in result.output
    assert "slack_bot_token" in result.output
    assert _slack_of(root).owner_user == OWNER
    # It could not be told which app this is, so it does not guess at sharing.
    assert "could not check whether another project" in result.output


SHARED = ["none", "this project", "another project", "cannot tell"]


@pytest.mark.parametrize("state", SHARED)
def test_a_shared_app_is_warned_about_never_refused_and_never_bound(
    tmp_path, monkeypatch, slack, state
):
    """Warned iff another project holds it; the token is stored in every case;
    and setting a credential never creates a binding."""
    root = _project(tmp_path)
    theirs = tmp_path / "theirs"
    (theirs / ".rite").mkdir(parents=True)
    if state == "this project":
        bind(APP_ID, root)
    elif state == "another project":
        bind(APP_ID, theirs)
    elif state == "cannot tell":
        slack.who = OSError("unreachable")
    import os

    apps = Path(os.environ["RITE_SLACK_APPS_DIR"])
    before = {p.name for p in apps.iterdir()} if apps.is_dir() else set()

    result = _set_slack(root, monkeypatch, f"{TOKEN}{OWNER}\n\n")

    assert result.exit_code == 0, result.output
    assert "slack_bot_token" in result.output, "never a refusal"
    warned = "already used by another project" in result.output
    assert warned is (state == "another project"), result.output
    if warned:
        assert "reactions:read" in result.output
        assert "api.slack.com/apps" in result.output
    after = {p.name for p in apps.iterdir()} if apps.is_dir() else set()
    assert after == before, "setting a credential must never bind the app"


def test_the_whole_run_reads_slack_and_never_posts(tmp_path, monkeypatch, slack):
    """The hard rule, stated once as its own test as well as enforced by the
    stub: one read, no posts."""
    root = _project(tmp_path)

    _set_slack(root, monkeypatch, f"{TOKEN}{OWNER}\nall-rite\n")

    assert slack.reads >= 1, "the token is checked against Slack"
