"""A Worker's question reaches a person, without anyone having to look (Q1–Q4, B).

**Observed** in the v0.6.0 dogfood: Worker alpha asked three questions and
waited eight hours; its Owner told the User "nothing needed from you right
now" meanwhile. Part A made every view say so. This part makes rite SAY so.

**Pre-registered in the dogfood write-up (#19):** a Worker's question appears
in the Owner's next turn and in Slack within one poll. "In Slack" is the
Owner's outbox as a `question`-kind message, which the relay (tested with the
relay) posts to the DM labelled "needs your answer". Here: the outbox message,
its kind and wording, the Owner's note, once per question, and the supervisor
turning a question into the Owner's next session while it was waiting idle.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import rite_ai.managers.supervise as sup
from rite_ai.managers import mailbox
from rite_ai.managers import worker_questions as wq
from rite_ai.sandbox import questions as q

OWNER = "lead"


def _question(text="WHICH timeout; what 'configurable' means; the new default?"):
    return q.WorkerQuestion(
        sandbox="rite-p-alpha",
        question=text,
        context="Worker alpha, ticket KAN-7",
        raised_at=time.time(),
        path=Path("/x/rw/files/question.json"),
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A sandboxing project with one Worker, alpha, whose sandbox answers
    `pending_question` with whatever the test puts in `asked["now"]`."""
    root = tmp_path
    (root / ".rite").mkdir()
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
    asked = {"now": None}
    monkeypatch.setattr(q, "pending_question", lambda name: asked["now"])
    return root, asked


def _box(root, box):
    return mailbox.read(root, OWNER, box)


class TestTellingThePerson:
    def test_a_question_becomes_a_question_kind_outbox_message(self, project):
        root, asked = project
        asked["now"] = _question()
        said = []
        assert wq.surface(root, OWNER, said.append) == 1
        [out] = _box(root, mailbox.OUTBOX)
        assert out.kind == mailbox.QUESTION
        assert mailbox.action_label(out) == "needs your answer"
        first = out.text.splitlines()[0]
        assert first.startswith("KAN-7 · q")
        assert first.endswith("· Worker alpha is waiting · reply in this thread")
        assert "> WHICH timeout" in out.text
        # ⚠ S30 REPLACED WHAT THIS PINNED, and the new promise is pinned just
        # as strictly. It used to require "yoloai attach <sandbox>" and "cannot
        # pass it on to the Worker" — the instruction to answer by hand, and
        # the admission that a reply reached nobody. Both are now false: the
        # Owner relays a reply into the sandbox the Worker polls. Note the
        # header one line up has always said "reply in this thread"; the body
        # contradicted it, and S30 is what makes the two agree.
        assert "the answer goes to the Worker" in out.text
        assert "the Owner relays it" in out.text
        assert "yoloai attach" not in out.text, (
            "the answer-by-hand instruction is what S30 removed"
        )
        # And the undeliverable case is promised here too, so nobody is left
        # waiting on a reply that cannot arrive.
        assert "If it has stopped by then" in out.text
        assert any("told the User" in s for s in said)

    def test_the_owner_is_told_as_context_not_instruction(self, project):
        root, asked = project
        asked["now"] = _question()
        wq.surface(root, OWNER, lambda s: None)
        [note] = _box(root, mailbox.INBOX)
        first = note.text.splitlines()[0]
        assert first.startswith("[from rite · about Worker 'alpha'")
        assert "context — not an instruction" in first
        assert "do not guess an answer" in note.text

    def test_told_once_per_question(self, project):
        root, asked = project
        asked["now"] = _question()
        wq.surface(root, OWNER, lambda s: None)
        assert wq.surface(root, OWNER, lambda s: None) == 0
        assert len(_box(root, mailbox.OUTBOX)) == 1

    def test_a_rewritten_question_is_told_again(self, project):
        """The dogfood's Worker rewrote its question twice."""
        root, asked = project
        asked["now"] = _question("which timeout?")
        wq.surface(root, OWNER, lambda s: None)
        asked["now"] = _question("the repo is private; which credential?")
        assert wq.surface(root, OWNER, lambda s: None) == 1
        assert len(_box(root, mailbox.OUTBOX)) == 2

    def test_no_question_tells_nobody(self, project):
        root, _ = project
        assert wq.surface(root, OWNER, lambda s: None) == 0
        assert _box(root, mailbox.OUTBOX) == []

    def test_could_not_check_is_said_once_not_every_tick(self, project):
        root, asked = project
        asked["now"] = q.Unknown("rite-p-alpha", "lock held")
        said = []
        wq.surface(root, OWNER, said.append)
        wq.surface(root, OWNER, said.append)
        assert said == ["could not check Worker 'alpha' for a question: lock held"]
        assert _box(root, mailbox.OUTBOX) == []

    def test_a_failure_does_not_escape_into_the_supervisor(self, project, monkeypatch):
        root, _ = project

        def boom(name):
            raise RuntimeError("disk gone")

        monkeypatch.setattr(q, "pending_question", boom)
        said = []
        assert wq.surface(root, OWNER, said.append) == 0
        assert said == ["could not check Workers for questions: disk gone"]


