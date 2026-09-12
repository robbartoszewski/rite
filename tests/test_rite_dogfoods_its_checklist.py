"""rite ships a review checklist and, until now, did not have one.

`rite review` printed `No checklist items found.` in this repository, because
`.rite/review-checklist.md` did not exist and `.gitignore` said it could not:
its comment declared that "a `.rite/` here is always residue". So every review
round on rite was worked against `templates/review-checklist.md` read by hand
— which is the thing the templates now forbid, and it meant nobody working on
this project had ever experienced the checklist the way a user does.

That is the built-and-nothing-calls-it defect one level up, and the checklist's
own widened line names it: "this" is every artifact the change adds, not only a
module — a template included.

These tests own the seam. The shipped template and rite's own copy of it cannot
drift, because a checklist this project does not itself work is one nobody has
used the way a user will.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.review.checklist import load_checklist
from rite_ai.review.merge import project_checklist_path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "templates" / "review-checklist.md"
OWN = REPO_ROOT / ".rite" / "review-checklist.md"


def test_rite_has_a_checklist_of_its_own():
    """The command it ships has to return something in the repo that ships
    it. This is the assertion whose absence was the whole defect."""
    assert OWN.is_file(), (
        "rite has no .rite/review-checklist.md — `rite review` prints "
        "'No checklist items found.' in its own repository"
    )
    assert load_checklist(OWN)


def test_it_is_the_path_the_command_actually_reads():
    """Not a lookalike somewhere else: the exact path `merge_checklists`
    resolves, so the test cannot pass against a file the command ignores."""
    assert project_checklist_path(REPO_ROOT) == OWN


def test_it_is_not_gitignored():
    """It was, and that is the mechanical reason this never happened.
    `.gitignore` excluded `.rite/*` with one re-include, and declared in a
    comment that anything under `.rite/` here was residue."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert "!.rite/review-checklist.md" in gitignore


def test_every_shipped_line_is_one_rite_works_itself():
    """The drift guard. Adding a line to the template that rite does not
    apply to itself is how a checklist becomes advice for other people."""
    shipped = {i.text for i in load_checklist(TEMPLATE)}
    own = {i.text for i in load_checklist(OWN)}
    assert shipped, "the template parsed to nothing — test is not testing"
    missing = shipped - own
    assert not missing, f"in the shipped template but not worked here: {missing}"


def test_it_adds_lines_of_its_own_rather_than_only_copying():
    """A verbatim copy would satisfy the drift guard and teach nothing. The
    point of a project-wide checklist is the defects THIS project shipped.

    Deliberately does NOT assert which category those lines sit in. An
    earlier version pinned them to `rite's own`, which meant rite could
    never file one of its own lines under the category it actually belongs
    to — "measured against the installed binary" is a Verification item —
    so the guard was quietly turning six of the categories into a
    provenance bucket."""
    shipped = {i.text for i in load_checklist(TEMPLATE)}
    extra = [i for i in load_checklist(OWN) if i.text not in shipped]
    assert extra, "rite's checklist is a bare copy of the template"


# There was a third guard here, asserting `OWN.read_text().startswith(
# TEMPLATE.read_text())` — the shipped half quoted byte-for-byte. It is gone,
# and its own removal is an instance of the line four above it in the file it
# was guarding: "the check observes the property it claims, not a proxy for
# it — that the thing works, not that its bytes are unchanged."
#
# It failed on a pure reflow that changed no item's text; it failed with a
# bare `assert False` and two truncated 3 KB strings, naming nothing
# actionable; and it had already deformed the artifact, because the template's
# bytes had to be a prefix and so `## rite's own` could not have a blank line
# before it. `test_every_shipped_line_is_one_rite_works_itself` above compares
# the parsed items, which is the property that actually matters.
