"""SCRUM-72e — Level 2 (the APPROACH) is required, persisted, and compared
against APPROVAL TIME.

§3.3a's own table said what was wrong, in three words each:

- **"Optional. Fail-open by DD-2.4"** — `_approach_for` returned `("", why)`
  for every failure and the subtask ran anyway, so a stage the definition of
  done names as mandatory was one any endpoint hiccup removed.
- **"never persisted"** — nothing afterwards could show the stage had
  happened, and §3.3a's premise is that each stage is "a persisted state with
  a guard".
- **"The §2.4 boundary check compares the plan with itself, so it cannot
  catch drift"** — `boundary_problem(plan.subtask(id) or subtask, subtask)`
  took both sides from one read, so it could only ever pass, while what it
  exists for is a subtask edited between approval and execution.

Robert's §5.5 decision overrides DD-2.4's fail-open: "cannot skip a stage"
requires it.

⚠ **Persisted in its OWN key, not in the `Decomposition`** — see
`level2`'s docstring. `approach.py` already says why the plan is the wrong
place: *"If it is written into `Decomposition`, the gates start reading the
executor's own words"* (RL-T26; the earlier prototype measurably lost review
depth exactly that way). §3.3a says "persisted" and does not say where, so
both properties hold rather than one being traded for the other — and there
is a test for that below.
"""

from __future__ import annotations

import pytest

from rite_ai.local import decomposition as dec
from rite_ai.local import level2, plan_state

TICKET = "T-1"


def _subtasks():
    return (
        dec.Subtask(
            id="s1",
            intent="first",
            scope=("a.txt",),
            verify="pytest -q",
            cites=("5.1",),
        ),
        dec.Subtask(
            id="s2",
            intent="second",
            scope=("b.txt",),
            verify="pytest -q",
            cites=("5.2",),
        ),
    )


def _state(tmp_path):
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    return plan_state.layer(tmp_path)


def _approved_plan(tmp_path, subtasks=None, *, record=True):
    state = _state(tmp_path)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=tuple(subtasks or _subtasks()),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )
    dec.write(state, plan, dec.read(state, TICKET).version)
    if record:
        level2.record_approval(state, TICKET, "lead", plan.subtasks)
    return state, plan


def _bytes(tmp_path) -> bytes:
    path = plan_state.home(tmp_path) / "state.json"
    return path.read_bytes() if path.exists() else b""


# --- 1. a step with no persisted approach is refused (§3.3a guard 4) ------------


def test_a_subtask_with_no_persisted_approach_may_not_run(tmp_path):
    state, plan = _approved_plan(tmp_path)
    got = level2.cleared_to_run(state, TICKET, plan.subtask("s1"))
    assert isinstance(got, level2.Blocked), got
    assert "no persisted Level-2 approach" in got.why
    assert "overriding DD-2.4's fail-open" in got.why


def test_a_persisted_approach_clears_it(tmp_path):
    """The control. Without this, a guard that refused everything would pass
    every refusal test in this file."""
    state, plan = _approved_plan(tmp_path)
    level2.write_approach(state, TICKET, plan.subtask("s1"), "1. edit a.txt")
    got = level2.cleared_to_run(state, TICKET, plan.subtask("s1"))
    assert isinstance(got, level2.Cleared), got
    assert got.steps == "1. edit a.txt"
    # And only for the subtask it was written for.
    assert isinstance(
        level2.cleared_to_run(state, TICKET, plan.subtask("s2")), level2.Blocked
    )


def test_an_approach_with_no_steps_in_it_is_not_an_approach(tmp_path):
    state, plan = _approved_plan(tmp_path)
    level2.write_approach(state, TICKET, plan.subtask("s1"), "   \n  ")
    got = level2.cleared_to_run(state, TICKET, plan.subtask("s1"))
    assert isinstance(got, level2.Blocked)
    assert "no steps in it" in got.why