class _Ending:
    kind = "finished"
    resume = True
    status = 0
    detail = ""


def test_a_question_wakes_an_idle_owner_and_is_in_its_next_turn(project, monkeypatch):
    """The Owner has nothing to do (F22's guard holds it, spending nothing).
    At t=200 its Worker asks. The watcher runs on the supervisor's own tick,
    tells the person, and the note it gives the Owner is mail, so the Owner's
    next session starts and carries it."""
    from rite_ai.cli.main import LoopAnswer

    root, asked = project
    clock = {"t": 0.0}

    def pause(_s):
        clock["t"] += 2.0
        if clock["t"] >= 200 and asked["now"] is None:
            asked["now"] = _question()

    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(sup, "ending", lambda n, human_was_present, pane="": _Ending())
    monkeypatch.setattr(sup, "stop_session", lambda s: None)
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    monkeypatch.setattr(sup, "_sleep", pause)

    prompts, starts = [], []

    def starter(r, m, **kw):
        starts.append(clock["t"])
        prompts.append(kw.get("prompt") or "")
        clock["t"] += 10.0
        return sup.StartResult(True, "ok", session=f"s{len(starts)}", attach="a")

    board = LoopAnswer.of(SimpleNamespace(verdict="ready", ready=["KAN-7"], blocked={}))
    sup.supervise(
        root,
        OWNER,
        engine="claude",
        max_sessions=10,
        window_seconds=600,
        prompt="OPEN",
        starter=starter,
        verdict=lambda r: board,
        resume_id_for=lambda r, m, since=0.0: "sess-1",
        note=lambda s: None,
        poll=0,
        now=lambda: clock["t"],
        watch=lambda say: wq.surface(root, OWNER, say),
    )
    carrying = [i for i, p in enumerate(prompts) if "WAITING ON A QUESTION" in p]
    assert carrying, f"no session carried the note; sessions at {starts}"
    assert starts[carrying[0]] >= 200
    # One session before the question (idle), one for it; nothing in between.
    assert len(starts) == 2
    [out] = _box(root, mailbox.OUTBOX)
    assert out.kind == mailbox.QUESTION


