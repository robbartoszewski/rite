"""A suppression whose line moved should say where it moved to.

A gate fingerprint is `commit:file:rule:line`, so for a working-tree entry the
line number IS the identity. An edit ANYWHERE ABOVE a suppressed line
invalidates its entry: the suppression stops matching, the finding it covered
becomes blocking, and the entry is reported stale. Nothing about the
suppressed code changed.

Measured four times in this repository, most recently while landing an
unrelated three-line comment in `gate.py`, which moved a suppressed docstring
from line 250 to 253 and turned a suppressed finding into a blocking one.

The report told the reader two true things and left them to connect the two:
"this entry matches nothing" and, separately, "here is a blocking finding".
The blocking finding is almost always the same one, at its new line. Saying so
turns a detective task into a one-line edit.

Content-addressed fingerprints (IMP-5) have since landed, and a moved finding
is now offered its CONTENT form to re-point to: the whole point of saying
"it moved" is that the reader should not have to do it again next month.

They also make one case of "stale" mean something new, and dangerous to get
wrong — see `TestAContentEntryIsNeverOfferedARePoint`.
"""

from __future__ import annotations

from rite_ai.gate.findings import Finding, content_digest
from rite_ai.gate.gate import GateReport, format_report
from rite_ai.gate.suppression import Suppression, moved_to, stale_hint

# Deliberately not path- or token-shaped: these tests are about identity, not
# about detection, so a realistic-looking string would only add findings this
# repo's own gate then has to suppress.
ACCEPTED = "the-text-a-decision-was-made-about"
REPLACEMENT = "the-text-that-replaced-it"
MATCHED = "the-matched-text"


def _finding(
    line: int,
    *,
    rule: str = "rite-hardcoded-linux-home-directory-path",
    file: str = "src/rite_ai/gate/gate.py",
    commit: str | None = None,
    text: str = "",
) -> Finding:
    return Finding(
        rule_id=rule,
        description="hardcoded home path",
        file=file,
        line=line,
        commit=commit,
        match_preview="/hom***ser/",
        source="rite-pattern",
        digest=content_digest(text) if text else "",
    )


def _suppression(
    line: int,
    *,
    rule: str = "rite-hardcoded-linux-home-directory-path",
    file: str = "src/rite_ai/gate/gate.py",
) -> Suppression:
    return Suppression(
        fingerprint=f"-:{file}:{rule}:{line}",
        reason="illustrative, not a real path",
        line_no=128,
    )


class TestMovedTo:
    def test_it_finds_the_same_rule_at_a_new_line(self):
        moved = moved_to(_suppression(250), [_finding(253)])
        assert moved is not None and moved.line == 253

    def test_a_different_rule_in_the_same_file_is_not_the_same_finding(self):
        assert (
            moved_to(
                _suppression(250),
                [_finding(253, rule="rite-hardcoded-macos-home-directory-path")],
            )
            is None
        )

    def test_a_different_file_is_not_the_same_finding(self):
        assert moved_to(_suppression(250), [_finding(253, file="src/other.py")]) is None

    def test_a_different_commit_is_not_the_same_finding(self):
        """A history finding re-anchored by a squash is a different problem
        with a different remedy — re-pointing to a new COMMIT, not a new
        line. Conflating them would send the reader to the wrong fix."""
        s = Suppression(fingerprint="abc123:f.py:github-pat:38", reason="r", line_no=1)
        assert (
            moved_to(s, [_finding(38, rule="github-pat", file="f.py", commit="def456")])
            is None
        )

    def test_two_candidates_are_ambiguous_and_reported_as_none(self):
        """Same rule twice in one file is the ordinary case in this very
        repo's suppression list. Guessing which one moved would be worse
        than saying nothing."""
        assert moved_to(_suppression(250), [_finding(253), _finding(260)]) is None


class TestTheReportSaysSo:
    def test_the_stale_line_names_the_new_line_and_the_re_point(self):
        """THE REGRESSION. The report listed the stale entry and the blocking
        finding separately and never connected them."""
        report = GateReport(
            findings=[_finding(253)],
            stale_suppressions=[_suppression(250)],
            files_scanned=1,
        )

        out = format_report(report)

        assert "line 253" in out, f"the report does not say where it moved:\n{out}"
        assert (
            "-:src/rite_ai/gate/gate.py:rite-hardcoded-linux-home-directory-path:253"
            in out
        ), "the report does not give the corrected fingerprint to paste"

    def test_an_unrelated_stale_entry_is_not_given_a_false_lead(self):
        """A suppression that is stale because the code was DELETED must not
        be told it moved somewhere it did not."""
        report = GateReport(
            findings=[_finding(253, file="src/other.py")],
            stale_suppressions=[_suppression(250)],
            files_scanned=1,
        )

        out = format_report(report)

        assert "line 253" not in out
        assert "no longer matches any finding" in out


