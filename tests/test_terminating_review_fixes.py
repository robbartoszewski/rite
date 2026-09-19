"""The defects a terminating review of the whole source found before v0.5.0.

Each test here corresponds to something that SHIPPED and was found by someone
who had not written it. They are grouped in one file deliberately: the value
is in reading them together, because four of the five are the same shape —
a check that could not tell "I looked and found nothing" from "I could not
look".

Every test in this file was confirmed to go red with its fix reverted.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

# --- a name is not a path segment until something checks ---------------------------


class TestWorkerNamesAreValidated:
    """`rite remove worker ..` deleted the entire project.

    `workers/..` is the project root, `.is_dir()` agreed, and `rmtree` walked
    it — emptying `.rite/`, `src/` and the user's own files before raising,
    so the first signal was a traceback about a directory that no longer
    existed.

    The guard could not have helped: `unsaved_work` looks for uncommitted
    work in the target's DIRECT CHILDREN that are git repositories. A
    worker's module clones live there; a project root's children are
    `.rite/`, `docs/`, `src/`. It reported truthfully that nothing was at
    risk, about a directory holding everything.
    """

    def _project(self, tmp_path: Path) -> Path:
        root = tmp_path / "proj"
        (root / ".rite").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "IMPORTANT.md").write_text("a user's work\n")
        (root / "workers" / "alpha").mkdir(parents=True)
        return root

    @pytest.mark.parametrize("name", ["..", ".", "../evil", "a/b", "", "   "])
    def test_a_traversing_name_never_reaches_a_path(self, tmp_path, name):
        from rite_ai.workspace.manage import remove_worker

        root = self._project(tmp_path)
        result = remove_worker(root, name)

        assert not result.ok
        # THE ASSERTION THAT MATTERS. A refusal message is not the point;
        # the project still existing is.
        assert (root / "IMPORTANT.md").exists()
        assert (root / ".rite").is_dir()
        assert (root / "src").is_dir()

    def test_the_refusal_says_which_rule_was_broken(self, tmp_path):
        """ "invalid name" tells someone neither what they did nor what to
        type instead, and this message is the only thing they see."""
        from rite_ai.workspace.manage import remove_worker

        root = self._project(tmp_path)
        assert "directory reference" in remove_worker(root, "..").message
        assert "one path segment" in remove_worker(root, "a/b").message

    def test_a_real_worker_is_still_removed(self, tmp_path):
        """The fix must not buy safety by refusing the ordinary case."""
        from rite_ai.workspace.manage import remove_worker

        root = self._project(tmp_path)
        result = remove_worker(root, "alpha")

        assert result.ok
        assert not (root / "workers" / "alpha").exists()

    def test_add_worker_is_guarded_too(self, tmp_path):
        """Creating `workers/../x` is not destructive; it writes a workspace
        outside the project that every later command fails to find. Same
        missing check."""
        from rite_ai.workspace.manage import add_worker

        root = self._project(tmp_path)
        assert not add_worker(root, "../escape").ok


# --- the gate could not tell "clean" from "could not open" -------------------------


def _repo_with(tmp_path: Path, names: list[str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, check=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    for name in names:
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        # A MADE-UP MARKER, not a realistic path. The first version of this
        # fixture embedded a literal macOS home directory path, which rite's
        # own built-in rule correctly flags — so this file became a finding
        # in the repository and the publish gate blocked the push that added
        # it. Twice: the comment explaining the first fix quoted the path it
        # was explaining. Both refusals were right. A gate that ignores test
        # fixtures is a gate with a hole shaped like a directory name, and
        # these tests need SOMETHING to match, not something realistic.
        p.write_text('TOKEN = "ZZ-FIXTURE-MARKER-ZZ"\n')
    run("git", "add", "-A")
    run("git", "commit", "-qm", "x")
    return repo


class TestTheGateSeesEveryTrackedFile:
    """`git ls-files` without `-z` quotes and octal-escapes any path outside
    ASCII. That string was used as a path, the open failed, and the file was
    skipped silently — while the count beside it said it had been scanned.

    Measured before the fix, same repo, same secret, one byte different:
        café.py -> exit 0, "clean", "scanned 1 tracked file(s)"
        cafe.py -> exit 2, finding
    """

    NAMES = ["café.py", "cafe.py", "naïve/ünï.py", "with space.py", "quote'd.py"]

    def test_every_name_parses_to_something_that_opens(self, tmp_path):
        from rite_ai.gate.pattern_scan import list_tracked_files

        repo = _repo_with(tmp_path, self.NAMES)
        tracked = list_tracked_files(repo)

        assert sorted(tracked) == sorted(self.NAMES)
        for rel in tracked:
            assert (repo / rel).is_file(), f"{rel!r} does not open"

    def test_blame_agrees_with_the_listing(self, tmp_path):
        """`files_touched_by` decides whether a finding is THIS push's. An
        escaped name never matched, so a secret this push added was demoted
        to "already in the repository, NOT from this push" — the gate does
        not merely miss it, it reassures."""
        from rite_ai.gate.pattern_scan import files_touched_by, list_tracked_files

        repo = _repo_with(tmp_path, self.NAMES)

        assert set(list_tracked_files(repo)) == set(files_touched_by(repo, "HEAD"))

    def test_a_secret_is_found_whatever_the_filename(self, tmp_path):
        from rite_ai.config.models import ScanPattern
        from rite_ai.gate.pattern_scan import list_tracked_files, scan_content

        repo = _repo_with(tmp_path, self.NAMES)
        pattern = [
            ScanPattern(
                type="regex", pattern=r"ZZ-FIXTURE-MARKER-ZZ", description="marker"
            )
        ]

        found = scan_content(repo, list_tracked_files(repo), pattern, source="t")

        assert len(found) == len(self.NAMES), [f.file for f in found]


class TestASkippedFileIsVisible:
    """`_read_text_safe` returned None and the caller `continue`d with no
    error channel. Its docstring justified that by citing a "zero-files
    sanity check" in the caller — THERE IS NO SUCH CHECK. Naming a guarantee
    nobody implemented is worse than naming none: it stops the next reader
    looking."""

    def test_an_unreadable_file_is_collected_not_dropped(self, tmp_path):
        from rite_ai.config.models import ScanPattern
        from rite_ai.gate.pattern_scan import scan_content

        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "binary.bin").write_bytes(b"ZZ-FIXTURE-MARKER-ZZ\x00\x01")

        unreadable: list[str] = []
        scan_content(
            repo,
            ["binary.bin", "gone.py"],
            [
                ScanPattern(
                    type="regex", pattern=r"ZZ-FIXTURE-MARKER-ZZ", description="d"
                )
            ],
            source="t",
            unreadable=unreadable,
        )

        assert sorted(unreadable) == ["binary.bin", "gone.py"]

    def test_the_report_does_not_count_them_as_scanned(self):
        """The count asserting the scan happened is what made this
        invisible. Fixing the parsing and leaving the count would have kept
        the trap for the next encoding."""
        from rite_ai.gate.gate import GateReport, format_report

        text = format_report(GateReport(files_scanned=10, unreadable_files=["a", "b"]))

        assert "scanned 8 tracked file(s)" in text
        assert "could NOT be read" in text
        assert "not the same as clean" in text


class TestAnUnprintableNameIsStillUsable:
    """The half the `-z` fix missed, found in review: a corpus of `café.py`
    and `naïve/ünï.py` is all valid UTF-8, so it exercises NUL-splitting and
    never touches `surrogateescape`.

    A genuinely non-UTF-8 name (ext4 permits them, APFS refuses, so this is
    unreachable on macOS) opens correctly via the surrogate string and then
    RAISES at every print and encode — turning a silent skip into a crash.
    """

    A = b"caf\xe9.py".decode("utf-8", "surrogateescape")
    B = b"caf\xe8.py".decode("utf-8", "surrogateescape")

    def test_it_is_printable(self):
        from rite_ai.gate.pattern_scan import display_path

        display_path(self.A).encode("utf-8")  # raises if unfixed

    def test_two_different_files_do_not_collapse(self):
        """The trap in the obvious fix. Both render to `caf?.py` under
        `errors="replace"`, so a fingerprint built from the display form
        would put two files under one suppression — a suppression silently
        covering a file nobody approved it for."""
        from rite_ai.gate.pattern_scan import display_path

        assert display_path(self.A) != display_path(self.B)

    def test_an_ordinary_name_is_untouched(self):
        from rite_ai.gate.pattern_scan import display_path

        assert display_path("café.py") == "café.py"
        assert display_path("a/b.py") == "a/b.py"


# --- the pane promised redaction and leaked under a flag the guide names -----------


class TestRedactionSurvivesColour:
    """README and the guide both say `rite sandbox pane` replaces these
    values, and the guide recommends `--ansi`. Redaction allowed a line
    break between a value's characters but not an escape sequence, and the
    `export NAME=` match needed `export ` literally adjacent to the name,
    which a colour reset breaks."""

    TOK = "ghp_AAAABBBBCCCCDDDD"

    @pytest.mark.parametrize(
        "screen",
        [
            "export GITHUB_TOKEN='{t}'",
            "GITHUB_TOKEN={t}",
            "export GITHUB_TOKEN='{h}\n{r}'",
            "the token is {t}",
            "{h}\x1b[0m{r}",
            "\x1b[32mexport\x1b[0m GITHUB_TOKEN='{h}\x1b[0m{r}'",
            "export \x1b[1mGITHUB_TOKEN\x1b[0m='{t}'",
            "\x1b]0;window title\x07{t}",
        ],
    )
    def test_the_token_never_survives(self, screen):
        from rite_ai.sandbox import redact_secrets

        text = screen.format(t=self.TOK, h=self.TOK[:8], r=self.TOK[8:])
        out = redact_secrets(text, [self.TOK])

        assert self.TOK[:8] not in out.replace("[redacted]", "")

    def test_an_unknown_secret_in_an_export_is_still_redacted(self):
        """The first pass covers values rite does not know, which is the
        only protection for a credential yoloAI injects that rite did not."""
        from rite_ai.sandbox import redact_secrets

        assert "[redacted]" in redact_secrets("export SOME_TOKEN='zzzzzzzzzzzz'", [])

    def test_short_values_are_not_hunted(self):
        """`false` and `5` are not secrets, and replacing every occurrence
        would destroy the capture."""
        from rite_ai.sandbox import redact_secrets

        assert redact_secrets("count=5 flag=false", ["5", "false"]) == (
            "count=5 flag=false"
        )


# --- "could not check" is not "nothing to check" ----------------------------------


class TestAFailedRemoteCheckIsNotAbsence:
    """`_has_remote` read stdout without reading the exit code, so anything
    that stopped `git remote` producing output came back as "this module has
    no remote". `fetch` calls that "local-only, nothing to fetch — not an
    error", and `rite prepare` then reports **up to date** for a module it
    never contacted a remote about."""

    def test_the_check_itself_reports_that_it_could_not_look(self, tmp_path):
        """THE UNIT UNDER TEST, and the first version of this class did not
        assert it.

        It asserted `fetch()` returned a GitError for a broken repository —
        which it does whether or not this fix is present, because `git
        fetch` then fails on its own. The test passed for a reason unrelated
        to the defect, and a revert-check proved it: with the fix removed it
        stayed GREEN.

        `_has_remote` is where the three answers have to exist, so that is
        what gets asserted.
        """
        from rite_ai.workspace.git_ops import GitError, _has_remote

        not_a_repo = tmp_path / "nope"
        not_a_repo.mkdir()

        assert isinstance(_has_remote(not_a_repo, "origin"), GitError)

    def test_fetch_propagates_it_rather_than_calling_it_local_only(self, tmp_path):
        """The consequence: `fetch` must not answer "nothing to fetch — not
        an error", which is what `rite prepare` renders as **up to date**."""
        from rite_ai.workspace.git_ops import GitError, fetch

        not_a_repo = tmp_path / "nope"
        not_a_repo.mkdir()

        result = fetch(not_a_repo)
        assert isinstance(result, GitError)
        assert result is not None, "None here means 'local-only, nothing to do'"

    def test_a_genuinely_local_repository_is_still_not_an_error(self, tmp_path):
        """The fix must not turn every local-only module into a failure —
        that is the case the original behaviour existed for."""
        from rite_ai.workspace.git_ops import fetch

        local = tmp_path / "local"
        local.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=local, check=True)

        assert fetch(local) is None

    def test_a_remote_under_another_name_is_absent_not_broken(self, tmp_path):
        from rite_ai.workspace.git_ops import _has_remote

        repo = tmp_path / "named"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "remote", "add", "upstream", "https://example.invalid/x.git"],
            cwd=repo,
            check=True,
        )

        assert _has_remote(repo, "origin") is False
        assert _has_remote(repo, "upstream") is True


