"""A suppression must outlive an edit somewhere else in the file.

A `commit:file:rule:line` fingerprint identifies a finding by WHERE it is.
Insert a line above it — a comment, an import, anything — and every finding
below moves: the entry matches nothing, so the finding it covered blocks the
push, and the entry itself is reported stale. Nothing about the suppressed
code changed. The reader is shown both halves and left to work out they are
the same finding.

Measured on rite's own repository, mid-push: three added comment lines in
`gate.py` moved a suppressed docstring from line 253 to 256 and the gate
failed on a decision that had already been made and written down.

`commit:file:rule:sha256-<digest>` pins the matched text instead. It cannot
drift, and it is narrower where narrowness matters — change the secret and
the suppression stops applying, which is precisely when someone should look
at it again.
"""

from __future__ import annotations

from rite_ai.gate.findings import Finding, content_digest
from rite_ai.gate.suppression import (
    Suppression,
    apply,
    find_stale,
)

# Not token-shaped on purpose. Nothing here runs gitleaks — these tests build
# Findings directly — so a realistic token would buy nothing and cost a
# suppression entry of its own in this repo's gate.
SECRET = "the-matched-text-a-decision-was-made-about"


def _finding(line: int, secret: str = SECRET) -> Finding:
    return Finding(
        rule_id="github-pat",
        description="GitHub PAT",
        file="src/config.py",
        line=line,
        commit=None,
        match_preview="the-***",
        source="gitleaks",
        digest=content_digest(secret),
    )


def _entry(fingerprint: str) -> Suppression:
    return Suppression(fingerprint=fingerprint, reason="synthetic fixture", line_no=1)


class TestTheContentFormSurvivesAMove:
    def test_still_suppressed_after_lines_are_added_above(self):
        """THE DEFECT. Same secret, same file, three lines lower."""
        entry = _entry(_finding(38).content_fingerprint)

        blocking, suppressed = apply([_finding(41)], [entry])

        assert blocking == []
        assert len(suppressed) == 1

    def test_and_is_not_reported_stale(self):
        """The other half of the same failure: the gate warned about an entry
        that was doing its job."""
        entry = _entry(_finding(38).content_fingerprint)

        assert find_stale([entry], [_finding(41)]) == []

    def test_the_line_form_is_what_breaks(self):
        """Why the content form exists. Kept as a test so the cost of the old
        form stays stated rather than remembered."""
        entry = _entry(_finding(38).fingerprint)

        blocking, _ = apply([_finding(41)], [entry])

        assert len(blocking) == 1
        assert find_stale([entry], [_finding(41)]) == [entry]


class TestItIsNarrowerWhereThatCounts:
    def test_a_different_secret_at_the_same_line_is_not_covered(self):
        """A line-pinned entry suppresses whatever ends up on that line. A
        content-pinned one suppresses the string someone actually decided
        about — so replacing a fixture token with a live credential blocks."""
        entry = _entry(_finding(38).content_fingerprint)
        same, different = _finding(38), _finding(38, secret="different-matched-text")

        assert apply([same], [entry])[0] == [], "the covered secret should pass"
        assert apply([different], [entry])[0] == [different]

    def test_a_different_file_is_not_covered(self):
        entry = _entry(_finding(38).content_fingerprint)
        elsewhere = Finding(
            rule_id="github-pat",
            description="GitHub PAT",
            file="src/other.py",
            line=38,
            commit=None,
            match_preview="the-***",
            source="gitleaks",
            digest=content_digest(SECRET),
        )

        assert apply([_finding(38)], [entry])[0] == [], "here it should pass"
        assert apply([elsewhere], [entry])[0] == [elsewhere]

    def test_no_digest_means_the_line_form_is_all_there_is(self):
        """A source that records no matched text must not silently acquire a
        wildcard identity."""
        bare = Finding(
            rule_id="r",
            description="d",
            file="f",
            line=1,
            commit=None,
            match_preview="",
            source="rite-path",
        )

        assert bare.content_fingerprint is None
        assert apply([bare], [_entry(bare.fingerprint)])[0] == []


