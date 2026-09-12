"""Is the publish gate actually armed in CI?

The sibling of `rite_ai.gate.hook`, and deliberately shaped like it. The hook
module answers "would `git push` from this repo run the gate?"; this one
answers "would a push to the remote run it?" — the layer SPEC §11.5.1 calls
**the load-bearing one, not the backup**, because `core.hooksPath` can disarm
the hook on the developer's machine without any action by the developer and
with no signal that it happened.

`rite doctor` had a check for the disarmable layer and none for this one. That
asymmetry is how the hook defect existed in the first place: nobody asked the
question, so nobody got the wrong answer.

**Inspection only — no rendering, no writing.** Generating the workflow is
`rite init`'s job and lives in `rite_ai.cli.init.scaffold`, which imports the
path and the markers from here rather than defining its own. That direction is
not incidental: `scaffold.py`'s own module comment records what happened the
last time the same constant existed in two places — `rite init` and
`python -m rite_ai.gate install-hook` shipped divergent pre-push markers and
divergent scripts, each wired to a different installer. One definition, here,
imported there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# GitHub Actions' own fixed layout — a workflow anywhere else is not run.
CI_WORKFLOW_REL_PATH = ".github/workflows/publish-gate.yml"

# Same job as `HOOK_MARKER` in the hook module: how rite tells a file it wrote
# from one the user wrote. Carried in the template's own header comment, so it
# survives into every generated copy.
CI_WORKFLOW_MARKER = "# installed-by: rite init"

# Every marker this project has ever generated, exactly as
# `hook._RECOGNISED_MARKERS` does and for the identical reason: reword
# `CI_WORKFLOW_MARKER` with only one string recognised and every workflow rite
# itself wrote becomes "foreign", so `rite init` starts reporting its own file
# as somebody else's and refuses to touch it. Add to this tuple, never remove
# from it, if the marker ever changes.
_RECOGNISED_CI_MARKERS = (CI_WORKFLOW_MARKER,)

# Every spelling of "run the publish gate" this project actually ships. A
# workflow running any of them is armed; matching only the first one is how
# `rite doctor` came to report rite's OWN working CI gate as broken, and to
# offer `install-ci --force` — which would have overwritten it.
#
#   `rite publish check`     the console script, and what `rite init` generates
#   `rite-ai publish check`  the collision-free alias; pyproject ships BOTH as
#                            real console scripts onto the same entry point, so
#                            this is the same program, not a fallback
#   `rite_ai.gate check`     `python -m rite_ai.gate check` — the standalone
#                            module path, which exists precisely so CI can
#                            shell out without an install step. rite's own
#                            workflow uses it and `gate/__main__.py`'s
#                            docstring names that workflow as the reason.
#
# Add a spelling here the day one ships. Never remove one.
GATE_INVOCATIONS = (
    "rite publish check",
    "rite-ai publish check",
    "rite_ai.gate check",
)

# What to name in a message when telling someone their workflow runs none of
# them. The canonical one, not the list — a user reading an error wants the
# command to add, not a taxonomy.
GATE_INVOCATION = GATE_INVOCATIONS[0]


def is_rite_workflow(text: str) -> bool:
    return any(marker in text for marker in _RECOGNISED_CI_MARKERS)


# Events that make a workflow run on the way to a remote. A gate firing on
# neither is not a gate: `workflow_dispatch` alone means it runs when a human
# clicks it, which is §11.5.1's shape exactly.
_GUARDING_EVENTS = frozenset({"push", "pull_request", "pull_request_target"})

# Any of these under a `push:`/`pull_request:` mapping means the trigger may
# not fire on a given push, and the file does not say which pushes it covers.
# `branches: [main]` is fine on a project whose branch is `main` and fires for
# nothing otherwise; `tags:` alone never fires on a branch push at all;
# `paths:` depends on what the commit touched. None of that is decidable here,
# so a filtered trigger is reported as "cannot tell" rather than guessed at.
_TRIGGER_FILTERS = (
    "branches",
    "branches-ignore",
    "tags",
    "tags-ignore",
    "paths",
    "paths-ignore",
)

# A `run:` segment beginning with one of these is talking ABOUT the gate, not
# running it.
_NOT_A_COMMAND = ("echo", "printf", "#", ":")

# Shell that runs the gate and then throws its verdict away. §11.5.1 calls a
# red gate "a publish-blocking condition"; a gate that cannot go red is not
# one. `|| true` is the case the command-splitting below made WORSE — it
# splits on `||`, so the neutraliser parsed as a clean invocation.
_NEUTRALISED = re.compile(r"\|\|\s*(true|:|exit\s+0)\b")
_DISABLES_ERREXIT = re.compile(r"(?m)^\s*set\s+\+e")
_BACKGROUNDED = re.compile(r"(?<!&)&\s*$")


def _normalised_events(doc: dict) -> dict:
    """`on:` parses as the YAML boolean True (the yes/no/on/off legacy), so
    it is fetched under both spellings. Returns event name -> its config."""
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {e: None for e in on if isinstance(e, str)}
    if isinstance(on, dict):
        return on
    return {}


def _trigger_status(doc: dict) -> tuple[bool, str]:
    """Does this workflow fire on an ordinary push or pull request?"""
    events = _normalised_events(doc)
    guarding = {e: cfg for e, cfg in events.items() if e in _GUARDING_EVENTS}
    if not guarding:
        return (
            False,
            "it does not trigger on a push or a pull request — whatever it "
            "runs, nothing runs it on the way to the remote",
        )
    for config in guarding.values():
        if not isinstance(config, dict) or not any(
            key in config for key in _TRIGGER_FILTERS
        ):
            return True, ""  # at least one trigger is unfiltered: it fires
    filters = sorted(
        {k for c in guarding.values() if isinstance(c, dict) for k in c}
        & set(_TRIGGER_FILTERS)
    )
    return (
        False,
        f"every push/pull_request trigger in it is filtered ({', '.join(filters)}), "
        "so whether it fires on a given push cannot be read off the file — a "
        "`branches:` list that does not name your branch, or a `tags:`-only "
        "trigger, fires on nothing. The workflow `rite init` generates is "
        "deliberately unfiltered",
    )


def _outside_quotes(segment: str) -> bool:
    """Is one of the invocations here a COMMAND, rather than an argument?

    `grep -r 'rite publish check' docs/` and `git commit -m "rite publish
    check"` both contain the invocation and neither runs it. Quote-balance
    before the match separates the two without pretending to parse shell.
    """
    for invocation in GATE_INVOCATIONS:
        index = segment.find(invocation)
        while index != -1:
            before = segment[:index]
            if before.count("'") % 2 == 0 and before.count('"') % 2 == 0:
                return True
            index = segment.find(invocation, index + 1)
    return False


def _runs_the_gate(run: object) -> bool:
    """Does this step's `run:` actually invoke the gate, unneutralised?

    A `run:` block is a shell script, and this does not parse shell. It
    handles the cases that have actually been seen to lie — a commented-out
    line, an `echo` mentioning the command, the invocation quoted as an
    argument, `|| true`, `set +e`, a backgrounded `&` — and bails out on a
    heredoc rather than guessing at its contents.
    """
    if not isinstance(run, str):
        return False  # a list `run:` is invalid for Actions; `str()` of one
        # used to stringify to "['rite publish check']" and match.
    if "<<" in run:
        return False  # a heredoc's body is data, not commands — cannot tell.
    if _DISABLES_ERREXIT.search(run):
        return False  # the step cannot fail once errexit is off.
    for line in run.splitlines():
        line = line.strip()
        if not any(invocation in line for invocation in GATE_INVOCATIONS):
            continue
        if _NEUTRALISED.search(line) or _BACKGROUNDED.search(line):
            continue
        for segment in re.split(r"&&|\|\||;|\||&", line):
            segment = segment.strip()
            if not segment or segment.startswith(_NOT_A_COMMAND):
                continue
            if _outside_quotes(segment):
                return True
    return False


def _truthy(value: object) -> bool:
    """A literal true, including the `${{ true }}` spelling."""
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().strip("${} ").lower() == "true"
    return False


def _falsy(value: object) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().strip("${} ").lower() == "false"
    return False


def _is_disabled(node: object) -> bool:
    """A job or step switched off by a literal false `if:`, or one whose
    failure is declared not to matter. Both leave a workflow that runs the
    gate and cannot block on it.

    Only the LITERAL cases. `if: github.event_name == 'push'` is an
    expression this cannot evaluate, and guessing at it would trade a known
    over-claim for an unknown one; such a step is counted as running, and
    `workflow_runs_gate`'s docstring says so rather than hiding it.
    """
    if not isinstance(node, dict):
        return False
    if _truthy(node.get("continue-on-error")):
        return True
    return _falsy(node.get("if"))


def workflow_runs_gate(text: str) -> bool:
    """Would a push or a pull request to this repo run the publish gate?

    Three questions, all of which must be yes, because each has been the
    answer that made a gate inert:

    1. **Does it trigger on a push or a pull request, unfiltered?** One that
       fires only on `workflow_dispatch` runs when a human clicks it — and a
       `branches:`/`tags:`/`paths:` filter may mean it fires on nothing at
       all, which the file cannot tell you. An earlier version looked at the
       event names and never at the filters.
    2. **Does a step actually invoke the gate?** Read off `run:`, segment by
       segment and outside quotes, never as a substring of the file: the
       template `rite init` generates says `rite publish check` twice in its
       own header comment.
    3. **Can that step fail the build?** A literal `if: false`,
       `continue-on-error: true`, `|| true`, or `set +e` leaves a gate that
       runs and cannot block. §11.5.1 calls a red gate a publish-blocking
       condition; one that cannot go red is not a gate.

    **This is static inspection of a shell script and it cannot be exact.**
    Where it cannot tell it answers NO — unparseable YAML, a heredoc, a
    filtered trigger, `run:` built from a variable — so it under-claims, and
    the user is told to look. That is the right direction to be wrong in for
    a security control and it is not free: a false alarm is how people learn
    to ignore a health check, which is why the invocations it recognises are
    every spelling this project ships rather than only the generated one.

    Known remaining over-claims, stated rather than hidden: a step gated on a
    non-literal `if:` expression, and a gate reached through a wrapper script
    whose own contents are not read.
    """
    return inspect_workflow(text)[0]


def inspect_workflow(text: str) -> tuple[bool, str]:
    """`workflow_runs_gate`, plus WHY when the answer is no.

    The reason is not decoration. One version reported every failure as "no
    job in it runs `rite publish check`" — a false statement about a workflow
    whose job runs it perfectly well and simply never triggers. Another set
    the "switched off" reason from ANY disabled job in the file, so a
    workflow that ran the gate nowhere at all was told its gate step was
    switched off. Both are "states only what was actually measured" failing
    inside the message that says a gate is inert.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        # Cannot tell, so: no. GitHub cannot run a file it cannot parse
        # either, which makes this both the safe answer and the true one.
        return False, f"it is not valid YAML ({e.__class__.__name__}), so nothing runs"
    if not isinstance(doc, dict):
        return False, "it is not a YAML mapping, so it is not a workflow"
    triggers, why = _trigger_status(doc)
    if not triggers:
        return False, why
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return False, "it declares no jobs"
    # Only a DISABLED step that runs the gate justifies the "switched off"
    # reason. An unrelated `continue-on-error` lint job must not produce a
    # sentence about a gate step that does not exist.
    disabled_gate_step = False
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        job_off = _is_disabled(job)
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or not _runs_the_gate(step.get("run")):
                continue
            if job_off or _is_disabled(step):
                disabled_gate_step = True
                continue
            return True, ""
    if disabled_gate_step:
        return (
            False,
            "the step that runs the gate is switched off (`if: false`) or "
            "cannot fail the build (`continue-on-error: true`)",
        )
    return False, f"no job in it runs `{GATE_INVOCATION}`"


