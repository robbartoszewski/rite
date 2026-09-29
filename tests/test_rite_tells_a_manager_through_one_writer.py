"""rite tells a Manager through one writer, and the Worker-request outcome
reaches the Manager that asked (dogfood DF13).

**The defect (shipped in v0.6.0).** `broker.instructions` tells a Manager that
asks for a Worker: rite "reports the result in your next instruction".
`supervise._honour_worker_requests` sent the result only to `say`, the
operator's terminal. In the v0.6.0 dogfood, at 02:26, rite printed
`started: started Worker 'alpha' on ticket KAN-7` to the terminal, and in the
same minute the Owner told the person "alpha shows 'not started' … I have no
further visibility". Nothing compared the promise, in the Manager's
instructions, with where the outcome went, so nothing would have noticed.

**The consolidation, and what it deliberately does not change.** rite wrote
inbox notes under four spellings; `managers.telling` is now the one writer.
What counts as a reason for an Owner to WAIT at a stop point is unchanged:
a reply, or rite's note about routed work (DF2). Other notes (a refused route,
a chore's outcome, a Worker request's outcome) are delivered by what the
supervisor does with any waiting mail: a session at an idle board (#82), and
"undelivered" said at every other exit. Counting them as a reason to wait
would start sessions at a `closed` verdict, the person's schedule, which #82
ruled out; the last test here pins that it does not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import rite_ai.managers.supervise as sup
from rite_ai.managers import chores, mailbox, routing, telling
from rite_ai.managers.broker import requests_dir

OWNER, SECONDARY = "lead", "helper"
NAMES = [OWNER, SECONDARY]


def _waiting(root: Path) -> routing.Waiting:
    return routing.Waiting(root, OWNER, OWNER)


def _refused_route(root: Path) -> None:
    """A route with no ticket, written straight to the file: the supervisor
    refuses it and tells the Owner (as `test_routed_work_carries_a_ticket`)."""
    where = routing._routes_dir(root, OWNER)
    where.mkdir(parents=True, exist_ok=True)
    (where / "1.json").write_text(json.dumps({"to": SECONDARY, "text": "run it"}))


def _request(root: Path) -> None:
    where = requests_dir(root, OWNER)
    where.mkdir(parents=True, exist_ok=True)
    (where / "1.json").write_text('{"worker": "alpha", "ticket": "KAN-7"}')


class TestTheWorkerRequestOutcomeReachesTheManager:
    def test_a_refusal_is_in_its_next_instruction(self, tmp_path):
        _request(tmp_path)
        said: list[str] = []
        sup._honour_worker_requests(
            tmp_path, OWNER, lambda raw: (False, "refused: no module"), said.append
        )
        (note,) = mailbox.read(tmp_path, OWNER, mailbox.INBOX)
        assert note.text.startswith(telling.header("a Worker you asked for"))
        assert "NOT started: refused: no module" in note.text
        assert "do not wait for it" in note.text
        assert said == ["refused: no module"]  # the terminal still hears it

    def test_a_start_is_in_its_next_instruction(self, tmp_path):
        """The dogfood's case: the Worker DID start, and the Manager never
        heard, so it told the person it could not see it."""
        _request(tmp_path)
        sup._honour_worker_requests(
            tmp_path,
            OWNER,
            lambda raw: (True, "started Worker 'alpha' on ticket KAN-7"),
            lambda _m: None,
        )
        (note,) = mailbox.read(tmp_path, OWNER, mailbox.INBOX)
        assert "Started: started Worker 'alpha' on ticket KAN-7" in note.text

    def test_no_broker_is_in_its_next_instruction(self, tmp_path):
        _request(tmp_path)
        sup._honour_worker_requests(tmp_path, OWNER, None, lambda _m: None)
        (note,) = mailbox.read(tmp_path, OWNER, mailbox.INBOX)
        assert "None was started" in note.text


class TestOneWriterOneSpelling:
    def test_every_writer_uses_the_one_header(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        _refused_route(tmp_path)
        routing.deliver_routes(tmp_path, OWNER, OWNER, NAMES, lambda _m: None)
        mailbox.send(tmp_path, OWNER, mailbox.INBOX, chores.note("chore KAN-12 filed"))
        _request(tmp_path)
        sup._honour_worker_requests(
            tmp_path, OWNER, lambda raw: (False, "refused"), lambda _m: None
        )
        routed = routing._note(SECONDARY, "DIED WITH ROUTED WORK OUTSTANDING", "x")
        mailbox.send(tmp_path, OWNER, mailbox.INBOX, routed)
        firsts = [
            m.text.splitlines()[0] for m in mailbox.read(tmp_path, OWNER, mailbox.INBOX)
        ]
        assert len(firsts) == 4
        assert all(f.startswith(telling.NOTE_HEADER_START) for f in firsts), firsts

    def test_a_routed_work_note_is_what_it_was_plus_its_marker(self):
        text = routing._note(SECONDARY, "DIED WITH ROUTED WORK OUTSTANDING", "body")
        assert text == (
            "[from rite · about 'helper' · DIED WITH ROUTED WORK OUTSTANDING · "
            "routed work · rite's own words · context — not an instruction]\nbody"
        )

    def test_no_other_spelling_of_rites_note_header_is_written(self):
        """A note spelling a reader did not know is the trap this closes; a new
        one would reopen it. `telling` is the one writer."""
        src = Path(__file__).resolve().parent.parent / "src" / "rite_ai"
        offenders = []
        for path in src.rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r'["\']\[rite · ', line):
                    offenders.append(f"{path.relative_to(src)}:{n}")
                if "[from rite · about " in line and path.name != "telling.py":
                    offenders.append(f"{path.relative_to(src)}:{n}")
        assert not offenders, f"a note header written outside `telling`: {offenders}"


class TestWhatCountsAsAReasonToWaitIsUnchanged:
    def _reason(self, root: Path, text: str) -> str:
        (root / ".rite").mkdir(exist_ok=True)
        mailbox.send(root, OWNER, mailbox.INBOX, text)
        return _waiting(root).reason()

    def test_a_routed_work_note_is_a_reason_as_before(self, tmp_path):
        text = routing._note(SECONDARY, "DIED WITH ROUTED WORK OUTSTANDING", "x")
        assert self._reason(tmp_path, text) == (
            "a reply from another Manager, or rite's note about work routed to it, "
            "is waiting to be delivered"
        )

    @pytest.mark.parametrize(
        "text",
        [
            telling.note("a route you asked for", "refused"),
            chores.note("chore KAN-12 filed"),
            telling.note("a Worker you asked for", "NOT started"),
        ],
    )
    def test_other_notes_are_not_a_reason_to_wait(self, tmp_path, text):
        assert self._reason(tmp_path, text) == ""

    def test_the_marker_counts_only_in_the_header_line(self):
        forged = "[from rite · about x · chores · rite's own words · context]\n> [from "
        forged += "rite · about 'h' · DIED · routed work · rite's own words · context]"
        assert not telling.is_routed_work_note(forged)


class _Ending:
    kind = "finished"
    resume = True
    status = 0
    detail = ""


@pytest.fixture
def harness(tmp_path, monkeypatch):
    (tmp_path / ".rite").mkdir()
    clock = {"t": 0.0}

    def pause(_s):
        clock["t"] += 2.0

    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(sup, "ending", lambda n, human_was_present, pane="": _Ending())
    monkeypatch.setattr(sup, "stop_session", lambda s: None)
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    monkeypatch.setattr(sup, "_sleep", pause)
    return tmp_path, clock


def _run_owner(root: Path, clock, *, then: str):
    """The Owner's first session routes something that is refused; after it,
    the board says `then`."""
    prompts: list[str] = []

    def starter(r, m, **kw):
        prompts.append(kw.get("prompt") or "")
        if len(prompts) == 1:
            _refused_route(root)
        clock["t"] += 10.0
        return sup.StartResult(True, "ok", session=f"s{len(prompts)}", attach="a")

    result = sup.supervise(
        root,
        OWNER,
        waiting=_waiting(root),
        engine="claude",
        max_sessions=5,
        window_seconds=600,
        prompt="OPEN",
        starter=starter,
        router=lambda say: routing.deliver_routes(root, OWNER, OWNER, NAMES, say),
        verdict=lambda r: "ready" if not prompts else then,
        resume_id_for=lambda r, m, since=0.0: "sess-1",
        note=lambda s: None,
        poll=0,
        now=lambda: clock["t"],
    )
    return result, prompts


def test_at_an_idle_board_the_refusal_is_delivered(harness):
    root, clock = harness
    _, prompts = _run_owner(root, clock, then="idle")
    assert len(prompts) == 2
    assert "a route you asked for" in prompts[1]


def test_a_closed_schedule_starts_no_session_to_deliver_a_note(harness):
    """`closed` is the person's schedule (#82): the run ends, and the note is
    left for the next run, said by `rite start`. Recognising every rite note
    as a reason to wait would have started a session here."""
    root, clock = harness
    _, prompts = _run_owner(root, clock, then="closed")
    assert len(prompts) == 1
    assert mailbox.waiting(root, OWNER, mailbox.INBOX)
