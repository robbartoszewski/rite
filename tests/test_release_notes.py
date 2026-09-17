"""The release body is the changelog section plus the checksum, from the tag.

`docs/install-notes.md` tells a reader to compare the installer's digest
"against the release notes". Three tags existed and no release did, so the
step it asks for pointed at nothing. `tools/release_notes.py` is what the
publish procedure runs to create one, and these hold the two properties that
make the instruction followable: the notes are the changelog section rather
than a second copy of it, and everything is read from the TAG — the blob a
reader downloads — not from the working tree.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "release_notes.py"


def _module():
    spec = importlib.util.spec_from_file_location("release_notes", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CHANGELOG = """# Changelog

## 0.2.10 (2026-09-20)

- Ten, not two.

## 0.2.0 (2026-09-14)

### Enhancements

- The real one.

## 0.1.0 (2026-09-13)

- First public release.
"""


# --- the section, not a second copy of it ----------------------------------------


def test_the_section_stops_at_the_next_release():
    section = _module().changelog_section(_CHANGELOG, "0.2.0")
    assert "The real one." in section
    assert "First public release." not in section
    assert "Ten, not two." not in section


def test_a_version_is_not_matched_by_a_longer_one():
    """`0.2.0` must not pick up `0.2.10`'s section."""
    assert "Ten, not two." in _module().changelog_section(_CHANGELOG, "0.2.10")


def test_a_missing_section_is_an_error_not_an_empty_release():
    module = _module()
    with pytest.raises(module.ToolError) as caught:
        module.changelog_section(_CHANGELOG, "9.9.9")
    assert "no `## 9.9.9` section" in str(caught.value)


def test_an_empty_section_is_refused():
    module = _module()
    with pytest.raises(module.ToolError):
        module.changelog_section(
            "## 0.4.0 (today)\n\n## 0.3.0 (before)\n- x\n", "0.4.0"
        )


def test_the_tag_names_the_version():
    module = _module()
    assert module.version_of("v0.3.0") == "0.3.0"
    assert module.version_of("0.3.0") == "0.3.0"


# --- read from the tag, not the working tree -------------------------------------


def _repo(tmp_path: Path) -> Path:
    """A tagged repo whose working tree has MOVED ON from the tag."""
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for tool in ("release_notes.py", "release_checksums.py"):
        (root / "tools" / tool).write_bytes((REPO_ROOT / "tools" / tool).read_bytes())
    (root / "CHANGELOG.md").write_text(_CHANGELOG)
    (root / "install.sh").write_text('VERSION="${RITE_VERSION:-v0.2.0}"\necho tagged\n')
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-qm", "release"], cwd=root, check=True)
    subprocess.run([*git, "tag", "-a", "v0.2.0", "-m", "r"], cwd=root, check=True)
    # After the tag: a different installer, and a changelog entry for a release
    # that has not happened. Neither may reach v0.2.0's notes.
    (root / "install.sh").write_text("echo LATER\n")
    (root / "CHANGELOG.md").write_text("## 0.3.0 (later)\n\n- later\n" + _CHANGELOG)
    subprocess.run([*git, "commit", "-qam", "after"], cwd=root, check=True)
    return root


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "release_notes.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def test_the_body_is_built_from_the_tag_not_the_working_tree(tmp_path):
    root = _repo(tmp_path)
    tagged = subprocess.run(
        ["git", "show", "v0.2.0:install.sh"], cwd=root, capture_output=True, check=True
    ).stdout

    proc = _run(root, "v0.2.0")

    assert proc.returncode == 0, proc.stderr
    assert hashlib.sha256(tagged).hexdigest() in proc.stdout
    assert hashlib.sha256(b"echo LATER\n").hexdigest() not in proc.stdout
    assert "The real one." in proc.stdout
    assert "later" not in proc.stdout


def test_the_body_carries_the_commit_and_the_reader_command(tmp_path):
    root = _repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "v0.2.0^{commit}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    proc = _run(root, "v0.2.0")

    assert commit in proc.stdout
    assert "shasum -a 256 install.sh" in proc.stdout


def test_a_tag_with_no_changelog_section_fails_loudly(tmp_path):
    root = _repo(tmp_path)
    proc = _run(root, "v9.9.9")
    assert proc.returncode == 1
    assert "release_notes:" in proc.stderr
    assert not proc.stdout


# --- verify checks the SERVED bytes ----------------------------------------------


def test_verify_fails_when_the_notes_carry_another_digest(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "served_installer", lambda tag: b"served bytes")
    monkeypatch.setattr(module, "published_body", lambda tag: "digest: " + "a" * 64)
    with pytest.raises(module.ToolError) as caught:
        module.verify("v0.3.0")
    assert "do not carry the digest" in str(caught.value)
    assert hashlib.sha256(b"served bytes").hexdigest() in str(caught.value)


def test_verify_fails_when_no_release_carries_a_digest_at_all(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "served_installer", lambda tag: b"x")
    monkeypatch.setattr(module, "published_body", lambda tag: "no digests here")
    with pytest.raises(module.ToolError) as caught:
        module.verify("v0.3.0")
    assert "no digest at all" in str(caught.value)


# --- the procedure runs it ---------------------------------------------------------


def test_the_runbook_creates_the_release_rather_than_leaving_it_to_a_human():
    """A manual step at the end of a checklist is the step that gets skipped;
    three tags shipped without a release that way."""
    runbook = Path.home() / "AI" / "rite" / ".docs" / "PUBLISH_RUNBOOK.md"
    if not runbook.is_file():
        pytest.skip("runbook is not part of the repository (.docs is untracked)")
    text = runbook.read_text()
    assert "gh release create" in text
    assert "tools/release_notes.py" in text
    assert "--verify" in text
