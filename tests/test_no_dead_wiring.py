"""Every public coordination function is called, or says why not.

Phase 2 produced NINE instances of one defect: code that was complete,
correct, tested — and invoked by nobody. The tick with no caller; the
heartbeat publishing a hardcoded zero; `rite claim` passing no state layer;
release never publishing; the stalled-Manager handover and the claim expiry
with no caller; the message log written and never read; a first promotion
recording nothing; and the Owner's own assignment path, still dead below.

Every one of them had passing tests, because every test supplied the
argument or called the function itself. Unit tests cannot see this: the
defect is the ABSENCE of a caller, and absence is exactly what a test that
calls the thing cannot detect.

So this is a test about the shape of the codebase rather than its
behaviour. A public function in `coordination/` must be reachable from
production code outside its own module — or be listed below WITH A REASON.
The list is the point: it makes "nothing calls this" a decision somebody
wrote down, instead of something nobody noticed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
COORDINATION = SRC / "rite_ai" / "coordination"

# ⚠ WIDENED 2026-09-20, after the third instance in one week of something
# that exists and is never invoked. `coordination/` was the scope because
# that is where nine of them shipped at once — but `manager_to_start` was
# written into SPEC as D-78, implemented, unit tested, and unreachable
# because of one condition in the caller, and it sat outside this glob.
#
# The common thread across all three (`Claim.ticket`, `workers_at`,
# `manager_to_start`) is that **unit tests prove a function correct and say
# nothing about whether anything calls it** — so passing tests are what made
# each of them look finished. This guard is the only check in the suite that
# asks the other question, which is an argument for widening it rather than
# for writing a second one.
WATCHED = (
    COORDINATION,
    SRC / "rite_ai" / "managers",
)

# name -> why nothing calls it. A reason is required; "not yet" is not one.
UNCALLED_ON_PURPOSE = {
    "project_transcript_dir": (
        "Called by `latest_session_id` in the same file. Public because it "
        "is the one place a provider's private directory layout is encoded, "
        "and a test asserts the naming rule against it — a private helper "
        "would move that assertion into a test of something else."
    ),
    "launch_command": (
        "Called by `_default_starter` in the same file. Public because it is "
        "the one place the engine string becomes a command line, and SPEC "
        "§9.14.11 points at it by name as the thing that is NOT an adapter — "
        "a private `_launch_command` would make the limit harder to cite than "
        "to keep."
    ),
    # --- rite_ai/managers/, added when this guard was widened ---
    "user_dir": (
        "Called by `instance_path` in the same file. `called_outside` counts "
        "callers in OTHER files, which is right for `coordination/` where "
        "each file is a mechanism, and counts a package's internal API as "
        "dead. Exempted rather than relaxing the rule, because relaxing it "
        "would hide the nine-at-once case this guard was written for."
    ),
    "instance_path": (
        "Same: called by `record_instance`, `read_instance` and "
        "`forget_instance` in the same file."
    ),
    "pane_pid": (
        "Called by `start` in the same file. It exists as a named function "
        "because what it returns — tmux's pane pid rather than the CLI's own "
        "— is the whole point, and a comment would not have carried that."
    ),
    "forget_instance": (
        "Nothing removes an instance record yet because `rite stop <manager>` "
        "is not built (v0.5.1 plan). A stale record does not wedge anything: "
        "`running()` treats a dead session or a dead pid as absent, so the "
        "cost of not calling this is a file, not a refusal."
    ),
    "running_instances": (
        "For `rite status` to list running Managers, which is not built. "
        "Kept rather than deferred because `_a_manager_is_running` needed "
        "exactly this shape and asking per-name was the wrong one."
    ),
    # `manager_views` and `assign_to_manager` were here, exempted because
    # "whether an unattended tick may [write to the board] is Q9, unanswered.
    # Wiring it is one call in `_owner_duties`." Q9 now has a switch
    # (`coordination.assign_unattended`) and that call exists, so those
    # exemptions are gone rather than reworded — which is what this file's own
    # staleness test is for.
    "choose_manager": (
        "The DECISION half of `assign_to_manager`, which is called. Split so "
        "the choice can be made and tested without a board write — the "
        "routing rules (duty before load, no fallback when nobody holds the "
        "duty) are the part worth testing, and a test that had to supply a "
        "fake board to reach them would be testing the write instead."
    ),
    "renewal_interval": (
        "Policy a caller may want to read (a third of the lease, so two "
        "renewals can fail before expiry). `RenewalLoop` uses it as its "
        "default; the scheduler drives ticks itself and needs no loop."
    ),
    "hand_over_machines_work": (
        "The one path both handover triggers share (D-14: do not write a "
        "second handover path). Its callers are the two public wrappers in "
        "the same module; it is public so a third trigger has something to "
        "call rather than a reason to copy it."
    ),
}


# Behavioural classes only. A result type nobody NAMES is normal — callers
# use the object a function handed them — but a class nobody constructs is
# the biggest instance this phase produced: the whole Manager loop.
UNCONSTRUCTED_ON_PURPOSE = {
    "LocalStateLayer": (
        "The second backend, and the reason the interface stayed honest: "
        "production defaults to git (D-19) while the conformance suite runs "
        "both. A substitutable backend that nothing in production picks is "
        "what D-21 asks for, not dead wiring."
    ),
    "RenewalLoop": (
        "A renewal thread for a long-lived process. The scheduler renews "
        "from its own tick instead (§5.1.2), so nothing here needs a thread "
        "— it exists for a caller that is not cron."
    ),
    # `Assigned` and `NotAssigned` were here too. They are dataclasses, so
    # `behavioural_classes()` never collected them and the exemptions did
    # nothing either way — but they said "blocked on Q9", and a list that
    # still says that after Q9 is answered is how the next dead entry hides.
}


def behavioural_classes() -> dict[str, Path]:
    """Public classes that DO something, as opposed to carrying a result."""
    found: dict[str, Path] = {}
    for directory in WATCHED:
        for path in sorted(directory.glob("*.py")):
            for node in ast.parse(path.read_text()).body:
                if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                    continue
                is_dataclass = any(
                    (isinstance(d, ast.Name) and d.id == "dataclass")
                    or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                    or (
                        isinstance(d, ast.Call)
                        and getattr(d.func, "id", "") == "dataclass"
                    )
                    for d in node.decorator_list
                )
                if is_dataclass:
                    continue
                found[node.name] = path
    return found


def mentioned_outside(name: str, defined_in: Path) -> bool:
    pattern = re.compile(rf"\b{name}\b")
    for path in SRC.rglob("*.py"):
        if path == defined_in:
            continue
        if pattern.search(path.read_text()):
            return True
    return False


def test_every_behavioural_class_is_constructed_or_explained():
    """The defect that started this: `ManagerMonitor` was complete, tested,
    and constructed by nobody, so the whole Phase 2 loop never ran."""
    dead = {
        name: path.name
        for name, path in behavioural_classes().items()
        if not mentioned_outside(name, path) and name not in UNCONSTRUCTED_ON_PURPOSE
    }
    assert not dead, (
        f"public coordination classes nothing constructs: {dead}. The Manager "
        "loop shipped in exactly this state and never ran."
    )


def public_functions() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for directory in WATCHED:
        for path in sorted(directory.glob("*.py")):
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    found[node.name] = path
    return found


def called_outside(name: str, defined_in: Path) -> bool:
    for path in SRC.rglob("*.py"):
        if path == defined_in:
            continue
        if f"{name}(" in path.read_text():
            return True
    return False


def test_every_public_coordination_function_is_called_or_explained():
    dead = {
        name: path.name
        for name, path in public_functions().items()
        if not called_outside(name, path) and name not in UNCALLED_ON_PURPOSE
    }
    assert not dead, (
        "public coordination functions with no production caller and no "
        f"recorded reason: {dead}. Either wire it up, or add it to "
        "UNCALLED_ON_PURPOSE with why — nine of these shipped complete and "
        "inert before this test existed."
    )


def test_the_exemption_list_does_not_outlive_its_entries():
    """An exemption for something that IS called is stale, and a stale list
    is how the next dead function hides — it gets added beside entries
    nobody re-reads."""
    defined = public_functions()
    stale = [
        name
        for name in UNCALLED_ON_PURPOSE
        if name in defined and called_outside(name, defined[name])
    ]
    assert not stale, f"these are called now; drop their exemptions: {stale}"


def test_every_exemption_names_a_function_that_exists():
    defined = public_functions()
    missing = [name for name in UNCALLED_ON_PURPOSE if name not in defined]
    assert not missing, f"exemptions for functions that no longer exist: {missing}"


def test_every_exemption_gives_a_real_reason():
    thin = [
        name
        for name, reason in UNCALLED_ON_PURPOSE.items()
        if len(reason) < 40 or "not yet" in reason.lower()
    ]
    assert not thin, (
        f'these exemptions do not say why: {thin} — "not yet" is a plan, not a reason'
    )
