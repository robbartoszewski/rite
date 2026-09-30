"""An answer to a Worker's question reaches the Worker (v0.7.0a4 dogfood S30).

**What happened.** rite carried a Worker's question all the way to Slack, and
then stopped. The person answered in the thread, the reply reached the Owner,
and there it sat: `worker_questions` said in its own docstring that it "does
not deliver an answer back", so the operator had to `yoloai attach` the
Worker and type the answer into its session by hand. Everything up to the
last inch worked.

**The claim that justified stopping was wrong.** It read "yoloAI 0.11.0 has
no way to send input to a running agent". True of the agent's *session*, and
irrelevant: yoloAI's injected `CLAUDE.md` tells the agent to write
`question.json` and then to POLL `answer.json`. The return path is a file the
Worker is already watching, in the directory rite already reads the question
from. Writing it is neither typing into a live session nor reaching into
yoloAI's private layout.

⚠ **Isolation-sensitive, and this is the honest statement of it.** The
delivery crosses from the host into a sandboxed Worker's exchange directory.
It changes NO sandbox rule: no flag, no grant, no permission, and nothing
here can be made to turn a Worker's sandbox off. What it writes is data, in
the place yoloAI created for exactly this exchange, read by the agent because
its own runtime instructions tell it to read it. The alternative route —
`tmux -S … send-keys` into the live pane — is what `sandbox.worker_pane`
calls "a separate, consequential act" and is deliberately NOT taken.

**The other half is the undeliverable case**, and it is why this is a
correctness fix rather than a convenience. A person who answers and hears
nothing believes the Worker is working. If the Worker stopped first, the
answer must come back to them saying so.
"""

from __future__ import annotations

import itertools
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from rite_ai.managers import mailbox
from rite_ai.managers import worker_questions as wq
from rite_ai.sandbox import questions as q

OWNER = "lead"
SANDBOX = "rite-p-alpha"


class _Status:
    """Stands in for `SandboxStatus` without asking a real yoloAI."""

    def __init__(self, value: str, known: bool = True) -> None:
        self.value, self.known = value, known

    def __str__(self) -> str:
        return self.value


# --- the exchange directory, for real on disk --------------------------------

NO_DIR = "the sandbox is gone"
UNREADABLE = "yoloai could not be asked"
PRESENT = "the exchange directory is there"

NO_QUESTION = "nothing was ever asked"
PENDING = "a question is waiting"
ALREADY_ANSWERED = "the question was already answered"

STATUSES = {
    "not established": None,
    "could not be asked": _Status("unknown — yoloai failed", known=False),
    "not found": _Status("not found"),
    "running": _Status("running"),
    "idle": _Status("idle"),
}


def _exchange(tmp_path: Path, question: str) -> Path:
    """A real exchange directory in `question` state."""
    where = tmp_path / "files"
    where.mkdir(parents=True, exist_ok=True)
    if question == NO_QUESTION:
        return where
    (where / q.QUESTION_FILE).write_text(
        json.dumps({"question": "WHICH timeout?", "context": "alpha, KAN-7"})
    )
    if question == ALREADY_ANSWERED:
        # Written AFTER the question, which is `question_in`'s own rule.
        time.sleep(0.01)
        (where / q.ANSWER_FILE).write_text(json.dumps({"answer": "30s"}))
    return where


def _arrange(monkeypatch, tmp_path, directory: str, question: str):
    if directory == NO_DIR:
        monkeypatch.setattr(q, "files_dir", lambda s: None)
        return None
    if directory == UNREADABLE:
        monkeypatch.setattr(q, "files_dir", lambda s: q.Unknown(s, "yoloai not found"))
        return None
    where = _exchange(tmp_path, question)
    monkeypatch.setattr(q, "files_dir", lambda s, w=where: w)
    return where