# --- destroying work needs two checks, not one ------------------------------------


class TestDestroyKeepsTwoIndependentGuards:
    """`--abandon-unapplied` was passed unconditionally, switching off
    yoloAI's own refusal on the ORDINARY path and leaving rite's guard alone
    — and that guard answers "nothing at risk" whenever it cannot look,
    because it tests a hard-coded directory the code itself calls "yoloAI's,
    not an interface"."""

    def test_a_library_call_without_a_root_refuses(self):
        """`destroy_worker(worker, root=None)` skipped the guard entirely."""
        from rite_ai.sandbox import _work_only_in_sandbox

        assert "without the project root" in _work_only_in_sandbox("n", "w", None)

    def test_the_unforced_path_leaves_yoloais_guard_armed(self, monkeypatch):
        from unittest.mock import MagicMock, patch

        from rite_ai.sandbox import destroy_worker

        with (
            patch("rite_ai.sandbox.shutil.which", return_value="/bin/yoloai"),
            patch("rite_ai.sandbox.subprocess.run") as run,
            tempfile.TemporaryDirectory() as tmp,
        ):
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            destroy_worker("alpha", root=tmp, force=False)
            assert "--abandon-unapplied" not in run.call_args[0][0]

            run.reset_mock()
            destroy_worker("alpha", root=tmp, force=True)
            assert "--abandon-unapplied" in run.call_args[0][0]


