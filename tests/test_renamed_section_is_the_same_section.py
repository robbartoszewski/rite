"""A section a release renamed must not end up in the file twice.

`refresh_text` matches a generated section to the one in your file by its
HEADING. Rename a heading and the match fails: the refresh inserts the new
section and leaves the old one where it was, so the file comes back carrying
both.

Measured end to end. A Worker `CLAUDE.md` generated before `## Your modules`
became `## Modules checked out in your workspace`, then `rite update
--files-only`:

    10:## Your modules                            <- stale list, old framing
    15:## Modules checked out in your workspace   <- the new one

which is worse than not refreshing at all — the superseded heading is the
possessive framing the rename existed to remove, and it now sits directly
above the paragraph explaining that Workers do not own modules.

Whether the old section can be REPLACED depends on the usual evidence: marked
and unedited, it is rite's own and is rewritten under the new heading; with no
marker and nothing in `section_history` to match, nothing can tell it from an
edit, so it is kept and reported. Either way there is one module section.
"""

from __future__ import annotations

import pytest

from rite_ai.generated_sections import mark_sections
from rite_ai.update.refresh import SUPERSEDED_HEADINGS, refresh_text
from rite_ai.workspace.manage import (
    WORKER_MODULES_HEADING,
    WORKER_MODULES_HEADING_LEGACY,
)

NEW = WORKER_MODULES_HEADING.removeprefix("## ")
OLD = WORKER_MODULES_HEADING_LEGACY.removeprefix("## ")


def _file(heading: str, body: str) -> str:
    return (
        f"# CLAUDE.md — Worker A\n\n## Your role\n\nYou are a Worker.\n\n"
        f"## {heading}\n\n{body}\n\n## Your ticket\n\nRead it.\n"
    )


def _generated() -> str:
    return mark_sections(
        _file(NEW, "- `backend/`\n\nA Worker is a workspace, not a module.")
    )


def _headings(text: str) -> list[str]:
    return [ln[3:] for ln in text.splitlines() if ln.startswith("## ")]


class TestARenamedSectionIsNotDuplicated:
    @pytest.mark.parametrize("marked", [True, False], ids=["marked", "pre-markers"])
    def test_one_module_section_however_the_old_one_was_written(self, marked):
        """THE REGRESSION. Neither branch may leave the file with two."""
        old = _file(OLD, "- `backend/`\n- `ios-app/`")
        current = mark_sections(old) if marked else old

        new, _ = refresh_text(current, _generated())

        modules = [h for h in _headings(new) if h in (NEW, OLD)]
        assert len(modules) == 1, f"got {modules}:\n\n{new}"

    def test_a_marked_section_is_rewritten_under_the_new_heading(self):
        old = mark_sections(_file(OLD, "- `backend/`\n- `ios-app/`"))

        new, changes = refresh_text(old, _generated())

        assert OLD not in _headings(new)
        assert NEW in _headings(new)
        assert "A Worker is a workspace" in new
        assert {c.target: c.action for c in changes}[NEW] == "refreshed"

    def test_it_is_never_reported_as_inserted(self):
        """"inserted" is the bug's own report: it says a section was ADDED,
        which is the one thing a rename must not do."""
        for current in (
            _file(OLD, "- `backend/`"),
            mark_sections(_file(OLD, "- `backend/`")),
        ):
            _, ch = refresh_text(current, _generated())
            changes = {c.target: c.action for c in ch}
            assert changes.get(NEW) != "inserted", changes

    def test_an_unprovable_section_is_kept_with_its_words(self):
        """The property the mechanism exists for. No marker and no release
        wrote these bytes, so this may be the user's writing — a rename is not
        a licence to overwrite it."""
        mine = _file(OLD, "- `backend/`  # MY NOTE")

        new, changes = refresh_text(mine, _generated())

        assert "MY NOTE" in new
        assert {c.target: c.action for c in changes}[NEW].startswith("kept")

    def test_take_rite_replaces_it_under_the_new_heading(self):
        """The escape hatch has to reach the section under its old name too,
        or a kept rename can never be accepted."""
        mine = _file(OLD, "- `backend/`  # MY NOTE")

        new, changes = refresh_text(mine, _generated(), take=frozenset({NEW}))

        assert "MY NOTE" not in new
        assert OLD not in _headings(new)
        assert {c.target: c.action for c in changes}[NEW] == "taken"


class TestTheMapCannotSilentlyGoStale:
    def test_the_worker_rename_is_recorded(self):
        """A future rename that forgets the map reintroduces the duplicate.
        Deriving the entry from the constants themselves means the rename and
        the map move together, or this fails."""
        assert OLD in SUPERSEDED_HEADINGS[NEW]


class TestTheReportNamesTheOldHeading:
    """`§ Modules checked out in your workspace: left as it is` sends the
    reader to a heading their file does not contain — they renamed nothing,
    rite did."""

    def test_a_kept_rename_says_what_the_file_calls_it(self):
        from rite_ai.update.refresh import FileResult, report

        _, changes = refresh_text(_file(OLD, "- `backend/`  # MY NOTE"), _generated())
        line = "\n".join(
            report([FileResult("workers/A/CLAUDE.md", changes)], False, frozenset())
        )

        assert f'"## {OLD}"' in line, line

    def test_an_unrenamed_section_says_nothing_extra(self):
        from rite_ai.update.refresh import FileResult, report

        _, changes = refresh_text(_file(NEW, "- `nope/`"), _generated())
        line = "\n".join(
            report([FileResult("workers/A/CLAUDE.md", changes)], False, frozenset())
        )

        assert "renamed" not in line, line
