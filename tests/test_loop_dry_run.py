"""One cycle, decided and not acted on (L-2).

The dry run exists because the loop's judgement is policy, and policy is worth
arguing with before it can spend anything. The first draft of the plan read
capacity off the claims ledger — which `start_worker` never writes to — and a
dry run would have shown a dispatched Worker still reading as free for the cost
of nothing.

Two properties carry most of the weight, both from the live dogfood:

- **"nothing to do" and "plenty to do, none of it takeable" are different
  verdicts.** They call for opposite responses and only one is a reason to
  stop, so a cycle that printed "dispatched nothing" for both would stop on the
  wrong one.
- **A conclusion arrives with its evidence.** The Owner next door reported
  "neither worker is free" and then showed dirty trees, absent commits and live
  heartbeats. A `WorkerView` with an empty `evidence` list is the failure this
  module was written to prevent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.loop import (
    CLOSED,
    IDLE,
    READY,
    SATURATED,
    UNKNOWN,
    format_cycle,
    plan_cycle,
)

NOW = 1_759_000_000.0


class FakeTicket:
    def __init__(self, id_):
        self.id = id_
        self.labels = ["scheduled"]


class FakeBoard:
    def __init__(self, *ids, error=None):
        self.tickets = [FakeTicket(i) for i in ids]
        self.error = error

    def list_tickets(self, _filter):
        if self.error is not None:
            from rite_ai.tickets import BackendError

            return BackendError(self.error)
        return self.tickets


def _free(_name, _root):
    class S:
        known = True

        def __str__(self):
            return "not found"

    return S()


def project(tmp_path: Path, workers=("alpha",), window="00:00-23:59", count=2) -> Path:
    root = tmp_path / "proj"
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\nwhat:\n  kind: app\n"
        "technology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        f"schedule:\n  timezone: UTC\n  windows:\n    - hours: '{window}'\n"
        f"      workers: {count}\n"
    )
    for name in workers:
        worker_dir = root / "workers" / name
        worker_dir.mkdir(parents=True)
        (worker_dir / "worker.yml").write_text(
            f"worker:\n  name: {name}\n  manager: ''\n  modules: []\n"
        )
    return root


def _claim(root: Path, worker: str, *paths, ticket=""):
    from rite_ai.claims.ledger import ClaimsLedger

    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    ledger.claim(list(paths), worker, ticket)


# --- the distinction the whole verdict vocabulary exists for ----------------------


def test_an_empty_board_is_idle_and_is_a_reason_to_stop(tmp_path):
    root = project(tmp_path)
    cycle = plan_cycle(root, board=FakeBoard(), sandbox_status=_free, clock=NOW)

    assert cycle.verdict == IDLE
    assert cycle.is_reason_to_stop


def test_work_with_nobody_free_is_saturated_and_is_not_a_reason_to_stop(tmp_path):
    """The live dogfood's actual state: the queue is not short of work. A loop
    that stopped here would turn a busy fleet into a stopped one and leave a
    queue nobody can see."""
    root = project(tmp_path)
    _claim(root, "alpha", "engine/parser.py", ticket="ABC-9")

    cycle = plan_cycle(root, board=FakeBoard("ABC-1", "ABC-2"), clock=NOW)

    assert cycle.verdict == SATURATED
    assert not cycle.is_reason_to_stop
    assert "NOT a reason to stop" in cycle.detail


def test_the_two_verdicts_are_not_the_same_word(tmp_path):
    """They print differently, because a reader skimming at 3am reads the
    word and not the sentence."""
    root = project(tmp_path)
    empty = plan_cycle(root, board=FakeBoard(), sandbox_status=_free, clock=NOW)
    _claim(root, "alpha", "engine/x.py")
    busy = plan_cycle(root, board=FakeBoard("ABC-1"), clock=NOW)

    assert empty.verdict != busy.verdict


def test_a_free_worker_and_waiting_work_would_dispatch(tmp_path):
    root = project(tmp_path)
    cycle = plan_cycle(root, board=FakeBoard("ABC-1"), sandbox_status=_free, clock=NOW)

    assert cycle.verdict == READY
    assert cycle.would_dispatch == [("ABC-1", "alpha")]


def test_a_dry_run_dispatches_nothing_however_ready_it_is(tmp_path):
    """`would_dispatch` is a sentence, not an action. Nothing in this module
    spawns, writes or spends."""
    root = project(tmp_path)
    cycle = plan_cycle(root, board=FakeBoard("ABC-1"), sandbox_status=_free, clock=NOW)

    assert cycle.would_dispatch
    assert not (root / ".rite" / "claims.json").exists()


def test_the_schedule_closing_the_window_is_not_idle(tmp_path):
    """§2.7.3's clean stop. Sleeping to the boundary and exiting are
    different, and only the verdict tells them apart."""
    root = project(tmp_path, window="03:00-03:01", count=0)
    cycle = plan_cycle(root, board=FakeBoard("ABC-1"), clock=NOW)

    assert cycle.verdict == CLOSED
    assert not cycle.is_reason_to_stop


# --- every conclusion arrives with its evidence -----------------------------------


def test_no_worker_is_judged_without_evidence(tmp_path):
    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "alpha", "engine/parser.py", ticket="ABC-9")

    cycle = plan_cycle(root, board=FakeBoard(), sandbox_status=_free, clock=NOW)

    for worker in cycle.workers:
        assert worker.evidence, f"{worker.name} was judged with nothing observed"
        assert worker.verdict


def test_a_busy_worker_says_what_it_is_holding(tmp_path):
    """ "Neither worker is free" is an assertion; the paths are the evidence."""
    root = project(tmp_path)
    _claim(root, "alpha", "engine/parser.py", "engine/lexer.py", ticket="ABC-9")

    cycle = plan_cycle(root, board=FakeBoard(), clock=NOW)
    (alpha,) = cycle.workers

    assert not alpha.free
    assert "engine/parser.py" in " ".join(alpha.evidence)
    assert alpha.held_paths == ("engine/parser.py", "engine/lexer.py")


def test_an_uncommitted_checkout_counts_as_busy_with_the_files_named(tmp_path):
    """The evidence the dogfood Owner showed: a dirty tree is somebody
    part-way through, whether or not they remembered to claim."""
    root = project(tmp_path)
    checkout = root / "workers" / "alpha" / "engine"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    (checkout / "half-done.py").write_text("x = 1\n")

    cycle = plan_cycle(root, board=FakeBoard(), sandbox_status=_free, clock=NOW)
    (alpha,) = cycle.workers

    assert not alpha.free
    assert "uncommitted" in alpha.verdict
    assert any("half-done.py" in e for e in alpha.evidence)


def test_a_sandbox_that_cannot_be_asked_is_not_a_free_worker(tmp_path):
    """ "Could not ask" and "no sandbox" are opposite answers, and only one is
    safe to dispatch onto."""

    def unknown(_name, _root):
        class S:
            known = False

            def __str__(self):
                return "yoloai not found"

        return S()

    root = project(tmp_path)
    cycle = plan_cycle(
        root, board=FakeBoard("ABC-1"), sandbox_status=unknown, clock=NOW
    )
    (alpha,) = cycle.workers

    assert not alpha.free
    assert "cannot tell" in alpha.verdict


# --- contention, and what rite honestly cannot say --------------------------------


def test_the_contention_surface_names_holders_and_ages(tmp_path):
    """The dogfood's queue was short of UNCONTENDED files, not of work, and
    the holders kept changing. This is the surface a reader judges against."""
    root = project(tmp_path)
    _claim(root, "alpha", "engine/parser.py", ticket="ABC-9")

    cycle = plan_cycle(root, board=FakeBoard("ABC-1"), clock=NOW + 3600)

    assert cycle.contention
    assert "alpha holds engine/parser.py" in cycle.contention[0]
    assert "ABC-9" in cycle.contention[0]


def test_the_output_admits_it_cannot_map_tickets_to_paths(tmp_path):
    """A ticket carries a label, not a file list. Printing "3 tickets blocked
    on contended paths" would be an invention, and the honest version is the
    one that says so where somebody is reading the queue."""
    root = project(tmp_path)
    _claim(root, "alpha", "engine/parser.py")

    text = "\n".join(
        format_cycle(plan_cycle(root, board=FakeBoard("ABC-1"), clock=NOW))
    )

    assert "a ticket carries a label, not a file list" in text


# --- the unknowns, which stop rather than guess -----------------------------------


def test_a_board_that_cannot_be_read_is_unknown_not_empty(tmp_path):
    """Reporting an unreadable board as an empty queue is how a loop exits
    successfully having done nothing, all night."""
    root = project(tmp_path)
    cycle = plan_cycle(
        root, board=FakeBoard(error="503 from JIRA"), sandbox_status=_free, clock=NOW
    )

    assert cycle.verdict == UNKNOWN
    assert not cycle.is_reason_to_stop
    assert any("503" in p for p in cycle.problems)


def test_no_board_configured_is_unknown_rather_than_an_empty_queue(tmp_path):
    root = project(tmp_path)
    cycle = plan_cycle(root, board=None, sandbox_status=_free, clock=NOW)

    assert cycle.verdict == UNKNOWN
    assert "no ticket backend" in cycle.detail


def test_a_worktree_is_refused_because_it_has_its_own_ledger(tmp_path):
    """`.rite/` is tracked, so a worktree is its own project root with its own
    claims.json while pointing at one board. Two loops would both see full
    capacity."""
    root = project(tmp_path)
    (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")

    cycle = plan_cycle(root, board=FakeBoard("ABC-1"), clock=NOW)

    assert cycle.verdict == UNKNOWN
    assert "worktree" in cycle.detail


def test_an_unloadable_project_is_unknown(tmp_path):
    root = tmp_path / "empty"
    (root / ".rite").mkdir(parents=True)
    cycle = plan_cycle(root, board=FakeBoard(), clock=NOW)
    assert cycle.verdict == UNKNOWN


# --- the printed artifact ---------------------------------------------------------


def test_the_report_puts_evidence_before_the_verdict(tmp_path):
    """A reader who disagrees needs to see what was observed before being told
    what it meant."""
    root = project(tmp_path)
    _claim(root, "alpha", "engine/parser.py")

    lines = format_cycle(plan_cycle(root, board=FakeBoard("ABC-1"), clock=NOW))
    text = "\n".join(lines)

    assert text.index("workers:") < text.index("verdict:")
    assert text.index("claimed paths") < text.index("verdict:")


def test_the_report_says_out_loud_that_it_started_nothing(tmp_path):
    root = project(tmp_path)
    lines = format_cycle(
        plan_cycle(root, board=FakeBoard("ABC-1"), sandbox_status=_free, clock=NOW)
    )
    assert any("DRY RUN" in line for line in lines)


def test_the_report_answers_the_stop_question_in_words(tmp_path):
    """The one thing a caller acts on, spelled out rather than inferred from
    the verdict's name."""
    root = project(tmp_path)
    lines = format_cycle(
        plan_cycle(root, board=FakeBoard(), sandbox_status=_free, clock=NOW)
    )
    assert any(line.startswith("a reason to stop: yes") for line in lines)


