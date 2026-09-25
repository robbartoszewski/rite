"""A message reaches the Manager, or it is still in the mailbox.

Three defects, each measured on `a51633c`.

⚠ **1. The fresh-fallback relaunch dropped the mail.** `supervise` TAKES the
inbox (deleting the files) and appends it to `cycle_prompt`; when a resumed
launch fails, the fallback relaunched with `prompt=prompt` — the opening
instruction — so the session that actually ran never saw the message and
nothing was left to redeliver. The supervisor had already printed
"delivering 1 message(s)".

**That call site has now silently dropped three different arguments in two
days** — `prompt`, `permission`, and mail. The first two are pinned; this
pins the third, because the line is not the lesson, the missing test was.

⚠ **2. `mailbox.read` raised, despite "Never raises".** The `try` covered
`json.loads` and not `float(data.get("timestamp", 0.0))`, so a `timestamp`
that was a string, a list or `null` came out as an uncaught `ValueError` or
`TypeError` — out of the supervisor's own loop, killing the run.

⚠ **3. And that was reachable on the ORDINARY path, not by hand-editing.**
`rite connect` briefed an interactive session to hand-write the JSON, while
`send()` — the validated writer — had no production caller at all. An LLM
writing `"timestamp": null` once killed the Manager the User was trying to
talk to. The fix is a command to run instead of a format to imitate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from known_session import designate_known
from rite_ai.cli.main import cli
from rite_ai.managers import mailbox
from rite_ai.managers.supervise import StartResult, supervise

MESSAGE = "PLEASE STOP AND REPORT"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        "coordination:\n  managers:\n    - planner\n"
        "  manager_roles:\n    - name: planner\n      engine: claude\n"
    )
    return tmp_path


def _run_with_dead_designation(root: Path) -> list[dict]:
    """Cycle one resumes, the provider has forgotten it, the fallback runs."""
    designate_known(root, "planner", "sidDEAD")
    seen: list[dict] = []

    def starter(
        r,
        m,
        *,
        engine,
        resume_id,
        prompt,
        permission,
        max_sessions,
        window_seconds,
        **kw,
    ):
        seen.append({"resume_id": resume_id, "prompt": prompt or ""})
        if resume_id:
            return StartResult(False, "session not found")
        return StartResult(False, "stop here")

    supervise(
        root,
        "planner",
        engine="fake",
        max_sessions=1,
        window_seconds=0,
        verdict=lambda _r: "ready",
        starter=starter,
        prompt="OPENING",
        resume_id_for=lambda _r, _m, _s=0.0: "",
        note=lambda _m: None,
        poll=0,
    )
    return seen


class TestTheFallbackCarriesTheMail:
    def test_the_relaunch_that_actually_runs_has_the_message(self, project):
        """⚠ The regression. The failed attempt had it; the one that ran
        did not, and the files were already deleted."""
        mailbox.send(project, "planner", mailbox.INBOX, MESSAGE)
        seen = _run_with_dead_designation(project)

        assert len(seen) == 2, seen
        assert MESSAGE in seen[1]["prompt"], (
            "the fallback relaunch was given a prompt with no message in it, "
            "and `take` had already emptied the mailbox"
        )

    def test_it_is_not_left_in_the_mailbox_twice(self, project):
        """The complement: never twice. ⚠ This asserted the inbox ended EMPTY,
        but in this harness the fallback launch FAILS ("stop here"), so
        nothing delivered the message — the test was pinning the loss the
        put-back fix removes. What it protects is no duplication: the message
        is back exactly once, for the next cycle that does start."""
        mailbox.send(project, "planner", mailbox.INBOX, MESSAGE)
        _run_with_dead_designation(project)
        assert [m.text for m in mailbox.read(project, "planner", mailbox.INBOX)] == [
            MESSAGE
        ]

    def test_the_relaunch_still_gets_the_opening_instruction(self, project):
        """A fresh start gets the opening prompt, not the continuation —
        the property the original line was protecting."""
        mailbox.send(project, "planner", mailbox.INBOX, MESSAGE)
        seen = _run_with_dead_designation(project)
        assert "OPENING" in seen[1]["prompt"]

    def test_the_relaunch_is_told_how_to_reply(self, project):
        """Dropped by the same line. A Manager handed a question and no way
        to answer it is the channel half-built."""
        mailbox.send(project, "planner", mailbox.INBOX, MESSAGE)
        seen = _run_with_dead_designation(project)
        assert "Talking to the User" in seen[1]["prompt"]


class TestAMalformedMessageIsSkippedNotFatal:
    @pytest.mark.parametrize("timestamp", ["abc", [1], None, {"a": 1}, float("nan")])
    def test_read_never_raises_on_a_bad_timestamp(self, project, timestamp):
        box = mailbox.mailbox_dir(project, "planner", mailbox.INBOX)
        box.mkdir(parents=True, exist_ok=True)
        (box / "1_1_1.json").write_text(
            json.dumps({"text": "hi", "timestamp": timestamp})
        )
        assert len(mailbox.read(project, "planner", mailbox.INBOX)) == 1

    def test_a_bad_message_does_not_take_the_supervisor_down(self, project):
        """⚠ Through `supervise`, because that is where it killed the run."""
        box = mailbox.mailbox_dir(project, "planner", mailbox.INBOX)
        box.mkdir(parents=True, exist_ok=True)
        (box / "1_1_1.json").write_text(
            json.dumps({"text": MESSAGE, "timestamp": None})
        )

        seen: list[str] = []
        supervise(
            project,
            "planner",
            engine="fake",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=lambda *a, **k: (
                seen.append(k.get("prompt") or ""),
                StartResult(False, "stop"),
            )[1],
            prompt="OPENING",
            resume_id_for=lambda _r, _m, _s=0.0: "",
            note=lambda _m: None,
            poll=0,
        )
        assert seen and MESSAGE in seen[0], (
            "a hand-written message with a bad timestamp should still be "
            "delivered, not skipped and not fatal"
        )

    def test_a_message_with_no_text_is_still_skipped(self, project):
        """Tolerance is not 'accept anything' — an empty message is noise."""
        box = mailbox.mailbox_dir(project, "planner", mailbox.INBOX)
        box.mkdir(parents=True, exist_ok=True)
        (box / "1_1_1.json").write_text(json.dumps({"text": "   ", "timestamp": 1.0}))
        assert mailbox.read(project, "planner", mailbox.INBOX) == []


class TestThereIsACommandInsteadOfAFormat:
    """⚠ The deeper fix. `send()` existed, was tested, and had no production
    caller; `rite connect` told an LLM to hand-write the JSON instead. A
    command removes the format contract rather than documenting it better."""

    def test_a_message_can_be_sent_without_writing_json(self, project, monkeypatch):
        monkeypatch.chdir(project)
        result = CliRunner().invoke(cli, ["message", "planner", MESSAGE])
        assert result.exit_code == 0, result.output

        waiting = mailbox.read(project, "planner", mailbox.INBOX)
        assert [m.text for m in waiting] == [MESSAGE]

    def test_it_refuses_a_manager_this_project_does_not_declare(
        self, project, monkeypatch
    ):
        monkeypatch.chdir(project)
        result = CliRunner().invoke(cli, ["message", "nobody", MESSAGE])
        assert result.exit_code == 1
        assert "nobody" in result.output

    def test_it_refuses_an_empty_message(self, project, monkeypatch):
        """Writing a file nothing will deliver is worse than refusing: the
        reader skips blank text, so it would vanish with no trace."""
        monkeypatch.chdir(project)
        result = CliRunner().invoke(cli, ["message", "planner", "   "])
        assert result.exit_code == 1
        assert mailbox.read(project, "planner", mailbox.INBOX) == []

    def test_connect_tells_the_session_to_use_the_command(self, project):
        """The briefing must name the command. If it still describes the
        JSON shape as the way to send, the format contract is back."""
        from rite_ai.cli.main import _connect_briefing

        briefing = _connect_briefing(project, "planner")
        assert "rite message planner" in briefing
        assert '"timestamp"' not in briefing, (
            "the briefing still asks the session to hand-write the format"
        )


class TestARefusedCycleGivesItsMailBack:
    """Observed in a two-Manager run: a routed instruction was taken for the
    secondary's cycle, the launch was refused ('already running'), and the run
    returned with the files gone and nothing having delivered them. Reproduced
    through `rite start` on main: a second start for a running Manager left the
    inbox with 0 messages; now 1."""

    def test_the_taken_messages_are_put_back_at_their_names(self, project):
        mailbox.send(project, "planner", mailbox.INBOX, MESSAGE)
        (before,) = mailbox.read(project, "planner", mailbox.INBOX)
        said: list[str] = []
        outcome = supervise(
            project,
            "planner",
            engine="fake",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=lambda *a, **kw: StartResult(False, "already running"),
            prompt="OPENING",
            resume_id_for=lambda _r, _m, _s=0.0: "",
            note=said.append,
            poll=0,
        )
        assert not outcome.ok
        (after,) = mailbox.read(project, "planner", mailbox.INBOX)
        assert after.text == MESSAGE and after.path.name == before.path.name
        assert any("put back" in line for line in said), said
