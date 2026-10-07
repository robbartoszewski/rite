"""Orchestration: run every scan source, merge, suppress, report.

This is the module's public entry point. Everything else in `rite_ai.gate` is
plumbing this function calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from rite_ai.config.models import DEFAULT_GITLEAKS_CONFIG, ProjectConfig
from rite_ai.config.parse import parse_config
from rite_ai.gate import gitleaks_runner, pattern_scan
from rite_ai.gate import suppression as supp_mod
from rite_ai.gate.builtin_rules import BUILTIN_PATH_PATTERNS
from rite_ai.gate.findings import (
    COMMIT_MESSAGE_FILE,
    Finding,
    publishable_scope,
    rev_range_args,
)
from rite_ai.gate.suppression import DEFAULT_SUPPRESSION_PATH, Suppression

# TWO QUESTIONS, TWO ANSWERS. `outcome` says what was found; the exit code
# says whether to proceed. They were one integer, and that is why the gate
# blocked pushes its own documentation promised not to block.
#
# Exit code contract — this is the interface the CLI wraps.
#   0  proceed: clean, OR warnings only (unless --strict)
#   1  warn:    warnings, and the caller asked for --strict
#   2  fail:    at least one unsuppressed finding — blocks publish
#   3  error:   the gate could not run at all (gitleaks missing, git failure,
#               malformed config/suppression file) — NEVER conflated with a
#               clean result. This code exists specifically so a broken gate
#               cannot report exit 0.
#
# A warning means "something here nobody has looked at": a finding that
# predates this push, or a suppression entry that no longer matches anything.
# Neither is a reason to stop someone pushing unrelated work, and both are a
# reason to tell them. `--strict` is for callers who want it to stop them.
EXIT_CLEAN = 0
EXIT_WARN = 1
EXIT_FAIL = 2
EXIT_ERROR = 3


@dataclass
class GateReport:
    findings: list[Finding] = field(default_factory=list)  # blocking
    suppressed: list[Finding] = field(default_factory=list)
    # Real findings in files this push does not touch — reported, not
    # blocking, and only ever populated for the pre-push hook. See
    # `_split_off_pre_existing`.
    pre_existing: list[Finding] = field(default_factory=list)
    stale_suppressions: list[Suppression] = field(default_factory=list)
    # Every entry that parsed, stale or not — so a report can say how many
    # findings one of them is covering.
    suppressions: list[Suppression] = field(default_factory=list)
    files_scanned: int = 0
    # Paths `git ls-files` listed that could NOT be opened. Reported
    # beside `files_scanned`, because "scanned 1 file(s)" about a file
    # nothing could read is the gate's worst possible sentence.
    unreadable_files: list[str] = field(default_factory=list)
    # How many commits the history scan actually walked. 🔴 SCRUM-63: this
    # field existed, was never once assigned, and was read as evidence that
    # the history scan was hollow — a gate reporting `commits_scanned=0`
    # beside "clean" for every run. The scan was real; the number was a
    # default nobody had wired. An observable the gate publishes and never
    # computes is worse than no observable, because it is believed.
    #
    # `None` when it could not be counted, which is NOT zero: zero is the
    # real and legitimate answer for a push whose range is already published.
    commits_scanned: int | None = None
    # Which gitleaks ruleset this run applied, in one clause. Always said —
    # see `_resolve_ruleset`.
    ruleset_note: str = ""
    # Where a suppression has to be WRITTEN, when that is not the tree being
    # scanned. `HOW_TO_SUPPRESS` names a rootless `.rite/gitleaksignore`, and
    # the hook and `python -m rite_ai.gate` print it through `format_report`,
    # which had no way to know the two roots differed — so in a split-root
    # layout the reader writes the entry into the repository they are
    # standing in and the gate goes on ignoring it.
    config_root_note: str = ""
    errors: list[str] = field(default_factory=list)
    # What the sources that COULD run found, when another source could not.
    # Reported so a blocked run is still worth something, and deliberately
    # kept out of `findings` and out of `exit_code`: a run missing a source
    # is EXIT_ERROR whatever these say, and they are not a verdict on the
    # tree. Empty here never means clean.
    partial_findings: list[Finding] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        """WHAT THE GATE FOUND — independent of whether anything should stop.

        One integer used to answer two different questions: "did the gate
        find something" and "should this push proceed". That conflation WAS
        the bug. `EXIT_WARN` was 1, a pre-push hook exiting non-zero aborts
        the push, and so the documented promise that a pre-existing finding
        "is surfaced but does not block" blocked every push — as did a single
        stale suppression entry, until somebody deleted it.

        Separating them makes the documented sentence true rather than
        aspirational: this property says what was found, `exit_code` says
        whether to proceed, and neither has to lie to satisfy the other.
        """
        if self.errors or self.partial_findings:
            # `partial_findings` is only ever populated beside an error. If
            # it is ever populated without one, the report is malformed and
            # the honest answer is still "this did not complete" — the one
            # thing this contract exists to stop is findings existing
            # somewhere the exit code cannot see.
            return "error"
        if self.findings:
            return "fail"
        if self.stale_suppressions or self.pre_existing:
            return "warn"
        return "clean"

    def exit_code_for(self, *, strict: bool = False) -> int:
        """SHOULD THIS PROCEED — 0 for yes.

        `strict` promotes a warning to a blocking result, for callers that
        want it: CI, typically, where "there is something here a human has
        not looked at" is worth failing the build over. The pre-push hook
        deliberately does not pass it, because blocking a push is the
        behaviour its own documentation promises not to have.
        """
        outcome = self.outcome
        if outcome == "error":
            return EXIT_ERROR
        if outcome == "fail":
            return EXIT_FAIL
        if outcome == "warn":
            return EXIT_WARN if strict else EXIT_CLEAN
        return EXIT_CLEAN

    @property
    def exit_code(self) -> int:
        return self.exit_code_for()

    @property
    def status(self) -> str:
        return self.outcome


def run_gate(
    root: Path,
    rev_range: str | None = None,
    config: ProjectConfig | None = None,
    config_root: Path | None = None,
) -> GateReport:
    """Run the full publish gate, and never raise.

    `EXIT_ERROR` exists "specifically so a broken gate cannot report exit
    0", and every failure this module anticipates is routed into it — but
    an exception nobody anticipated went straight past that and out of
    the process, where Python's exit code is 1. In this module's own
    vocabulary 1 is WARN: "no blocking findings, but stale suppression
    entries exist". A caller reading the exit code was told the gate had
    run and found nothing blocking, by a gate that had crashed.

    Found by a commit message containing a Latin-1 byte — `git show -s
    --format=%B` decoded with `text=True` and no `errors=`, raising
    `UnicodeDecodeError` out of `scan_commit_messages`, out of here, and
    out of `rite publish check`. That specific decode is fixed at every
    call site; this catch is for the next one, because the guarantee
    worth having is structural rather than a list of anticipated
    exceptions.
    """
    try:
        return _run_gate(
            root, rev_range=rev_range, config=config, config_root=config_root
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        return GateReport(
            errors=[
                f"the gate crashed and cannot vouch for this tree: "
                f"{type(exc).__name__}: {exc}"
            ]
        )


def _run_gate(
    root: Path,
    rev_range: str | None = None,
    config: ProjectConfig | None = None,
    config_root: Path | None = None,
) -> GateReport:
    """The gate itself. Call `run_gate`, which cannot raise.

    `rev_range` is a `git log`-style revision range (e.g.
    `"<remote-sha>..<local-sha>"`). When given, gitleaks' history scan and
    the commit-message relay are both scoped to it — this is what makes the
    pre-push hook fast regardless of total repo history. When omitted, the
    scan covers full history, which is what `rite publish check` and CI want:
    "safe to publish" means "safe for the whole history to become public",
    not just the tip.

    `config` is normally loaded from `.rite/config.yaml`; passed explicitly
    mainly for testing. A project with no config.yaml yet still gets the
    built-in path rules — those are never optional (SPEC §11.3).
    """
    errors: list[str] = []

    # WHERE THE RULES LIVE, which is not always the tree being scanned. A
    # `rite prepare` project keeps its `.rite/` at the project root with the
    # repositories beneath it, so resolving both through one path either
    # scans a directory git cannot answer about (🔴 SCRUM-60) or silently
    # drops every suppression the project declared. Defaults to `root`,
    # which is every other layout and every existing caller.
    config_root = root if config_root is None else config_root
    try:
        split_roots = root.resolve() != config_root.resolve()
    except OSError:
        split_roots = False
    config_root_note = (
        f"rules and suppressions read from {config_root} (not {root})"
        if split_roots
        else ""
    )

    binary = gitleaks_runner.find_gitleaks_binary()
    if binary is None:
        # Still an error, and still EXIT_ERROR: rite does not reimplement
        # secret detection, so without gitleaks it cannot say this tree is
        # safe, and saying so would be the one failure this exit code exists
        # to prevent. But rite's OWN rules — the built-in hardcoded-path
        # rules §11.3 calls never optional, the user's declared patterns, and
        # the kb/ cross-reference — need no gitleaks at all. Returning here
        # ran none of them, so someone on a machine without gitleaks got
        # nothing: not a partial answer, no answer, and then a second round
        # of failures after they installed it. They run, and what they find
        # is reported under the error.
        errors.append(
            "gitleaks is not installed or not on PATH. rite's publish "
            "gate is built on gitleaks and does not reimplement secret "
            "detection, so it cannot run without it. " + gitleaks_runner.HOW_TO_INSTALL
        )

    if config is None:
        loaded = parse_config(config_root / ".rite" / "config.yaml")
        if not isinstance(loaded, ProjectConfig):
            return GateReport(
                errors=errors + [f"invalid .rite/config.yaml: {loaded.message}"]
            )
        config = loaded

    tracked = pattern_scan.list_tracked_files(root)
    if isinstance(tracked, pattern_scan.ScanError):
        if not _looks_like_a_repo(root):
            # 🔴 SCRUM-60's other half. The gate cannot scan a directory
            # that is not a repository and should not pretend otherwise —
            # but `'git ls-files' failed: fatal: not a git repository` is
            # git talking to git, and it leaves the reader of a `rite`
            # command with nowhere to go. In the `rite prepare` layout this
            # is exactly what `rite publish check` says at the project root,
            # where the repositories are one level down.
            return GateReport(
                errors=errors
                + [
                    f"{root} is not a git repository, and the publish gate "
                    "scans repositories — it lists tracked files and walks "
                    "history. If this is a rite project root, the "
                    "repositories are the modules beneath it: run the gate "
                    "inside one of them."
                ]
            )
        # `errors +` and not `[...]`: a missing gitleaks is recorded above,
        # and dropping it here left the user reading about the second problem
        # with no mention of the first.
        return GateReport(errors=errors + [tracked.message])

    user_config_path, ruleset_note, ruleset_error = _resolve_ruleset(
        config_root, config
    )
    if ruleset_error:
        errors.append(ruleset_error)

    # 🔴 SCRUM-17. gitleaks applies a `.gitleaksignore` at the root of what
    # it scans by itself, and nothing turns that off: measured on 8.30.1, it
    # hid a token even with `--gitleaks-ignore-path` pointed elsewhere. Every
    # suppression rite honours carries a reason (`.rite/gitleaksignore`); one
    # in that file carries none, and rite cannot see which findings it hid.
    # So the gate cannot vouch for the scan, and says how to make it able to.
    stray_ignore = root / ".gitleaksignore"
    if stray_ignore.exists():
        errors.append(
            f"{stray_ignore} exists, and gitleaks applies it on its own, so "
            "any finding it lists is hidden without a reason and rite cannot "
            "tell which. Move each entry to .rite/gitleaksignore with the "
            "reason it is safe (`rite publish check` prints the fingerprint "
            "and the format), then remove .gitleaksignore."
        )

    commits_scanned = _count_commits(root, rev_range)

    all_findings: list[Finding] = []

    # `ruleset_error` and not just `binary`: the error says the gate "will
    # not quietly fall back to gitleaks' default rules", and then running
    # them anyway printed findings produced by exactly those rules under
    # exactly that sentence. The exit code was never wrong — it is
    # EXIT_ERROR either way — but a message contradicted by the output
    # beneath it teaches people to skim both.
    if binary is not None and not ruleset_error:
        # 1. gitleaks: maintained secret-detection ruleset over full git
        #    history (or the push range). Current + historical file content.
        # The SAME ref scope as rite's own two passes. Left to itself
        # gitleaks walks `--all`, which reaches `refs/stash` and `refs/notes`
        # — local by definition, on their way nowhere. Measured: a stashed
        # file holding a token produced exit 2 with a fingerprint keyed to the
        # stash commit's sha, so the block could not even be suppressed (the
        # sha changes every time the stash is rebuilt), and on this project
        # one stash stack is shared by every worktree.
        #
        # Narrowing a secret scan deserves suspicion, so: a stash cannot be
        # pushed. The gate's question is whether this tree is safe to PUBLISH,
        # and nothing in `refs/stash` is going anywhere. Everything that can
        # be published is still covered, and now all three passes — content,
        # commit messages, and the count that reports on them — cover exactly
        # the same commits, which they did not before.
        history = gitleaks_runner.scan_history(
            root,
            binary,
            config_path=user_config_path,
            log_opts=rev_range or " ".join(publishable_scope(root)),
        )
        if isinstance(history, gitleaks_runner.ScanError):
            errors.append(f"gitleaks history scan failed: {history.message}")
        else:
            all_findings.extend(history)

        # 2. gitleaks via the commit-message relay (see gitleaks_runner module
        #    docstring — gitleaks does not scan commit messages on its own).
        msg_scan = gitleaks_runner.scan_commit_messages(
            root, binary, config_path=user_config_path, rev_range=rev_range
        )
        if isinstance(msg_scan, gitleaks_runner.ScanError):
            errors.append(f"gitleaks commit-message scan failed: {msg_scan.message}")
        else:
            all_findings.extend(msg_scan)

    # 3. rite's own patterns: user-declared (config.yaml) + always-on built-in
    #    hardcoded-path rules. Applied to content, file names, and commit
    #    messages — always over the full current tree, regardless of
    #    `rev_range`: these are cheap regex scans, not git-history walks, so
    #    there's no speed reason to scope them, and scoping them would miss
    #    pre-existing issues a push doesn't touch.
    declared_patterns = [p for p in config.publish_gate.scan_patterns]
    all_patterns = declared_patterns + BUILTIN_PATH_PATTERNS

    unreadable: list[str] = []
    all_findings.extend(
        pattern_scan.scan_content(
            root, tracked, all_patterns, source="rite-pattern", unreadable=unreadable
        )
    )
    all_findings.extend(
        pattern_scan.scan_filenames(tracked, all_patterns, source="rite-path")
    )
    msg_pattern_scan = pattern_scan.scan_commit_messages(
        root, all_patterns, source="rite-pattern", rev_range=rev_range
    )
    if isinstance(msg_pattern_scan, pattern_scan.ScanError):
        errors.append(f"pattern commit-message scan failed: {msg_pattern_scan.message}")
    else:
        all_findings.extend(msg_pattern_scan)

    # 4. kb/ cross-reference — SPEC §8.7/§11.2: kb/ is scanned hardest.
    all_findings.extend(pattern_scan.scan_kb_cross_reference(root, tracked))

    if errors:
        # A partial scan is not a scan the gate can vouch for — every source
        # above must succeed, or this is EXIT_ERROR, never a possibly-clean
        # EXIT_CLEAN/EXIT_FAIL built from incomplete data. This is the
        # specific guard against "errored to stderr, stdout stayed empty,
        # read as clean."
        #
        # What the sources that DID run found still goes in the report, under
        # a field no exit code reads: throwing it away made a blocked run
        # worth nothing, and told the user about their real problems one
        # round at a time. Suppressions are applied when the file parses, so
        # this does not re-raise decisions someone already made; when it does
        # not parse, everything found is shown unfiltered rather than hidden.
        partial, suppressed, entries = _suppress_best_effort(
            config_root, _dedupe(all_findings)
        )
        return GateReport(
            errors=errors,
            files_scanned=len(tracked),
            unreadable_files=unreadable,
            commits_scanned=commits_scanned,
            ruleset_note=ruleset_note,
            config_root_note=config_root_note,
            partial_findings=partial,
            suppressed=suppressed,
            suppressions=entries,
        )

    merged = _dedupe(all_findings)

    suppression_path = config_root / DEFAULT_SUPPRESSION_PATH
    try:
        suppressions = supp_mod.parse(suppression_path)
    except Exception as exc:  # noqa: BLE001 - `parse` reads bytes it did not write
        suppressions = supp_mod.SuppressionError(
            f"{DEFAULT_SUPPRESSION_PATH} could not be read: {type(exc).__name__}: {exc}"
        )
    if isinstance(suppressions, supp_mod.SuppressionError):
        # Still EXIT_ERROR — nothing here can be suppressed, so nothing here
        # can be vouched for. But the findings go out under `partial_findings`
        # rather than into the bin: a typo'd suppression line used to hide
        # every real finding in the tree behind one parse error, which is the
        # same information loss the missing-gitleaks path had.
        return GateReport(
            errors=[suppressions.message],
            files_scanned=len(tracked),
            unreadable_files=unreadable,
            commits_scanned=commits_scanned,
            ruleset_note=ruleset_note,
            config_root_note=config_root_note,
            partial_findings=merged,
        )

    blocking, suppressed = supp_mod.apply(merged, suppressions)
    stale = supp_mod.find_stale(suppressions, merged)

    blocking, pre_existing = _split_off_pre_existing(root, rev_range, blocking)

    return GateReport(
        findings=blocking,
        suppressed=suppressed,
        pre_existing=pre_existing,
        stale_suppressions=stale,
        suppressions=suppressions,
        files_scanned=len(tracked),
        unreadable_files=unreadable,
        commits_scanned=commits_scanned,
        ruleset_note=ruleset_note,
        config_root_note=config_root_note,
    )


def _looks_like_a_repo(root: Path) -> bool:
    """Is `root` inside a git repository at all? Used only to choose which
    error to print, never to decide whether to scan."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return proc.returncode == 0