# --- 2. a subtask edited after approval is refused (§3.3a guard 3) --------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("scope", ("a.txt", "secrets.env")),
        ("verify", "true"),
        ("cites", ()),
        ("intent", "something else entirely"),
    ],
)
def test_a_subtask_edited_after_approval_may_not_run(tmp_path, field, value):
    """🔴 The check the old one could not make. `scope` is the committer's
    allowlist and `verify` is the only thing RL-7 trusts, so a change to
    either turns the gates into decoration — and widening `scope` is how a
    subtask reaches a path plan review never saw."""
    from dataclasses import replace

    state, plan = _approved_plan(tmp_path)
    level2.write_approach(state, TICKET, plan.subtask("s1"), "1. do it")
    before = _bytes(tmp_path)

    edited = replace(plan.subtask("s1"), **{field: value})
    got = level2.cleared_to_run(state, TICKET, edited)
    assert isinstance(got, level2.Blocked), got
    assert "not the one that was approved" in got.why
    assert "compared the plan with itself" in got.why
    assert _bytes(tmp_path) == before, "a refusal changes nothing"


@pytest.mark.parametrize("field", ["status", "attempts", "branch", "last_failure"])
def test_the_harnesss_own_bookkeeping_is_not_part_of_the_digest(tmp_path, field):
    """The control for the test above: `status`, `attempts`, `branch` and
    `last_failure` move legitimately during a turn. A digest covering them
    would refuse every second subtask of every plan."""
    from dataclasses import replace

    state, plan = _approved_plan(tmp_path)
    level2.write_approach(state, TICKET, plan.subtask("s1"), "1. do it")
    moved = replace(
        plan.subtask("s1"),
        **{field: {"status": dec.RUNNING, "attempts": 3}.get(field, "anything")},
    )
    assert isinstance(level2.cleared_to_run(state, TICKET, moved), level2.Cleared)


def test_an_approach_written_for_a_different_subtask_does_not_clear_it(tmp_path):
    """Steps written for one subtask are not an approach for another wearing
    the same id — which is what an edit between the approach and the step
    produces."""
    from dataclasses import replace

    state, plan = _approved_plan(tmp_path)
    # An approach written for a WIDER scope, then the approved subtask runs.
    level2.write_approach(
        state, TICKET, replace(plan.subtask("s1"), scope=("a.txt", "b.txt")), "1. do it"
    )
    got = level2.cleared_to_run(state, TICKET, plan.subtask("s1"))
    assert isinstance(got, level2.Blocked), got
    assert "written for a different subtask" in got.why


# --- 3. what approval recorded is what the comparison is against ---------------


def test_a_plan_approved_with_no_digests_recorded_runs_nothing(tmp_path):
    """Fail-closed. `approve_plan` writes the plan first and the digests
    second, so a failure there leaves the plan APPROVED with nothing
    recorded — and then no subtask may run, which is the safe direction."""
    state, plan = _approved_plan(tmp_path, record=False)
    level2.write_approach(state, TICKET, plan.subtask("s1"), "1. do it")
    got = level2.cleared_to_run(state, TICKET, plan.subtask("s1"))
    assert isinstance(got, level2.Blocked), got
    assert "recorded no subtask digests" in got.why


def test_a_subtask_the_approval_never_recorded_may_not_run(tmp_path):
    """A subtask appended to the plan after approval is one nobody reviewed."""
    state, plan = _approved_plan(tmp_path)
    extra = dec.Subtask(
        id="s9", intent="snuck in", scope=("c.txt",), verify="pytest -q", cites=("5.1",)
    )
    level2.write_approach(state, TICKET, extra, "1. do it")
    got = level2.cleared_to_run(state, TICKET, extra)
    assert isinstance(got, level2.Blocked), got
    assert "never approved" in got.why


@pytest.mark.parametrize(
    "value, expected",
    [
        (b"not json", "not readable"),
        (b'{"format_version": 9, "digests": {}}', "format"),
        (b'{"format_version": 1, "digests": "nope"}', "not a map of digests"),
    ],
)
def test_an_unreadable_approval_record_runs_nothing(tmp_path, value, expected):
    state, plan = _approved_plan(tmp_path, record=False)
    state.write_state(
        level2.approvals_key(TICKET), value, level2.approval_version(state, TICKET)
    )
    level2.write_approach(state, TICKET, plan.subtask("s1"), "1. do it")
    got = level2.cleared_to_run(state, TICKET, plan.subtask("s1"))
    assert isinstance(got, level2.Blocked), got
    assert expected in got.why


def test_an_unreadable_approach_is_not_mistaken_for_none(tmp_path):
    """Three answers, not two: "cannot read" must not run a turn, and must not
    be read as "none yet" either — which would overwrite it."""
    state, plan = _approved_plan(tmp_path)
    key = level2.approach_key(TICKET, "s1")
    state.write_state(key, b"not json", state.read_state(key).version)
    got = level2.read_approach(state, TICKET, "s1")
    assert isinstance(got, str) and "not readable" in got
    assert isinstance(
        level2.cleared_to_run(state, TICKET, plan.subtask("s1")), level2.Blocked
    )