# --- the CLI ----------------------------------------------------------------------


def test_the_command_refuses_to_act_rather_than_quietly_reporting(
    tmp_path, monkeypatch
):
    """A user who asked for the acting version and got the reporting one
    would believe work had been dispatched."""
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = project(tmp_path)
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["loop", "run", "--no-dry-run"])

    assert result.exit_code == 1
    assert "not built" in result.output


@pytest.mark.parametrize(("ids", "code"), [((), 2), (("ABC-1",), 0)])
def test_the_exit_code_carries_the_verdict(tmp_path, monkeypatch, ids, code):
    """So a caller can branch without parsing prose — and "empty" gets its own
    code because it is the only one that is a reason to stop."""
    from click.testing import CliRunner

    import rite_ai.loop as loop_mod
    from rite_ai.cli.main import cli

    root = project(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "rite_ai.cli.main._ticket_backend", lambda _role: (FakeBoard(*ids), None)
    )
    monkeypatch.setattr(loop_mod, "_look_at_worker", _free_view)

    result = CliRunner().invoke(cli, ["loop", "run"])
    assert result.exit_code == code, result.output


def _free_view(_root, name, _clock, _status):
    from rite_ai.loop import WorkerView

    return WorkerView(name=name, free=True, verdict="free", evidence=["stub"])