def _is_default_config(configured: str) -> bool:
    """Is this the default `gitleaks_config`, however it is spelt?

    Raw string equality decided this, so `./.rite/gitleaks.toml` — the same
    file, written the way a person writes a relative path — counted as a
    deliberately chosen config, and a project that had never chosen anything
    became EXIT_ERROR. The hook fails closed, so that is every push refused
    over a leading `./`.
    """
    return PurePosixPath(configured) == PurePosixPath(DEFAULT_GITLEAKS_CONFIG)


def _resolve_ruleset(root: Path, config: ProjectConfig) -> tuple[Path | None, str, str]:
    """`(config_path, note, error)` — which gitleaks ruleset this run applies.

    🔴 SCRUM-63. This was three lines with no third element: a configured
    `gitleaks_config` that does not exist set the path to None, gitleaks ran
    on its built-in defaults, and nothing anywhere said so. For the DEFAULT
    path that is correct and harmless — the defaults are a real ruleset, and
    a scan with them demonstrably catches planted secrets. For a path someone
    chose it is the hole: they point the gate at the rules they want enforced,
    the file is missing or misspelt, and the gate quietly enforces weaker ones
    and reports clean. Rules that vanish without a word are not rules.

    So: a missing NON-default config is EXIT_ERROR, and the default ruleset
    is never applied silently — `note` is returned on every path and the
    report prints it, so "clean" always arrives next to what it was clean
    against.
    """
    configured = config.publish_gate.gitleaks_config
    candidate = root / configured
    if candidate.exists():
        return candidate, f"gitleaks ruleset: {configured}", ""
    if not _is_default_config(configured):
        return (
            None,
            "",
            f"publish_gate.gitleaks_config names {configured}, and "
            f"{candidate} does not exist. The gate will not quietly fall back "
            "to gitleaks' default rules when it was pointed at different "
            "ones — create that file, or remove the setting to use the "
            "defaults deliberately.",
        )
    return None, "gitleaks ruleset: gitleaks' defaults (no " + configured + ")", ""


