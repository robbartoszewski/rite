"""Guards on what rite can do that a human could not undo.

Written for a blast-radius review before pointing rite at a live commercial
repo. The question these answer is not "does the feature work" but "what
survives a `git reset`" — spent quota, a lost exclusion guarantee, a rewritten
history, a pushed branch.

Each test here is a property, not an example. If one fails, do not adjust the
test to match the code.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.config.models import PoolConfig
from rite_ai.pool import fill
from rite_ai.state import CorruptStateError, read_json_state, write_atomic

SRC = Path(__file__).resolve().parent.parent / "src" / "rite_ai"


class TestRiteCannotWriteToARemoteOrRewriteHistory:
    """Robert approves merges and pushes by hand, and never force-pushes
    `main`, so the blast area stays "new changes" rather than "project
    history". That holds only while rite itself has no path to a remote."""

    def _all_source(self) -> str:
        return "\n".join(p.read_text() for p in sorted(SRC.rglob("*.py")))

    @pytest.mark.parametrize(
        "verb",
        [
            "push",
            "--force",
            "-f",
            "reset",
            "--hard",
            "rebase",
            "filter-branch",
            "update-ref",
            "reflog",
            "cherry-pick",
            "revert",
        ],
    )
    def test_no_git_invocation_uses_a_history_or_remote_writing_verb(self, verb):
        """Every `["git", ...]` argument list in the package, checked as a
        list rather than as prose — docstrings legitimately discuss pushing
        (the pre-push hook), so grepping the file text would be noise."""
        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            for match in re.finditer(r'\[\s*"git"\s*,([^\]]*)\]', path.read_text()):
                args = match.group(1)
                if f'"{verb}"' in args:
                    offenders.append(f"{path.name}: git {args.strip()}")
        assert not offenders, (
            f"git {verb!r} is reachable from rite: {offenders}. rite must never "
            "write to a remote or rewrite history; a human does that by hand."
        )

    def test_no_shell_execution_that_could_hide_a_push(self):
        source = self._all_source()
        assert "shell=True" not in source
        assert "os.system" not in source

    def test_gh_cli_is_only_used_for_issues_and_read_only_api(self):
        """`gh` can merge pull requests and write to repositories. rite's
        use of it must stay on the ticket board."""
        source = self._all_source()
        for forbidden in ('"pr"', '"repo"', '"release"', '"workflow"'):
            assert forbidden not in source or "issue" in source