# --- 4. the digest itself --------------------------------------------------------


def test_the_digest_is_over_the_load_bearing_fields_only(tmp_path):
    from dataclasses import replace

    sub = _subtasks()[0]
    base = level2.digest(sub)
    for field in level2.BOUND_FIELDS:
        changed = {
            "id": "other",
            "intent": "other",
            "scope": ("other.txt",),
            "verify": "other",
            "cites": ("9.9",),
        }[field]
        assert level2.digest(replace(sub, **{field: changed})) != base, field
    for field, value in (
        ("status", dec.RUNNING),
        ("attempts", 7),
        ("branch", "b"),
        ("last_failure", "x"),
    ):
        assert level2.digest(replace(sub, **{field: value})) == base, field


def test_the_digest_distinguishes_the_ORDER_within_a_scope_list(tmp_path):
    """⚠ The lists are NOT sorted, deliberately. `scope` is the committer's
    allowlist and `cites` is what RL-63 resolves; two orderings are two
    different values and a digest that normalised them would let one be
    swapped for the other after approval.

    (The mutation run found the first version of this test asserting the
    opposite property — that field ORDER does not matter — which is
    unobservable: the digest is built from `BOUND_FIELDS` in a fixed order, so
    nothing could ever produce a different one. A test nothing can fail is not
    a test.)"""
    a = dec.Subtask(id="s", intent="i", scope=("x", "y"), verify="v", cites=("5.1",))
    assert level2.digest(a) == level2.digest(
        dec.Subtask(id="s", intent="i", scope=("x", "y"), verify="v", cites=("5.1",))
    )
    from dataclasses import replace

    assert level2.digest(replace(a, scope=("y", "x"))) != level2.digest(a)
    assert level2.digest(replace(a, cites=("5.2", "5.1"))) != level2.digest(
        replace(a, cites=("5.1", "5.2"))
    )


def test_the_digest_is_stable_across_processes(tmp_path):
    """sha256 over canonical JSON, not `hash()`: a digest recorded by one
    process is compared by another, and Python randomises `hash()` per
    process."""
    import subprocess
    import sys

    code = (
        "from rite_ai.local import level2\n"
        "from rite_ai.local.decomposition import Subtask\n"
        "print(level2.digest(Subtask(id='s', intent='i', scope=('x',), "
        "verify='v', cites=('5.1',))))"
    )
    here = level2.digest(
        dec.Subtask(id="s", intent="i", scope=("x",), verify="v", cites=("5.1",))
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == here


# --- 5. the channel stays segregated (RL-T26) -----------------------------------


def test_nothing_an_executor_wrote_enters_the_decomposition(tmp_path):
    """⚠ §3.3a says "persisted" and does not say where; `approach.py` says why
    it must not be the plan: *"If it is written into `Decomposition`, the
    gates start reading the executor's own words"*. Plan review, step review
    and recomposition all read that artifact, so this is the property that
    keeps a review a review."""
    state, plan = _approved_plan(tmp_path)
    before = dec.render(dec.read(state, TICKET).plan)

    level2.write_approach(
        state, TICKET, plan.subtask("s1"), "1. I will rewrite the verify"
    )
    after = dec.read(state, TICKET).plan
    assert dec.render(after) == before, "the plan is byte-unchanged"
    assert "rewrite the verify" not in dec.render(after).decode()
    # And the Decomposition has no field one could be written into.
    assert not any(
        name in {f.name for f in __import__("dataclasses").fields(dec.Decomposition)}
        for name in ("approach", "steps", "notes", "guidance", "reviewer_hint")
    )


def test_the_approach_and_the_approval_live_outside_the_project_tree(tmp_path):
    """They are read by the guard that decides whether work may run, so a
    Manager that could write them could clear its own subtask (SCRUM-72c)."""
    state, plan = _approved_plan(tmp_path)
    level2.write_approach(state, TICKET, plan.subtask("s1"), "1. do it")
    where = plan_state.home(tmp_path)
    assert (where / "state.json").is_file()
    assert not str(where.resolve()).startswith(str(tmp_path.resolve()) + "/")
    assert not plan_state.legacy_path(tmp_path).exists()


# --- 6. through the executor, which is where it has to hold ---------------------


def _project(tmp_path):
    from rite_ai.spec.digest_files import unit_filename, units_dir

    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: p\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  manager_roles:\n"
        "  - name: planner\n    engine: claude\n    duties: [decompose, execute]\n"
    )
    where = units_dir(tmp_path)
    where.mkdir(parents=True, exist_ok=True)
    for unit in ("5.1", "5.2"):
        (where / unit_filename(unit)).write_text(f"Section {unit}.\n")
    return tmp_path