def _count_commits(root: Path, rev_range: str | None) -> int | None:
    """How many commits the history scan has in scope. None if git cannot say.

    🔴 SCRUM-63. Zero is a real answer and the one that matters: a pre-push
    range of `<sha> --not --remotes` for a branch already on the remote
    contains no commits, so the history scan walks nothing and the gate still
    prints "clean". That is not wrong — nothing new is being published — but
    unqualified it reads as "history was scanned and is clean", which is how
    a run that looked at no commits came to be offered as proof the scanner
    works. The count is reported so the sentence cannot be misread.

    It counts commits IN SCOPE, which slightly overstates what the CONTENT
    scan reads: gitleaks walks `git log -p`, which emits no diff for a merge
    commit, so a secret introduced only in a conflict resolution falls inside
    this number and outside that scan. The commit-MESSAGE passes do read
    merges. Said here rather than quietly corrected, because the number's
    whole purpose is to be the thing you can check.
    """
    import subprocess

    scope = rev_range_args(rev_range) if rev_range else publishable_scope(root)
    args = ["git", "rev-list", "--count", *scope]
    try:
        proc = subprocess.run(
            args, cwd=root, capture_output=True, text=True, errors="replace", timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """The same finding can legitimately surface twice (e.g. gitleaks' default
    ruleset run both standalone and via the user's extended config, if the
    user's config also sets useDefault).

    Keyed on the digest too: two DIFFERENT secrets reported at one
    file:line:rule (a key and its twin on the same line, say) are two findings
    and two decisions, and keying on position alone silently dropped the
    second before anything could suppress either.
    """
    deduped: dict[tuple[str, str], Finding] = {}
    for f in findings:
        deduped.setdefault((f.fingerprint, f.digest), f)
    return list(deduped.values())


def _suppress_best_effort(
    root: Path, findings: list[Finding]
) -> tuple[list[Finding], list[Finding], list[Suppression]]:
    """`findings` split into (blocking, suppressed) plus the entries applied.

    Best effort by design: this runs only on a path that is already going to
    be EXIT_ERROR, so anything wrong with the suppression file must not
    swallow the findings — every failure falls back to showing all of them.
    `parse` reads the file without decoding guarantees, so this catches rather
    than trusting it to return its error type.

    Stale entries are deliberately NOT computed here: a scan missing a source
    is missing findings, so entries covering those findings would be reported
    as matching nothing, and the fix for "stale" is deleting the line that is
    still doing its job.
    """
    try:
        suppressions = supp_mod.parse(root / DEFAULT_SUPPRESSION_PATH)
    except Exception:  # noqa: BLE001 - the fallback IS showing everything
        return findings, [], []
    if isinstance(suppressions, supp_mod.SuppressionError):
        return findings, [], []
    blocking, suppressed = supp_mod.apply(findings, suppressions)
    return blocking, suppressed, suppressions


def _split_off_pre_existing(
    root: Path, rev_range: str | None, blocking: list[Finding]
) -> tuple[list[Finding], list[Finding]]:
    """Separate what THIS push is responsible for from what was already
    in the repository.

    Only when `rev_range` is set — that is the pre-push hook. `rite
    publish check` passes no range and keeps blocking on everything,
    because that command is the audit: "is this repository safe to
    publish" really does mean all of it.

    The hook is a different question. Its job is to stop this push
    publishing a secret, and the decisive fact is that **blocking the
    push does not unpublish anything**: a finding in a file the push does
    not touch is already on the remote, and refusing unrelated work does
    not remove it. It stops the work and leaves the exposure.

    Measured on git's own repository, adopting rite on it: the first push
    of a one-commit branch produced 46 blocking findings, every one of
    them a hardcoded `/home/user/` path in git's test fixtures, none of
    them in a file the push touched. Each would have needed its own
    suppression entry with a reason before any work could be pushed.
    That is how a gate gets disabled.

    Conservative in both directions: a finding in a touched file blocks
    even if it was there before, and if the touched-file list cannot be
    computed at all, nothing is demoted.
    """
    if not rev_range or not blocking:
        return blocking, []
    touched = pattern_scan.files_touched_by(root, rev_range)
    if isinstance(touched, pattern_scan.ScanError):
        return blocking, []

    def _from_this_push(f: Finding) -> bool:
        if not f.file:
            return True
        if f.file == COMMIT_MESSAGE_FILE:
            # 🔴 A commit message is not a file, and matching it against a
            # touched-FILE set can only ever fail — so EVERY commit-message
            # finding was demoted, and the pre-push hook exited 0 over a
            # token in the message of the very commit being pushed.
            # Measured: `rite publish check` exit 2, the hook exit 0, the
            # push allowed, under the words "already in the repository, NOT
            # from this push".
            #
            # It is from this push by construction: both message scanners
            # are given the same `rev_range`, so in hook mode every message
            # finding came out of a commit the push would publish. There is
            # no demotion to make.
            return True
        return f.file in touched

    still_blocking = [f for f in blocking if _from_this_push(f)]
    pre_existing = [f for f in blocking if not _from_this_push(f)]
    return still_blocking, pre_existing


def _partial_lines(report: GateReport, limit: int = 10) -> list[str]:
    """The findings from the sources that completed.

    Says only that — the checks above it are whichever ones failed, which is
    not always the secret detector. Wording this as "the checks that did not
    run are the ones that detect secrets" printed a gitleaks-confirmed token
    under a sentence telling the reader to discount it.
    """
    if not report.partial_findings and not report.suppressed:
        return []
    lines: list[str] = []
    n = len(report.partial_findings)
    if n:
        lines.append(
            f"\n{n} finding(s) from the checks that DID complete — not a verdict "
            "on this tree, which has not been fully scanned:"
        )
        for f in report.partial_findings[:limit]:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"  [{f.rule_id}] {loc} — {f.description}")
            lines.append(f"    fingerprint: {f.content_fingerprint or f.fingerprint}")
        if n > limit:
            lines.append(f"  … and {n - limit} more")
    if report.suppressed:
        # The complete path says this further down; the error branch returns
        # before it, so without this a filtered list reads as the whole one —
        # and when everything found was suppressed, the run said nothing at
        # all about what it had seen.
        lines.append(
            f"  ({len(report.suppressed)} finding(s) matched an entry in "
            f"{DEFAULT_SUPPRESSION_PATH} and are not listed)"
        )
    if n:
        lines.append(supp_mod.HOW_TO_SUPPRESS)
    return lines


