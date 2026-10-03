"""Approval is a checked act, not a hand edit (OL7).

`approval` moved from PENDING to APPROVED by editing the JSON in the state layer.
That made RL-6's gate a convention: the one thing a decomposer may not set was
set by whoever held a text editor, and nothing checked that the approver held
plan-review, was not the author, or was a different model from the author.

⚠ It matters more since OL8. An all-Ollama fleet integrates its own work, so the
chain from plan to pushed branch can run with no Claude session and no person in
it, and independence is the whole of what makes a local verdict worth acting on.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.local import decomposition as dec
from rite_ai.local.approve import Approved, Refused, approve_plan

GPU = (
    "    endpoint: http://localhost:11434\n"
    "    agent: goose\n"
    "    context_window: 32768\n"
)


def _project(tmp_path: Path, *, author_model="qwen3:8b", reviewer_model="qwen3:32b"):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n"
        "  manager_roles:\n"
        "  - name: planner\n    engine: local:small\n"
        f"    model: {author_model}\n{GPU}"
        "    duties: [decompose]\n"
        "  - name: lead\n    engine: local:large\n"
        f"    model: {reviewer_model}\n{GPU}"
        "    duties: [plan-review, decide, board, route, integrate]\n"
    )
    return tmp_path


def _spec_units(root: Path, *ids: str) -> None:
    """Derived spec units on disk, so `cites` resolve (RL-63).

    ⚠ Approval re-runs validation, which asks the question `_slice_for` asks:
    is the derived unit text there? A fixture without these is not a plan a
    project could run — an unresolvable cite stalls the subtask AFTER approval,
    which is the reason RL-63 is checked before it.
    """
    from rite_ai.spec.digest_files import unit_filename, units_dir

    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    for unit in ids:
        (where / unit_filename(unit)).write_text(f"Section {unit}: the rule.\n")


def _plan(tmp_path: Path, **kw):
    state = LocalStateLayer(tmp_path / ".rite")
    _spec_units(tmp_path, "5.1", "5.2")
    plan = dec.Decomposition(
        ticket="T-1",
        subtasks=(
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
        ),
        decomposed_by=kw.pop("decomposed_by", "planner"),
        approval=kw.pop("approval", dec.PENDING),
        **kw,
    )
    dec.write(state, plan, dec.ABSENT)
    return state


def _stored(tmp_path: Path):
    return dec.read(LocalStateLayer(tmp_path / ".rite"), "T-1").plan


# ── it works ─────────────────────────────────────────────────────────────────


def test_an_independent_plan_review_holder_approves(tmp_path):
    root = _project(tmp_path)
    _plan(root)
    got = approve_plan(root, "T-1", "lead")
    assert isinstance(got, Approved), got
    stored = _stored(root)
    assert stored.approval == dec.APPROVED
    assert stored.approved_by == "lead"
    # And that is the gate the harness reads.
    assert stored.released


def test_nothing_else_about_the_plan_is_rewritten(tmp_path):
    root = _project(tmp_path)
    _plan(root)
    before = _stored(root)
    approve_plan(root, "T-1", "lead")
    after = _stored(root)
    assert after.subtasks == before.subtasks
    assert after.decomposed_by == before.decomposed_by


# ── who may ──────────────────────────────────────────────────────────────────


def test_a_manager_without_the_duty_cannot_approve(tmp_path):
    root = _project(tmp_path)
    _plan(root)
    got = approve_plan(root, "T-1", "planner")
    assert isinstance(got, Refused)
    assert "plan-review" in got.why
    assert _stored(root).approval == dec.PENDING


def test_an_unknown_manager_cannot_approve(tmp_path):
    root = _project(tmp_path)
    _plan(root)
    assert isinstance(approve_plan(root, "T-1", "ghost"), Refused)


def test_the_author_may_not_approve_its_own_plan(tmp_path):
    """DD-3.5, at the other door into APPROVED."""
    root = _project(tmp_path)
    # give the author plan-review too, so only DD-3.5 can stop it
    cfg = root / ".rite" / "config.yaml"
    cfg.write_text(
        cfg.read_text().replace(
            "duties: [decompose]", "duties: [decompose, plan-review]"
        )
    )
    _plan(root, decomposed_by="planner")
    got = approve_plan(root, "T-1", "planner")
    assert isinstance(got, Refused)
    assert "DD-3.5" in got.why
    assert _stored(root).approval == dec.PENDING


def test_the_same_model_under_two_labels_is_not_independent(tmp_path):
    """RL-6 as Robert ruled it: the model, not the class label."""
    root = _project(
        tmp_path, author_model="qwen3.8:latest", reviewer_model="qwen3.8:latest"
    )
    _plan(root)
    got = approve_plan(root, "T-1", "lead")
    assert isinstance(got, Refused)
    assert "same model" in got.why
    assert _stored(root).approval == dec.PENDING


def test_rites_window_pin_cannot_pose_as_a_second_model(tmp_path):
    root = _project(
        tmp_path,
        author_model="qwen3.8:latest",
        reviewer_model="rite-ctx32768-qwen3.8-latest",
    )
    _plan(root)
    assert isinstance(approve_plan(root, "T-1", "lead"), Refused)


def test_an_author_that_cannot_be_placed_fails_closed(tmp_path):
    # RL-67: rite cannot check an independence claim against an author it cannot
    # find, so the plan goes back rather than through.
    root = _project(tmp_path)
    _plan(root, decomposed_by="ghost")
    got = approve_plan(root, "T-1", "lead")
    assert isinstance(got, Refused)
    assert "RL-67" in got.why


# ── what is being approved ───────────────────────────────────────────────────


def test_a_plan_that_no_longer_validates_is_not_approvable(tmp_path):
    # The file may have been edited since it was written, and this is the last
    # point before its subtasks may run.
    root = _project(tmp_path)
    state = LocalStateLayer(root / ".rite")
    read = dec.read(state, "T-1")
    _plan(root)
    read = dec.read(state, "T-1")
    broken = dec.Decomposition(
        ticket="T-1",
        subtasks=(
            dec.Subtask(
                id="s1",
                intent="only one",
                scope=("a.txt",),
                verify="pytest -q",
                cites=("5.1",),
            ),
        ),
        decomposed_by="planner",
    )
    dec.write(state, broken, read.version)
    got = approve_plan(root, "T-1", "lead")
    assert isinstance(got, Refused)
    assert "validation" in got.why


def test_an_already_approved_plan_is_said_so_not_written_again(tmp_path):
    root = _project(tmp_path)
    _plan(root, approval=dec.APPROVED, approved_by="lead")
    got = approve_plan(root, "T-1", "lead")
    assert isinstance(got, Refused)
    assert "already approved" in got.why


def test_a_rejected_plan_goes_back_rather_than_through(tmp_path):
    root = _project(tmp_path)
    _plan(root, approval=dec.REJECTED, returns=("the slicing crosses modules",))
    got = approve_plan(root, "T-1", "lead")
    assert isinstance(got, Refused)
    assert "rejected" in got.why
    assert "crosses modules" in got.why


def test_a_missing_decomposition_is_not_approvable(tmp_path):
    root = _project(tmp_path)
    (root / ".rite").mkdir(exist_ok=True)
    got = approve_plan(root, "T-404", "lead")
    assert isinstance(got, Refused)
    assert "no decomposition" in got.why