# --- the fix that reintroduced the defect it was written to close -----------------


class TestBlameSurvivesEveryPathSpelling:
    """ROUND 2 FOUND THIS IN THE ROUND 1 FIX.

    `Finding.file` serves three consumers with conflicting needs — printing
    (must encode), suppression fingerprints (must be stable and distinct),
    and blame, which compares against `files_touched_by`. Routing findings
    through `display_path` satisfied the first two and broke the third: the
    display form never equals the raw path `git log` reports, so a secret
    THIS PUSH ADDED was demoted to "already in the repository, NOT from this
    push". That is the exact sentence the surrounding work exists to
    prevent, reintroduced by the fix for it.

    And gitleaks — the PRIMARY detector — was never touched at all. It
    decodes its own JSON with `errors="replace"`, so it reports a third
    spelling again.
    """

    RAW = b"caf\xe9.py".decode("utf-8", "surrogateescape")

    def _finding(self, path: str, source: str):
        from rite_ai.gate.findings import Finding

        return Finding(
            rule_id="r",
            description="d",
            file=path,
            line=1,
            commit=None,
            match_preview="p",
            digest="abc",
            source=source,
        )

    def test_rites_own_finding_is_blamed_on_the_push(self):
        from rite_ai.gate.pattern_scan import blame_keys, display_path

        touched = blame_keys(self.RAW)
        f = self._finding(display_path(self.RAW), "rite-pattern")

        assert f.file in touched, "demoted to pre-existing"

    def test_gitleaks_finding_is_blamed_on_the_push(self):
        """Its path has already lost the bytes by the time rite sees it."""
        from rite_ai.gate.pattern_scan import blame_keys

        touched = blame_keys(self.RAW)
        replaced = self.RAW.encode("utf-8", "surrogateescape").decode(
            "utf-8", "replace"
        )
        f = self._finding(replaced, "gitleaks")

        assert f.file in touched, "demoted to pre-existing"

    def test_an_ordinary_path_needs_no_special_case(self):
        from rite_ai.gate.pattern_scan import blame_keys

        assert "src/app.py" in blame_keys("src/app.py")

    def test_the_fingerprint_can_be_written_to_the_suppression_file(self):
        """It is encoded to UTF-8 on the way to `.rite/gitleaksignore`, so a
        surrogate in it raises at the moment someone tries to suppress."""
        from rite_ai.gate.pattern_scan import display_path

        self._finding(display_path(self.RAW), "rite-pattern").fingerprint.encode(
            "utf-8"
        )