class TestTheInvariant:
    """⚠ **The full case range, not its ends.** Delivery depends on three
    independent facts — whether rite established the Worker is alive, whether
    the exchange directory is reachable, and whether anything is actually
    waiting — and the interesting cases are the middles: a live Worker with
    no pending question (an answer would be read against its NEXT question), a
    pending question rite cannot prove anyone is still there to read (an
    answer into a void), a directory that exists but cannot be asked about.

    Asserted as an exact iff over all 45 combinations, so neither a rule that
    is too strict nor one that is too loose can pass.
    """

    @pytest.mark.parametrize(
        ("status", "directory", "question"),
        list(
            itertools.product(
                STATUSES,
                [NO_DIR, UNREADABLE, PRESENT],
                [NO_QUESTION, PENDING, ALREADY_ANSWERED],
            )
        ),
    )
    def test_an_answer_lands_exactly_when_all_three_hold(
        self, tmp_path, monkeypatch, status, directory, question
    ):
        where = _arrange(monkeypatch, tmp_path, directory, question)

        outcome = q.deliver_answer(SANDBOX, "30 seconds", status=STATUSES[status])

        alive = status in ("running", "idle")
        should = alive and directory == PRESENT and question == PENDING
        assert isinstance(outcome, q.Delivered) is should, (
            f"status={status!r} dir={directory!r} question={question!r}: got {outcome}"
        )
        if should:
            assert json.loads((where / q.ANSWER_FILE).read_text())["answer"] == (
                "30 seconds"
            )
            # The Worker's own rule for "answered" now holds.
            assert q.question_in(where, SANDBOX) is None
        else:
            assert isinstance(outcome, q.Undeliverable) and outcome.reason
            if where is not None and question != ALREADY_ANSWERED:
                assert not (where / q.ANSWER_FILE).exists(), (
                    "an undeliverable answer must leave nothing behind"
                )

    @pytest.mark.parametrize("status", ["not found"])
    def test_a_stopped_worker_is_reported_as_gone_not_merely_unreachable(
        self, tmp_path, monkeypatch, status
    ):
        """The person needs a different sentence for the two: one means try
        again, the other means the work needs starting over."""
        _arrange(monkeypatch, tmp_path, PRESENT, PENDING)

        outcome = q.deliver_answer(SANDBOX, "30s", status=STATUSES[status])

        assert isinstance(outcome, q.Undeliverable) and outcome.stopped

    def test_not_having_asked_is_a_different_sentence_from_having_asked(
        self, tmp_path, monkeypatch
    ):
        """⚠ Added because a mutation survived. Dropping the `status is None`
        branch changes no OUTCOME — `getattr(None, "known", False)` is False,
        so both paths refuse — and every other test here passed without it.
        What it changes is what the person is told: "rite never established
        whether the Worker is alive" is a bug in the caller, and "yoloAI could
        not be asked" is a fact about the machine. A guard nothing
        distinguishes is a guard nothing protects."""
        _arrange(monkeypatch, tmp_path, PRESENT, PENDING)

        never_asked = q.deliver_answer(SANDBOX, "30s", status=None)
        asked_failed = q.deliver_answer(
            SANDBOX, "30s", status=STATUSES["could not be asked"]
        )

        assert isinstance(never_asked, q.Undeliverable)
        assert isinstance(asked_failed, q.Undeliverable)
        assert "did not establish" in never_asked.reason
        assert "could not ask" in asked_failed.reason
        assert never_asked.reason != asked_failed.reason

    def test_unreachable_is_not_reported_as_gone(self, tmp_path, monkeypatch):
        _arrange(monkeypatch, tmp_path, PRESENT, PENDING)

        outcome = q.deliver_answer(
            SANDBOX, "30s", status=STATUSES["could not be asked"]
        )

        assert isinstance(outcome, q.Undeliverable) and not outcome.stopped


# --- the relay: the Owner carries it -----------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A sandboxing project with one Worker, alpha, and a real mailbox."""
    from rite_ai.managers.mailbox import MAIL_DIR_ENV

    monkeypatch.setenv(MAIL_DIR_ENV, str(tmp_path / "data"))
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "events.jsonl").write_text(
        '{"event": "sandbox-started", "sandbox": "rite-p-alpha", '
        '"worker": "alpha", "ticket": "KAN-7"}\n'
    )
    config = SimpleNamespace(sandbox=SimpleNamespace(enabled=True))
    loaded = SimpleNamespace(config=config, workers=[SimpleNamespace(name="alpha")])
    monkeypatch.setattr("rite_ai.config.parse.load_project", lambda r: loaded)
    monkeypatch.setattr(
        "rite_ai.sandbox.existing_sandbox_name", lambda w, r=None: f"rite-p-{w}"
    )
    return root


def _raised(root: Path, qid: str = "q1a2b") -> str:
    """The ledger `surface` leaves: this sandbox's question is outstanding."""
    from rite_ai.managers import manager_dir

    path = manager_dir(root, OWNER) / wq.LEDGER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({SANDBOX: qid}))
    return qid


