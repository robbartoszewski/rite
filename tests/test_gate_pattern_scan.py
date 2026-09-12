from pathlib import Path

from rite_ai.config.models import ScanPattern
from rite_ai.gate.builtin_rules import BUILTIN_PATH_PATTERNS
from rite_ai.gate.pattern_scan import (
    ScanError,
    list_tracked_files,
    scan_commit_messages,
    scan_content,
    scan_filenames,
    scan_kb_cross_reference,
)
from tests.gate_helpers import commit_all, init_repo, write


def test_list_tracked_files_not_a_repo(tmp_path: Path):
    result = list_tracked_files(tmp_path)
    assert isinstance(result, ScanError)


def test_list_tracked_files_returns_committed_only(tmp_path: Path):
    import subprocess

    init_repo(tmp_path)
    write(tmp_path, "a.py", "print(1)\n")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add a.py only"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "untracked.py").write_text("print(2)\n")
    files = list_tracked_files(tmp_path)
    assert files == ["a.py"]


def test_scan_content_matches_hardcoded_home_path(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "config.py", 'DATA_DIR = "/Users/exampleuser/dev/client-x"\n')
    commit_all(tmp_path, "add config")
    files = list_tracked_files(tmp_path)
    assert isinstance(files, list)
    findings = scan_content(
        tmp_path, files, BUILTIN_PATH_PATTERNS, source="rite-pattern"
    )
    assert len(findings) == 1
    assert findings[0].file == "config.py"
    assert findings[0].line == 1
    assert "exampleuser" not in findings[0].match_preview


def test_scan_content_no_match_on_clean_file(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "config.py", 'DATA_DIR = "./data"\n')
    commit_all(tmp_path, "add config")
    files = list_tracked_files(tmp_path)
    assert isinstance(files, list)
    findings = scan_content(
        tmp_path, files, BUILTIN_PATH_PATTERNS, source="rite-pattern"
    )
    assert findings == []


def test_scan_filenames_matches_declared_pattern():
    patterns = [
        ScanPattern(
            type="path", pattern=r"REF-[A-Z]+-\d+/\d+", description="Record reference"
        )
    ]
    findings = scan_filenames(
        ["records/REF-GC-123/456.pdf"], patterns, source="rite-path"
    )
    assert len(findings) == 1
    assert findings[0].source == "rite-path"


def test_scan_filenames_no_match_when_pattern_absent():
    patterns = [
        ScanPattern(
            type="path", pattern=r"REF-[A-Z]+-\d+/\d+", description="Record reference"
        )
    ]
    findings = scan_filenames(["readme.md"], patterns, source="rite-path")
    assert findings == []


def test_scan_commit_messages_matches_declared_pattern(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    commit_all(tmp_path, "closed the ACME-CORP issue, ref REF-GC-9001/26")
    patterns = [
        ScanPattern(type="regex", pattern="ACME-CORP", description="customer name")
    ]
    result = scan_commit_messages(tmp_path, patterns, source="rite-pattern")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].file == "<commit message>"
    assert result[0].commit is not None


def test_scan_commit_messages_scoped_to_rev_range(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    first = commit_all(tmp_path, "clean first commit")
    write(tmp_path, "a.txt", "hello again\n")
    commit_all(tmp_path, "mentions ACME-CORP here")
    patterns = [
        ScanPattern(type="regex", pattern="ACME-CORP", description="customer name")
    ]

    # full history: caught
    full = scan_commit_messages(tmp_path, patterns, source="rite-pattern")
    assert isinstance(full, list) and len(full) == 1

    # scoped to only the first commit: not caught (it's not in range)
    scoped = scan_commit_messages(
        tmp_path, patterns, source="rite-pattern", rev_range=first
    )
    assert scoped == []


def test_scan_kb_cross_reference_detects_copy_paste(tmp_path: Path):
    init_repo(tmp_path)
    long_line = (
        "this is a genuinely long line of proprietary algorithm text that repeats"
    )
    write(tmp_path, ".rite/kb/algorithm.md", f"# Algorithm\n\n{long_line}\n")
    write(tmp_path, "src/impl.py", f"{long_line}\ndef f(): pass\n")
    commit_all(tmp_path, "add kb and source")
    files = list_tracked_files(tmp_path)
    assert isinstance(files, list)
    findings = scan_kb_cross_reference(tmp_path, files)
    assert len(findings) == 1
    assert findings[0].file == ".rite/kb/algorithm.md"
    assert findings[0].source == "rite-kb"


def test_scan_kb_cross_reference_no_match_when_not_copied(tmp_path: Path):
    init_repo(tmp_path)
    write(
        tmp_path,
        ".rite/kb/algorithm.md",
        "# Algorithm\n\nsome unique kb-only content here\n",
    )
    write(tmp_path, "src/impl.py", "def f(): pass\n")
    commit_all(tmp_path, "add kb and source")
    files = list_tracked_files(tmp_path)
    assert isinstance(files, list)
    findings = scan_kb_cross_reference(tmp_path, files)
    assert findings == []


def test_scan_kb_cross_reference_ignores_short_lines(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, ".rite/kb/notes.md", "short\n")
    write(tmp_path, "src/impl.py", "short\n")
    commit_all(tmp_path, "add kb and source")
    files = list_tracked_files(tmp_path)
    assert isinstance(files, list)
    findings = scan_kb_cross_reference(tmp_path, files)
    assert findings == []