# --- contention, now observed rather than guessed ----------------------------------


def test_a_ticket_refused_on_a_held_path_is_blocked_not_takeable(tmp_path):
    """The dogfood's actual state: work is ready, a Worker is free, and the
    files are held. Dispatching burns a session to rediscover a collision rite
    already knows about; stopping abandons real work."""
    from rite_ai.loop import BLOCKED

    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine/parser.py", ticket="BEN-9")
    # alpha tries the same path for BEN-1 and is refused — the observation.
    from rite_ai.claims.ledger import ClaimsLedger

    refused = ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )
    assert not refused.ok

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert cycle.verdict == BLOCKED
    assert "BEN-1" in cycle.blocked
    assert not cycle.would_dispatch
    assert not cycle.is_reason_to_stop


def test_a_blocked_ticket_says_who_is_holding_it(tmp_path):
    from rite_ai.claims.ledger import ClaimsLedger

    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine/parser.py")
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert "beta" in cycle.blocked["BEN-1"]


def test_a_refusal_stops_counting_once_the_holder_lets_go(tmp_path):
    """The holders change — that is what makes this a queue rather than a
    deadlock. A stale refusal would retire a ticket for ever."""
    from rite_ai.claims.ledger import ClaimsLedger

    root = project(tmp_path, workers=("alpha", "beta"))
    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    _claim(root, "beta", "engine/parser.py")
    ledger.claim(["engine/parser.py"], "alpha", "BEN-1")
    ledger.release("beta")

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert cycle.blocked == {}
    assert cycle.would_dispatch == [("BEN-1", "alpha")]


