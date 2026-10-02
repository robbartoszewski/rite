"""Plan validation — the SHAPE rite checks, so a reviewer spends attention on
the SLICING (DD-3, RL-63..RL-67).

Pure functions over hand-built candidates (DD-4.1). The cases that matter are the
ones with ONE defect in an otherwise-valid plan — the middles, not the empty
and perfect endpoints, because that is where a validator silently accepts
(DD-4.2, the S11 class). Each rule has a case that would go red if the rule were
reverted (DD-4.3).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from rite_ai.local import decomposition as dec
from rite_ai.local.plan_validation import DEFAULT_MAX_SUBTASKS, candidate_problems
from rite_ai.spec.digest_files import unit_filename, units_dir

AUTHOR = ("planner",)


def _root(cites=("U1", "U2", "U3", "U4")):
    root = Path(tempfile.mkdtemp())
    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    for cite in cites:
        (where / unit_filename(cite)).write_text("x")
    return root


def _sub(i, cite, scope=None, verify="pytest -q", intent=None):
    return dec.Subtask(
        id=f"s{i}",
        intent=intent or f"part {i}",
        scope=tuple(scope or [f"src/p{i}.py"]),
        verify=verify,
        cites=(cite,),
    )


def _plan(subs, **over):
    body = dict(ticket="KAN-1", decomposed_by="planner", subtasks=tuple(subs))
    body.update(over)
    return dec.Decomposition(**body)


def _valid():
    return _plan([_sub(1, "U1"), _sub(2, "U2")])


def test_a_well_formed_plan_has_no_refusals():
    root = _root()
    out = candidate_problems(_valid(), root=root, decompose_managers=AUTHOR)
    assert out.ok
    assert out.refusals == ()


def test_a_cite_that_is_not_on_disk_is_refused_RL63():
    root = _root()
    plan = _plan([_sub(1, "U1"), _sub(2, "U404")])
    out = candidate_problems(plan, root=root, decompose_managers=AUTHOR)
    assert not out.ok
    assert any("U404" in r and "RL-63" in r for r in out.refusals)
    # Control: the rule removed (nothing checking cites) would make this .ok —
    # the valid-plan test above is the paired green.


def test_a_subtask_with_no_cites_is_refused_RL63():
    root = _root()
    plan = _plan(
        [
            _sub(1, "U1"),
            dec.Subtask(id="s2", intent="x", scope=("src/b.py",), verify="t"),
        ]
    )
    out = candidate_problems(plan, root=root, decompose_managers=AUTHOR)
    assert any("cites no spec unit" in r for r in out.refusals)


def test_an_author_that_names_nothing_is_refused_RL67():
    # The independence-fails-open case: an unnamed author is reviewable by
    # anyone, including the engine that wrote it.
    root = _root()
    plan = _plan([_sub(1, "U1"), _sub(2, "U2")], decomposed_by="")
    out = candidate_problems(plan, root=root, decompose_managers=AUTHOR)
    assert any("decomposed_by" in r and "RL-67" in r for r in out.refusals)


def test_an_author_that_is_not_a_decompose_holder_is_refused_RL67():
    root = _root()
    plan = _plan([_sub(1, "U1"), _sub(2, "U2")], decomposed_by="ghost")
    out = candidate_problems(plan, root=root, decompose_managers=AUTHOR)
    assert any("ghost" in r and "RL-67" in r for r in out.refusals)


def test_exactly_one_subtask_is_refused_and_says_why_RL66():
    # Not an error — useful information: the ticket did not need decomposing.
    root = _root()
    out = candidate_problems(
        _plan([_sub(1, "U1")]), root=root, decompose_managers=AUTHOR
    )
    assert any("one subtask" in r and "RL-66" in r for r in out.refusals)


def test_too_many_subtasks_is_refused_RL66():
    root = _root(cites=tuple(f"U{i}" for i in range(DEFAULT_MAX_SUBTASKS + 1)))
    subs = [
        _sub(i, f"U{i}", scope=[f"src/p{i}.py"])
        for i in range(DEFAULT_MAX_SUBTASKS + 1)
    ]
    out = candidate_problems(_plan(subs), root=root, decompose_managers=AUTHOR)
    assert any("above the" in r and "RL-66" in r for r in out.refusals)


def test_an_absolute_scope_path_is_refused_RL65():
    root = _root()
    plan = _plan([_sub(1, "U1", scope=["/etc/passwd"]), _sub(2, "U2")])
    out = candidate_problems(plan, root=root, decompose_managers=AUTHOR)
    assert any("absolute" in r and "RL-65" in r for r in out.refusals)


def test_a_scope_path_escaping_the_repo_is_refused_RL65():
    root = _root()
    plan = _plan([_sub(1, "U1", scope=["../../etc/x"]), _sub(2, "U2")])
    out = candidate_problems(plan, root=root, decompose_managers=AUTHOR)
    assert any(
        "escapes the repository root" in r and "RL-65" in r for r in out.refusals
    )


def test_a_balanced_dotdot_inside_the_repo_is_allowed_RL65():
    # Control for RL-65: `a/../b` resolves to `b`, inside the repo — not refused.
    root = _root()
    plan = _plan([_sub(1, "U1", scope=["src/sub/../a.py"]), _sub(2, "U2")])
    out = candidate_problems(plan, root=root, decompose_managers=AUTHOR)
    assert not any("RL-65" in r for r in out.refusals)


def test_a_self_approved_candidate_is_refused():
    root = _root()
    plan = _plan([_sub(1, "U1"), _sub(2, "U2")], approval=dec.APPROVED)
    out = candidate_problems(plan, root=root, decompose_managers=AUTHOR)
    assert any("already marked approved" in r for r in out.refusals)


def test_coverage_mismatch_is_a_warning_not_a_refusal_RL64():
    # DD-3.2: keyword overlap is a poor proxy, so it warns and never blocks; the
    # parent's own verify (RL-8) is the real coverage gate.
    root = _root()
    plan = _plan(
        [_sub(1, "U1", intent="zzz"), _sub(2, "U2", intent="qqq")],
    )
    out = candidate_problems(
        plan, root=root, decompose_managers=AUTHOR, ticket_text="implement the parser"
    )
    assert out.ok  # a warning does not refuse
    assert any("RL-64" in w for w in out.warnings)


def test_coverage_overlap_produces_no_warning_RL64():
    root = _root()
    plan = _plan([_sub(1, "U1", intent="implement the parser core"), _sub(2, "U2")])
    out = candidate_problems(
        plan, root=root, decompose_managers=AUTHOR, ticket_text="implement the parser"
    )
    assert out.warnings == ()
