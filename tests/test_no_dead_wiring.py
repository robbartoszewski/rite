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
    "compose": (
        "Called by `write_profile` in the same file. Public because it IS "
        "the boundary — the text of the profile — and the tests assert "
        "against it directly: deny-default, the network grant, the pointer "
        "to what it does not buy. Inlining it would move those assertions "
        "onto bytes on disk, and this is the one function in rite whose "
        "output the kernel enforces."
    ),
    "profile_path": (
        "Called by `write_profile` and `why_it_was_refused` in the same "
        "file. Public because it is the one place rite decides WHERE a "
        "Manager's profile lives — under `user_dir`, because what a Manager "
        "may reach on this machine is a local fact and the absolute paths "
        "inside make it meaningless anywhere else — and the refusal message "
        "tells a user that path by name."
    ),
    "requests_dir": (
        "Called by `instructions` and `take_requests` in the same file. "
        "Public because it is the one place rite decides WHERE a Manager "
        "writes a Worker request — a directory of its own rather than the "
        "mailbox outbox a human reads — and a test pins that by name."
    ),
    "launch_argv": (
        "Called by `honour` in the same file. Public because the exact argv "
        "IS the security property: two validated values, a list rather than "
        "a string, never through a shell. A test asserts the whole list, "
        "which a private helper would put out of reach."
    ),
    "honour": (
        "Called by `for_project`'s returned handler. Public because it is "
        "the one place a validated request becomes a real launch, and the "
        "boundary between deciding and doing is worth being able to name."
    ),
    "project_capacity": (
        "Called by `for_project`'s returned handler. Public because -1 "
        "meaning `unknown` is a decision — an unreadable limit becomes a "
        "refusal, not `no limit` — and the tests pin that case by name."
    ),
    "settings_document": (
        "Called by `write_settings` in the same file. Public because it is "
        "the one place the engine's settings SHAPE is written down, and the "
        "tests assert against it by name — that the document carries "
        "permissions and nothing else, since `--settings` merges over the "
        "user's own file and every key rite writes is one it takes from "
        "them. Inlining it would move that assertion onto the bytes on disk."
    ),
    "project_transcript_dir": (
        "Called by `latest_session_id` in the same file. Public because it "
        "is the one place a provider's private directory layout is encoded, "
        "and a test asserts the naming rule against it — a private helper "
        "would move that assertion into a test of something else."
    ),
    "token_is_absent": (
        "Called by `start` in the same file. Public because it is the one "
        "place rite decides whether an unattended run has a credential, "
        "and the tests pin both the absent and the blank-string cases by "
        "name — a private helper would move those assertions onto the "
        "refusal's wording instead of onto the rule."
    ),
    "attachment": (
        "Called by `ending` and by `was_attached` in the same file. Public "
        "because it is the three-valued answer — attached / not attached / "
        "could not tell — that `ending` branches on, and the tests pin the "
        "`known=False` cases by name: `was_attached` returns a bare bool "
        "and cannot express the third, which is precisely the conflation "
        "that made a failed probe read as FINISHED and resume a session a "
        "human had quit. A private `_attachment` would put that distinction "
        "back out of reach of the tests that exist to hold it."
    ),
    "launch_command": (
        "Called by `_default_starter` in the same file. Public because it is "
        "the one place the engine string becomes a command line, and SPEC "
        "§9.14.11 points at it by name as the thing that is NOT an adapter — "
        "a private `_launch_command` would make the limit harder to cite than "
        "to keep."
    ),
    "journal_dir": (
        "Called by `start_notice`, `write_observation` and "
        "`write_retrospective` in the same file, and by nothing outside it "
        "BY DESIGN — that is asserted as a property in "
        "`test_manager_journal.py`, not merely left true. §9.15.5 requires "
        "the journal be inert, and a module that can locate the directory "
        "is one line from reading it, so every caller goes through the "
        "writing API instead. Public because the path layout is §9.15.3's "
        "and the tests pin it by name; a private `_journal_dir` would move "
        "that assertion onto a literal string, which is the same reason "
        "`user_dir` is here."
    ),
    # --- rite_ai/managers/, added when this guard was widened ---
    "instance_path": (
        "Same: called by `record_instance`, `read_instance` and "
        "`forget_instance` in the same file."
    ),
    "pane_pid": (
        "Called by `start` in the same file. It exists as a named function "
        "because what it returns — tmux's pane pid rather than the CLI's own "
        "— is the whole point, and a comment would not have carried that."
    ),
    # `running_instances` was here — "for `rite status` to list running
    # Managers, which is not built". It is built now, and THIS TEST IS HOW
    # THAT WAS NOTICED: the push that wired it was refused with "these are
    # called now; drop their exemptions". An exemption that outlives its
    # reason is a standing permission to leave something unwired, so the
    # guard checks the list shrinks as well as that nothing escapes it.
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


# --- the guard's own floor ----------------------------------------------------------
#
# ⚠ EVERY CHECK IN THIS FILE ITERATES A COMPUTED SET. `public_functions`
# and `behavioural_classes` both walk `WATCHED` and glob `*.py`; `dead` is
# then whatever survives filtering. `Path.glob` on a directory that does
# not exist returns NOTHING and raises NOTHING — so renaming or moving
# `coordination/` or `managers/` makes the set empty, `dead` empty, and
# every test here pass while checking no functions at all.
#
# That is not hypothetical rot: this guard exists because nine functions
# shipped dead at once, and it was WIDENED on 2026-09-20 by adding a second
# directory to `WATCHED` — the exact edit that can silently misspell a path.
#
# A vacuous pass and a real pass are byte-identical in pytest's output, so
# the floor has to be asserted explicitly.


def test_the_watched_directories_actually_exist():
    """A renamed package must go RED here, not quietly empty the guard."""
    missing = [str(d) for d in WATCHED if not d.is_dir()]
    assert not missing, (
        f"WATCHED names directories that do not exist: {missing}. Every "
        "check in this file would pass over an empty set"
    )
    barren = [str(d) for d in WATCHED if not list(d.glob("*.py"))]
    assert not barren, (
        f"WATCHED names directories with no Python files: {barren} — the "
        "guard would report no dead wiring because it examined nothing"
    )


def test_the_guard_examines_a_plausible_number_of_subjects():
    """The floor. Deliberately loose: this asserts the machinery RAN, not
    how much code exists, so ordinary growth and deletion never touch it."""
    functions = public_functions()
    classes = behavioural_classes()
    assert len(functions) >= 10, (
        f"only {len(functions)} public functions discovered across "
        f"{len(WATCHED)} directories. Either the AST walk is broken or "
        "WATCHED no longer points at the code — either way every other "
        "test in this file is now vacuous"
    )
    assert len(classes) >= 1, (
        f"no behavioural classes discovered across {len(WATCHED)} "
        "directories; the dataclass filter or the walk is broken"
    )


def test_every_exemption_names_something_that_still_exists():
    """An exemption for a deleted symbol is the other way this list rots.

    `UNCALLED_ON_PURPOSE` only ever suppresses; an entry naming a symbol
    that no longer exists suppresses nothing and reads as documentation of
    a decision that is no longer in force.
    """
    known = set(public_functions()) | set(behavioural_classes())
    stale = sorted(name for name in UNCALLED_ON_PURPOSE if name not in known)
    assert not stale, (
        f"these exemptions name symbols that no longer exist: {stale} — "
        "remove them, so the list says what is actually exempt today"
    )