def test_an_untried_ticket_is_not_blocked(tmp_path):
    """Untried and blocked are different. Guessing would park work nothing
    was holding."""
    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine/parser.py")

    cycle = plan_cycle(root, board=FakeBoard("BEN-7"), sandbox_status=_free, clock=NOW)

    assert cycle.blocked == {}
    assert cycle.would_dispatch


def test_a_takeable_ticket_beside_a_blocked_one_still_goes(tmp_path):
    """Four blocked tickets must not park the fifth."""
    from rite_ai.claims.ledger import ClaimsLedger

    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine/parser.py")
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    cycle = plan_cycle(
        root, board=FakeBoard("BEN-1", "BEN-2"), sandbox_status=_free, clock=NOW
    )

    assert cycle.would_dispatch == [("BEN-2", "alpha")]


def test_the_report_counts_takeable_separately_from_waiting(tmp_path):
    """ "3 waiting" and "3 takeable" are different numbers and the difference
    is the whole finding."""
    from rite_ai.claims.ledger import ClaimsLedger

    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine/parser.py")
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    text = "\n".join(
        format_cycle(
            plan_cycle(
                root,
                board=FakeBoard("BEN-1", "BEN-2"),
                sandbox_status=_free,
                clock=NOW,
            )
        )
    )

    assert "1 takeable, 1 blocked on held paths" in text
    assert "blocked: BEN-1" in text


def test_the_refusal_record_is_bounded(tmp_path):
    """A Worker refused every thirty seconds appends a row every thirty
    seconds. The scheduler already has the scar from a record that only grew."""
    from rite_ai.claims.ledger import CONTENTION_KEEP, ClaimsLedger, read_contention

    root = project(tmp_path)
    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    _claim(root, "beta", "engine/parser.py")
    for _ in range(CONTENTION_KEEP + 25):
        ledger.claim(["engine/parser.py"], "alpha", "BEN-1")

    rows = (root / ".rite" / "contention.jsonl").read_text().splitlines()
    assert len(rows) <= CONTENTION_KEEP
    assert read_contention(root)