def _answer_arrives(
    root: Path, qid: str, text: str = "30 seconds, and it is fine"
) -> None:
    """A Slack reply, as it reaches the Owner: rite's labelled first line
    (which carries the question id) and the person's words under it."""
    mailbox.send(
        root, OWNER, mailbox.INBOX, f"rite's question at 10:00 ({qid})\n{text}"
    )


class TestTheAnswerReachesTheWorker:
    def test_it_is_written_where_the_worker_polls(self, project, tmp_path, monkeypatch):
        root = project
        where = _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        monkeypatch.setattr(
            "rite_ai.sandbox.worker_sandbox_status",
            lambda w, r=None: _Status("running"),
        )
        qid = _raised(root)
        _answer_arrives(root, qid)
        said: list[str] = []

        carried = wq.relay(root, OWNER, said.append)

        assert carried == 1
        assert "30 seconds" in json.loads((where / q.ANSWER_FILE).read_text())["answer"]
        assert any("relayed" in line for line in said), said

    def test_rites_own_first_line_is_not_handed_back_as_the_answer(
        self, project, tmp_path, monkeypatch
    ):
        """⚠ The reply carries the line rite sent. Passing it through would
        answer the Worker's question with its own question id."""
        root = project
        where = _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        monkeypatch.setattr(
            "rite_ai.sandbox.worker_sandbox_status",
            lambda w, r=None: _Status("running"),
        )
        qid = _raised(root)
        _answer_arrives(root, qid)

        wq.relay(root, OWNER, lambda _m: None)

        answer = json.loads((where / q.ANSWER_FILE).read_text())["answer"]
        assert qid not in answer and "rite's question" not in answer

    def test_the_owners_mail_is_not_consumed_by_the_relay(
        self, project, tmp_path, monkeypatch
    ):
        """The answer is the Owner's to read too. Taking it here would remove
        it from the Manager's next prompt without anyone saying so."""
        root = project
        _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        monkeypatch.setattr(
            "rite_ai.sandbox.worker_sandbox_status",
            lambda w, r=None: _Status("running"),
        )
        _answer_arrives(root, _raised(root))

        wq.relay(root, OWNER, lambda _m: None)

        assert mailbox.read(root, OWNER, mailbox.INBOX), "the message was eaten"

    def test_it_is_carried_once_not_on_every_tick(self, project, tmp_path, monkeypatch):
        root = project
        _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        monkeypatch.setattr(
            "rite_ai.sandbox.worker_sandbox_status",
            lambda w, r=None: _Status("running"),
        )
        _answer_arrives(root, _raised(root))

        first = wq.relay(root, OWNER, lambda _m: None)
        second = wq.relay(root, OWNER, lambda _m: None)

        assert (first, second) == (1, 0)

    def test_mail_that_is_not_an_answer_is_left_alone(
        self, project, tmp_path, monkeypatch
    ):
        root = project
        where = _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        monkeypatch.setattr(
            "rite_ai.sandbox.worker_sandbox_status",
            lambda w, r=None: _Status("running"),
        )
        _raised(root)
        mailbox.send(root, OWNER, mailbox.INBOX, "just a note from the User")

        assert wq.relay(root, OWNER, lambda _m: None) == 0
        assert not (where / q.ANSWER_FILE).exists()


