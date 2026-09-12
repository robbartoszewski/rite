"""Merge project-wide and per-repo review checklists.

SPEC §7.2 gives the two locations, and this is the whole of what it says
about how they relate:

    - **One project-wide checklist** at
      `<project-root>/.rite/review-checklist.md`.
    - **One per package/repo** at `<repo>/.rite/review-checklist.md`.

**It does not say how they combine.** This docstring used to run a third
clause — "Merge: repo checklist appends to project checklist" — on into
the same quotation marks as those two, and that clause is not in SPEC. It
was the load-bearing half, too: it is the one that decides the order, and
it was the only authority cited for it.

A fabricated citation is worse than a missing one: it is unfalsifiable
unless someone goes and looks, and it spreads. Both happened here. It went
unchallenged long enough to be copied into two review templates during the
change that removed it — caught in review before those shipped — and when
this docstring was finally rewritten, the same invented clause was left
standing in `tests/test_review_merge.py`, still framed as a quotation.
Retracting a citation in one of the places that carries it is not
retracting it.

So the order is stated here as this module's decision, with its reasoning,
and attributed to nobody else: project-wide items are the baseline every
repo inherits, and a repo's own additions come after, so a reviewer reads
general expectations first and repo-specific ones as an addendum, never
the reverse. If that ever needs to be binding rather than merely sensible,
it goes in SPEC first and gets quoted back here afterwards.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.review.checklist import ChecklistItem, load_checklist

CHECKLIST_REL_PATH = ".rite/review-checklist.md"


def project_checklist_path(project_root: Path) -> Path:
    return project_root / CHECKLIST_REL_PATH


def repo_checklist_path(project_root: Path, module_path: str) -> Path:
    return project_root / module_path / CHECKLIST_REL_PATH


def merge_checklists(
    project_root: Path, module_path: str | None = None
) -> list[ChecklistItem]:
    """Load the project-wide checklist, then append the given module's own
    checklist if it has one. `load_checklist` already returns `[]` for a
    missing file, so a project or repo with no checklist yet contributes
    nothing rather than erroring — a checklist is additive infrastructure,
    never a hard requirement to have authored one."""
    items = load_checklist(project_checklist_path(project_root))
    if module_path:
        items = items + load_checklist(repo_checklist_path(project_root, module_path))
    return items
