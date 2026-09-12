"""Regression tests for the gate meeting bytes that are not UTF-8.

Found by a background job left running against git's own repository,
which died on `byte 0xf8` — a Latin-1 name in a commit message from
before the project standardised on UTF-8. Every real repository with any
age has one.

`git show -s --format=%B <sha>` was read with `text=True` and no
`errors=`, so the decode raised `UnicodeDecodeError` out of
`scan_commit_messages`, out of `run_gate`, and out of the command.

The crash is half the defect. The other half is the exit code. Python
exits 1 on an unhandled exception, and 1 is this module's **WARN**: "no
blocking findings, but stale suppression entries exist". A caller
reading the exit code — `rite publish check && git push`, or CI — was
told the gate had run and found nothing blocking, by a gate that had
crashed. `EXIT_ERROR` exists "specifically so a broken gate cannot
report exit 0", and an uncaught exception was the one path around it.

Through the pre-push hook the same crash blocks the push, because git
treats any non-zero as refusal. It failed closed there by accident, with
a traceback in place of a message.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.gate.gate import EXIT_CLEAN, EXIT_ERROR, EXIT_FAIL, run_gate

SECRET = 'API_KEY = "j8s7Hs9dJ2kLm4Np6Qr8Tv0Wx2Yz4Ab6Cd8Ef0Gh"\n'


def _repo_with_a_latin1_commit_message(tmp_path: Path) -> Path:
    """A commit whose stored message really is not UTF-8.

    `git commit -F` and `git commit-tree` both re-encode, so the object
    is written by hand: that is the only way to reproduce what an old
    repository already contains."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    (tmp_path / "a.txt").write_text("x\n")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)

    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    when = "1757500000 +0000"
    obj = (
        f"tree {tree}\n".encode()
        + f"author t <t@e> {when}\n".encode()
        + f"committer t <t@e> {when}\n".encode()
        + b"\n"
        + b"fix by Torv\xf8ld\n"  # Latin-1 oslash
    )
    sha = (
        subprocess.run(
            ["git", "hash-object", "-t", "commit", "-w", "--stdin"],
            cwd=tmp_path,
            input=obj,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    subprocess.run(["git", "reset", "-q", "--hard", sha], cwd=tmp_path, check=True)

    stored = subprocess.run(
        ["git", "cat-file", "commit", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    ).stdout
    assert b"\xf8" in stored, "the fixture re-encoded; the test would prove nothing"
    return tmp_path


class TestACommitMessageThatIsNotUtf8:
    def test_the_gate_completes_instead_of_crashing(self, tmp_path: Path):
        repo = _repo_with_a_latin1_commit_message(tmp_path)

        report = run_gate(repo)

        assert report.exit_code == EXIT_CLEAN
        assert report.errors == []

    def test_a_secret_beside_the_bad_byte_is_still_caught(self, tmp_path: Path):
        """The undecodable byte must not become a blind spot."""
        repo = _repo_with_a_latin1_commit_message(tmp_path)
        (repo / "secret.txt").write_text(SECRET)
        subprocess.run(["git", "add", "secret.txt"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@e",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "add secret",
            ],
            cwd=repo,
            check=True,
        )

        report = run_gate(repo)

        assert report.exit_code == EXIT_FAIL
        assert "secret.txt" in {f.file for f in report.findings}

    def test_the_cli_does_not_traceback(self, tmp_path: Path):
        repo = _repo_with_a_latin1_commit_message(tmp_path)
        rite = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "rite"
        if not rite.exists():
            pytest.skip("no built console script to drive")

        proc = subprocess.run(
            [str(rite), "publish", "check"],
            cwd=repo,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )

        assert "Traceback" not in proc.stderr
        assert proc.returncode == EXIT_CLEAN


class TestTheGateCannotRaise:
    def test_an_unexpected_exception_becomes_exit_error_not_warn(self, monkeypatch):
        """1 is WARN in this module's vocabulary, and Python exits 1 on an
        unhandled exception. A crashed gate must never be readable as
        "ran, nothing blocking"."""
        from rite_ai.gate import gate as gate_module

        def boom(*_a, **_k):
            raise RuntimeError("something nobody anticipated")

        monkeypatch.setattr(gate_module, "_run_gate", boom)

        report = run_gate(Path("."))

        assert report.exit_code == EXIT_ERROR
        assert report.status == "error"
        assert "crashed" in report.errors[0]
        assert "RuntimeError" in report.errors[0]

    def test_the_crash_message_names_the_exception(self, monkeypatch):
        """ "The gate could not run" with no cause is a dead end for
        whoever has to fix it."""
        from rite_ai.gate import gate as gate_module

        monkeypatch.setattr(
            gate_module,
            "_run_gate",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("bad byte 0xf8")),
        )

        report = run_gate(Path("."))

        assert "ValueError" in report.errors[0]
        assert "bad byte 0xf8" in report.errors[0]


def test_every_text_mode_subprocess_handles_undecodable_output():
    """A guard on the family rather than on the one call that crashed.

    Twelve modules read subprocess output with `text=True`; not one set
    `errors=`, so any of them would raise on a byte git handed back that
    is not valid UTF-8 — a filename, a branch name, a commit message, a
    file's contents. `errors="replace"` cannot lose a match that matters:
    a byte that is not valid UTF-8 cannot be part of a UTF-8 secret.

    Only the commit-message door is demonstrated above, and the reason is
    the machine rather than the code: APFS refuses to create a filename
    that is not valid UTF-8 ("Illegal byte sequence"), so the filename
    path cannot be reproduced here at all. It is reachable on ext4, which
    is what CI and a Linux contributor run — so the guard covers every
    call site rather than only the one that could be proved.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "rite_ai"
    offenders = []
    for path in sorted(src.rglob("*.py")):
        lines = path.read_text().splitlines()
        for number, line in enumerate(lines, start=1):
            if "text=True" not in line or line.strip().startswith("#"):
                continue
            window = " ".join(lines[max(0, number - 6) : number + 6])
            if 'errors="replace"' in window or "errors=" in window:
                continue
            offenders.append(f"{path.relative_to(src)}:{number}: {line.strip()}")
    assert not offenders, (
        "subprocess output decoded as text with no `errors=` — git can hand "
        f"back bytes that are not UTF-8: {offenders}"
    )
