"""A check-in window that passes with no Manager running (K6).

There is no daemon (A5, Decision 2), so a window that passes while nothing
runs posts nothing. Proposed, and Robert's to confirm: the queue persists,
`rite start` says how many questions are waiting and when the next check-in
is, and the next standup covers everything since the last check-in actually
DELIVERED, so the gap is reported rather than lost.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from rite_ai.managers import checkins
from rite_ai.managers.mailbox import OUTBOX, read
from rite_ai.managers.session import StartResult, Stopped
from rite_ai.managers.supervise import supervise

ALWAYS_OPEN = 'checkins:\n  windows:\n    - {hours: "00:00-24:00"}\n'
NEVER_TODAY = 'checkins:\n  windows:\n    - {days: "%s", hours: "09:00-10:00"}\n'


def _not_today() -> str:
    import datetime

    return ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[
        (datetime.datetime.now().weekday() + 3) % 7
    ]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _build(root: Path, windows: str) -> Path:
    rite = root / ".rite"
    rite.mkdir(exist_ok=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n" + windows
    )
    _git(root, "init", "-q")
    return root


def _commit(root: Path, subject: str) -> str:
    _git(
        root,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        subject,
    )
    return _git(root, "rev-parse", "--short", "HEAD")


def _drive(monkeypatch, root: Path) -> list[str]:
    import rite_ai.managers.supervise as sup

    said: list[str] = []
    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(
        sup,
        "ending",
        lambda n, human_was_present, pane="": type(
            "E", (), {"kind": "quit", "resume": False, "status": 0, "detail": ""}
        )(),
    )
    monkeypatch.setattr(sup, "stop_session", lambda s: Stopped(True, True))
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    monkeypatch.setattr(
        __import__("rite_ai.sandbox", fromlist=["x"]), "list_rite_sandboxes", lambda: []
    )
    supervise(
        root,
        "lead",
        engine="claude",
        max_sessions=1,
        window_seconds=0,
        prompt="work",
        verdict=lambda _r: "ready",
        note=said.append,
        starter=lambda *a, **k: StartResult(True, "ok", session="s1", attach="a"),
        poll=0,
    )
    return said


def test_start_says_what_is_waiting_and_when_it_will_be_asked(tmp_path, monkeypatch):
    root = _build(tmp_path, NEVER_TODAY % _not_today())
    checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    checkins.defer(root, "lead", "drop python 3.11?", "ticket 15")
    said = _drive(monkeypatch, root)
    [line] = [s for s in said if s.startswith("check-ins:")]
    assert "2 deferred question(s) waiting for 'lead'" in line
    assert f"next at {_not_today()} 09:00" in line


def test_start_inside_a_due_window_says_the_check_in_goes_out_this_run(
    tmp_path, monkeypatch
):
    root = _build(tmp_path, ALWAYS_OPEN)
    checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    line = checkins.start_line(root, "lead")
    assert "the check-in goes out when this run's first session ends" in line


def test_start_with_no_windows_says_deferrals_are_asked_at_once(tmp_path):
    root = _build(tmp_path, "")
    assert "a deferral is asked at once" in checkins.start_line(root, "lead")


def test_the_queue_persists_across_runs(tmp_path, monkeypatch):
    root = _build(tmp_path, NEVER_TODAY % _not_today())
    q = checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    _drive(monkeypatch, root)
    _drive(monkeypatch, root)
    assert [x.id for x in checkins._queued(root, "lead")] == [q.id]
    assert [m.text for m in read(root, "lead", OUTBOX)] == []


def test_the_next_standup_covers_the_whole_gap_including_its_commits(
    tmp_path, monkeypatch
):
    """The last check-in went out two days ago; windows passed with nothing
    running. The next one covers everything since the one DELIVERED."""
    root = _build(tmp_path, ALWAYS_OPEN)
    last = time.time() - 2 * 24 * 3600
    path = checkins._checkins_dir(root, "lead") / checkins.LAST_CHECKIN_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": last}) + "\n")
    sha = _commit(root, "Work done while nobody was checking in")

    said = _drive(monkeypatch, root)

    [line] = [s for s in said if s.startswith("check-ins:")]
    assert "the last check-in went out" in line
    [sent] = [m.text for m in read(root, "lead", OUTBOX)]
    assert f"Standup since {time.strftime('%a %H:%M', time.localtime(last))}" in sent
    assert f"- commit {sha} Work done while nobody was checking in" in sent