def scanned_line(report: GateReport) -> str:
    """The one sentence every caller says about coverage: "scanned N tracked
    file(s) and M commit(s) …".

    Shared because `rite publish check` builds its own output and does not
    call `format_report`, which is how 🔴 SCRUM-63's fix nearly landed in a
    formatter the reported command never runs."""
    scanned = report.files_scanned - len(report.unreadable_files)
    return f"scanned {scanned} tracked file(s)" + _commit_clause(report)


def _commit_clause(report: GateReport) -> str:
    """What the HISTORY half of the scan covered, said beside the file half.

    🔴 SCRUM-63. "scanned N tracked file(s) … clean" described the working
    tree and said nothing about commits, so a run over an empty revision
    range — every pre-push of an already-published branch — produced a
    confident "clean" that had walked no history at all. Reading it as proof
    the history scan worked is the obvious mistake, and it was made.
    """
    n = report.commits_scanned
    if report.errors or n is None:
        # A count printed above "gate: ERROR" is the SCRUM-63 defect
        # inverted: the number was honest about the commits git listed and
        # said nothing about the scan that did not run over them. With
        # gitleaks off PATH this read "scanned 2 tracked file(s) and 2
        # commit(s) of history" directly above a report that had scanned
        # for no secrets at all.
        return ""
    if n == 0:
        return (
            " and 0 commit(s) — NO history was in range, so nothing below is "
            "a statement about this repository's commit history"
        )
    return f" and {n} commit(s) of history"


