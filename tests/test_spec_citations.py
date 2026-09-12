"""Two gates retiring two checklist lines.

A checklist line that keeps firing is supposed to become a gate — the shipped
template says so in its own opening paragraph. These are the two from rite's
own section that turned out to be mechanisable, and both of them fired during
the change that added them:

  - "A quotation from SPEC.md is the text that is in SPEC.md." A fabricated
    `SPEC §7` clause lived in `merge.py`'s docstring, reached two review
    templates and a test docstring, and survived the first retraction in one
    of the two files that carried it. The quotations themselves are not
    mechanisable, but the SECTION NUMBERS are, and a citation to a section
    that does not exist is the cheap half of the same defect.
  - The revision history's own version header. §14 gained a "Changes in
    0.18.2" entry while the header at the top still said 0.18.1 — a document
    whose stated purpose is "judging how much to trust a section" mis-stating
    its own version.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "SPEC.md"

# Where a citation can be written and be read by somebody.
#
# `tests/` is in this list because leaving it out was the same defect the gate
# exists to catch: the fabricated `SPEC §7` clause outlived its first
# retraction precisely by sitting in `tests/test_review_merge.py`, and the
# first version of this gate could not see that file. A gate blind to the
# place the defect was last found is not a gate.
#
# `.docs/` is deliberately NOT here: those documents number their own sections
# and cite them with the same `§`, so scanning them would compare one
# document's headings against another's. If they ever cite SPEC, they need
# their own rule rather than this one.
_CITING = [
    *(REPO_ROOT / "src").rglob("*.py"),
    *(REPO_ROOT / "tests").rglob("*.py"),
    *(REPO_ROOT / "tools").rglob("*.py"),
    *(REPO_ROOT / "templates").rglob("*.md"),
    *(REPO_ROOT / "templates").rglob("*.yml"),
    *(REPO_ROOT / ".github" / "workflows").glob("*.yml"),
    REPO_ROOT / "README.md",
    REPO_ROOT / ".rite" / "review-checklist.md",
]

_CITATION = re.compile(r"§\s*(\d+(?:\.\d+)*)")


def _sections_that_exist() -> set[str]:
    """Every numbered heading in SPEC, and their parents.

    NOT decision-register ids: `D-31` is not matched by `_CITATION` and is not
    collected here, so a fabricated `D-99` passes this gate. Said plainly
    rather than left as an implication — an earlier version of this docstring
    claimed "plus every decision-register id", which the code never did, in
    the file whose subject is claims that do not match the thing they
    describe.
    """
    text = SPEC.read_text()
    out = {m.group(1) for m in re.finditer(r"(?m)^#{2,6}\s+(\d+(?:\.\d+)*)\.", text)}
    # A citation to §5.3.3 is legitimate when §5.3.3 exists; so is one to
    # §5.3 when only §5.3.1 does not — parents of real sections count.
    for section in list(out):
        parts = section.split(".")
        for i in range(1, len(parts)):
            out.add(".".join(parts[:i]))
    return out


def test_every_spec_section_cited_anywhere_actually_exists():
    """The mechanisable half of "a quotation from SPEC is the text in SPEC".

    It cannot check that a quoted sentence is really there — but a citation
    naming a section that does not exist is the same defect, costs nothing
    to catch, and is how a fabricated citation usually reads.
    """
    existing = _sections_that_exist()
    assert existing, "no numbered sections parsed out of SPEC.md — test is broken"

    dangling: dict[str, set[str]] = {}
    for path in _CITING:
        if not path.is_file():
            continue
        for match in _CITATION.finditer(path.read_text()):
            if match.group(1) not in existing:
                rel = str(path.relative_to(REPO_ROOT))
                dangling.setdefault(rel, set()).add(f"§{match.group(1)}")

    assert not dangling, f"citations to SPEC sections that do not exist: {dangling}"


def test_specs_version_header_matches_its_newest_revision_entry():
    """§14's whole stated purpose is "judging how much to trust a section".
    A 0.18.2 entry under a 0.18.1 header fails at that on its own terms."""
    text = SPEC.read_text()

    header = re.search(r"(?m)^\*\*Version:\*\*\s*(\S+)", text)
    assert header, "SPEC.md has no **Version:** header — test is stale"

    entries = re.findall(r"(?m)^\*\*Changes in (\d+\.\d+\.\d+)", text)
    assert entries, "no revision-history entries parsed — test is stale"

    assert header.group(1) == entries[0], (
        f"SPEC header says {header.group(1)} but the newest revision entry is "
        f"{entries[0]} — bump one or the other"
    )