class TestOldEntriesKeepWorking:
    def test_a_line_form_entry_still_suppresses_its_finding(self):
        """Every existing `.rite/gitleaksignore` is in the old form. Adding a
        second form must not invalidate a single one of them."""
        f = _finding(38)

        blocking, suppressed = apply([f], [_entry(f.fingerprint)])

        assert blocking == []
        assert suppressed == [f]
        assert find_stale([_entry(f.fingerprint)], [f]) == []


class TestTheDigestCannotBecomeAWildcard:
    """A content fingerprint is only as narrow as the text it names. Two ways
    it could stop naming anything, both found by reviewing this change rather
    than by it failing."""

    def test_an_empty_match_has_no_content_fingerprint(self):
        """A user-declared scan pattern that can match empty — anything
        ending in `*` or `?` — matches at position 0 of EVERY line. Hashing
        that gives one fingerprint per file, and a single entry would switch
        the rule off for the whole file: the global off switch SPEC 11.4
        refuses to have."""
        from rite_ai.config.models import ScanPattern
        from rite_ai.gate.pattern_scan import scan_content

        findings = scan_content(
            root=_repo(),
            files=["a.py"],
            patterns=[ScanPattern(type="regex", pattern="(NOPE)?", description="d")],
            source="rite-pattern",
        )

        assert findings, "fixture no longer produces findings"
        assert all(f.content_fingerprint is None for f in findings), (
            "an empty match must fall back to the line form, which still names one line"
        )

    def test_the_digest_is_128_bits(self):
        """Short enough to grind is short enough to abuse: a rule that
        accepts any high-entropy body lets a contributor search for a token
        whose digest matches an entry already in the file, and be exempted by
        a diff that touches only a test fixture."""
        assert len(content_digest("x")) == 32


def _repo():
    """A tmp dir with one file, for the scanners that read from disk."""
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    (d / "a.py").write_text("one\ntwo\n")
    return d


class TestOneEntryCoveringSeveralIsNotSilent:
    """Dropping the line from the identity means a second occurrence of the
    same string, under the same rule, in the same file is covered by the same
    entry. Usually right — it is the same string and the reason is about the
    string — but a decision quietly growing to cover something nobody looked
    at is not something a gate should do without saying."""

    def test_the_count_is_reported(self):
        from rite_ai.gate.suppression import covering_more_than_one

        entry = _entry(_finding(38).content_fingerprint)

        covered = covering_more_than_one([_finding(38), _finding(60)], [entry])

        assert covered == [(entry, 2)]

    def test_one_finding_is_not_reported(self):
        from rite_ai.gate.suppression import covering_more_than_one

        entry = _entry(_finding(38).content_fingerprint)

        assert covering_more_than_one([_finding(38)], [entry]) == []


class TestABlockedReaderIsToldWhatToDo:
    """`rite publish check` named the finding and stopped. The fingerprint
    was printed only by `format_report`, which serves the pre-push hook and
    `python -m rite_ai.gate` — so the command the docs tell you to run was
    the one that told you least."""

    def test_publish_check_prints_the_fingerprint_and_where_it_goes(
        self, tmp_path, monkeypatch
    ):
        from unittest.mock import patch

        from click.testing import CliRunner

        from rite_ai.cli.main import cli
        from rite_ai.gate.gate import GateReport

        f = _finding(38)
        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
        (rite / "modules.yaml").write_text("modules: {}\n")
        monkeypatch.chdir(tmp_path)

        with patch(
            "rite_ai.gate.run_gate",
            return_value=GateReport(findings=[f], files_scanned=1),
        ):
            out = CliRunner().invoke(cli, ["publish", "check"]).output

        assert f.content_fingerprint in out, out
        assert ".rite/gitleaksignore" in out
        assert "reason is required" in out