class TestTheKbRuleCanReportItself:
    """`scan_kb_cross_reference` embedded raw paths in both `file` and the
    description, so `format_report` raised `UnicodeEncodeError` at print —
    the "silent skip becomes a crash" outcome the display fix claimed to
    have avoided, still live one function below it."""

    def test_a_kb_finding_prints(self, tmp_path):
        from rite_ai.gate.findings import Finding
        from rite_ai.gate.gate import GateReport, format_report
        from rite_ai.gate.pattern_scan import display_path

        raw = b"kb/caf\xe9.md".decode("utf-8", "surrogateescape")
        report = GateReport(
            findings=[
                Finding(
                    rule_id="kb",
                    description=f"{display_path(raw)}:1 — leaked",
                    file=display_path(raw),
                    line=1,
                    commit=None,
                    match_preview="p",
                    digest="d",
                    source="rite-kb",
                )
            ]
        )

        format_report(report).encode("utf-8")  # raises if unfixed


# --- "never overwrites your edits" was false for one section ----------------------


class TestTheSpecSectionIsDerivedAndSaysSo:
    """`README.md` promised rite "never overwrites your edits", and `##
    Project spec` was rewritten on every `rite update` regardless — so prose
    added there was destroyed with no diff and no prompt.

    THE DECISION WAS NOT TO DETECT EDITS. An attempt at that broke the
    upgrade delivery the refresh exists for, because an older rite's output
    is textually indistinguishable from a human's edit. Instead the section
    is DERIVED — regenerated unconditionally — and authored notes get a file
    of their own that refresh never writes. The promise becomes
    unconditional because there is nothing of the user's in the derived
    half.
    """

    def _project(self, tmp_path: Path):
        from rite_ai.cli.init.claude_gen import GENERATED_MARKER, _spec_section
        from rite_ai.config.models import ProjectConfig, SpecConfig

        cfg = ProjectConfig(spec=SpecConfig(paths=["SPEC.md"]))
        root = tmp_path / "proj"
        (root / ".rite").mkdir(parents=True)
        (root / "CLAUDE.md").write_text(
            f"{GENERATED_MARKER}\n\n{_spec_section(cfg)}\n\n## Modules\n"
        )
        return root, cfg

    def test_the_generated_section_names_where_edits_survive(self):
        """A derived section is only honest if it tells the reader where to
        put what it will not keep."""
        from rite_ai.cli.init.claude_gen import _spec_section
        from rite_ai.config.models import ProjectConfig, SpecConfig

        text = _spec_section(ProjectConfig(spec=SpecConfig(paths=["SPEC.md"])))

        assert ".rite/spec-notes.md" in text
        assert "regenerated" in text

    def test_it_regenerates_unconditionally(self, tmp_path):
        """Including over an edit — that is what derived means, and the
        earlier attempt to make it conditional blocked upgrades."""
        from rite_ai.config.models import ProjectConfig, SpecConfig
        from rite_ai.project_spec import refresh_spec_sections

        root, _ = self._project(tmp_path)
        f = root / "CLAUDE.md"
        f.write_text(f.read_text().replace("## Project spec", "## Project spec\n\nX."))

        result = refresh_spec_sections(
            root, ProjectConfig(spec=SpecConfig(paths=["OTHER.md"]))
        )

        assert result.updated == ["CLAUDE.md"]
        assert "OTHER.md" in f.read_text()

    def test_the_notes_file_is_never_written_by_refresh(self, tmp_path):
        """The whole basis of the promise: refresh does not know how to
        write this file, so it cannot destroy it."""
        from rite_ai.config.models import ProjectConfig, SpecConfig
        from rite_ai.project_spec import refresh_spec_sections

        root, _ = self._project(tmp_path)
        notes = root / ".rite" / "spec-notes.md"
        notes.write_text("# my own notes\n\nwhy the spec says what it says\n")

        refresh_spec_sections(root, ProjectConfig(spec=SpecConfig(paths=["OTHER.md"])))

        assert notes.read_text().startswith("# my own notes")


class TestTheDisplayTagCannotBeForged:
    """`display_path` appended ` [bytes:<digest>]` only to names it could not
    encode — so a file literally NAMED `caf?.py [bytes:34e37bc1]` is valid
    UTF-8, passed through untouched, and rendered byte-identical to the
    undecodable original. Fingerprints embed this string, so one suppression
    would have covered both, and the digest is computable rather than
    guessable."""

    def test_a_decoy_does_not_collide_with_the_real_thing(self):
        from rite_ai.gate.pattern_scan import display_path

        real = display_path(b"caf\xe9.py".decode("utf-8", "surrogateescape"))
        decoy = display_path(real)

        assert real != decoy

    def test_an_ordinary_name_is_still_untouched(self):
        from rite_ai.gate.pattern_scan import display_path

        assert display_path("src/app.py") == "src/app.py"
