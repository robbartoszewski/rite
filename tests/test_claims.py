from pathlib import Path

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