def brief(report: GateReport, limit: int = 10) -> list[str]:
    """The gate's findings in a few lines, for a NOTE to a Manager (SCRUM-59):
    a refused delivery, or `rite request gate`. What and where, never the
    match itself: a note is mail, and a real secret must not ride in it.
    "" findings and no error is said as clean, never as nothing."""
    if report.errors:
        return [f"could not complete: {e}" for e in report.errors[:limit]]
    lines = []
    for f in report.findings[:limit]:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        commit = f" ({f.commit[:8]})" if f.commit else ""
        lines.append(f"[{f.rule_id}] {loc}{commit} — {f.description}")
    if len(report.findings) > limit:
        lines.append(f"… and {len(report.findings) - limit} more")
    if not lines:
        lines.append("no blocking findings")
    return lines


def format_report(report: GateReport) -> str:
    """Human-readable summary — used by both the standalone `__main__` CLI
    and (once wired) the `rite publish check` command."""
    lines: list[str] = []
    if report.errors or report.partial_findings:
        lines.append("ERROR — the gate could not complete:")
        for e in report.errors:
            lines.append(f"  {e}")
        if report.config_root_note:
            lines.append(report.config_root_note)
        lines.extend(_partial_lines(report))
        return "\n".join(lines)

    lines.append(scanned_line(report))
    if report.ruleset_note:
        lines.append(report.ruleset_note)
    if report.config_root_note:
        lines.append(report.config_root_note)
    if report.unreadable_files:
        # NOT a warning buried below the verdict. A gate that could not read
        # a file has not cleared it, and the number that used to be printed
        # counted those files as scanned — so "clean, scanned 1 file(s)" was
        # said about a file nothing had opened.
        shown = ", ".join(sorted(report.unreadable_files)[:5])
        more = (
            f" (+{len(report.unreadable_files) - 5} more)"
            if len(report.unreadable_files) > 5
            else ""
        )
        lines.append(
            f"⚠ {len(report.unreadable_files)} tracked file(s) could NOT be "
            f"read and were not scanned — this is not the same as clean: "
            f"{shown}{more}"
        )
    if report.findings:
        lines.append(f"\n{len(report.findings)} finding(s) — BLOCKING:")
        for f in report.findings:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            commit = f" ({f.commit[:8]})" if f.commit else ""
            lines.append(f"  [{f.rule_id}] {loc}{commit} — {f.description}")
            lines.append(f"    match: {f.match_preview}")
            # The content form when there is one: it is what a suppression
            # should be pinned to, and printing the line form beside it just
            # offers the reader the one that drifts.
            lines.append(f"    fingerprint: {f.content_fingerprint or f.fingerprint}")
        lines.append(supp_mod.HOW_TO_SUPPRESS)
    if report.pre_existing:
        n = len(report.pre_existing)
        lines.append(
            f"\n{n} finding(s) already in the repository, NOT from this push "
            "— not blocking:"
        )
        for f in report.pre_existing[:10]:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            lines.append(f"  [{f.rule_id}] {loc} — {f.description}")
        if n > 10:
            lines.append(f"  … and {n - 10} more")
        lines.append(
            "  Blocking this push would not unpublish them. Run `rite publish "
            "check` to see all of them and decide."
        )
    if report.stale_suppressions:
        n = len(report.stale_suppressions)
        lines.append(f"\n{n} stale suppression(s) — WARNING:")
        for s in report.stale_suppressions:
            lines.append(
                f"  {s.fingerprint} (line {s.line_no} of {DEFAULT_SUPPRESSION_PATH}) "
                f"— no longer matches any finding, reason was: {s.reason}"
            )
            # Why an entry went stale, when that can be said: the commonest
            # cause by far is an edit above a suppressed line moving it, so
            # the entry goes stale AND the finding it covered turns up in the
            # blocking list above. Both facts were printed; nothing said they
            # were the same finding.
            hint = supp_mod.stale_hint(s, report.findings)
            if hint:
                lines.append(hint)
    if report.suppressed:
        lines.append(f"\n{len(report.suppressed)} finding(s) suppressed (with reason)")
        for s, n in supp_mod.covering_more_than_one(
            report.suppressed, report.suppressions
        ):
            lines.append(f"  one entry covers {n} of them: {s.fingerprint}")
    if report.outcome == "clean":
        lines.append("\nclean")
    return "\n".join(lines)


