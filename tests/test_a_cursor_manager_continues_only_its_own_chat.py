"""CU3 through the supervisor: a Cursor Manager continues its recorded chat,
or refuses, and never passes an empty chat off as a continuation.

The starter stands in for Cursor and does to the chat directory what Cursor
was measured to do (CU1, CU1b): a turn on an unknown UUID CREATES
`chats/<hash>/<uuid>/meta.json` with a new `createdAtMs` and reports success.
Everything else is the real `supervise` loop.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

import rite_ai.managers.supervise as sup
from rite_ai.managers import record_chat, recorded_chat
from rite_ai.managers.session import StartResult
from rite_ai.managers.supervise import CONTINUATION, supervise


class FakeCursor:
    """What Cursor does to its chat store, one turn per `start`."""

    def __init__(self, config: Path, *, make_chat: bool = True):
        self.config = config
        self.make_chat = make_chat
        self.calls: list[tuple[str, str]] = []
        self.clock = 1000
        self.replace_on_call: int | None = None

    def chat(self, handle: str) -> Path:
        return self.config / "chats" / "hash" / handle

    def create(self, handle: str) -> None:
        self.clock += 1
        d = self.chat(handle)
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"createdAtMs": self.clock}))

    def __call__(self, root, manager, *, engine, resume_id, prompt="", **kw):
        self.calls.append((resume_id, prompt))
        if self.replace_on_call == len(self.calls):
            # The window rite cannot close: the chat went, and Cursor
            # recreated it empty under the same UUID.
            shutil.rmtree(self.chat(resume_id))
        if self.make_chat and not self.chat(resume_id).exists():
            self.create(resume_id)
        return StartResult(True, "ok", session="s1", attach="a", pane="%1")


def _endings(monkeypatch, kinds):
    """Each cycle ends as the next of `kinds`: "finished" resumes, "quit"
    stops the run."""
    left = list(kinds)
    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(sup, "stop_session", lambda n: None)

    def ending(n, human_was_present, pane=""):
        kind = left.pop(0) if left else "quit"
        return type(
            "E",
            (),
            {"kind": kind, "resume": kind == "finished", "status": 0, "detail": ""},
        )()

    monkeypatch.setattr(sup, "ending", ending)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    config = tmp_path / "cursor-config"
    monkeypatch.setattr(sup.cursor_chat, "config_dir", lambda r, m, home=None: config)
    return root, config


def _run(root, cursor, *, sessions=1, fresh=False, said=None):
    return supervise(
        root,
        "lead",
        engine="cursor",
        prompt="OPENING",
        max_sessions=sessions,
        window_seconds=0,
        fresh=fresh,
        verdict=lambda _r: "ready",
        starter=cursor,
        note=(said.append if said is not None else None),
        poll=0,
    )


def test_a_new_chat_is_recorded_before_it_is_launched(project, monkeypatch):
    root, config = project
    _endings(monkeypatch, ["quit"])
    seen_at_launch = []

    cursor = FakeCursor(config)
    original = cursor.__call__

    def checking(r, m, **kw):
        seen_at_launch.append(recorded_chat(r, m))
        return original(r, m, **kw)

    _run(root, checking)
    handle, prompt = cursor.calls[0]
    assert str(uuid.UUID(handle)) == handle
    assert seen_at_launch[0] is not None and seen_at_launch[0].handle == handle
    assert seen_at_launch[0].created_ms is None
    assert prompt.startswith("OPENING")
    record = recorded_chat(root, "lead")
    assert (record.handle, record.created_ms) == (handle, 1001)


def test_the_next_cycle_continues_the_same_chat(project, monkeypatch):
    root, config = project
    _endings(monkeypatch, ["finished", "quit"])
    cursor = FakeCursor(config)
    _run(root, cursor, sessions=2)
    (h1, p1), (h2, p2) = cursor.calls
    assert h1 == h2
    assert p1.startswith("OPENING") and p2.startswith(CONTINUATION)


def test_the_next_run_continues_the_recorded_chat(project, monkeypatch):
    root, config = project
    _endings(monkeypatch, ["quit", "quit"])
    cursor = FakeCursor(config)
    _run(root, cursor)
    _run(root, cursor)
    (h1, _), (h2, p2) = cursor.calls
    assert h1 == h2 and p2.startswith(CONTINUATION)


def test_a_removed_chat_is_refused_and_nothing_launches(project, monkeypatch):
    """CU3's acceptance: REFUSED with the path named, not run as a fresh chat
    reported as success."""
    root, config = project
    _endings(monkeypatch, ["quit"])
    cursor = FakeCursor(config)
    _run(root, cursor)
    handle = cursor.calls[0][0]
    shutil.rmtree(config / "chats")
    outcome = _run(root, cursor)
    assert len(cursor.calls) == 1, "a continuation of a gone chat was launched"
    assert not outcome.ok
    assert handle in outcome.reason and "--fresh" in outcome.reason


def test_a_chat_replaced_during_a_cycle_fails_closed_and_stays_closed(
    project, monkeypatch
):
    root, config = project
    _endings(monkeypatch, ["finished", "finished", "quit"])
    cursor = FakeCursor(config)
    cursor.replace_on_call = 2
    outcome = _run(root, cursor, sessions=3)
    assert len(cursor.calls) == 2, "a cycle ran after the chat was replaced"
    assert not outcome.ok and "WITHOUT its history" in outcome.reason
    assert recorded_chat(root, "lead").broken

    # A bare start refuses; nothing launches.
    again = _run(root, cursor)
    assert not again.ok and "--fresh" in again.reason
    assert len(cursor.calls) == 2

    # --fresh starts a NEW chat deliberately.
    _endings(monkeypatch, ["quit"])
    fresh = _run(root, cursor, fresh=True)
    assert fresh.ok, fresh.reason
    assert cursor.calls[2][0] != cursor.calls[0][0]
    assert not recorded_chat(root, "lead").broken


def test_a_first_turn_that_made_no_chat_is_retried_with_the_same_handle(
    project, monkeypatch
):
    root, config = project
    _endings(monkeypatch, ["quit", "quit"])
    cursor = FakeCursor(config, make_chat=False)
    _run(root, cursor)
    cursor.make_chat = True
    _run(root, cursor)
    (h1, _), (h2, p2) = cursor.calls
    assert h1 == h2 and p2.startswith("OPENING")


def test_a_chat_rite_never_confirmed_is_adopted_not_restarted(project, monkeypatch):
    """rite stopped between the first turn and recording it."""
    root, config = project
    _endings(monkeypatch, ["quit"])
    handle = str(uuid.uuid4())
    record_chat(root, "lead", handle)
    cursor = FakeCursor(config)
    cursor.create(handle)
    _run(root, cursor)
    (h, p) = cursor.calls[0]
    assert h == handle and p.startswith(CONTINUATION)
    assert recorded_chat(root, "lead").created_ms == 1001


def test_an_engine_that_died_is_not_retried_on_a_new_chat(project, monkeypatch):
    """Claude's fallback starts FRESH when a resume dies. For Cursor that
    would mint a second handle and orphan the recorded one."""
    root, config = project
    _endings(monkeypatch, ["quit"])
    cursor = FakeCursor(config)
    _run(root, cursor)
    calls: list[str] = []

    def dies(r, m, *, resume_id, **kw):
        calls.append(resume_id)
        result = StartResult(False, "engine exited at once")
        result.engine_died = True
        return result

    outcome = _run(root, dies)
    assert calls == [cursor.calls[0][0]]
    assert not outcome.ok
