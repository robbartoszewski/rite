"""The checksum a reader is told to compare against has to be the one that
gets published, computed the way the reader computes it.

README install option 2 says: download `install.sh`, run `shasum -a 256`, and
"compare against the v0.1.0 release notes". Three separate places have to
agree for that sentence to mean anything — the README's command, the digest
`tools/release_checksums.py` emits for the notes, and the runbook step that
tells the maintainer to run it. Until this file, nothing connected them: the
runbook said "run shasum and paste the value", and a wrong or stale paste
would reach every security-conscious reader as a failed check, which teaches
them the check is noise.

This is a distribution-security claim on a tool whose whole job is stopping
secrets reaching a remote. It is the claim the audience will test first.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "release_checksums.py"


def _run(*args: str, repo: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Runs the copy of the tool INSIDE `repo`.

    The tool finds its own repository from `__file__`, not from the working
    directory, so invoking the checkout's copy with `cwd=` pointed elsewhere
    silently inspected this repository instead of the one under test — the
    test would have passed while measuring the wrong tree.
    """
    root = repo or REPO_ROOT
    return subprocess.run(
        [sys.executable, str(root / "tools" / "release_checksums.py"), *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _committed_install_sh() -> bytes:
    return subprocess.run(
        ["git", "show", "HEAD:install.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout


def _clean_clone(tmp_path: Path) -> Path:
    """A clone, so the tool's dirty-tree refusal does not fire.

    The refusal is correct for the publish path and it made the tool's own
    tests unrunnable whenever `install.sh` was being edited — which is
    exactly when someone is most likely to run them. Testing against a clean
    clone keeps the guard strict without making the suite depend on the
    state of the working tree.
    """
    clone = tmp_path / "clone"
    # `--no-tags`, and it is load-bearing. This helper's whole premise is a
    # clone with NO release tag, so that `source_ref()`'s HEAD branch is the
    # one under test and `_tagged_clone` below can add a tag to exercise the
    # other. That premise held only while the repository had never been
    # tagged: publishing `v0.1.0` made every `_clean_clone` inherit it, the
    # tool correctly started hashing the tag instead of HEAD, and four tests
    # here failed — on a freshly published repo, for anyone who cloned it.
    subprocess.run(
        ["git", "clone", "-q", "--no-tags", str(REPO_ROOT), str(clone)],
        check=True,
        timeout=120,
    )
    (clone / "tools").mkdir(exist_ok=True)
    (clone / "tools" / "release_checksums.py").write_bytes(TOOL.read_bytes())
    return clone


def test_the_tool_publishes_the_digest_a_reader_would_compute(tmp_path):
    """`shasum -a 256 install.sh` on the committed file. Computed here with
    hashlib rather than by shelling out, so the test does not pass merely
    because both sides called the same binary."""
    clone = _clean_clone(tmp_path)
    committed = subprocess.run(
        ["git", "show", "HEAD:install.sh"],
        cwd=clone,
        capture_output=True,
        check=True,
    ).stdout
    expected = hashlib.sha256(committed).hexdigest()

    proc = _run(repo=clone)

    assert proc.returncode == 0, proc.stderr
    assert expected in proc.stdout
    assert "install.sh" in proc.stdout


def test_it_publishes_the_commit_sha_not_only_the_digest(tmp_path):
    """The digest covers the installer; the tool itself is fetched from the
    tag, which is a movable pointer. Telling a reader to use the commit SHA
    instead is only usable advice if the SHA is published."""
    clone = _clean_clone(tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=clone,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    out = _run(repo=clone).stdout

    assert sha in out
    assert "covers the installer only" in out


def test_it_publishes_the_command_the_readme_tells_the_reader_to_run():
    """If the README ever changes to `sha256sum`, the block would still say
    `shasum` and half the readers would be comparing different things."""
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("release_checksums", TOOL)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    # Both files, for the same reason as the length assertion below: the
    # README was cut to a landing page and the verify-first install path
    # moved to the notes page with the rest of the rationale. The property
    # is unchanged — wherever a reader is told to verify the download, the
    # command named must be the one the release block publishes.
    told = "".join(
        (REPO_ROOT / rel).read_text()
        for rel in ("README.md", "docs/install-notes.md")
    )
    assert module.READER_COMMAND in told, (
        "no install document tells readers to run the command the release "
        f"block publishes ({module.READER_COMMAND!r})"
    )


def test_the_runbook_points_at_the_tool_rather_than_a_manual_paste():
    """Skipped where `.docs/` is absent, which is everywhere but a maintainer's
    own checkout: `.gitignore` excludes it, so the runbook never ships.

    Without the skip this failed in every fresh clone — including the one the
    runbook's own step 4 tells you to make and run the suite in. A test that
    goes red on the instruction to run it teaches people to ignore red.
    """
    runbook = REPO_ROOT / ".docs" / "PUBLISH_RUNBOOK.md"
    if not runbook.is_file():
        pytest.skip("`.docs/` is gitignored and absent — maintainer-only check")
    assert "tools/release_checksums.py" in runbook.read_text()


def test_the_tool_is_referenced_from_a_file_that_actually_ships():
    """The runbook is gitignored, so it cannot be the only mention: strip
    `.docs/` and `tools/release_checksums.py` ships referenced by nothing.

    Honest about what this asserts: a REFERENCE, not a caller. Nothing in the
    tree invokes the tool — it is a maintainer command run by a human
    following the runbook, and pretending a comment is a caller would be the
    same overclaim the checklist line is about. What it buys is that a reader
    of `install.sh` — the file whose digest it publishes, and the file
    sceptics are told to read — can find out where that digest came from."""
    assert "tools/release_checksums.py" in (REPO_ROOT / "install.sh").read_text()


def test_the_published_digest_is_the_committed_one_not_the_working_copy(tmp_path):
    """The property the tool's docstring puts in bold, and nothing checked.

    Mutating `block()` to hash the working copy left every test green, because
    the tree is clean when they run and the two byte-strings are identical.
    This dirties the tree first, so they are not."""
    clone = _clean_clone(tmp_path)
    committed = subprocess.run(
        ["git", "show", "HEAD:install.sh"], cwd=clone, capture_output=True, check=True
    ).stdout
    (clone / "install.sh").write_bytes(committed + b"\n# a local edit\n")

    ok = _run("--check", hashlib.sha256(committed).hexdigest(), repo=clone)
    assert ok.returncode == 0, ok.stderr

    # ...and the working copy's digest is NOT accepted, which is the half
    # that fails if the tool ever reads the file from disk.
    working = (clone / "install.sh").read_bytes()
    assert working != committed
    bad = _run("--check", hashlib.sha256(working).hexdigest(), repo=clone)
    assert bad.returncode == 1, "the tool hashed the working copy"


def test_check_rejects_an_empty_or_malformed_digest(tmp_path):
    """`--check ""` printed the checksum block and exited 0, so
    `--check "$UNSET"` read as a successful verification."""
    clone = _clean_clone(tmp_path)
    for bad in ("", "   ", "not-a-digest", "abc123"):
        proc = _run("--check", bad, repo=clone)
        assert proc.returncode == 1, f"accepted {bad!r}"
        assert "not a sha256 digest" in proc.stderr, bad


def test_check_accepts_the_forms_a_human_pastes(tmp_path):
    """A bare digest, `sha256:<digest>`, and the whole `shasum` output line.
    The middle two were rejected, and they are the two most likely pastes."""
    clone = _clean_clone(tmp_path)
    committed = subprocess.run(
        ["git", "show", "HEAD:install.sh"], cwd=clone, capture_output=True, check=True
    ).stdout
    good = hashlib.sha256(committed).hexdigest()
    for form in (good, f"sha256:{good}", f"{good}  install.sh", f"  {good.upper()} "):
        assert _run("--check", form, repo=clone).returncode == 0, form


def test_check_accepts_the_right_digest_and_rejects_a_wrong_one(tmp_path):
    clone = _clean_clone(tmp_path)
    committed = subprocess.run(
        ["git", "show", "HEAD:install.sh"],
        cwd=clone,
        capture_output=True,
        check=True,
    ).stdout
    good = hashlib.sha256(committed).hexdigest()

    assert _run("--check", good, repo=clone).returncode == 0
    bad = _run("--check", "0" * 64, repo=clone)
    assert bad.returncode == 1
    assert "MISMATCH" in bad.stderr


def test_it_refuses_to_publish_a_digest_of_an_uncommitted_working_copy(tmp_path):
    """A reader's digest comes from the blob at the tag. Publishing one from
    an edited working tree guarantees every careful reader a mismatch."""
    clone = _clean_clone(tmp_path)
    # Appended, not replaced: overwriting the file removes its `VERSION=`
    # line, and the tool then fails on THAT instead — the test would have
    # passed on the wrong error had it asserted less specifically.
    installer = clone / "install.sh"
    installer.write_text(installer.read_text() + "\n# a local edit\n")

    proc = _run(repo=clone)

    assert proc.returncode == 1
    assert "no v0.2.0 tag exists yet" in proc.stderr
    assert "Commit, then tag, then run this again" in proc.stderr


def test_the_readme_is_honest_about_how_long_the_script_is():
    """README install option 3 tells a reader to `less install.sh` and says
    how long it is, because "read it yourself" is only an answer if that is a
    small ask. The number was `~90 lines` while the file was 102 — the kind
    of stale precision nobody re-checks.

    A range rather than an exact count: the point is the order of magnitude a
    reader is agreeing to read, and pinning the exact number would fail on
    every comment added.
    """
    lines = len((REPO_ROOT / "install.sh").read_text().splitlines())
    # BOTH places state it, and since the README was cut down to a landing
    # page they live in two different FILES: the code-block comment stayed
    # with the three install commands, the prose went to the notes page with
    # the rest of the rationale. Concatenated rather than checked separately
    # because the property is unchanged — every place that tells a reader how
    # long the script is must say the real number — and splitting the assert
    # per file would let one of them drift while the other held the test up.
    stated_in = " ".join(
        " ".join((REPO_ROOT / rel).read_text().split())
        for rel in ("README.md", "docs/install-notes.md")
    )
    readme = stated_in

    stated = [
        f"{lines} lines of sh",  # the code-block comment, in README.md
        f"{lines} lines of `sh`",  # the prose, in docs/install-notes.md
    ]
    # The EXACT count, in both places, not an "about" that drifts. It has now
    # gone stale twice — "~90" against 102, then "about 100" against 115, the
    # second made stale by the same change that fixed the first. A range let
    # the gate watch it drift.
    for phrase in stated:
        assert phrase in readme, (
            f"README does not state install.sh's real length ({lines}) as "
            f"{phrase!r} — it has gone stale twice already"
        )

    # Raised 150 -> 170 on 2026-09-12, deliberately, and this note is the
    # receipt. `git ls-remote --exit-code` returns 2 for "reached the remote,
    # no such ref" and other codes for "could not ask"; the script collapsed
    # both into "that release is not published yet" and discarded git's own
    # words, so a DNS failure, an auth failure and an absent tag were
    # indistinguishable. Telling the two apart costs a second branch and a
    # second message — about twelve lines that cannot be compressed away
    # without deleting the explanation that is the whole point of the fix.
    #
    # The budget is a READABILITY judgement, not a correctness one, and the
    # exact-count assertion above is what actually catches drift. Raising it
    # to buy a comprehensible failure is the right trade; raising it again to
    # avoid trimming something that could be trimmed is not. Read this note
    # before moving the number a third time.
    assert lines <= 170, (
        f"install.sh is {lines} lines. Option 3's pitch is that reading it is "
        "a small ask; at some point that stops being true."
    )


# --- the three fixes whose evidence was a manual run ------------------------


def _tagged_clone(tmp_path: Path, tag: str = "v0.2.0") -> Path:
    """A clone WITH the release tag. `_clean_clone` has none, so the tag
    branch of `source_ref()` — the whole point of the fix that moved hashing
    off HEAD — was never executed by any test."""
    clone = _clean_clone(tmp_path)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "tag",
            "-a",
            tag,
            "-m",
            tag,
        ],
        cwd=clone,
        check=True,
    )
    return clone


def test_it_hashes_the_tag_not_head_once_the_tag_exists(tmp_path):
    """Hashing HEAD was wrong in both directions: after any post-release
    commit it published a digest no reader could ever compute, with exit 0,
    and then reported the correctly published digest as a mismatch."""
    clone = _tagged_clone(tmp_path)
    at_tag = subprocess.run(
        ["git", "show", "v0.2.0:install.sh"], cwd=clone, capture_output=True, check=True
    ).stdout
    tag_digest = hashlib.sha256(at_tag).hexdigest()

    # A commit after the release, exactly as a hotfix would be.
    installer = clone / "install.sh"
    installer.write_text(installer.read_text() + "\n# a post-release fix\n")
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "fix"],
        cwd=clone,
        check=True,
    )
    head_digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    assert head_digest != tag_digest

    out = _run(repo=clone)
    assert out.returncode == 0, out.stderr
    assert tag_digest in out.stdout, "published a digest no reader would compute"
    assert head_digest not in out.stdout
    assert "tag v0.2.0" in out.stdout

    # ...and the digest a reader really has still verifies.
    assert _run("--check", tag_digest, repo=clone).returncode == 0


def test_a_commit_after_the_release_does_not_block_publishing(tmp_path):
    """The guard compared the working copy against the tag's blob, so it
    refused on a clean, fully committed tree whose only sin was a later
    commit — and told the maintainer to "tag first", meaning `git tag -f`,
    the move the README calls the security hazard."""
    clone = _tagged_clone(tmp_path)
    installer = clone / "install.sh"
    installer.write_text(installer.read_text() + "\n# a post-release fix\n")
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "fix"],
        cwd=clone,
        check=True,
    )
    # `install.sh` specifically, not the whole tree: `_clean_clone` copies
    # the tool in untracked, so a whole-tree check would fail for a reason
    # that has nothing to do with what is being tested.
    assert not subprocess.run(
        ["git", "status", "--porcelain", "--", "install.sh"],
        cwd=clone,
        capture_output=True,
        text=True,
    ).stdout.strip(), "install.sh is not committed — the test would prove nothing"

    proc = _run(repo=clone)

    assert proc.returncode == 0, proc.stderr
    assert "refusing" not in proc.stderr