def test_a_granted_claim_records_no_contention(tmp_path):
    from rite_ai.claims.ledger import read_contention

    root = project(tmp_path)
    _claim(root, "alpha", "engine/parser.py")
    assert read_contention(root) == []


# --- the blind window shows up in the cycle ---------------------------------------


def test_a_just_dispatched_worker_is_not_offered_again(tmp_path):
    """Observation says free about a Worker a session is booting into, and a
    120s cycle would dispatch it again."""
    from rite_ai.loop.intents import record_intent

    root = project(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW - 5)

    cycle = plan_cycle(root, board=FakeBoard("BEN-2"), sandbox_status=_free, clock=NOW)

    assert cycle.free_workers == []
    assert not cycle.would_dispatch
    assert any("dispatched BEN-1" in e for w in cycle.workers for e in w.evidence)


def test_a_leaked_dispatch_is_reported_and_does_not_make_the_cycle_unknown(tmp_path):
    """It is a problem to say out loud, not a reason to call the whole cycle
    unreadable — the board was read fine."""
    from rite_ai.loop.intents import BLIND_SECONDS, record_intent

    root = project(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW - BLIND_SECONDS - 60)

    cycle = plan_cycle(root, board=FakeBoard("BEN-2"), sandbox_status=_free, clock=NOW)

    assert any("nothing ever appeared" in p for p in cycle.problems)
    assert cycle.verdict != UNKNOWN
    assert cycle.would_dispatch == [("BEN-2", "alpha")]


# --- L-4: a queue and a wall are different --------------------------------------


def _stale_claim(root, worker, *paths, days=9.3):
    import json as _json

    from rite_ai.claims.ledger import ClaimsLedger

    ClaimsLedger(root / ".rite" / "claims.json").claim(list(paths), worker, "BEN-9")
    path = root / ".rite" / "claims.json"
    raw = _json.loads(path.read_text())
    for entry in raw:
        if entry["worker"] == worker:
            entry["timestamp"] = NOW - days * 86400
    path.write_text(_json.dumps(raw))


def test_a_claim_whose_holder_looks_dead_is_reported_in_the_cycle(tmp_path):
    """A claim nobody will release is the difference between a queue that
    drains and one that never does. The loop is the thing watching."""
    root = project(tmp_path)
    _stale_claim(root, "ghost", "engine/parser.py")

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert [s.worker for s in cycle.suspects] == ["ghost"]


def test_blocked_by_a_dead_holder_is_deadlocked_not_blocked(tmp_path):
    """`blocked` is a queue — the holder finishes and lets go. This one will
    not clear, and sleeping on it prints the same thing until morning."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.loop import DEADLOCKED

    root = project(tmp_path, workers=("alpha",))
    _stale_claim(root, "ghost", "engine/parser.py")
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert cycle.verdict == DEADLOCKED
    assert "will not clear on its own" in cycle.detail


def test_blocked_by_a_live_holder_is_still_just_blocked(tmp_path):
    """The holder is working. That is a wait, and stopping would abandon it."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.loop import BLOCKED

    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine/parser.py")
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert cycle.verdict == BLOCKED


def test_the_loop_never_releases_a_suspect_claim(tmp_path):
    """L-4 was planned as a reap. rite cannot tell a crashed session from a
    session thinking hard, and the cost of being wrong is two sessions on one
    path — so it reports, exactly as `rite status` does."""
    root = project(tmp_path)
    _stale_claim(root, "ghost", "engine/parser.py")
    before = (root / ".rite" / "claims.json").read_text()

    plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert (root / ".rite" / "claims.json").read_text() == before


def test_the_report_prints_the_remedy(tmp_path):
    root = project(tmp_path)
    _stale_claim(root, "ghost", "engine/parser.py")

    text = "\n".join(
        format_cycle(
            plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)
        )
    )

    assert "rite release --worker ghost --force" in text