class TestAnAnswerThatCannotLandComesBack:
    """⚠ **Never a silent drop — the reason S30 is a correctness fix.** A
    person who answers and hears nothing believes the Worker is working on it.
    """

    def _stopped(self, project, tmp_path, monkeypatch):
        root = project
        _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        monkeypatch.setattr(
            "rite_ai.sandbox.worker_sandbox_status",
            lambda w, r=None: _Status("not found"),
        )
        _answer_arrives(root, _raised(root))
        said: list[str] = []
        carried = wq.relay(root, OWNER, said.append)
        return root, said, carried

    def test_nothing_is_reported_as_carried(self, project, tmp_path, monkeypatch):
        _root, _said, carried = self._stopped(project, tmp_path, monkeypatch)
        assert carried == 0

    def test_the_person_is_told_in_the_thread(self, project, tmp_path, monkeypatch):
        root, _said, _c = self._stopped(project, tmp_path, monkeypatch)

        out = [m.text for m in mailbox.read(root, OWNER, mailbox.OUTBOX)]

        assert any("did NOT reach" in t for t in out), out
        assert any("gone" in t for t in out), out

    def test_the_supervisor_says_it_too(self, project, tmp_path, monkeypatch):
        _root, said, _c = self._stopped(project, tmp_path, monkeypatch)
        assert any("could not be delivered" in line for line in said), said

    def test_the_sandbox_is_not_probed_again_on_every_tick(
        self, project, tmp_path, monkeypatch
    ):
        """⚠ Added because a mutation survived. Dropping the relayed-message
        ledger changed nothing observable: a delivered answer stops matching
        because its question id is settled, and a repeated undeliverable
        REPORT is swallowed by `asking`'s own once-per-question ledger. What
        the ledger uniquely prevents is re-attempting DELIVERY every tick —
        a yoloAI subprocess per poll, for an answer that will never land —
        and nothing here observed that until this counted it."""
        root = project
        _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        probes: list[str] = []

        def status(worker, r=None):
            probes.append(worker)
            return _Status("not found")

        monkeypatch.setattr("rite_ai.sandbox.worker_sandbox_status", status)
        _answer_arrives(root, _raised(root))

        wq.relay(root, OWNER, lambda _m: None)
        wq.relay(root, OWNER, lambda _m: None)
        wq.relay(root, OWNER, lambda _m: None)

        assert probes == ["alpha"], f"the sandbox was asked {len(probes)} times"

    def test_it_is_not_retried_forever(self, project, tmp_path, monkeypatch):
        """Reported once. A Worker that is gone stays gone, and repeating it
        every tick is how a person learns to ignore the channel."""
        root, _said, _c = self._stopped(project, tmp_path, monkeypatch)
        before = len(mailbox.read(root, OWNER, mailbox.OUTBOX))

        wq.relay(root, OWNER, lambda _m: None)

        assert len(mailbox.read(root, OWNER, mailbox.OUTBOX)) == before


class TestTheRelayIsActuallyWiredIn:
    """⚠ **The defect class this repository has already paid for.** `rite
    start`'s D-78/D-80 policy was "written in SPEC, implemented, unit tested —
    and called by nothing", and `test_no_dead_wiring` exists because nine
    public functions shipped complete and inert. A relay that is correct and
    never called would leave S30 exactly where it was, and every test above
    would still be green.

    So this asserts the join itself: the Owner's watcher — the thing the
    supervisor actually calls at each poll, cycle boundary and wait tick —
    carries an answer in.
    """

    def _watcher(self, root, monkeypatch):
        from rite_ai.cli import main as cli_main

        config = SimpleNamespace(
            sandbox=SimpleNamespace(enabled=True),
            coordination=SimpleNamespace(manager_roles=[]),
        )
        monkeypatch.setattr("rite_ai.config.parse.parse_config", lambda p: config)
        return cli_main._worker_question_watch(root, OWNER)  # noqa: PLC2701

    def test_the_watcher_delivers_an_answer(self, project, tmp_path, monkeypatch):
        root = project
        where = _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        monkeypatch.setattr(
            "rite_ai.sandbox.worker_sandbox_status",
            lambda w, r=None: _Status("running"),
        )
        _answer_arrives(root, _raised(root))

        watch = self._watcher(root, monkeypatch)
        assert watch is not None, "the Owner has no watcher at all"
        watch(lambda _m: None)

        assert (where / q.ANSWER_FILE).exists(), (
            "the watcher ran and no answer reached the Worker — the relay is "
            "not joined to the supervisor"
        )

    def test_the_watcher_relays_before_it_raises(self, project, tmp_path, monkeypatch):
        """Order matters: an answer settles its question, so relaying first
        stops `surface` re-raising a question this very tick resolved."""
        root = project
        _arrange(monkeypatch, tmp_path, PRESENT, PENDING)
        monkeypatch.setattr(
            "rite_ai.sandbox.worker_sandbox_status",
            lambda w, r=None: _Status("running"),
        )
        _answer_arrives(root, _raised(root))
        order: list[str] = []
        monkeypatch.setattr(
            "rite_ai.managers.worker_questions.relay",
            lambda r, m, s: order.append("relay") or 0,
        )
        monkeypatch.setattr(
            "rite_ai.managers.worker_questions.surface",
            lambda r, m, s: order.append("surface") or 0,
        )

        self._watcher(root, monkeypatch)(lambda _m: None)

        assert order == ["relay", "surface"], order
