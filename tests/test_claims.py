from pathlib import Path

import pytest

from rite_ai.claims.ledger import ClaimsLedger, normalise_path, paths_overlap


def test_normalise_path():
    assert normalise_path("file.ts:42-50") == "file.ts"
    assert normalise_path("file.ts#L10") == "file.ts"
    assert normalise_path("src/main.py") == "src/main.py"
    assert normalise_path("src/dir/") == "src/dir"


def test_paths_overlap_identical():
    assert paths_overlap("src/a.ts", "src/a.ts")


def test_paths_overlap_nesting():
    assert paths_overlap("src/", "src/a.ts")
    assert paths_overlap("src/a.ts", "src/")


def test_paths_no_overlap():
    assert not paths_overlap("src/a.ts", "src/b.ts")
    assert not paths_overlap("src/", "tests/")


def test_paths_overlap_with_decoration():
    assert paths_overlap("src/a.ts:10-20", "src/a.ts")


def test_claim_basic(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    result = ledger.claim(["src/a.ts"], "worker-1", "PROJ-1")
    assert result.ok
    claims = ledger.list_claims()
    assert len(claims) == 1
    assert claims[0].worker == "worker-1"
    assert claims[0].paths == ["src/a.ts"]


def test_claim_contention(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1")
    result = ledger.claim(["src/a.ts"], "worker-2")
    assert not result.ok
    assert result.overlaps
    assert "worker-1" in result.overlaps[0]


def test_claim_nested_contention(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/"], "worker-1")
    result = ledger.claim(["src/a.ts"], "worker-2")
    assert not result.ok


def test_claim_same_worker_no_contention(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1")
    result = ledger.claim(["src/b.ts"], "worker-1")
    assert result.ok


def test_claim_disjoint_workers(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1")
    result = ledger.claim(["tests/a.ts"], "worker-2")
    assert result.ok
    claims = ledger.list_claims()
    assert len(claims) == 2


def test_release_all(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1")
    ledger.claim(["src/b.ts"], "worker-1")
    released = ledger.release("worker-1")
    assert released == 2
    assert ledger.list_claims() == []


def test_release_specific(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1", "T-1")
    ledger.claim(["src/b.ts"], "worker-1", "T-2")
    released = ledger.release("worker-1", ["src/a.ts"])
    assert released == 1
    remaining = ledger.list_claims()
    assert len(remaining) == 1
    assert remaining[0].paths == ["src/b.ts"]


def test_force_release(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1")
    released = ledger.force_release(["src/a.ts"], by="admin", reason="stale claim")
    assert released == 1
    assert ledger.list_claims() == []


def test_force_release_persists_an_audit_record(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1", ticket="T-1")
    ledger.force_release(["src/a.ts"], by="admin", reason="stale claim")

    audit = ledger.force_release_audit()
    assert len(audit) == 1
    assert audit[0]["by"] == "admin"
    assert audit[0]["reason"] == "stale claim"
    assert audit[0]["released"] == [
        {"worker": "worker-1", "paths": ["src/a.ts"], "ticket": "T-1"}
    ]


def test_force_release_audit_accumulates_across_calls(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1")
    ledger.claim(["src/b.ts"], "worker-2")
    ledger.force_release(["src/a.ts"], by="admin", reason="first")
    ledger.force_release(["src/b.ts"], by="admin", reason="second")

    audit = ledger.force_release_audit()
    assert len(audit) == 2
    assert [r["reason"] for r in audit] == ["first", "second"]


def test_force_release_with_nothing_to_release_writes_no_audit_record(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    released = ledger.force_release(["src/nothing.ts"], by="admin", reason="n/a")
    assert released == 0
    assert ledger.force_release_audit() == []


def test_claims_for_worker(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "worker-1")
    ledger.claim(["tests/b.ts"], "worker-2")
    w1 = ledger.claims_for("worker-1")
    assert len(w1) == 1
    assert w1[0].worker == "worker-1"


def test_claim_empty_paths(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    result = ledger.claim([], "worker-1")
    assert not result.ok
    assert "no paths" in result.message


def test_force_release_can_be_narrowed_to_one_worker(tmp_path: Path):
    """Without this, anything acting on its own behalf — a reaper that knows
    one session died — would also release a live worker that claimed an
    identically-named path in the window since. That is the failure the
    ledger exists to prevent, arriving as the fix for it."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "alpha", "T-1")
    ledger.claim(["src/b.ts"], "beta", "T-2")

    released = ledger.force_release(
        ["src/a.ts", "src/b.ts"], by="reaper", reason="alpha died", worker="alpha"
    )

    assert released == 1
    assert [c.worker for c in ledger.list_claims()] == ["beta"]


def test_force_release_by_worker_alone_takes_all_of_theirs(tmp_path: Path):
    """What an automatic caller actually knows: whose session died, not which
    paths it held."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "alpha", "T-1")
    ledger.claim(["src/c.ts"], "alpha", "T-3")
    ledger.claim(["src/b.ts"], "beta", "T-2")

    released = ledger.force_release(worker="alpha", by="reaper", reason="gone")

    assert released == 2
    assert [c.worker for c in ledger.list_claims()] == ["beta"]


def test_force_release_still_ignores_the_owner_when_given_only_paths(tmp_path: Path):
    """The human case, unchanged: `rite release --force <path>` means "clear
    this path, I do not care who has it"."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "alpha", "T-1")

    assert ledger.force_release(["src/a.ts"], by="admin", reason="stale") == 1
    assert ledger.list_claims() == []


def test_an_empty_path_list_is_refused_even_with_a_worker(tmp_path: Path):
    """A caller that computed paths and got none of them, asking to release
    "those". It released nothing and wrote no audit line — the reads-as-
    success shape, with a worker present to make it look deliberate."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "alpha", "T-1")

    with pytest.raises(ValueError, match="empty path list"):
        ledger.force_release([], by="reaper", reason="none", worker="alpha")

    assert ledger.list_claims()


def test_force_release_refuses_to_mean_everything(tmp_path: Path):
    """Releasing every claim in the project, by nobody's request, must not be
    expressible by leaving both arguments out."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "alpha", "T-1")

    with pytest.raises(ValueError):
        ledger.force_release(by="admin", reason="oops")

    assert ledger.list_claims()


def test_a_narrowed_force_release_is_still_audited(tmp_path: Path):
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["src/a.ts"], "alpha", "T-1")

    ledger.force_release(worker="alpha", by="rite pool archive", reason="slot gone")

    (record,) = ledger.force_release_audit()
    assert record["by"] == "rite pool archive"
    assert record["released"][0]["worker"] == "alpha"


def test_force_release_names_an_overlapping_claim_it_did_not_release(tmp_path: Path):
    """The trap, and it is sharper than "released fewer than you meant".

    `claim()` refuses overlaps, so the parent and child can never both be
    held. What actually happens is: beta holds `users/src/auth.ts`, somebody
    clearing orphans forces `users/src`, NOTHING matches exactly, and the
    output is "force-released 0 claim(s)" — while the path they just tried to
    clear is still blocked by a claim sitting right there. Hit while tidying
    up, which is when nobody re-reads the semantics."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["users/src/auth.ts"], "beta", "T-2")

    released = ledger.force_release(["users/src"], by="admin", reason="orphan")

    assert released == 0
    assert ledger.last_skipped_overlaps == [("beta", "users/src/auth.ts")]


def test_nothing_is_reported_when_the_path_is_genuinely_clear(tmp_path: Path):
    """A line that fires when there is nothing to say is one people learn to
    scroll past."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["users/src"], "alpha", "T-1")

    ledger.force_release(["users/src"], by="admin", reason="orphan")

    assert ledger.last_skipped_overlaps == []


def test_overlap_is_reported_in_both_nesting_directions(tmp_path: Path):
    """`claim()` refuses either way round, so the report must see either way
    round — a claim on the parent and a claim on the child both block."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["users/src/auth.ts"], "alpha", "T-1")
    ledger.claim(["docs"], "beta", "T-2")

    ledger.force_release(["users/src"], by="a", reason="r")
    assert ledger.last_skipped_overlaps == [("alpha", "users/src/auth.ts")]

    ledger.force_release(["docs/guide.md"], by="a", reason="r")
    assert ledger.last_skipped_overlaps == [("beta", "docs")]


def test_only_the_overlapping_path_of_a_multi_path_claim_is_named(tmp_path: Path):
    """A claim holding several paths must not have all of them reported
    because one overlapped — `unrelated/file.py` shares nothing with
    `users/src` and saying otherwise sends somebody to release it."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["users/src/auth.ts", "unrelated/file.py"], "beta", "T-2")

    ledger.force_release(["users/src"], by="admin", reason="orphan")

    assert ledger.last_skipped_overlaps == [("beta", "users/src/auth.ts")]


def test_a_worker_scoped_release_does_not_advise_taking_someone_elses_claim(
    tmp_path: Path,
):
    """`worker=` leaves other holders alone on purpose, so reporting theirs
    would advise force-releasing a LIVE worker's claim as though it were an
    orphan.

    ⚠ The first version of this test asserted the opposite and called it the
    fix: `worker` was in scope and never referenced, so the filter existed in
    the docstring only. A test that locks in the behaviour its own docstring
    warns against is worse than no test — it certifies the bug."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["users/src/auth.ts"], "beta", "T-2")

    ledger.force_release(
        ["users/src"], by="reaper", reason="alpha died", worker="alpha"
    )

    assert ledger.last_skipped_overlaps == []


def test_a_worker_scoped_release_still_reports_that_workers_own_overlap(
    tmp_path: Path,
):
    """Narrowed, not silenced: the holder the caller IS acting on is still
    worth naming, because its other claim still blocks the path."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["users/src/auth.ts"], "alpha", "T-1")

    ledger.force_release(
        ["users/src"], by="reaper", reason="alpha died", worker="alpha"
    )

    assert ledger.last_skipped_overlaps == [("alpha", "users/src/auth.ts")]


def test_the_overlap_record_is_cleared_when_the_call_is_refused(tmp_path: Path):
    """Both guards raise before the lock is taken, so a caller that catches
    the ValueError would otherwise read the previous call's overlaps and
    believe them current."""
    ledger = ClaimsLedger(tmp_path / "claims.json")
    ledger.claim(["users/src/auth.ts"], "beta", "T-2")
    ledger.force_release(["users/src"], by="admin", reason="orphan")
    assert ledger.last_skipped_overlaps

    with pytest.raises(ValueError):
        ledger.force_release(by="admin", reason="oops")

    assert ledger.last_skipped_overlaps == []


def test_the_cli_prints_the_skipped_claim_and_what_to_do(tmp_path, monkeypatch):
    """The whole point is that a human reading the output knows the next
    command rather than discovering the gap by being refused."""
    import subprocess

    from click.testing import CliRunner

    import rite_ai.sandbox as sb
    from rite_ai.cli.init import run_init
    from rite_ai.cli.main import cli

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    run_init(root, yes=True)

    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    ledger.claim(["users/src/auth.ts"], "beta", "T-2")

    result = CliRunner().invoke(
        cli,
        ["release", "--force", "users/src", "--by", "admin", "--reason", "orphan"],
    )

    assert result.exit_code == 0, result.output
    # "0 claim(s)" alone is what sent somebody away believing the path was
    # clear. The next line is the one that stops them.
    assert "force-released 0 claim(s)" in result.output
    assert "still held: users/src/auth.ts (by beta)" in result.output
    assert "name it directly" in result.output


def test_a_workers_own_nested_claim_is_a_note_not_an_instruction(tmp_path, monkeypatch):
    """`claim()` only refuses overlaps BETWEEN workers, so one worker holding
    a parent and a child is legal. Telling them to "name it directly to
    release it too" nudges them into force-releasing their own live claim."""
    import subprocess

    from click.testing import CliRunner

    import rite_ai.sandbox as sb
    from rite_ai.cli.init import run_init
    from rite_ai.cli.main import cli

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    run_init(root, yes=True)

    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    # TWO claims, not one holding both paths — a single claim matching
    # `users/src` exactly is released whole, taking the nested path with it.
    # The case worth reporting is a separate nested claim left standing.
    ledger.claim(["users/src"], "alpha", "T-1")
    ledger.claim(["users/src/auth.ts"], "alpha", "T-2")

    result = CliRunner().invoke(
        cli,
        ["release", "--force", "users/src", "--by", "admin", "--reason", "tidy"],
    )

    assert result.exit_code == 0, result.output
    assert "note: alpha also holds users/src/auth.ts" in result.output
    assert "ordinarily nothing to do" in result.output
    assert "name it directly" not in result.output