def test_a_nested_claim_still_blocks_the_ticket_it_refused(tmp_path):
    """Claims nest: a claim on `engine/` is why a request for
    `engine/parser.py` was refused. Comparing the path strings instead of
    overlapping them reports the ticket takeable — and lets it suppress a
    real deadlock by looking available."""
    from rite_ai.claims.ledger import ClaimsLedger

    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine")
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert "BEN-1" in cycle.blocked, cycle.blocked
    assert not cycle.would_dispatch


def test_a_live_blocker_keeps_it_blocked_even_beside_an_unrelated_dead_holder(
    tmp_path,
):
    """The first version asked "are all registered claim-holding Workers
    dead?", so an unrelated stale claim elsewhere in the ledger turned a
    queue into a false deadlock and stopped the loop."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.loop import BLOCKED

    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine/parser.py")  # live, and the real blocker
    _stale_claim(root, "ghost", "unrelated/elsewhere.py")  # dead, irrelevant
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert cycle.suspects, "the unrelated stale claim should still be reported"
    assert cycle.verdict == BLOCKED, cycle.detail


def test_a_dead_blocker_deadlocks_even_beside_an_unrelated_live_worker(tmp_path):
    """And the mirror: a live Worker holding something unrelated used to hide
    a real deadlock, so the loop slept until morning."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.loop import DEADLOCKED

    root = project(tmp_path, workers=("alpha", "beta"))
    _stale_claim(root, "ghost", "engine/parser.py")  # dead, and the blocker
    _claim(root, "beta", "unrelated/elsewhere.py")  # live, irrelevant
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert cycle.verdict == DEADLOCKED, cycle.detail


def test_one_dead_holder_dooms_a_ticket_even_if_another_holder_is_alive(tmp_path):
    """A ticket clears only when EVERY path in its refusal is free again, so
    one dead holder dooms it whatever the others do. A union test let one
    live holder anywhere suppress the verdict for every ticket — which is the
    sleeping-until-morning the verdict exists to stop."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.loop import DEADLOCKED

    root = project(tmp_path, workers=("alpha", "beta"))
    _stale_claim(root, "ghost", "engine/parser.py")  # dead
    _claim(root, "beta", "engine/lexer.py")  # alive
    # One ticket needs both paths, so beta letting go is not enough.
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py", "engine/lexer.py"], "alpha", "BEN-1"
    )

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert cycle.verdict == DEADLOCKED, cycle.detail


def test_an_unidentifiable_holder_is_not_counted_as_dead(tmp_path):
    """Unknown is "not provably gone". A cross-machine overlap arrives as a
    sentence with no claim object behind it, so no holder is recorded — and
    being wrong in this direction costs a cycle, not a session."""
    import json as _json

    from rite_ai.loop import BLOCKED

    root = project(tmp_path, workers=("alpha",))
    _stale_claim(root, "ghost", "engine/parser.py")
    # A refusal recorded the way a cross-machine one is: message, no held_by.
    (root / ".rite" / "contention.jsonl").write_text(
        _json.dumps(
            {
                "timestamp": NOW - 600,
                "worker": "alpha",
                "ticket": "BEN-1",
                "paths": ["engine/parser.py"],
                "overlaps": ["engine/parser.py overlaps it (held by w1 on other-box)"],
            }
        )
        + "\n"
    )

    cycle = plan_cycle(root, board=FakeBoard("BEN-1"), sandbox_status=_free, clock=NOW)

    assert cycle.verdict == BLOCKED, cycle.detail


def test_the_holder_is_recorded_as_data_not_parsed_from_the_message(tmp_path):
    """Identity came from string-partitioning a display sentence, so a
    cross-machine overlap ("held by alpha on mac-studio") parsed to a name
    matching nothing — silently disabling the verdict for exactly the case
    it was needed. Recorded beside the sentence now."""
    from rite_ai.claims.ledger import ClaimsLedger, read_contention

    root = project(tmp_path, workers=("alpha", "beta"))
    _claim(root, "beta", "engine/parser.py")
    ClaimsLedger(root / ".rite" / "claims.json").claim(
        ["engine/parser.py"], "alpha", "BEN-1"
    )

    (record,) = read_contention(root)
    assert record.held_by == ["beta"]
    assert record.holders == ["beta"]
