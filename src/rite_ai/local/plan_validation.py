"""Plan validation — rite validates the SHAPE, a reviewer judges the SLICING
(DD-3, RL-63..RL-67).

`decomposition.problems()` is the pure shape check a plan-review holder is told
to assume. It cannot reach the three things a bad candidate most needs caught
before approval, because each needs context `problems()` does not take: the
spec on disk (does a cite resolve?), the repo root (is a scope path inside it?),
and the project's Managers (did a real DECOMPOSE holder author this?). This
layer adds exactly those, as refusals, plus one advisory warning (RL-64).

**It moves existing refusals EARLIER, it does not invent safety.** A cite that
does not resolve is already refused by `local/step.py:_slice_for` — but per
subtask, at execution, after review and after the APPROVED gate released the
plan. Here it is whole-plan and pre-approval. Saying it the other way round
would oversell it (DD-2.3).

Nothing here decides whether a decomposition is *good*: the slicing is the
reviewer's judgement (RL-6), the coverage is the parent's own verify (RL-8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from rite_ai.local import decomposition as dec

# RL-66. One subtask is not a decomposition; two hundred is a model that has
# lost the plot. Small and chosen to be argued with rather than defended (DD-3.2).
DEFAULT_MAX_SUBTASKS = 8


@dataclass(frozen=True)
class PlanProblems:
    """Refusals stop the plan being written at all (RL-68); warnings travel to
    plan review as advice (RL-64) and never block."""

    refusals: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.refusals


def candidate_problems(
    plan: dec.Decomposition,
    *,
    root: Path,
    decompose_managers: tuple[str, ...] = (),
    max_subtasks: int = DEFAULT_MAX_SUBTASKS,
    ticket_text: str = "",
) -> PlanProblems:
    """Every reason this candidate must not be written as a plan, and the
    advisory warnings a reviewer should see (DD-3.1/DD-3.2).

    `decompose_managers` is the set of Managers that actually hold the decompose
    duty; empty disables the author check (RL-67) rather than guessing.
    """
    refusals: list[str] = list(dec.problems(plan))  # the pure shape checks (RL-T4)
    warnings: list[str] = []

    # DD-3.5 / DD-4.2 — a decomposer may NEVER approve its own plan. The APPROVED
    # gate (RL-6) is worthless if the thing it gates can set it.
    if plan.approval == dec.APPROVED:
        refusals.append(
            "the candidate is already marked approved — a decomposer writes a "
            "PENDING plan and a plan-review holder approves it (RL-6, DD-3.5)"
        )

    # RL-66 — between 2 and N subtasks. `problems()` already refused zero.
    count = len(plan.subtasks)
    if count == 1:
        refusals.append(
            "the plan has one subtask, so the ticket did not need decomposing — "
            "run it as a single subtask, not a decomposition (RL-66)"
        )
    elif count > max_subtasks:
        refusals.append(
            f"the plan has {count} subtasks, above the {max_subtasks} a plan may "
            f"hold (RL-66) — a ticket split this far has usually lost the plot"
        )

    # RL-65 — scope paths are repo-relative and resolve inside the repo. This is
    # the one gap here that is a safety property, not a quality one: `scope`
    # becomes `GitCommitter`'s allowlist (DD-2.3), so it fails closed.
    for sub in plan.subtasks:
        for path in sub.scope:
            why = _unsafe_scope(path)
            if why:
                refusals.append(f"{sub.id}: scope path {path!r} {why} (RL-65)")

    # RL-63 — every cite resolves at plan time. The slice IS the model's context
    # instead of the spec (DD-2.3), so this couples a valid plan to `rite spec
    # index` having run — the correct answer, said in the refusal.
    for sub in plan.subtasks:
        problem = _cites_problem(root, sub)
        if problem:
            refusals.append(problem)

    # RL-67 (the validation half) — decomposed_by names a Manager that exists
    # and holds decompose. An unnamed author defeats RL-6's independence lookup,
    # which falls through to "reviewable by anyone", including the engine that
    # wrote it. The behavioural fix to `_independent()` is a separate PR (DD-7).
    if decompose_managers:
        if not plan.decomposed_by:
            refusals.append(
                "the plan names no author in decomposed_by — RL-6's independence "
                "rule cannot place a reviewer against an author it cannot find, "
                "and an unnamed author is reviewable by anyone (RL-67)"
            )
        elif plan.decomposed_by not in decompose_managers:
            refusals.append(
                f"the plan's author {plan.decomposed_by!r} is not a Manager that "
                f"holds decompose ({', '.join(sorted(decompose_managers))}) — "
                "RL-6's independence check needs a real author (RL-67)"
            )

    # RL-64 (ADVISORY, DD-3.2) — a plan whose intents share no word with the
    # ticket MAY cover something else. Keyword overlap is a poor proxy and will
    # false-refuse a correctly-sliced plan that uses different words, so it is a
    # warning to plan review, not a refusal; RL-8's parent verify stays the gate.
    if ticket_text.strip() and not _covers_any_word(plan, ticket_text):
        warnings.append(
            "no subtask intent shares a word with the ticket text — this may be "
            "a plan that covers something other than the ticket, or just one "
            "that uses different words; the parent's own verify (RL-8) is the "
            "real coverage gate (RL-64, advisory)"
        )

    return PlanProblems(tuple(refusals), tuple(warnings))


def _unsafe_scope(path: str) -> str:
    """Why this scope path is not a safe repo-relative path, or "" (RL-65)."""
    if not path.strip():
        return "is empty"
    # A Windows drive (`C:\...`) or a POSIX absolute path both reach the
    # committer as an allowlist entry outside the repo.
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return "is absolute — scope is repo-relative"
    resolved: list[str] = []
    for part in PurePosixPath(path).parts:
        if part == "..":
            if not resolved:
                return "escapes the repository root with '..'"
            resolved.pop()
        elif part not in (".", ""):
            resolved.append(part)
    if not resolved:
        return "does not name a path inside the repository"
    return ""


def _cites_problem(root: Path, sub: dec.Subtask) -> str:
    """Why this subtask's cites do not resolve at plan time, or "" (RL-63).

    The same question `local/step.py:_slice_for` asks, asked earlier: is the
    derived unit text on disk? A subtask that cites nothing is refused for the
    same reason `_slice_for` refuses it — an empty slice is the free-form run
    this path replaces.
    """
    from rite_ai.spec.digest_files import unit_filename, units_dir

    if not sub.cites:
        return (
            f"{sub.id}: cites no spec unit — the slice IS the context the model "
            "gets instead of the spec, so a subtask with no cites is the "
            "free-form run this path replaces (RL-63)"
        )
    where = units_dir(Path(root))
    missing = [c for c in sub.cites if not (where / unit_filename(c)).exists()]
    if missing:
        return (
            f"{sub.id}: the derived spec text for {', '.join(missing)} is not in "
            f"{where} — run `rite spec index` so the cite resolves, because a "
            "cite rite cannot resolve stalls the subtask after approval (RL-63)"
        )
    return ""


def _covers_any_word(plan: dec.Decomposition, ticket_text: str) -> bool:
    """Whether any subtask intent shares a non-trivial word with the ticket.

    Words of four or more characters only — short tokens (`a`, `the`, `id`)
    overlap by accident and would make the proxy meaningless.
    """
    words = set(re.findall(r"[a-z0-9]{4,}", ticket_text.lower()))
    if not words:
        return True  # nothing to measure against is not a failure to cover
    intents = " ".join(s.intent for s in plan.subtasks).lower()
    return any(word in intents for word in words)
