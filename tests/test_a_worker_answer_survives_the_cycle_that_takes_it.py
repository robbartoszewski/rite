"""SCRUM-52: an Owner's answer to a Worker question reaches the Worker.

🔴 **Measured in the dogfood (Robert, 2026-10-03).** The Owner replied in a
Worker's question thread, the reply reached the Manager, and the Worker never
got it: its `answer.json` stayed the old one, and it went on with its own
default. The Owner believed the Worker was answered.

**Two defects, both in the supervisor:**

1. **A race.** The answer relay read the inbox WITHOUT consuming it, but ran
   only from a watcher throttled to once every 30 s. A reply that woke a
   Manager cycle was taken by that cycle (`take_mail`) and handed to the
   Manager's prompt before the relay's next turn, which then found nothing.
   Two replies in the dogfood went exactly this way.
2. **One question per Worker.** The ledger kept only the LATEST question id
   per sandbox, so a reply to an earlier question that was still unanswered
   matched nothing and was skipped without a word.

These drive the real supervisor boundary and the real relay, on a real mailbox
and a real exchange directory; only yoloAI is stood in for.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import rite_ai.managers.supervise as sup
from rite_ai.managers import mailbox
from rite_ai.managers import worker_questions as wq
from rite_ai.managers.supervise import StartResult, supervise
from rite_ai.sandbox import questions as q
from tests.test_the_owner_relays_a_workers_answer import (
    OWNER,
    PENDING,
    PRESENT,
    _arrange,
    _owner_dm,
    _raised,
    _Status,
    project,  # noqa: F401 - the fixture
)


class _Ending:
    kind = "finished"
    resume = True
    status = 0
    detail = ""


def _watcher(root, monkeypatch):
    from rite_ai.cli import main as cli_main

    config = SimpleNamespace(
        sandbox=SimpleNamespace(enabled=True),
        coordination=SimpleNamespace(manager_roles=[]),
    )
    # Scoped to building the watcher: `supervise` reads the real config.
    with monkeypatch.context() as m:
        m.setattr("rite_ai.config.parse.parse_config", lambda p: config)
        return cli_main._worker_question_watch(root, OWNER)  # noqa: PLC2701


@pytest.fixture
def worker_waiting(project, tmp_path, monkeypatch):  # noqa: F811
    """alpha is running and waiting on question `q1a2b`."""
    where = _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
    monkeypatch.setattr(
        "rite_ai.sandbox.worker_sandbox_status", lambda w, r=None: _Status("running")
    )
    return project, where


def _one_bounded_cycle(root, monkeypatch, watch, arrive):
    """One real `supervise` cycle, with the reply arriving in the window that
    lost it in the dogfood: AFTER the boundary's own relay pass has run, and
    before `take_mail` takes the inbox. Returns the prompts sessions got."""
    real_take = sup.take_mail

    def a_reply_lands_then_is_taken(r, m, box):
        arrive()
        return real_take(r, m, box)

    monkeypatch.setattr(sup, "take_mail", a_reply_lands_then_is_taken)
    for name, value in {
        "liveness": lambda n: type("L", (), {"alive": False, "known": True})(),
        "was_attached": lambda n: False,
        "ending": lambda n, human_was_present, pane="": _Ending(),
        "stop_session": lambda s: None,
        "forget_instance": lambda r, m: None,
        "_sleep": lambda s: None,
    }.items():
        monkeypatch.setattr(sup, name, value)
    prompts: list[str] = []

    def starter(r, m, *, engine, resume_id, max_sessions, window_seconds, **kw):
        prompts.append(kw.get("prompt") or "")
        return StartResult(True, "ok", session=f"s{len(prompts)}", attach="a")

    supervise(
        root,
        OWNER,
        engine="claude",
        prompt="OPEN",
        starter=starter,
        resume_id_for=lambda r, m, since=0.0: "sess-1",
        note=[].append,
        poll=0,
        max_sessions=1,
        window_seconds=3600,
        watch=watch,
    )
    return prompts


def _raised_by_surface(root) -> str:
    """The id `surface` itself raises for alpha's pending question, so a test
    of the race answers the LATEST question and does not lean on the
    earlier-question fix below."""
    from rite_ai.managers.asking import own_question_id

    wq.surface(root, OWNER, lambda _m: None)
    (qid,) = {
        own_question_id(m.text)
        for m in mailbox.read(root, OWNER, mailbox.OUTBOX)
        if own_question_id(m.text)
    }
    return qid


class TestAReplyTakenByTheCycleItWokeStillReachesTheWorker:
    def test_it_is_written_to_answer_json(self, worker_waiting, monkeypatch):
        """🔴 (a) The dogfood's case: polled and taken in the same cycle."""
        root, where = worker_waiting
        qid = _raised_by_surface(root)
        prompts = _one_bounded_cycle(
            root,
            monkeypatch,
            _watcher(root, monkeypatch),
            lambda: mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid)),
        )
        assert prompts and "30 seconds" in prompts[0], "the Manager lost it too"
        assert (where / q.ANSWER_FILE).exists(), (
            "the reply was taken by the cycle it woke and never reached the "
            "Worker's answer.json"
        )
        assert "30 seconds" in json.loads((where / q.ANSWER_FILE).read_text())["answer"]

    def test_control_without_the_hand_off_it_is_lost(self, worker_waiting, monkeypatch):
        """The same cycle with the boundary's hand-off removed: the Manager
        gets the reply and the Worker does not, which is the dogfood."""
        root, where = worker_waiting
        qid = _raised_by_surface(root)
        watch = _watcher(root, monkeypatch)

        def without_the_hand_off(say):
            watch(say)

        prompts = _one_bounded_cycle(
            root,
            monkeypatch,
            without_the_hand_off,
            lambda: mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid)),
        )
        assert prompts and "30 seconds" in prompts[0]
        assert not (where / q.ANSWER_FILE).exists()

    def test_the_watcher_relays_at_every_call_not_every_30_seconds(
        self, worker_waiting, monkeypatch
    ):
        """The other half of the race: the watcher's relay was throttled with
        its look. Two calls in a row, the reply arriving between them."""
        root, where = worker_waiting
        qid = _raised_by_surface(root)
        watch = _watcher(root, monkeypatch)
        watch(lambda _m: None)
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid))
        watch(lambda _m: None)
        assert (where / q.ANSWER_FILE).exists()

    def test_it_is_carried_once_when_both_paths_see_it(
        self, worker_waiting, monkeypatch
    ):
        root, where = worker_waiting
        qid = _raised(root)
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid))
        said: list[str] = []
        watch = _watcher(root, monkeypatch)
        watch(said.append)
        taken = mailbox.take(root, OWNER, mailbox.INBOX)
        watch.taken(said.append, taken)
        assert sum("relayed" in line for line in said) == 1, said