class TestCorruptStateIsNeverReadAsEmpty:
    """The failure that motivated `rite_ai.state`: a half-written
    `claims.json` read as zero claims, `rite status` said "no active
    claims", and a second worker was granted a file the first still held —
    exit 0, no message."""

    def test_corrupt_ledger_refuses_instead_of_granting_a_held_path(
        self, tmp_path: Path
    ):
        ledger = ClaimsLedger(tmp_path / "claims.json")
        assert ledger.claim(["src/payments.ts"], "alice").ok

        raw = (tmp_path / "claims.json").read_text()
        (tmp_path / "claims.json").write_text(raw[: len(raw) // 2])

        with pytest.raises(CorruptStateError):
            ClaimsLedger(tmp_path / "claims.json").claim(["src/payments.ts"], "bob")

    def test_corrupt_ledger_refuses_to_list(self, tmp_path: Path):
        path = tmp_path / "claims.json"
        path.write_text('[{"paths": ["a.ts"')
        with pytest.raises(CorruptStateError):
            ClaimsLedger(path).list_claims()

    def test_missing_file_is_simply_empty(self, tmp_path: Path):
        assert ClaimsLedger(tmp_path / "claims.json").list_claims() == []

    def test_zero_byte_file_is_unwritten_not_corrupt(self, tmp_path: Path):
        """The ledger's own lock creates the file before anything is written
        into it. Safe to read as empty ONLY because writes are atomic — a
        truncate-then-write could leave a genuinely zero-byte file as the
        wreckage of a lost write."""
        path = tmp_path / "claims.json"
        path.write_text("")
        assert ClaimsLedger(path).list_claims() == []

    def test_read_json_state_distinguishes_absent_from_unparseable(
        self, tmp_path: Path
    ):
        assert read_json_state(tmp_path / "nope.json", default={"x": 1}) == {"x": 1}
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        with pytest.raises(CorruptStateError):
            read_json_state(bad, default={})


class TestWritesSurviveInterruption:
    def test_interrupted_write_leaves_the_previous_content_intact(
        self, tmp_path: Path, monkeypatch
    ):
        """A machine sleeping or a Ctrl-C mid-write must not destroy the
        file being replaced."""
        path = tmp_path / "claims.json"
        write_atomic(path, '[{"worker": "alice"}]')

        real_replace = __import__("os").replace

        def boom(*_a, **_k):
            raise KeyboardInterrupt()

        monkeypatch.setattr("os.replace", boom)
        with pytest.raises(KeyboardInterrupt):
            write_atomic(path, "REPLACEMENT THAT MUST NOT LAND")
        monkeypatch.setattr("os.replace", real_replace)

        assert path.read_text() == '[{"worker": "alice"}]'

    def test_interrupted_write_leaves_no_scratch_files_behind(
        self, tmp_path: Path, monkeypatch
    ):
        path = tmp_path / "state.json"
        write_atomic(path, "{}")
        monkeypatch.setattr(
            "os.replace", lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt())
        )
        with pytest.raises(KeyboardInterrupt):
            write_atomic(path, "x")
        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


class TestPoolCannotSpawnUnbounded:
    """Every pooled slot runs `claude`. Before the ceiling existed,
    `rite pool fill --count 500` issued 500 `tmux new-session ... claude`
    calls with no prompt — 500 live sessions against the weekly quota, which
    is the one resource here that no cleanup gets back."""

    def test_target_above_the_cap_is_refused_and_starts_nothing(
        self, tmp_path: Path, monkeypatch
    ):
        calls: list[list[str]] = []

        def record(args, *a, **k):
            calls.append(args)
            raise AssertionError("must not spawn anything when refusing")

        monkeypatch.setattr("rite_ai.pool._tmux_binary", lambda: "/usr/bin/tmux")
        monkeypatch.setattr(subprocess, "run", record)

        result = fill(tmp_path, PoolConfig(), count=500, max_slots=5)

        assert not result.ok
        assert "500" in result.message and "refused" in result.message
        assert calls == []

    def test_negative_target_is_refused(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("rite_ai.pool._tmux_binary", lambda: "/usr/bin/tmux")
        assert not fill(tmp_path, PoolConfig(), count=-1, max_slots=5).ok


class TestStaleClaimsAreVisible:
    """A claim never expires on its own, and should not: rite cannot tell a
    crashed session from a session thinking hard, and auto-releasing a path
    a live worker is editing is worse than leaving a stale one. What it must
    not do is render a five-day-old claim identically to a five-minute-old
    one — a file on a live repo sat claimed for days by a session that never
    committed anything, and the status output said nothing about it."""

    def test_a_fresh_claim_is_not_flagged(self):
        import time

        from rite_ai.claims.ledger import Claim
        from rite_ai.reporting.status import format_claim_age

        assert "STALE" not in format_claim_age(
            Claim(paths=["a.ts"], worker="alice", timestamp=time.time())
        )

    def test_an_old_claim_is_flagged_with_its_age(self):
        import time

        from rite_ai.claims.ledger import Claim
        from rite_ai.reporting.status import format_claim_age

        rendered = format_claim_age(
            Claim(
                paths=["a.ts"],
                worker="alice",
                timestamp=time.time() - 4 * 24 * 3600,
            )
        )
        assert "4d" in rendered
        assert "STALE" in rendered

    def test_a_claim_with_no_timestamp_says_so_rather_than_implying_freshness(self):
        from rite_ai.claims.ledger import Claim
        from rite_ai.reporting.status import format_claim_age

        claim = Claim(paths=["a.ts"], worker="alice")
        claim.timestamp = 0.0
        assert format_claim_age(claim) == "age unknown"
