"""A message a person sent is delivered, or the person is told it was not.

The coordinator, 2026-09-28, after the fifth instance of one defect in a day:
a message someone sent, believed delivered, never seen. Found this time on
Linux (SB11): `rite message lead` said "delivered at the start of its next
turn", then `rite start lead` stopped on "the board has nothing ready" with
the message still in the inbox, and nothing said so.

The property, not a start rule:

* an idle board with mail waiting starts ONE session to deliver it, read as
  a state (what is in the inbox now), never as an event;
* mail a delivery session could not take is reported, not retried (F22:
  repeating a session with the same inputs is the loop);
* every run that ends with mail undelivered says so, at the terminal and in
  Slack, however it ended;
* `rite message` says at send time when nothing is running to read it.
"""

from __future__ import annotations

import os
from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers.mailbox import INBOX, read, send
from rite_ai.managers.session import StartResult, Stopped
from rite_ai.managers.supervise import supervise, undelivered_line


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n"
    )
    return tmp_path


def _drive(monkeypatch, root: Path, verdict: str, *, sessions: int = 5, during=None):
    """`supervise` with the engine faked. `during(n)` runs inside session n,
    as a person or another process would act while it runs."""
    import rite_ai.managers.supervise as sup

    prompts: list[str] = []
    said: list[str] = []

    def starter(root, manager, **kw):
        prompts.append(kw.get("prompt", ""))
        if during is not None:
            during(len(prompts))
        return StartResult(True, "ok", session="s1", attach="a")

    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(
        sup,
        "ending",
        lambda n, human_was_present, pane="": type(
            "E", (), {"kind": "finished", "resume": True, "status": 0, "detail": ""}
        )(),
    )
    monkeypatch.setattr(sup, "stop_session", lambda s: Stopped(True, True))
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    result = supervise(
        root,
        "lead",
        engine="claude",
        max_sessions=sessions,
        window_seconds=0,
        prompt="work the queue",
        verdict=lambda _r: verdict,
        note=said.append,
        starter=starter,
        resume_id_for=lambda r, m, since=0.0: "sess-1",
        poll=0,
    )
    return result, prompts, said


# --- an idle board delivers what is waiting ------------------------------------


def test_an_idle_board_with_mail_waiting_starts_one_session_to_deliver_it(
    tmp_path, monkeypatch
):
    root = _project(tmp_path)
    send(root, "lead", INBOX, "please look at RT-9")
    result, prompts, said = _drive(monkeypatch, root, "idle")
    assert len(prompts) == 1
    assert "please look at RT-9" in prompts[0]
    assert read(root, "lead", INBOX) == []
    assert any("a session starts to deliver them" in line for line in said)
    assert undelivered_line(root, "lead", result.reason) == ""


def test_an_idle_board_with_no_mail_still_starts_nothing(tmp_path, monkeypatch):
    root = _project(tmp_path)
    result, prompts, _ = _drive(monkeypatch, root, "idle")
    assert prompts == []
    assert "the board has nothing ready (0 session(s))" in result.reason


def test_mail_that_arrived_during_the_session_is_delivered_too(tmp_path, monkeypatch):
    """A state, not an event: what arrived while a session ran is simply in
    the inbox when the next decision is made."""
    root = _project(tmp_path)
    send(root, "lead", INBOX, "first")

    def during(n):
        if n == 1:
            send(root, "lead", INBOX, "second, sent while it ran")

    _, prompts, _ = _drive(monkeypatch, root, "idle", during=during)
    assert len(prompts) == 2
    assert "second, sent while it ran" in prompts[1]
    assert read(root, "lead", INBOX) == []


def test_mail_a_session_could_not_take_is_reported_not_retried(tmp_path, monkeypatch):
    """F22's lesson: the same inputs again start the same useless session."""
    root = _project(tmp_path)
    kept = send(root, "lead", INBOX, "cannot be taken")
    body = kept.read_text()

    def during(n):
        kept.write_text(body)  # the same message, back under the same name

    result, prompts, said = _drive(monkeypatch, root, "idle", during=during)
    assert len(prompts) == 1
    assert any("Not retried" in line for line in said)
    line = undelivered_line(root, "lead", result.reason)
    assert "1 message(s) sent to 'lead' were NOT delivered" in line
    assert "`rite start lead`" in line


def test_a_closed_schedule_starts_nothing_and_the_mail_is_said(tmp_path, monkeypatch):
    """`closed` is the person's own schedule: not overridden, reported."""
    root = _project(tmp_path)
    send(root, "lead", INBOX, "please look at RT-9")
    result, prompts, _ = _drive(monkeypatch, root, "closed")
    assert prompts == []
    assert "NOT delivered" in undelivered_line(root, "lead", result.reason)


def test_the_session_ceiling_still_bounds_delivery(tmp_path, monkeypatch):
    root = _project(tmp_path)
    send(root, "lead", INBOX, "first")

    def during(n):
        send(root, "lead", INBOX, f"more {n}")

    result, prompts, _ = _drive(monkeypatch, root, "idle", sessions=2, during=during)
    assert len(prompts) == 2
    assert "ceiling reached" in result.reason
    assert "NOT delivered" in undelivered_line(root, "lead", result.reason)


# --- the run's end says it, however the run ended ------------------------------


def test_the_end_of_a_run_says_it_at_the_terminal_and_in_slack(tmp_path):
    import inspect

    import rite_ai.cli.main as main

    source = inspect.getsource(main)
    body = source[source.index("listener = _slack_listener(root, role.name)") :]
    finally_at = body.index("finally:")
    assert finally_at < body.index("undelivered_line(")
    assert finally_at < body.index("listener.close(undelivered=undelivered)")
    assert '"the run was interrupted"' in body


def test_the_slack_goodbye_carries_the_undelivered_line(tmp_path):
    from rite_ai.managers.slack import Listener

    posts: list[dict] = []

    def slack(method, token, params=None, payload=None):
        args = payload or params or {}
        if method == "chat.postMessage":
            posts.append(args)
            return {"ok": True, "channel": "D1", "ts": f"{100 + len(posts)}.0"}
        if method == "auth.test":
            return {"ok": True, "user_id": "UR1TE"}
        return {"ok": True, "messages": []}

    listener = Listener(token="t", manager="lead", owner="U0WNER", dm="D1")
    listener.close(call=slack, undelivered="2 message(s) sent to 'lead' were NOT")
    [stop] = [p["text"] for p in posts if "has stopped" in p["text"]]
    assert "⚠ 2 message(s) sent to 'lead' were NOT" in stop


# --- `rite message` says whether anything is reading ---------------------------


def test_rite_message_says_when_nothing_is_running_to_read_it(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.delenv("RITE_MANAGER", raising=False)
    got = CliRunner().invoke(cli, ["message", "lead", "please look at RT-9"])
    assert got.exit_code == 0, got.output
    assert "which is NOT running" in got.output
    assert "`rite start lead`" in got.output


def test_rite_message_to_a_running_manager_says_its_next_turn(tmp_path, monkeypatch):
    from rite_ai.managers.routing import record_supervisor

    root = _project(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.delenv("RITE_MANAGER", raising=False)
    record_supervisor(root, "lead", os.getpid())
    got = CliRunner().invoke(cli, ["message", "lead", "please look at RT-9"])
    assert got.exit_code == 0, got.output
    assert "delivered at the start of its next turn" in got.output
    assert "NOT running" not in got.output