def _ask(where, text: str) -> None:
    """The Worker writes (or rewrites) its question file."""
    time.sleep(0.01)
    (where / q.QUESTION_FILE).write_text(json.dumps({"question": text, "context": ""}))


def _raised_ids(root) -> list[str]:
    """The ids `surface` raised, read from what it sent the person."""
    from rite_ai.managers.asking import own_question_id

    ids = []
    for m in mailbox.read(root, OWNER, mailbox.OUTBOX):
        found = own_question_id(m.text)
        if found and found not in ids:
            ids.append(found)
    return ids


class TestAReplyToAnEarlierOpenQuestionIsDelivered:
    def test_b_it_reaches_the_worker_labelled(self, worker_waiting, monkeypatch):
        """🔴 (b) The Worker asked, then asked again before anyone answered.
        A reply to the FIRST question is still an answer: delivered, and
        labelled so the Worker does not take it for an answer to the second."""
        root, where = worker_waiting
        _ask(where, "Which timeout, 30s or 60s?")
        wq.surface(root, OWNER, lambda _m: None)
        _ask(where, "And should the flag repeat?")
        wq.surface(root, OWNER, lambda _m: None)
        first, second = _raised_ids(root)
        assert first != second
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(first, "30 seconds"))
        said: list[str] = []

        carried = wq.relay(root, OWNER, said.append)

        assert carried == 1, said
        answer = json.loads((where / q.ANSWER_FILE).read_text())["answer"]
        assert "30 seconds" in answer
        assert first in answer and second in answer, answer

    def test_control_a_reply_to_the_latest_is_not_labelled(
        self, worker_waiting, monkeypatch
    ):
        root, where = worker_waiting
        _ask(where, "Which timeout, 30s or 60s?")
        wq.surface(root, OWNER, lambda _m: None)
        (only,) = _raised_ids(root)
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(only, "30 seconds"))
        wq.relay(root, OWNER, lambda _m: None)
        answer = json.loads((where / q.ANSWER_FILE).read_text())["answer"]
        assert "earlier question" not in answer

    def test_a_ledger_from_before_scrum_52_still_matches(
        self, worker_waiting, monkeypatch
    ):
        """An existing ledger holds only `{sandbox: qid}`: still an open
        question, and still answered."""
        root, where = worker_waiting
        qid = _raised(root)
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid))
        assert wq.relay(root, OWNER, lambda _m: None) == 1


class TestAReplyThatMatchesNoOpenQuestionIsSaid:
    def test_c_it_surfaces_a_notice_and_writes_nothing(
        self, worker_waiting, monkeypatch
    ):
        """🔴 (c) A reply to a Worker question that is no longer open (here:
        answered already) is said in the pane AND told to the person, never
        skipped silently, and nothing is written into a Worker."""
        root, where = worker_waiting
        qid = _raised(root)
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid, "30 seconds"))
        wq.relay(root, OWNER, lambda _m: None)
        (where / q.ANSWER_FILE).unlink()
        outbox_before = len(mailbox.read(root, OWNER, mailbox.OUTBOX))
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid, "no, 60 seconds"))
        said: list[str] = []

        carried = wq.relay(root, OWNER, said.append)

        assert carried == 0
        assert not (where / q.ANSWER_FILE).exists(), "it was written anyway"
        assert any("matched no open question" in line for line in said), said
        told = mailbox.read(root, OWNER, mailbox.OUTBOX)[outbox_before:]
        assert any("matched no open question" in m.text for m in told), [
            m.text for m in told
        ]

    def test_and_only_once(self, worker_waiting, monkeypatch):
        root, where = worker_waiting
        qid = _raised(root)
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid, "30 seconds"))
        wq.relay(root, OWNER, lambda _m: None)
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm(qid, "no, 60 seconds"))
        said: list[str] = []
        wq.relay(root, OWNER, said.append)
        wq.relay(root, OWNER, said.append)
        assert sum("matched no open question" in line for line in said) == 1, said

    def test_control_mail_that_was_never_a_worker_question_is_left_alone(
        self, worker_waiting, monkeypatch
    ):
        """A refinement round's id has the same shape. Not a Worker question,
        so not this relay's to report on."""
        root, _ = worker_waiting
        _raised(root)
        mailbox.send(root, OWNER, mailbox.INBOX, _owner_dm("q9f9f", "about KAN-9"))
        said: list[str] = []
        assert wq.relay(root, OWNER, said.append) == 0
        assert not any("matched no open question" in line for line in said), said
        assert any("q9f9f" in m.text for m in mailbox.read(root, OWNER, mailbox.INBOX))