@dataclass
class CiWorkflowStatus:
    """Whether the publish gate would actually run in CI for this project.

    `active` is the only state in which it does. The ways it does not are kept
    apart because the fix differs for each — the same reasoning `HookStatus`
    records, and for the same reason: a tool that only says "not installed"
    for a file that exists but runs nothing sends the user to an installer
    that will refuse.
    """

    state: str  # "active" | "missing" | "inert" | "unreadable" | "not_a_repo"
    detail: str = ""
    # True only when a job in the file actually runs the gate. Never inferred
    # from the rite marker: a workflow rite wrote and the user has since
    # edited into a no-op is ours and inert, and a workflow someone else wrote
    # that calls `rite publish check` covers this project completely.
    runs_gate: bool = False
    # Whether rite wrote it. Reported, never used to decide `active`.
    is_ours: bool = False

    @property
    def active(self) -> bool:
        return self.state == "active"


def ci_workflow_status(project_root: Path) -> CiWorkflowStatus:
    """Would a push to the remote run the publish gate?

    Answered for the PROJECT ROOT only, because that is the only repo
    `rite init` writes the workflow into — the gate reads its scan patterns
    from `.rite/config.yaml` and its suppressions from `.rite/gitleaksignore`,
    both of which live there, so a module repo built alone in CI would re-flag
    everything the project already suppressed with a reason. Callers must say
    so rather than letting a clean line read as "every repo is covered".

    Deliberately does NOT report on the CI *run*: whether the last one was
    green is a remote fact, and `rite doctor` is local and read-only. A line
    asserting it would be the "generated content stating what nobody measured"
    failure this project's own checklist names.
    """
    if not (project_root / ".git").exists():
        return CiWorkflowStatus("not_a_repo")

    path = project_root / CI_WORKFLOW_REL_PATH
    if not path.is_file():
        return CiWorkflowStatus(
            "missing",
            f"no {CI_WORKFLOW_REL_PATH}. This check reads that one path, so "
            "if your gate runs from another workflow file or another CI "
            "system it cannot see it — but nothing rite installed is running "
            "it, and CI is the only layer a local git config cannot switch "
            "off (SPEC §11.5.1). Install one with `rite publish install-ci`.",
        )
    try:
        text = path.read_text()
    except OSError as e:
        return CiWorkflowStatus(
            "unreadable",
            f"{CI_WORKFLOW_REL_PATH} could not be read ({e}), so whether the "
            "publish gate runs in CI is unknown from here. Open it and check "
            f"a job runs `{GATE_INVOCATION}`.",
        )

    ours = is_rite_workflow(text)
    armed, why = inspect_workflow(text)
    if armed:
        return CiWorkflowStatus("active", runs_gate=True, is_ours=ours)
    whose = "rite wrote it" if ours else "rite did not write it"
    return CiWorkflowStatus(
        "inert",
        f"{CI_WORKFLOW_REL_PATH} exists ({whose}) but {why}, so the publish "
        "gate does not run in CI. Fix that file, or replace it with `rite "
        "publish install-ci --force`.",
        is_ours=ours,
    )


__all__ = [
    "CI_WORKFLOW_MARKER",
    "CI_WORKFLOW_REL_PATH",
    "GATE_INVOCATION",
    "GATE_INVOCATIONS",
    "CiWorkflowStatus",
    "ci_workflow_status",
    "is_rite_workflow",
    "inspect_workflow",
    "workflow_runs_gate",
]