def test_take_one_step_refuses_a_subtask_with_no_approach_and_fails_nothing(tmp_path):
    """🔴 The property end to end, and the one §5.5 changes: a step does not
    run without a persisted approach.

    ⚠ **And it is not a failed subtask** (RL-47). The step is refused and
    reported; the subtask keeps its status and burns no attempt, because an
    endpoint that was down did not produce a bad approach — it produced none,
    and retiring a subtask for an outage is the distinction RL-47 exists for.
    """
    from rite_ai.local import step as executor

    root = _project(tmp_path)
    state, plan = _approved_plan(root)
    before = dec.render(dec.read(state, TICKET).plan)

    class _Agent:
        def __init__(self):
            self.turns = 0

        def run(self, *a, **kw):
            self.turns += 1
            raise AssertionError("the turn must not run without an approach")

    agent = _Agent()
    got = executor.take_one_step(root, "planner", TICKET, state=state, agent=agent)
    assert got.ran is False
    assert "no persisted Level-2 approach" in got.problem
    assert agent.turns == 0
    # The plan is byte-unchanged: no status moved, no attempt spent.
    assert dec.render(dec.read(state, TICKET).plan) == before


def test_the_PERSISTED_approach_is_what_reaches_the_turn(tmp_path):
    """The control, and the point of persisting it: the turn is given the
    stored steps, not a fresh guess. `harness.Context.approach` is where they
    land, and a turn built without them would make the stage decorative."""
    from rite_ai.local import step as executor
    from rite_ai.local.harness import AgentReport, Commit, VerifyResult

    root = _project(tmp_path)
    state, plan = _approved_plan(root)
    level2.write_approach(state, TICKET, plan.subtask("s1"), "1. edit a.txt")

    seen = []

    class _Agent:
        def run(self, context, workspace):
            seen.append(context.approach)
            return AgentReport(
                claimed_success=True, summary="did it", touched=("a.txt",)
            )

    class _Verifier:
        def run(self, command, workspace):
            return VerifyResult(True, output="")

    class _Committer:
        def commit_to_branch(self, workspace, branch, message):
            return Commit(sha="c0ffee1234")

    class _Claims:
        def take(self, paths, worker):
            return True

        def release(self, paths, worker):
            return None

    got = executor.take_one_step(
        root,
        "planner",
        TICKET,
        state=state,
        agent=_Agent(),
        verifier=_Verifier(),
        committer=_Committer(),
        claims=_Claims(),
    )
    assert got.ran is True, got.problem
    assert seen == ["1. edit a.txt"], seen


# --- 7. what the mutation run found missing ------------------------------------


def test_approve_plan_records_a_digest_for_every_subtask(tmp_path):
    """🔴 The producing half. Nothing asserted that `approve_plan` records
    anything, so a version of it that recorded nothing would have passed every
    test here — and then every step would be refused, which looks like a
    different bug entirely."""
    from rite_ai.local.approve import Approved, approve_plan
    from rite_ai.spec.digest_files import unit_filename, units_dir

    root = _project(tmp_path)
    (root / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  manager_roles:\n"
        "  - name: planner\n    engine: local:small\n    model: qwen3:8b\n"
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n    duties: [decompose, execute]\n"
        "  - name: lead\n    engine: claude\n"
        "    duties: [plan-review, decide, board, route, integrate]\n"
    )
    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    for unit in ("5.1", "5.2"):
        (where / unit_filename(unit)).write_text(f"Section {unit}.\n")

    state = _state(root)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=_subtasks(),
        decomposed_by="planner",
        approval=dec.PENDING,
    )
    dec.write(state, plan, dec.read(state, TICKET).version)

    got = approve_plan(root, TICKET, "lead", state=state)
    assert isinstance(got, Approved), got
    digests = level2.approved_digests(state, TICKET)
    assert not isinstance(digests, str), digests
    assert digests == {s.id: level2.digest(s) for s in plan.subtasks}


