"""The table of things that have lied about whether a gate is armed.

`rite_ai.gate.ci.inspect_workflow` decides, for `rite doctor` and for
`rite init`, whether a push to this repo would run the publish gate. SPEC
§11.5.1 calls that the load-bearing layer, and its named failure — "a check
that was never installed, reporting itself installed" — has now been
reproduced five times inside the work that was meant to remove it:

  1. the generated workflow hardcoded `branches: [main]`;
  2. coverage was read off the rite marker, so an edited-to-inert file counted;
  3. then off a substring of the file, so the template's own header comment
     counted;
  4. then off the step alone, so a `workflow_dispatch`-only workflow counted;
  5. then off the event NAME alone, so `branches-ignore: ['**']` counted, and
     `|| true` counted, and `grep -r 'rite publish check'` counted.

Every one was found by rehearsal and none by the suite, because each fix was
verified by running it once by hand and never pinned. **That is what this file
is for.** A one-off manual check is not evidence; it is a memory of evidence.

Each row is a workflow and the answer it must get. Add the row first when the
next one of these turns up.
"""

from __future__ import annotations

import pytest

from rite_ai.gate.ci import inspect_workflow, workflow_runs_gate

_STEPS = "jobs:\n  g:\n    runs-on: ubuntu-latest\n    steps:\n"


def _wf(on: str, steps: str) -> str:
    return f"{on}\n{_STEPS}{steps}"


# (label, workflow, armed?)
CASES = [
    # --- the baseline, and every spelling this project ships ---------------
    ("plain push", _wf("on: [push]", "      - run: rite publish check\n"), True),
    (
        "pull_request mapping",
        _wf("on:\n  pull_request:", "      - run: rite publish check\n"),
        True,
    ),
    (
        "the rite-ai alias",
        _wf("on: [push]", "      - run: rite-ai publish check\n"),
        True,
    ),
    (
        "the module form rite's own workflow uses",
        _wf("on: [push]", "      - run: uv run python -m rite_ai.gate check\n"),
        True,
    ),
    (
        "chained after &&",
        _wf("on: [push]", "      - run: cd x && rite publish check\n"),
        True,
    ),
    # --- triggers: it has to actually fire (iterations 4 and 5) ------------
    (
        "workflow_dispatch only — runs when a human clicks",
        _wf("on: workflow_dispatch", "      - run: rite publish check\n"),
        False,
    ),
    ("no on: key at all", _wf("name: x", "      - run: rite publish check\n"), False),
    (
        "branches-ignore ** — can never match",
        _wf(
            "on:\n  push:\n    branches-ignore: ['**']",
            "      - run: rite publish check\n",
        ),
        False,
    ),
    (
        "tags only — a branch push never triggers it",
        _wf("on:\n  push:\n    tags: ['v*']", "      - run: rite publish check\n"),
        False,
    ),
    (
        "paths filter — depends on what the commit touched",
        _wf(
            "on:\n  push:\n    paths: ['docs/**']", "      - run: rite publish check\n"
        ),
        False,
    ),
    (
        "branches list — fires for some pushes, not readable from the file",
        _wf("on:\n  push:\n    branches: [main]", "      - run: rite publish check\n"),
        False,
    ),
    (
        "one unfiltered trigger among filtered ones is enough",
        _wf(
            "on:\n  push:\n    branches: [main]\n  pull_request:",
            "      - run: rite publish check\n",
        ),
        True,
    ),
    # --- the step has to be able to fail the build -------------------------
    (
        "if: false on the step",
        _wf("on: [push]", "      - if: false\n        run: rite publish check\n"),
        False,
    ),
    (
        "if: false on the job",
        "on: [push]\njobs:\n  g:\n    if: false\n    steps:\n"
        "      - run: rite publish check\n",
        False,
    ),
    (
        "continue-on-error",
        _wf(
            "on: [push]",
            "      - run: rite publish check\n        continue-on-error: true\n",
        ),
        False,
    ),
    (
        "continue-on-error as an expression",
        _wf(
            "on: [push]",
            "      - run: rite publish check\n        continue-on-error: ${{ true }}\n",
        ),
        False,
    ),
    (
        "|| true — and the || splitter used to make this WORSE",
        _wf("on: [push]", "      - run: rite publish check || true\n"),
        False,
    ),
    ("|| :", _wf("on: [push]", "      - run: rite publish check || :\n"), False),
    (
        "|| exit 0",
        _wf("on: [push]", "      - run: rite publish check || exit 0\n"),
        False,
    ),
    (
        "set +e disables errexit for the whole block",
        _wf(
            "on: [push]",
            "      - run: |\n          set +e\n          rite publish check\n",
        ),
        False,
    ),
    (
        "backgrounded with &",
        _wf("on: [push]", "      - run: rite publish check &\n"),
        False,
    ),
    # --- mentioned, not run (iterations 3 and 5) ---------------------------
    (
        "the template's own header comment, with an empty jobs block",
        "# rite publish check\n# rite publish check\non: [push]\njobs: {}\n",
        False,
    ),
    (
        "commented out inside a run block",
        _wf(
            "on: [push]",
            "      - run: |\n          # rite publish check (flaky)\n"
            "          echo skipped\n",
        ),
        False,
    ),
    (
        "merely mentioned in an echo",
        _wf("on: [push]", '      - run: echo "run rite publish check locally"\n'),
        False,
    ),
    (
        "quoted as a grep argument",
        _wf("on: [push]", "      - run: grep -r 'rite publish check' docs/\n"),
        False,
    ),
    (
        "quoted as a commit message",
        _wf("on: [push]", '      - run: git commit -m "rite publish check"\n'),
        False,
    ),
    (
        "inside a heredoc — body is data, not commands",
        _wf(
            "on: [push]",
            "      - run: |\n          cat <<'EOF' > notes.txt\n"
            "          rite publish check\n          EOF\n",
        ),
        False,
    ),
    # --- shapes GitHub itself would reject ---------------------------------
    (
        "run: as a list",
        _wf("on: [push]", "      - run: ['rite publish check']\n"),
        False,
    ),
    ("unparseable YAML", "# rite publish check\n\tbroken: [yaml(\n", False),
    ("not a mapping", "- just\n- a\n- list\n", False),
    ("no jobs key", "on: [push]\nname: x\n", False),
]