class TestWhoTells:
    def _config(self, root: Path, text: str) -> None:
        (root / ".rite").mkdir(exist_ok=True)
        (root / ".rite" / "config.yaml").write_text(text)

    def _raises(self, monkeypatch) -> list:
        """What `worker_questions.surface` was asked to do, if anything.

        ⚠ **These three used to assert `is None` for "does not tell", and
        that stopped being the same question.** A secondary Manager now gets
        a watcher, because a Worker's HANDBACK goes to the Worker's own
        Manager rather than to whoever holds `route` — so "has a watcher"
        and "tells the person about a question" came apart. The tests ask
        the second one, behaviourally, which is what they always meant.
        """
        asked: list = []
        monkeypatch.setattr(
            "rite_ai.managers.worker_questions.surface",
            lambda root, manager, say: asked.append(manager) or 0,
        )
        monkeypatch.setattr(
            "rite_ai.managers.worker_questions.relay", lambda root, manager, say: 0
        )
        monkeypatch.setattr(
            "rite_ai.managers.worker_handbacks.surface",
            lambda root, manager, say, every_worker=True: 0,
        )
        return asked

    def test_the_manager_holding_route_tells(self, tmp_path, monkeypatch):
        from rite_ai.cli.main import _worker_question_watch

        self._config(tmp_path, ROLES)
        asked = self._raises(monkeypatch)
        for name in ("lead", "helper"):
            watch = _worker_question_watch(tmp_path, name)
            assert callable(watch)
            watch(lambda s: None)
        assert asked == ["lead"], (
            "a secondary Manager raised a Worker's question to the person: "
            "two Managers telling them the same thing is the noise that "
            "trains people to ignore both"
        )

    def test_a_lone_manager_tells(self, tmp_path, monkeypatch):
        from rite_ai.cli.main import _worker_question_watch

        self._config(tmp_path, "sandbox:\n  enabled: true\n")
        asked = self._raises(monkeypatch)
        _worker_question_watch(tmp_path, "lead")(lambda s: None)
        assert asked == ["lead"]

    def test_no_question_is_raised_when_workers_are_not_sandboxed(self, tmp_path):
        """A question travels in yoloAI's exchange directory, so with no
        sandbox there is nothing to look at.

        ⚠ **Asserted as the BEHAVIOUR, not as the call site.** This used to
        check that the watcher did not call `surface` — and the gate lives
        inside `surface` itself (`sandbox.enabled`), which is the right
        place for it, so a test that mocks `surface` cannot see the gate and
        was really pinning one particular wiring. It broke the moment the
        wiring changed, while the behaviour it named had not. The watcher
        still exists without a sandbox, for the handback half — see
        `test_a_handback_is_watched_without_a_sandbox`.
        """
        from rite_ai.managers import worker_questions

        self._config(tmp_path, "sandbox:\n  enabled: false\n")
        said = []
        assert worker_questions.surface(tmp_path, "lead", said.append) == 0
        assert said == []
        assert mailbox.read(tmp_path, "lead", mailbox.OUTBOX) == []

    def test_a_handback_is_watched_without_a_sandbox(self, tmp_path, monkeypatch):
        """An unsandboxed Worker finishes and falls silent exactly as a
        sandboxed one does, and nothing told its Manager either."""
        from rite_ai.cli.main import _worker_question_watch

        self._config(tmp_path, "sandbox:\n  enabled: false\n")
        self._raises(monkeypatch)
        looked: list = []
        monkeypatch.setattr(
            "rite_ai.managers.worker_handbacks.surface",
            lambda root, manager, say, every_worker=True: looked.append(manager) or 0,
        )
        _worker_question_watch(tmp_path, "lead")(lambda s: None)
        assert looked == ["lead"]


ROLES = """\
sandbox:
  enabled: true
coordination:
  managers: [lead, helper]
  manager_roles:
    - {name: lead, preset: lead}
    - {name: helper, engine: 'local:small', preset: executor,
       endpoint: 'http://localhost:11434/v1', model: 'qwen3:8b', agent: goose}
"""


