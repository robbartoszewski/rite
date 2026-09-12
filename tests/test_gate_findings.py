from rite_ai.gate.findings import Finding, redact


def test_fingerprint_with_commit():
    f = Finding(
        rule_id="github-pat",
        description="d",
        file="secret.py",
        line=2,
        commit="d2b9566cdca96a3e9abce78ac53e9b12a61fdebe",
        match_preview="ghp_****",
        source="gitleaks",
    )
    assert (
        f.fingerprint
        == "d2b9566cdca96a3e9abce78ac53e9b12a61fdebe:secret.py:github-pat:2"
    )


def test_fingerprint_without_commit_uses_dash():
    f = Finding(
        rule_id="rite-hardcoded-path",
        description="d",
        file="src/main.py",
        line=10,
        commit=None,
        match_preview="/Use****",
        source="rite-pattern",
    )
    assert f.fingerprint == "-:src/main.py:rite-hardcoded-path:10"


def test_redact_short_string_fully_masked():
    assert redact("abc") == "***"


def test_redact_long_string_keeps_edges():
    out = redact("ghp_1234567890abcdefghij1234567890ABCDEF")
    assert out.startswith("ghp_")
    assert out.endswith("CDEF")
    assert "*" in out
    # never leak the raw secret in the middle
    assert "1234567890abcdefghij" not in out
