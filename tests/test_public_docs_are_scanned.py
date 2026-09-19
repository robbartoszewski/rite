"""The gate is what makes a public `docs/` safe, so pin that it reaches it.

`.docs/` was gitignored wholesale and the IGNORE was the protection: nothing
in it could leak because none of it shipped. Moving design documents into a
tracked `docs/` trades that guarantee for a different one — the publish gate
scanning what ships — and a guarantee nobody asserts is one that can be
removed by accident.

Coverage is by construction today: `list_tracked_files` runs `git ls-files`,
so every tracked path is scanned and `docs/` needs no special case. That is
the right design and it is exactly why it deserves a test — a scan that
silently stopped covering a directory looks identical to a directory with
nothing wrong in it, which is this project's whole recurring defect.

Verified against a REAL repo and a REAL scan rather than by reading the
selection code, because "the scanner would have found it" is the claim under
test.
"""

from __future__ import annotations

from pathlib import Path

from gate_helpers import commit_all, init_repo, write
from rite_ai.gate.builtin_rules import BUILTIN_PATH_PATTERNS
from rite_ai.gate.pattern_scan import list_tracked_files, scan_content

REPO_ROOT = Path(__file__).resolve().parents[1]
# Assembled rather than written out, because the gate scans THIS file too and
# a literal home path here is a finding about the test instead of about the
# thing under test. Suppressing it would work and would be worse: the
# suppression list should hold real judgements, not fixtures that could just
# as easily not fire. A test for a scanner must not itself be live ammunition.
HOME_PATH = "/" + "Users/somebody/.rite/credentials.json"


def _findings(root: Path):
    files = list_tracked_files(root)
    assert not isinstance(files, str), files
    assert isinstance(files, list)
    return files, scan_content(root, files, BUILTIN_PATH_PATTERNS, source="builtin")


def test_a_home_path_in_public_docs_is_found(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "docs/design-note.md", f"The token sits at {HOME_PATH}\n")
    commit_all(tmp_path, "a design note")

    files, findings = _findings(tmp_path)
    assert "docs/design-note.md" in files, "the scan never looked at docs/"
    assert [f for f in findings if f.file == "docs/design-note.md"], (
        "a hardcoded home path under docs/ was not flagged — the gate is the "
        "only thing standing between a public docs directory and whatever "
        "somebody saved there"
    )


def test_a_private_note_is_out_of_scope_because_it_does_not_ship(tmp_path: Path):
    """`docs/private/` is protected by never being tracked, not by scanning.

    Asserted so the two protections are not confused: a finding in private
    would be noise, and the ABSENCE of a finding there is not evidence the
    file is clean. It is evidence the file is not published.
    """
    init_repo(tmp_path)
    write(tmp_path, ".gitignore", "docs/private/\n")
    write(tmp_path, "docs/private/bentora.md", f"worker PAT at {HOME_PATH}\n")
    commit_all(tmp_path, "ignore the private half")

    files, findings = _findings(tmp_path)
    assert "docs/private/bentora.md" not in files
    assert not findings, "an ignored file reached the scanner"


def test_force_adding_into_private_is_caught(tmp_path: Path):
    """The mistake that created this situation, so it cannot recur silently.

    Five notes were force-added into an ignored `.docs/`, which is why git
    stopped reporting their drift. If somebody force-adds into
    `docs/private/`, the file starts shipping — and the gate must be what
    notices, because `git status` will not.
    """
    import subprocess

    init_repo(tmp_path)
    write(tmp_path, ".gitignore", "docs/private/\n")
    write(tmp_path, "docs/private/bentora.md", f"worker PAT at {HOME_PATH}\n")
    subprocess.run(
        ["git", "add", "-f", "docs/private/bentora.md"], cwd=tmp_path, check=True
    )
    commit_all(tmp_path, "force-added, the way .docs/ was")

    files, findings = _findings(tmp_path)
    assert "docs/private/bentora.md" in files, "force-add did not track the file"
    assert [f for f in findings if f.file == "docs/private/bentora.md"], (
        "a force-added private note is published and unscanned — this is the "
        "exact shape that left five stale notes invisible in .docs/"
    )


def test_this_repositorys_own_docs_are_in_scope():
    """Against rite itself, not a fixture. A temp repo proves the scanner
    works; this proves it is pointed at the directory this change makes
    public."""
    files = list_tracked_files(REPO_ROOT)
    assert isinstance(files, list), files
    docs = [f for f in files if f.startswith("docs/")]
    assert docs, "no docs/ files are tracked — the scan cannot be covering them"
    public = [f for f in docs if not f.startswith("docs/private/")]
    assert public, "docs/ holds nothing public, so this check proves nothing"
