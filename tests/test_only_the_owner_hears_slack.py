"""Only the Manager holding `route` reads or posts Slack (multi-Manager, one root).

Every Manager used to open a listener, so two Managers in one root both read the
Owner's DM, both treated it as INSTRUCTIONS and both would act — MMQ2's accident
— and doubled the history polling on the project's one app (60/min against
Tier 3's "50+", §9.16.6). Observed through `rite start` with a refusing proxy:
before, the secondary attempted 3 Slack connections; after, 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rite_ai.managers.slack as slack_mod
from rite_ai.cli.main import _slack_listener

CONFIG = """\
ticket_backend:
  type: none
slack:
  owner_user: U0FAKEOWNER
  broadcast_channel: "#all-rite"
coordination:
{remote}  managers:
    - lead
    - helper
  manager_roles:
    - name: lead
      engine: claude
      preset: {lead}
    - name: helper
      engine: claude
      preset: executor
"""


@pytest.fixture
def opened(monkeypatch):
    made: list[str] = []

    class Recorder:
        def __init__(self, *, manager, **_kw):
            made.append(manager)

        def open(self):
            return []

    monkeypatch.setattr(slack_mod, "Listener", Recorder)
    monkeypatch.setenv("RITE_SLACK_BOT_TOKEN", "xoxb-fake")
    return made


def _project(tmp_path: Path, *, lead="lead", remote="") -> Path:
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text(
        CONFIG.format(lead=lead, remote=f"  remote: {remote}\n" if remote else "")
    )
    return tmp_path


def test_the_owner_opens_a_listener(tmp_path, opened):
    assert _slack_listener(_project(tmp_path), "lead") is not None
    assert opened == ["lead"]


def test_a_secondary_opens_none_and_says_whose_it_is(tmp_path, opened, capsys):
    assert _slack_listener(_project(tmp_path), "helper") is None
    assert opened == [], "a secondary constructed a Slack listener"
    assert "only the Manager holding 'route'" in capsys.readouterr().err


def test_with_no_single_route_holder_nobody_hears_slack(tmp_path, opened):
    root = _project(tmp_path, lead="executor")
    assert _slack_listener(root, "lead") is None
    assert _slack_listener(root, "helper") is None
    assert opened == []


def test_with_a_remote_the_gate_does_not_apply(tmp_path, opened):
    """Multi-machine: the election decides the Owner, and that gate is v0.7.0.
    Taking Slack away here would break a project that has it."""
    root = _project(tmp_path, remote="origin")
    assert _slack_listener(root, "helper") is not None
