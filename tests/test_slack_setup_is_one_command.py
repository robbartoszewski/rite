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

import pytest
import yaml
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.init import run_init
from rite_ai.cli.main import cli
from rite_ai.config.parse import ParseError, normalize_slack_channel, parse_config
from rite_ai.credentials.services import SERVICES

TOKEN = "xoxb-000\nxoxb-000\n"
OWNER = "U0C4HK552HF"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setattr("keyring.set_password", lambda *a: None)
    monkeypatch.setenv("RITE_CREDENTIAL_DIR", str(tmp_path / "creds"))


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


OFF = "Slack is OFF"

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
