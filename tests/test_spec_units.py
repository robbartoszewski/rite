"""The spec unit parser (S-1).

Checked first against the shape `/spec` writes, because that is what every new
project's spec will look like: six fixed headings, none numbered, and a
mandatory decision register. rite's own SPEC.md is the other shape — numbered
headings, a handful of unnumbered ones, a long register — and it is checked too,
but it is unusually well structured and cannot be the only input.
"""

from pathlib import Path

from rite_ai.spec.units import (
    DECISION,
    PREAMBLE_ID,
    SECTION,
    expand_paths,
    parse_paths,
    parse_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Built from the `/spec` skeleton in templates/commands/spec.md, filled in the way
# a session is told to fill it: every heading kept, one register row per decision.
# Synthetic — no real `/spec` output was available to copy — so if the skeleton
# changes, this fixture has to follow it.
SPEC_SESSION_OUTPUT = """\
# news-aggregator

## Problem
Readers follow a dozen outlets and see the same story many times.

## Scope
An iOS app that groups coverage of one story from several outlets.

### Non-goals
No comments, no accounts, no publishing.

## Architecture
A SwiftUI app using The Composable Architecture, and a Node backend that fetches
and clusters feeds. See D-2 and D-4.

## Decisions

Tickets cite these as D-<number> instead of restating them. A number is
never reused or renumbered; a reversed decision gets a new row that says
which one it replaces.

| D | Decision | Choice | Why |
|---|----------|--------|-----|
| D-1 | Scope of the first version | Read-only story clusters | Smallest useful thing |
| D-2 | App architecture | **The Composable Architecture** | Testable reducers |
| D-3 | Feed source | RSS only | Every outlet has one |
| D-4 | Clustering | Server-side, nightly | Keeps the app thin |
| D-5 | Storage | SQLite on the backend | One process, no ops |
| D-6 | Distribution | TestFlight | No App Store review yet |

## Open questions
- Which outlets are in the first set? (the user)
"""


def _by_id(parsed):
    return {u.id: u for u in parsed.units}


# --- the shape /spec writes -----------------------------------------------------


def test_a_spec_session_document_parses_into_slugs_and_the_register():
    parsed = parse_text(SPEC_SESSION_OUTPUT, "SPEC.md")

    assert [u.id for u in parsed.units if u.kind == SECTION] == [
        "news-aggregator",
        "problem",
        "scope",
        "scope/non-goals",
        "architecture",
        "decisions",
        "open-questions",
    ]
    assert [u.id for u in parsed.units if u.kind == DECISION] == [
        f"D-{n}" for n in range(1, 7)
    ]
    assert parsed.problems == []


def test_the_document_title_does_not_qualify_the_sections_under_it():
    """Renaming the project must not rename every section."""
    units = _by_id(parse_text(SPEC_SESSION_OUTPUT, "SPEC.md"))
    assert units["problem"].parent == "news-aggregator"
    renamed = SPEC_SESSION_OUTPUT.replace("# news-aggregator", "# headlines")
    assert "problem" in _by_id(parse_text(renamed, "SPEC.md"))


def test_register_rows_are_units_inside_the_decisions_section():
    units = _by_id(parse_text(SPEC_SESSION_OUTPUT, "SPEC.md"))
    d2 = units["D-2"]
    assert d2.kind == DECISION
    assert d2.parent == "decisions"
    assert d2.title == "App architecture"
    assert d2.start == d2.end
    assert units["decisions"].start < d2.start <= units["decisions"].end
    # The header and separator rows are not decisions.
    assert "D" not in units


# --- numbered headings, the shape of rite's own spec ----------------------------


def test_numbered_and_unnumbered_headings_side_by_side():
    text = """\
# rite
## 2. Roles
### 2.4. Owner handover
#### 2.4.1. Lease, not heartbeat
#### Promotion
#### Graceful demotion
## 13. Decisions register
| D-1 | Transport | REST | Simple |
"""
    units = _by_id(parse_text(text, "SPEC.md"))
    assert {
        "2",
        "2.4",
        "2.4.1",
        "2.4/promotion",
        "2.4/graceful-demotion",
        "13",
        "D-1",
    } <= set(units)
    assert units["2.4/promotion"].parent == "2.4"
    assert units["2.4.1"].title == "Lease, not heartbeat"
    assert units["D-1"].parent == "13"


def test_a_number_needs_its_dot_when_it_stands_alone():
    units = _by_id(
        parse_text("## 1. What rite is\n## 2024 roadmap\n## 3.1 Nested\n", "s.md")
    )
    assert {"1", "2024-roadmap", "3.1"} <= set(units)


# --- what is not a unit ---------------------------------------------------------


def test_headings_in_fenced_examples_and_front_matter_are_not_units():
    text = """\
---
title: # not a heading
---
# Spec
## Real

```markdown
# Context Index
## Not a section
```

~~~
## Also not
~~~

   ```markdown
   ## Indented fence, still an example
   ```
## Also real
"""
    ids = [u.id for u in parse_text(text, "s.md").units]
    assert ids == ["spec", "real", "also-real"]


def test_a_document_with_no_headings_is_refused_not_split():
    parsed = parse_text("Just prose.\nMore prose.\n| D-1 | x | y | z |\n", "notes.md")
    sections = [u for u in parsed.units if u.kind == SECTION and u.id != PREAMBLE_ID]
    assert sections == []
    assert any("no headings" in p for p in parsed.problems)
    # The register row is still addressable, without a section to sit in.
    assert _by_id(parsed)["D-1"].parent is None


def test_text_before_the_first_heading_is_a_preamble_unit_not_lost():
    parsed = parse_text("Read this first.\n\n## Scope\nx\n", "s.md")
    pre = _by_id(parsed)[PREAMBLE_ID]
    assert (pre.start, pre.end) == (1, 2)


# --- ranges, hashes, ids --------------------------------------------------------


def test_a_unit_runs_from_its_heading_to_the_line_before_the_next():
    units = _by_id(parse_text(SPEC_SESSION_OUTPUT, "SPEC.md"))
    lines = SPEC_SESSION_OUTPUT.splitlines()
    scope = units["scope"]
    assert lines[scope.start - 1] == "## Scope"
    assert lines[scope.end] == "### Non-goals"
    last = units["open-questions"]
    assert last.end == len(lines)
    assert scope.lines == scope.end - scope.start + 1


def test_reparsing_an_unedited_file_gives_identical_hashes():
    first = parse_text(SPEC_SESSION_OUTPUT, "SPEC.md").units
    again = parse_text(SPEC_SESSION_OUTPUT, "SPEC.md").units
    assert [(u.id, u.sha) for u in first] == [(u.id, u.sha) for u in again]


def test_line_endings_do_not_change_a_hash():
    lf = parse_text(SPEC_SESSION_OUTPUT, "SPEC.md").units
    crlf = parse_text(SPEC_SESSION_OUTPUT.replace("\n", "\r\n"), "SPEC.md").units
    assert [u.sha for u in lf] == [u.sha for u in crlf]


def test_an_edit_changes_only_the_edited_units_hash():
    before = _by_id(parse_text(SPEC_SESSION_OUTPUT, "SPEC.md"))
    edited = SPEC_SESSION_OUTPUT.replace(
        "No comments, no accounts", "No comments, no login"
    )
    after = _by_id(parse_text(edited, "SPEC.md"))
    changed = {uid for uid in before if before[uid].sha != after[uid].sha}
    assert changed == {"scope/non-goals"}


def test_a_duplicate_heading_is_reported_and_still_addressable():
    parsed = parse_text("## Scope\n### Files\n### Files\n", "s.md")
    assert {"scope/files", "scope/files~2"} <= {u.id for u in parsed.units}
    assert any("'scope/files'" in p and "scope/files~2" in p for p in parsed.problems)


def test_a_renamed_heading_reads_as_one_unit_removed_and_one_added():
    before = {u.id for u in parse_text(SPEC_SESSION_OUTPUT, "SPEC.md").units}
    renamed = SPEC_SESSION_OUTPUT.replace("## Open questions", "## Unresolved")
    after = {u.id for u in parse_text(renamed, "SPEC.md").units}
    assert before - after == {"open-questions"}
    assert after - before == {"unresolved"}


# --- registered paths ------------------------------------------------------------


def test_a_registered_directory_is_expanded_to_its_markdown_files(tmp_path: Path):
    docs = tmp_path / "docs"
    (docs / "adr").mkdir(parents=True)
    (docs / ".drafts").mkdir()
    (docs / "b.md").write_text("## Beta\n")
    (docs / "adr" / "a.markdown").write_text("## Alpha\n")
    (docs / "notes.txt").write_text("## Not markdown\n")
    (docs / ".drafts" / "wip.md").write_text("## Hidden\n")
    (tmp_path / "SPEC.txt").write_text("## Registered by name\n")

    files, problems = expand_paths(tmp_path, ["docs/", "SPEC.txt", "gone.md"])

    assert files == ["docs/adr/a.markdown", "docs/b.md", "SPEC.txt"]
    assert problems == ["gone.md: registered, but nothing is there"]


def test_an_id_reused_by_a_later_file_is_qualified_and_the_earlier_one_keeps_its_id(
    tmp_path: Path,
):
    (tmp_path / "SPEC.md").write_text("## Scope\n")
    (tmp_path / "API.md").write_text("## Scope\n")
    parsed = parse_paths(tmp_path, ["SPEC.md", "API.md"])
    assert [u.id for u in parsed.units] == ["scope", "API.md#scope"]
    assert parsed.find("API.md#scope")[0].source == "API.md"
    assert any("API.md#scope" in p for p in parsed.problems)


# --- rite's own spec --------------------------------------------------------------


def test_rites_own_spec_parses_without_collisions():
    parsed = parse_paths(REPO_ROOT, ["SPEC.md"])
    ids = [u.id for u in parsed.units]
    assert len(ids) == len(set(ids))
    assert parsed.problems == []

    sections = [u for u in parsed.units if u.kind == SECTION]
    numbered = [u for u in sections if u.id[0].isdigit() and "/" not in u.id]
    slugged = [u for u in sections if "/" in u.id]
    # Every unnumbered section sits under a numbered one, so each is still
    # anchored to a stable number.
    assert slugged and all(u.id.split("/")[0][0].isdigit() for u in slugged)
    assert len(numbered) >= 118
    assert "13" in ids and "D-1" in ids
    decisions = [u for u in parsed.units if u.kind == DECISION]
    assert len(decisions) >= 51
    assert {d.parent for d in decisions} == {"13"}
