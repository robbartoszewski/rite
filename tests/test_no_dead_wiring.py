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

# name -> why nothing calls it. A reason is required; "not yet" is not one.
UNCALLED_ON_PURPOSE = {
    "manager_views": (
        "The Owner's assignment path (P2-3a/P2-4a): read every Manager's "
        "status, choose the least loaded, label the ticket with its name. "
        "Nothing calls it because assigning writes to the shared ticket "
        "backend, and whether an unattended tick may do that is Q9, "
        "unanswered. Wiring it is one call in `_owner_duties`."
    ),
    "choose_manager": "Same path as `manager_views` — blocked on Q9.",
    "assign_to_manager": "Same path as `manager_views` — blocked on Q9.",
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
    "Assigned": "Result type of the Owner assignment path — blocked on Q9.",
    "NotAssigned": "Result type of the Owner assignment path — blocked on Q9.",
}


def behavioural_classes() -> dict[str, Path]:
    """Public classes that DO something, as opposed to carrying a result."""
    found: dict[str, Path] = {}
    for path in sorted(COORDINATION.glob("*.py")):
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
    for path in sorted(COORDINATION.glob("*.py")):
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
        f"these exemptions do not say why: {thin} — "
        '"not yet" is a plan, not a reason'
    )
