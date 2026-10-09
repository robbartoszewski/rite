"""SCRUM-100 — a pipeline-delivered ticket hands back an account of itself.

🔴 **Measured on the v0.7.0 gate run.** Ticket 1, driven by the Claude Worker,
produced 11 KB of account. Ticket 2, driven by the staged pipeline, produced a
branch and silence — because `handback` is "the record a Worker writes when it
finishes" and no Worker session finishes on that path. The gate's fourth check,
`delivered_and_handed_back`, failed on exactly that.

⚠ **The fixture is the gate run's OWN records**, copied into the test, not
hand-written: a decomposition authored by `planner` and approved by `lead`,
both subtasks accepted, the RL-8 pass with its plan fingerprint, and the stage
log with its `rejected` transition. Hand-built fixtures are how a composer
comes to read fields that real records do not have — which is the shape of the
bug this file's second test pins.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.local import decomposition as dec
from rite_ai.local import plan_state
from rite_ai.local import recompose as rc
from rite_ai.local import stage as st
from rite_ai.local.handover import account_for, branch_for

TICKET = "2"


def _repo(project: Path) -> None:
    """A project checkout carrying the ticket branch, as `deliver` leaves it."""
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(argv, cwd=project, check=True, capture_output=True)
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "tally.py").write_text("def parse_amount(t):\n    return t\n")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "tally: the throwaway app"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "-q", "-b", TICKET], cwd=project, check=True)
    (project / "src" / "tally.py").write_text(
        "def parse_amount(t):\n    return t\n\n\ndef format_amount(v):\n    return v\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"{TICKET} s1: Implement tally.format_amount"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", "-q", "main"], cwd=project, check=True)


@pytest.fixture
def run(tmp_path, monkeypatch):
    """The gate run's records, as rite wrote them."""
    monkeypatch.setenv("RITE_MAIL_DIR", str(tmp_path / "mail"))
    project = tmp_path / "app"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "brief.yaml").write_text("project:\n  name: tally\n")
    (project / ".rite" / "modules.yaml").write_text(
        "modules:\n  app:\n    path: ./\n    branch: main\n"
    )
    (project / ".rite" / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    _repo(project)

    state = plan_state.layer(project)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=(
            dec.Subtask(
                id="s1",
                intent="Implement tally.format_amount, ROUND_HALF_UP, explicit",
                scope=("src/tally.py",),
                verify="uv run pytest -q tests/test_format.py",
                cites=("tally-amounts-parsing-and-formatting-money",),
                status=dec.ACCEPTED,
                attempts=1,
                branch=TICKET,
            ),
            dec.Subtask(
                id="s2",
                intent="Extend tests/test_format.py to pin the HALF_UP case",
                scope=("tests/test_format.py",),
                verify="uv run pytest -q tests/test_format.py",
                cites=("tally-tests-how-this-library-is-checked",),
                status=dec.ACCEPTED,
                attempts=1,
                branch=TICKET,
            ),
        ),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )
    dec.write(state, plan, dec.read(state, TICKET).version)
    rc.write(
        state,
        rc.Result(
            ticket=TICKET,
            ok=True,
            commands=("uv run pytest -q tests/test_format.py",),
            plan="e367a22a9be350112be9455babddf71205c9763978d9ce4290c4ddda9ba1a65a",
            at=1791521003.0,
        ),
    )
    got = state.read_state(st.key_for(TICKET))
    state.write_state(
        st.key_for(TICKET),
        st.render(
            st.Record(
                ticket=TICKET,
                stage=st.DELIVERY_REQUESTED,
                log=tuple(
                    st.Transition(frm="", to=to, at=at, why="x")
                    for to, at in (
                        (st.DEFINED, 1.0),
                        (st.DECOMPOSED, 2.0),
                        (st.REJECTED, 3.0),
                        (st.DECOMPOSED, 4.0),
                        ("approved", 5.0),
                        (st.STEPPING, 6.0),
                        ("recomposed", 7.0),
                        (st.DELIVERY_REQUESTED, 8.0),
                    )
                ),
            )
        ),
        got.version,
    )
    return project


def test_the_account_names_the_rl6_pair_and_the_rl8_pass(run: Path):
    text = account_for(run, "lead", TICKET)
    assert text, "a pipeline ticket must get an account"
    # RL-6: who wrote it and who approved it, and that they differ.
    assert "authored by `planner`" in text
    assert "approved by `lead`" in text
    assert "RL-6 holds" in text
    # RL-8: the agreed command, and the fingerprint that ties the pass to a plan.
    assert "uv run pytest -q tests/test_format.py" in text
    assert "PASSED the verify agreed at refinement" in text
    assert "e367a22a9be35011" in text
    # Both subtasks, with what decided them.
    assert "`s1`" in text and "`s2`" in text
    assert "accepted, 1 attempt(s)" in text
    # And what actually landed, read from git.
    assert "Implement tally.format_amount" in text
    assert "src/tally.py" in text