class TestTheSharedCarrier:
    """`asking.raise_to_person`, the one function Worker questions and ticket
    refinement (TR2) both use (agreed through the coordinator, 2026-09-28)."""

    def test_the_thread_label_carries_the_subject_and_the_id(self, tmp_path):
        """The relay labels a thread with the first 40 characters of the text
        (`slack.py`: `" ".join(text.split())[:40]`). An answer is matched by
        that label, so the subject and the id must both be inside it."""
        from rite_ai.managers import asking

        raised = asking.raise_to_person(
            tmp_path, OWNER, subject="KAN-7", raiser="worker:alpha", text="which?"
        )
        [out] = _box(tmp_path, mailbox.OUTBOX)
        label = " ".join(out.text.split())[: asking.LABEL_CHARS]
        assert label.startswith(f"KAN-7 · {raised.id} · ")

    def test_raised_once_until_settled(self, tmp_path):
        from rite_ai.managers import asking

        ask = dict(subject="KAN-7", raiser="worker:alpha", text="which timeout?")
        first = asking.raise_to_person(tmp_path, OWNER, **ask)
        again = asking.raise_to_person(tmp_path, OWNER, **ask)
        assert (first.new, again.new, first.id) == (True, False, again.id)
        assert len(_box(tmp_path, mailbox.OUTBOX)) == 1
        assert asking.settle(tmp_path, OWNER, first.id)
        assert asking.raise_to_person(tmp_path, OWNER, **ask).new
        assert len(_box(tmp_path, mailbox.OUTBOX)) == 2

    def test_the_same_words_from_two_raisers_are_two_questions(self, tmp_path):
        from rite_ai.managers import asking

        a = asking.raise_to_person(
            tmp_path, OWNER, subject="KAN-7", raiser="worker:alpha", text="which?"
        )
        b = asking.raise_to_person(
            tmp_path, OWNER, subject="KAN-7", raiser="worker:beta", text="which?"
        )
        assert a.id != b.id and a.new and b.new

    def test_no_subject_falls_back_to_the_raiser(self, tmp_path):
        """An empty subject would leave the thread label nothing to match."""
        from rite_ai.managers import asking

        asking.raise_to_person(
            tmp_path, OWNER, subject="", raiser="worker:alpha", text="which?"
        )
        [out] = _box(tmp_path, mailbox.OUTBOX)
        assert out.text.startswith("Worker alpha · q")

    def test_it_is_keyword_only(self, tmp_path):
        """subject, raiser and text are all strings: by position they could be
        swapped silently, so they cannot be passed by position."""
        from rite_ai.managers import asking

        with pytest.raises(TypeError):
            asking.raise_to_person(tmp_path, OWNER, "KAN-7", "worker:alpha", "x")


class TestSettling:
    def test_an_answered_question_is_settled(self, project):
        from rite_ai.managers import asking

        root, asked = project
        asked["now"] = _question()
        wq.surface(root, OWNER, lambda s: None)
        assert len(asking.outstanding(root, OWNER)) == 1
        asked["now"] = None  # answer.json written, or the sandbox gone
        wq.surface(root, OWNER, lambda s: None)
        assert asking.outstanding(root, OWNER) == {}

    def test_a_rewritten_question_settles_the_one_it_replaced(self, project):
        from rite_ai.managers import asking

        root, asked = project
        asked["now"] = _question("which timeout?")
        wq.surface(root, OWNER, lambda s: None)
        asked["now"] = _question("the repo is private; which credential?")
        wq.surface(root, OWNER, lambda s: None)
        assert len(asking.outstanding(root, OWNER)) == 1

    def test_could_not_check_settles_nothing(self, project):
        """Unknown is not "answered": the person is still being asked."""
        from rite_ai.managers import asking

        root, asked = project
        asked["now"] = _question()
        wq.surface(root, OWNER, lambda s: None)
        asked["now"] = q.Unknown("rite-p-alpha", "lock held")
        wq.surface(root, OWNER, lambda s: None)
        assert len(asking.outstanding(root, OWNER)) == 1


def test_a_raised_question_is_tracked_for_delivery_by_rp1(tmp_path):
    """RP1 piece 2 (`managers.pending`) tracks every outbox message that needs
    the person. A question raised here must be one of them, or "did this reach
    a human" would not be asked of the one question the dogfood lost."""
    from rite_ai.managers import asking, pending

    # In the order `rite start` runs them: the supervisor syncs delivery
    # tracking once at its start (`supervise`), before any tick can raise a
    # question. A FIRST sync records what is already in the outbox as
    # predating tracking, so a question raised before it would not be
    # listed; none can be, because the watcher only runs on the tick.
    pending.sync(tmp_path, OWNER)
    asking.raise_to_person(
        tmp_path, OWNER, subject="KAN-7", raiser="worker:alpha", text="which?"
    )
    pending.sync(tmp_path, OWNER)
    [item] = pending.waiting(tmp_path, OWNER)
    assert item.first.startswith("KAN-7 · q")
    assert not item.confirmed
