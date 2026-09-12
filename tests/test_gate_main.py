"""Tests for the standalone `python -m rite_ai.gate` entry point — specifically
the pre-push revision-range computation, since that's the part with real
logic (branch deletion, new-branch push, normal incremental push)."""

from __future__ import annotations

from pathlib import Path

import rite_ai.gate.__main__ as main_mod


def _run_pre_push(monkeypatch, tmp_path: Path, stdin_lines: list[str]) -> list[str]:
    seen_ranges: list[str] = []

    def fake_run_gate(root, rev_range=None, config=None):
        seen_ranges.append(rev_range)

        class _R:
            exit_code = 0

        return _R()

    monkeypatch.setattr(main_mod, "run_gate", fake_run_gate)
    monkeypatch.setattr(main_mod, "format_report", lambda r: "ok")
    monkeypatch.setattr(main_mod, "_find_root", lambda start: tmp_path)

    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(stdin_lines) + "\n"))
    main_mod._cmd_pre_push([])
    return seen_ranges


def test_incremental_push_uses_remote_to_local_range(tmp_path, monkeypatch):
    local = "a" * 40
    remote = "b" * 40
    ranges = _run_pre_push(
        monkeypatch, tmp_path, [f"refs/heads/main {local} refs/heads/main {remote}"]
    )
    assert ranges == [f"{remote}..{local}"]


def test_new_branch_push_scans_only_what_it_would_publish(tmp_path, monkeypatch):
    """Was `[local]` — everything reachable, which git's own
    `pre-push.sample` also does. Measured on git's 82,180-commit
    repository that is 60,764 commits and 22.9s in gitleaks alone, for a
    branch whose new work was one commit."""
    local = "a" * 40
    zero = "0" * 40
    ranges = _run_pre_push(
        monkeypatch, tmp_path, [f"refs/heads/new {local} refs/heads/new {zero}"]
    )
    assert ranges == [f"{local} --not --remotes"]


def test_branch_deletion_scans_nothing(tmp_path, monkeypatch):
    zero = "0" * 40
    remote = "b" * 40
    ranges = _run_pre_push(
        monkeypatch, tmp_path, [f"refs/heads/gone {zero} refs/heads/gone {remote}"]
    )
    assert ranges == []


def test_empty_stdin_scans_nothing(tmp_path, monkeypatch):
    ranges = _run_pre_push(monkeypatch, tmp_path, [])
    assert ranges == []


def test_worst_exit_code_wins_across_multiple_refs(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_run_gate(root, rev_range=None, config=None):
        calls["n"] += 1

        class _R:
            exit_code = 0 if calls["n"] == 1 else 2

        return _R()

    monkeypatch.setattr(main_mod, "run_gate", fake_run_gate)
    monkeypatch.setattr(main_mod, "format_report", lambda r: "ok")
    monkeypatch.setattr(main_mod, "_find_root", lambda start: tmp_path)

    import io

    local1, remote1 = "a" * 40, "b" * 40
    local2, remote2 = "c" * 40, "d" * 40
    stdin = (
        f"refs/heads/one {local1} refs/heads/one {remote1}\n"
        f"refs/heads/two {local2} refs/heads/two {remote2}\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main_mod._cmd_pre_push([])
    assert code == 2