def test_it_says_it_is_composed_and_not_a_workers_testimony(run: Path):
    """🔴 In the PROSE, never a field: `worker_handbacks._fields` keeps only
    `str` values, so a `by_rite: bool` on the record would be silently dropped
    on the way to the Manager — a flag that looks set and travels nowhere."""
    text = account_for(run, "lead", TICKET)
    assert "composed by rite" in text
    assert "not a Worker's account of its own work" in text
    assert "cannot tell you what the agent was unsure about" in text


def test_the_rejection_count_comes_from_the_append_only_stage_log(run: Path):
    """🔴 SCRUM-101, and the bug this test exists because of: a re-authored plan
    carries `returns=()`, so reading the plan said "approved without being sent
    back" two sections above a timeline showing `rejected`."""
    text = account_for(run, "lead", TICKET)
    assert "Sent back to its author 1 time(s)" in text
    assert "Approved without being sent back" not in text
    # And it does not pretend to have the objection it cannot see.
    assert "does not carry the rejection it answers" in text


def test_a_ticket_with_no_plan_gets_no_account(run: Path, tmp_path):
    """CONTROL: a Worker-driven ticket is not a pipeline ticket, and "" is how
    the caller knows to write nothing rather than an empty handback."""
    assert account_for(run, "lead", "no-such-ticket") == ""


def test_an_rl8_failure_is_said_as_one(run: Path):
    """CONTROL on the other side: the account must not read like a pass when
    the composed work failed."""
    state = plan_state.layer(run)
    rc.write(
        state,
        rc.Result(
            ticket=TICKET,
            ok=False,
            commands=("uv run pytest -q tests/test_format.py",),
            failed="uv run pytest -q tests/test_format.py",
            output="1 failed, 1 passed",
            plan="deadbeef",
            at=9.0,
        ),
    )
    text = account_for(run, "lead", TICKET)
    assert "did NOT pass its agreed verify" in text
    assert "PASSED the verify agreed at refinement" not in text
    assert "1 failed, 1 passed" in text


def test_branch_for_takes_the_branch_the_subtasks_name(run: Path):
    state = plan_state.layer(run)
    plan = dec.read(state, TICKET).plan
    assert branch_for(plan) == TICKET


# --- the delivery step actually writes it -------------------------------------


def test_the_delivery_step_writes_the_account_and_says_so(run: Path):
    """Drives the REAL call site, not the composer alone."""
    from rite_ai import handback
    from rite_ai.publishing.requests import _hand_back_the_pipelines_account

    said = _hand_back_the_pipelines_account(run, "lead", "gpu1", TICKET)
    assert any("wrote the pipeline's own account" in line for line in said), said

    record = handback.read(run, "gpu1")
    assert record is not None, "the handback the gate's 4th check looks for"
    assert record.ticket == TICKET
    assert record.branch == TICKET
    assert "composed by rite" in record.summary
    assert "RL-6 holds" in record.summary


def test_it_does_not_overwrite_a_workers_own_testimony(run: Path):
    """🔴 A Worker's account says things rite cannot reconstruct. Composing over
    it would replace testimony with a transcript."""
    from rite_ai import handback
    from rite_ai.publishing.requests import _hand_back_the_pipelines_account

    handback.write(
        run, "gpu1", ticket=TICKET, branch=TICKET, summary="what I was unsure about"
    )
    said = _hand_back_the_pipelines_account(run, "lead", "gpu1", TICKET)
    assert said == []
    assert handback.read(run, "gpu1").summary == "what I was unsure about"


def test_a_worker_record_for_ANOTHER_ticket_is_replaced(run: Path):
    """CONTROL for the guard above: one handback file per worker is the existing
    layout, so a record about a previous ticket must not block this one."""
    from rite_ai import handback
    from rite_ai.publishing.requests import _hand_back_the_pipelines_account

    handback.write(run, "gpu1", ticket="1", branch="1", summary="an older ticket")
    said = _hand_back_the_pipelines_account(run, "lead", "gpu1", TICKET)
    assert any("wrote the pipeline's own account" in line for line in said), said
    assert handback.read(run, "gpu1").ticket == TICKET


def test_a_delivery_is_never_failed_by_a_missing_account(run: Path, monkeypatch):
    """A delivery that happened must not be reported as not having happened
    because the prose could not be written."""
    from rite_ai.publishing import requests as req

    monkeypatch.setattr(
        "rite_ai.local.handover.account_for",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk is full")),
    )
    said = req._hand_back_the_pipelines_account(run, "lead", "gpu1", TICKET)
    # Reported, not swallowed: the delivery stands and the reader is told the
    # account is missing. Silence here would be a handback nobody knows is
    # absent, which is the defect this whole ticket is about.
    assert len(said) == 1, said
    assert "was delivered, but rite could not write the account" in said[0]
    assert "nothing about the delivery is in doubt" in said[0]