def ruleset(
    root: Path, config: ProjectConfig, scan_root: Path | None = None
) -> tuple[str, str]:
    """`(line, problem)`: which rules the gate applies here, for `rite doctor`.

    🔴 SCRUM-17. Doctor called the gate "active" and said nothing of WHAT it
    scans for, while the config named a `.rite/gitleaks.toml` that does not
    exist. The missing file was never the gap (gitleaks' defaults apply,
    now passed explicitly); the gap was that the effective rules could come
    from outside rite. This names them, and `problem` is non-empty only when
    the scan cannot be vouched for."""
    import tomllib

    declared = len(config.publish_gate.scan_patterns)
    extra = (
        f", plus {declared} pattern(s) from config.yaml" if declared else ""
    ) + f", plus rite's {len(BUILTIN_PATH_PATTERNS)} built-in path rules"
    # gitleaks applies a `.gitleaksignore` at the root of what it SCANS, so
    # this question belongs to the scanned tree and the `gitleaks_config`
    # question below belongs to the project. They were both asked of one
    # path: a stray file in the module repository then failed every push
    # closed while doctor reported the rules healthy.
    stray = (scan_root or root) / ".gitleaksignore"
    if stray.exists():
        return (
            f"publish gate rules: CANNOT VOUCH — {stray} exists, and gitleaks "
            "applies it on its own, hiding what it lists with no reason. Move "
            "its entries to .rite/gitleaksignore with reasons, then remove it",
            f"{stray} would hide findings without a reason",
        )
    configured = config.publish_gate.gitleaks_config
    named = root / configured
    if not named.exists():
        if not _is_default_config(configured):
            # 🔴 SCRUM-63. Doctor said "optional" about every absent config,
            # including one someone chose — the same sentence over a healthy
            # project and over a gate enforcing rules nobody asked for. The
            # gate now refuses that case outright, and doctor has to agree
            # with it or one of the two instruments is lying.
            return (
                f"publish gate rules: CANNOT VOUCH — config.yaml names "
                f"{configured} and {named} does not exist, so the gate "
                "refuses to run rather than fall back to gitleaks' "
                f"defaults{extra}",
                f"{named} does not exist",
            )
        return (
            "publish gate rules: gitleaks' default ruleset, passed by rite "
            f"explicitly (no {configured}, which is optional){extra}",
            "",
        )
    try:
        parsed = tomllib.loads(named.read_text())
    except (OSError, ValueError) as e:
        return (
            f"publish gate rules: {named} cannot be read ({e}), so gitleaks "
            "will refuse to scan",
            f"{named} cannot be read",
        )
    extends = (parsed.get("extend") or {}).get("useDefault") is True
    return (
        (
            f"publish gate rules: {config.publish_gate.gitleaks_config}, "
            f"extending gitleaks' default ruleset{extra}"
        )
        if extends
        else (
            f"publish gate rules: {config.publish_gate.gitleaks_config} REPLACES "
            "gitleaks' default ruleset (it has no `[extend] useDefault = true`), "
            f"so only its own rules run{extra}"
        ),
        "",
    )