def test_a_git_failure_reports_gits_own_reason(tmp_path):
    """`capture_output=True, check=True` captured git's stderr and then threw
    it away when `CalledProcessError` propagated, so "not a git repository"
    reached the user as a traceback with the answer deleted."""
    outside = tmp_path / "not-a-repo"
    (outside / "tools").mkdir(parents=True)
    (outside / "tools" / "release_checksums.py").write_bytes(TOOL.read_bytes())
    (outside / "install.sh").write_bytes((REPO_ROOT / "install.sh").read_bytes())

    proc = _run(repo=outside)

    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "not a git repository" in proc.stderr


def test_a_missing_installer_is_reported_not_raised(tmp_path):
    """`release_ref()` read the working copy with a bare `read_text()`, so an
    absent `install.sh` raised past the error handler — the class the handler
    was added for, surviving in the one place that reads the working tree."""
    outside = tmp_path / "empty"
    (outside / "tools").mkdir(parents=True)
    (outside / "tools" / "release_checksums.py").write_bytes(TOOL.read_bytes())

    proc = _run(repo=outside)

    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "cannot read install.sh" in proc.stderr


def test_install_sh_does_not_contradict_the_readme_on_tag_mutability():
    """Both files argue about what pinning to a tag buys you, and `install.sh`
    is the one a sceptic reads. It restated the overclaim the README had just
    retracted — "the URL cannot start serving you something else tomorrow" —
    and separately said it "does not run rite afterwards" while running
    `rite --version`. Nothing asserted the two agreed."""
    installer = " ".join((REPO_ROOT / "install.sh").read_text().split())

    for retracted in (
        "cannot start serving you something else",
        "does not run rite afterwards",
    ):
        assert retracted not in installer, f"install.sh restates {retracted!r}"

    # ...and says the things the README says.
    assert "movable pointer" in installer
    assert "rite --version" in installer