def test_an_approval_whose_digests_could_not_be_recorded_is_said_not_hidden(tmp_path):
    """Fail-closed AND said. The plan is APPROVED either way, and with no
    digests recorded every step is refused — so a caller told "approved" with
    nothing recorded would be told the pipeline is fine while no subtask can
    ever run."""
    from rite_ai.coordination.state_layer import Unavailable
    from rite_ai.local.approve import Refused, approve_plan
    from rite_ai.spec.digest_files import unit_filename, units_dir

    root = _project(tmp_path)
    (root / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  manager_roles:\n"
        "  - name: planner\n    engine: local:small\n    model: qwen3:8b\n"
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n    duties: [decompose, execute]\n"
        "  - name: lead\n    engine: claude\n"
        "    duties: [plan-review, decide, board, route, integrate]\n"
    )
    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    for unit in ("5.1", "5.2"):
        (where / unit_filename(unit)).write_text(f"Section {unit}.\n")

    inner = _state(root)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=_subtasks(),
        decomposed_by="planner",
        approval=dec.PENDING,
    )
    dec.write(inner, plan, dec.read(inner, TICKET).version)

    class _ApprovalsUnwritable:
        """The plan write goes through; the digests write does not."""

        def __init__(self, real):
            self.real = real

        def read_state(self, key):
            return self.real.read_state(key)

        def write_state(self, key, value, expected):
            if key == level2.approvals_key(TICKET):
                return Unavailable(reason="the disk went away")
            return self.real.write_state(key, value, expected)

    got = approve_plan(root, TICKET, "lead", state=_ApprovalsUnwritable(inner))
    assert isinstance(got, Refused), got
    assert "IS now approved" in got.why
    assert "could not be recorded" in got.why
    assert "may run until the plan is reviewed again" in got.why
    # The plan really is approved, and nothing may run from it.
    assert dec.read(inner, TICKET).plan.approval == dec.APPROVED
    blocked = level2.cleared_to_run(inner, TICKET, plan.subtask("s1"))
    assert isinstance(blocked, level2.Blocked)
    assert "recorded no subtask digests" in blocked.why


def test_a_stale_persisted_approach_is_RE_PRODUCED_not_merely_refused(tmp_path):
    """A plan rejected, re-authored and re-approved with a changed subtask of
    the same id leaves an approach written for the old one. The step must
    produce a new approach for what was approved NOW, rather than stalling on
    a stale one for ever."""
    from dataclasses import replace

    from rite_ai.local import step as executor
    from rite_ai.local.harness import AgentReport, Commit, VerifyResult

    root = _project(tmp_path)
    state, plan = _approved_plan(root)
    old = plan.subtask("s1")
    level2.write_approach(state, TICKET, old, "1. the OLD steps")

    # Re-approved with a wider scope: a different subtask under the same id.
    renewed = replace(old, scope=("a.txt", "c.txt"))
    plan2 = dec.Decomposition(
        ticket=TICKET,
        subtasks=(renewed, plan.subtask("s2")),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )
    dec.write(state, plan2, dec.read(state, TICKET).version)
    level2.record_approval(
        state,
        TICKET,
        "lead",
        plan2.subtasks,
        expected=level2.approval_version(state, TICKET),
    )

    seen = []

    class _Agent:
        def run(self, context, workspace):
            seen.append(context.approach)
            return AgentReport(claimed_success=True, summary="x", touched=("a.txt",))

    class _Verifier:
        def run(self, command, workspace):
            return VerifyResult(True, output="")

    class _Committer:
        def commit_to_branch(self, workspace, branch, message):
            return Commit(sha="c0ffee1234")

    class _Claims:
        def take(self, paths, worker):
            return True

        def release(self, paths, worker):
            return None

    got = executor.take_one_step(
        root,
        "planner",
        TICKET,
        state=state,
        agent=_Agent(),
        verifier=_Verifier(),
        committer=_Committer(),
        claims=_Claims(),
        approach_for=lambda *a, **kw: ("1. the NEW steps", ""),
    )
    assert got.ran is True, got.problem
    assert seen == ["1. the NEW steps"], seen
    stored = level2.read_approach(state, TICKET, "s1")
    assert not isinstance(stored, str) and stored is not None
    assert stored.steps == "1. the NEW steps"
    assert stored.digest == level2.digest(renewed)