@pytest.mark.parametrize("label,workflow,armed", CASES, ids=[c[0] for c in CASES])
def test_inspection(label: str, workflow: str, armed: bool):
    assert workflow_runs_gate(workflow) is armed


def test_the_generated_workflow_is_armed():
    """Not a constructed string — what `rite init` actually writes."""
    from rite_ai.cli.init.scaffold import render_ci_workflow

    assert workflow_runs_gate(render_ci_workflow())


def test_rites_own_workflow_is_armed():
    """And the file in this repository, which was filtered to `main` until
    the trigger check caught it — rite not applying to itself the fix it had
    shipped to every generated project."""
    from pathlib import Path

    own = Path(__file__).resolve().parent.parent / ".github/workflows/publish-gate.yml"
    assert own.is_file()
    assert workflow_runs_gate(own.read_text())


# --- the reason has to be true too, not just the verdict --------------------


def test_an_untriggered_workflow_is_not_told_its_job_is_missing():
    """It runs the gate perfectly well. Saying "no job in it runs the gate"
    is a false statement in the message that tells someone their gate is
    inert."""
    armed, why = inspect_workflow(
        _wf("on: workflow_dispatch", "      - run: rite publish check\n")
    )
    assert not armed
    assert "does not trigger" in why
    assert "no job in it runs" not in why


def test_a_filtered_trigger_says_so_specifically():
    armed, why = inspect_workflow(
        _wf("on:\n  push:\n    paths: ['docs/**']", "      - run: rite publish check\n")
    )
    assert not armed
    assert "filtered" in why and "paths" in why


def test_an_unrelated_disabled_job_does_not_invent_a_gate_step():
    """`saw_disabled` was set by ANY disabled job, so a workflow that runs
    the gate nowhere at all was told its gate step was switched off — a
    sentence about a step that does not exist."""
    armed, why = inspect_workflow(
        "on: [push]\njobs:\n  lint:\n    continue-on-error: true\n"
        "    steps:\n      - run: ruff check .\n"
    )
    assert not armed
    assert "switched off" not in why
    assert "no job in it runs" in why


def test_a_genuinely_disabled_gate_step_does_say_switched_off():
    armed, why = inspect_workflow(
        _wf("on: [push]", "      - if: false\n        run: rite publish check\n")
    )
    assert not armed
    assert "switched off" in why
