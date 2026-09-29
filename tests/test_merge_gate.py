"""rite merges only on a green matched to the head, and only when that head
contains the base branch's current tip.

One test per stale-green shape (`publishing/merge_gate.py`), each built from
a PR that would otherwise be merged (`_green`), so a test that passes proves
that shape alone is what refused it. `test_the_all_green_pr_is_merged` is the
control: without it, a `refusal` that refused everything would pass every
other test here.

The live case against GitHub (a PR green, then `main` moves) was observed on
a scratch repository; see `docs/design/V070_PUBLISHING.md` § 4.
"""

from __future__ import annotations

import dataclasses

import pytest

from rite_ai.publishing.merge_gate import (
    GATE_CHECK,
    Check,
    Facts,
    read_facts,
    refusal,
)

HEAD = "a" * 40
OLD = "b" * 40
TIP = "c" * 40


def _green(**changes) -> Facts:
    facts = Facts(
        number=7,
        state="open",
        merged=False,
        head_sha=HEAD,
        base_ref="main",
        base_tip=TIP,
        behind_by=0,
        mergeable=True,
        mergeable_state="clean",
        strict=True,
        checks=[
            Check("tests", HEAD, "completed", "success"),
            Check(GATE_CHECK, HEAD, "completed", "success"),
        ],
    )
    return dataclasses.replace(facts, **changes)


def test_the_all_green_pr_is_merged():
    assert refusal(_green(), HEAD) is None


@pytest.mark.parametrize(("state", "merged"), [("closed", True), ("closed", False)])
def test_shape_1_a_merged_or_closed_pr_is_not_merged(state, merged):
    said = refusal(_green(state=state, merged=merged), HEAD)
    assert said and "shape 1" in said


def test_shape_2_a_conflicting_pr_is_not_merged():
    said = refusal(_green(mergeable=False, mergeable_state="dirty"), HEAD)
    assert said and "shape 2" in said


def test_an_uncomputed_mergeability_is_not_an_answer():
    said = refusal(_green(mergeable=None), HEAD)
    assert said and "not computed" in said


def test_shape_3_a_check_from_another_head_is_refused_not_filtered():
    stale = [
        Check("tests", OLD, "completed", "success"),
        Check(GATE_CHECK, HEAD, "completed", "success"),
    ]
    said = refusal(_green(checks=stale), HEAD)
    assert said and "shape 3" in said


def test_shape_4_a_head_behind_the_current_base_tip_is_not_merged():
    """The case rite's `main` exposes: 'require branches up to date' is off,
    so GitHub itself would merge this."""
    said = refusal(_green(behind_by=1), HEAD)
    assert said and "shape 4" in said and TIP[:7] in said


def test_shape_4_unreadable_is_refused():
    said = refusal(_green(behind_by=None), HEAD)
    assert said and "could not read whether it contains" in said


@pytest.mark.parametrize(("strict", "word"), [(False, "is off"), (None, "could not")])
def test_a_base_branch_that_is_not_strict_refuses(strict, word):
    said = refusal(_green(strict=strict), HEAD)
    assert said and word in said and "up to date" in said


def test_a_head_rite_did_not_publish_is_not_merged():
    said = refusal(_green(), OLD)
    assert said and "not" in said and OLD[:7] in said


def test_a_missing_gate_is_not_a_passed_gate():
    said = refusal(_green(checks=[Check("tests", HEAD, "completed", "success")]), HEAD)
    assert said and f"no {GATE_CHECK} run" in said


def test_a_gate_alone_is_not_a_green():
    only_gate = [Check(GATE_CHECK, HEAD, "completed", "success")]
    said = refusal(_green(checks=only_gate), HEAD)
    assert said and "Zero tests is no answer" in said


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [("in_progress", ""), ("completed", "failure"), ("completed", "skipped")],
)
def test_any_check_short_of_success_refuses(status, conclusion):
    checks = [
        Check("tests", HEAD, status, conclusion),
        Check(GATE_CHECK, HEAD, "completed", "success"),
    ]
    assert refusal(_green(checks=checks), HEAD)


def test_github_saying_not_clean_refuses():
    assert refusal(_green(mergeable_state="unstable"), HEAD)


# --- reading ------------------------------------------------------------------------


def _api(responses: dict):
    def api(path: str):
        for prefix, value in responses.items():
            if path.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected read: {path}")

    return api


def _responses(**overrides):
    base = {
        "repos/o/r/pulls/7": {
            "state": "open",
            "merged": False,
            "head": {"sha": HEAD},
            # Deliberately stale, as GitHub returns it: the base when the PR
            # was last updated, NOT main's tip.
            "base": {"ref": "main", "sha": OLD},
            "mergeable": True,
            "mergeable_state": "clean",
        },
        "repos/o/r/git/ref/heads/main": {"object": {"sha": TIP}},
        f"repos/o/r/compare/{TIP}...{HEAD}": {"behind_by": 1},
        "repos/o/r/rules/branches/main": [
            {
                "type": "required_status_checks",
                "parameters": {"strict_required_status_checks_policy": True},
            }
        ],
        f"repos/o/r/commits/{HEAD}/check-runs": {
            "check_runs": [
                {"id": 1, "name": "tests", "head_sha": HEAD, "status": "completed",
                 "conclusion": "failure"},
                {"id": 2, "name": "tests", "head_sha": HEAD, "status": "completed",
                 "conclusion": "success"},
                {"id": 3, "name": GATE_CHECK, "head_sha": HEAD,
                 "status": "completed", "conclusion": "success"},
            ]
        },
        f"repos/o/r/commits/{HEAD}/status": {"sha": HEAD, "statuses": []},
    }
    base.update(overrides)
    return base


def test_the_base_tip_is_read_from_the_branch_not_from_the_pr():
    """`base.sha` is OLD here and the branch's ref is TIP. Comparing against
    `base.sha` would ask the wrong question and could answer 'contains'."""
    facts = read_facts("o/r", 7, api=_api(_responses()))
    assert facts.base_tip == TIP
    assert facts.behind_by == 1
    assert "shape 4" in refusal(facts, HEAD)


def test_a_rerun_counts_its_newest_run():
    facts = read_facts("o/r", 7, api=_api(_responses()))
    tests = [c for c in facts.checks if c.name == "tests"]
    assert tests == [Check("tests", HEAD, "completed", "success")]


def test_an_unreadable_compare_is_none_not_zero():
    api = _api(_responses(**{f"repos/o/r/compare/{TIP}...{HEAD}": RuntimeError("x")}))
    assert read_facts("o/r", 7, api=api).behind_by is None


def test_unreadable_rules_are_none_not_false_and_not_true():
    api = _api(_responses(**{"repos/o/r/rules/branches/main": RuntimeError("403")}))
    assert read_facts("o/r", 7, api=api).strict is None