class TestTheCommandAHumanTypesSaysItToo:
    """`rite publish check` renders the stale block itself rather than
    through `format_report`, which serves `python -m rite_ai.gate` and the
    pre-push hook. Fixing only the shared formatter would have improved the
    path a human does not read — the same mistake this audit found in
    v0.3.0's worker-instruction fix, one release earlier.
    """

    def test_publish_check_names_the_new_line(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        report = GateReport(
            findings=[_finding(253)],
            stale_suppressions=[_suppression(250)],
            files_scanned=1,
        )
        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
        (rite / "modules.yaml").write_text("modules: {}\n")
        monkeypatch.chdir(tmp_path)

        with patch("rite_ai.gate.run_gate", return_value=report):
            out = CliRunner().invoke(cli, ["publish", "check"]).output

        assert "line 253" in out, (
            f"the typed command does not say where it moved:\n{out}"
        )
        assert (
            "-:src/rite_ai/gate/gate.py:rite-hardcoded-linux-home-directory-path:253"
            in out
        )


class TestAContentEntryIsNeverOfferedARePoint:
    """A content-pinned entry cannot move — position is not in its identity.
    The only way one goes stale is that the text it named is GONE, which is
    the case the form exists to force a second look at: someone replaced the
    fixture token, or the placeholder path, with something else.

    `moved_to` matched on `(commit, file, rule)` with a different LINE, and
    for a content entry the last field is `sha256-…`, which never equals a
    line number — so the guard was inert and the report volunteered the new
    text's fingerprint as "re-point this entry to". One paste would have
    carried an accepted reason onto a string nobody had looked at, which is
    the exact inversion of what content pinning is for.
    """

    def _entry(self, text: str) -> Suppression:
        return Suppression(
            fingerprint=_finding(250, text=text).content_fingerprint,
            reason="placeholder path in an example",
            line_no=12,
        )

    def test_no_re_point_is_offered_when_the_text_changed(self):
        replaced = _finding(250, text=REPLACEMENT)

        hint = stale_hint(self._entry(ACCEPTED), [replaced])

        assert replaced.content_fingerprint not in (hint or "")
        assert "re-point" not in (hint or "")

    def test_the_hint_says_what_actually_happened(self):
        hint = stale_hint(self._entry(ACCEPTED), [_finding(250, text=REPLACEMENT)])

        assert hint is not None
        assert "no longer there" in hint
        assert "new decision" in hint

    def test_moved_to_refuses_it_outright(self):
        """Belt and braces: whatever any future report does with the result,
        the answer to "did this entry move?" is no."""
        replaced = _finding(253, text=REPLACEMENT)

        assert moved_to(self._entry(ACCEPTED), [replaced]) is None


class TestBothReportsOfferTheDurableForm:
    """The line form is what drifts. A reader who is being told "re-point
    this" is, by definition, being told so BECAUSE it drifted, and handing
    them the drifting form again guarantees a third visit.

    Both surfaces, because they render the stale block separately and the
    last two fixes here each landed in only one of them.
    """

    def _report(self) -> GateReport:
        return GateReport(
            findings=[_finding(253, text=MATCHED)],
            stale_suppressions=[_suppression(250)],
            files_scanned=1,
        )

    def test_format_report_offers_the_content_form(self):
        out = format_report(self._report())

        assert "sha256-" in out, out
        assert _finding(253, text=MATCHED).content_fingerprint in out

    def test_publish_check_offers_the_content_form(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        rite = tmp_path / ".rite"
        rite.mkdir()
        (rite / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
        (rite / "modules.yaml").write_text("modules: {}\n")
        monkeypatch.chdir(tmp_path)

        with patch("rite_ai.gate.run_gate", return_value=self._report()):
            out = CliRunner().invoke(cli, ["publish", "check"]).output

        assert "sha256-" in out, out
        assert _finding(253, text=MATCHED).content_fingerprint in out
