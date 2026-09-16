"""`rite doctor` is the first command a new user runs, and the one meant to
explain a broken machine. A traceback there explains nothing and stops every
later check.

Measured on a tester's machine: yoloai on PATH but built for another
architecture raised `OSError: [Errno 8] Exec format error` out of the sandbox
round-trip, and doctor died mid-report — the present-but-broken case the
round-trip check exists to find.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli

EXEC_FORMAT = OSError(8, "Exec format error")


def _project(tmp_path: Path, monkeypatch, sandbox: bool = True) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        f"sandbox:\n  enabled: {str(sandbox).lower()}\n  backend: seatbelt\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RITE_CLAUDE_TOKEN", "sk-ant-oat-test")
    return tmp_path


def _doctor() -> object:
    with patch("keyring.get_password", return_value=None):
        return CliRunner().invoke(cli, ["doctor"])


def test_a_check_that_raises_is_reported_not_a_traceback(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    with patch("rite_ai.sandbox.verify_sandbox", side_effect=EXEC_FORMAT):
        result = _doctor()
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "CHECK FAILED" in result.output
    assert "Exec format error" in result.output
    assert "problem(s) found" in result.output


def test_a_guarded_section_is_named_and_the_rest_still_runs(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, sandbox=False)
    with patch("rite_ai.cli.main.exclusion_holds", side_effect=RuntimeError("boom")):
        result = _doctor()
    assert result.exit_code == 1
    assert "file locking: CHECK FAILED — RuntimeError: boom" in result.output
    # A later check still ran: the summary is reached and the spec row printed.
    assert "spec:" in result.output


def test_a_missing_gh_is_a_problem_when_workers_are_sandboxed(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    real_which = __import__("shutil").which

    def which(tool, *a, **kw):
        return None if tool == "gh" else real_which(tool, *a, **kw)

    ok = type(
        "C",
        (),
        {
            "ok": True,
            "installed": True,
            "backend": "seatbelt",
            "detail": "ok",
            "elapsed_ms": 1,
        },
    )()
    with (
        patch("shutil.which", side_effect=which),
        patch("rite_ai.sandbox.verify_sandbox", return_value=ok),
    ):
        result = _doctor()
    assert "tool gh: not found" in result.output
    assert "every push from a sandbox fails" in result.output
    assert "cli.github.com" in result.output
    assert result.exit_code == 1


def test_a_missing_gh_is_only_a_note_without_sandboxing(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, sandbox=False)
    real_which = __import__("shutil").which

    def which(tool, *a, **kw):
        return None if tool == "gh" else real_which(tool, *a, **kw)

    with patch("shutil.which", side_effect=which):
        result = _doctor()
    assert "tool gh: not found" in result.output
    assert "every push from a sandbox fails" not in result.output
