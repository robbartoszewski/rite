"""Orchestration: run every scan source, merge, suppress, report.

This is the module's public entry point. Everything else in `rite_ai.gate` is
plumbing this function calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.config.models import ProjectConfig
from rite_ai.config.parse import parse_config
from rite_ai.gate import gitleaks_runner, pattern_scan
from rite_ai.gate import suppression as supp_mod
from rite_ai.gate.builtin_rules import BUILTIN_PATH_PATTERNS
from rite_ai.gate.findings import Finding
from rite_ai.gate.suppression import DEFAULT_SUPPRESSION_PATH, Suppression

# Exit code contract — this is the interface the CLI wraps.
#   0  clean:  no findings, no stale suppressions
#   1  warn:   no blocking findings, but stale suppression entries exist
#   2  fail:   at least one unsuppressed finding — blocks publish
#   3  error:  the gate could not run at all (gitleaks missing, git failure,
#              malformed config/suppression file) — NEVER conflated with a
#              clean result. This code exists specifically so a broken gate
#              cannot report exit 0.
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
    files_scanned: int = 0
    commits_scanned: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.errors:
            return EXIT_ERROR
        if self.findings:
            return EXIT_FAIL
        if self.stale_suppressions or self.pre_existing:
            return EXIT_WARN
        return EXIT_CLEAN

    @property
    def status(self) -> str:
        return {
            EXIT_CLEAN: "clean",
            EXIT_WARN: "warn",
            EXIT_FAIL: "fail",
            EXIT_ERROR: "error",
        }[self.exit_code]


def run_gate(
    root: Path,
    rev_range: str | None = None,
    config: ProjectConfig | None = None,
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
        return _run_gate(root, rev_range=rev_range, config=config)
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

    binary = gitleaks_runner.find_gitleaks_binary()
    if binary is None:
        return GateReport(
            errors=[
                "gitleaks is not installed or not on PATH. rite's publish "
                "gate is built on gitleaks and does not reimplement secret "
                "detection, so it cannot run without it. Install it from "
                "https://github.com/gitleaks/gitleaks (release binaries for "
                "macOS and Linux), or via a package manager if you use one "
                "(`brew install gitleaks`) — naming only the package manager "
                "was a dead end for a tester who had neither."
            ]
        )

    if config is None:
        loaded = parse_config(root / ".rite" / "config.yaml")
        if not isinstance(loaded, ProjectConfig):
            return GateReport(errors=[f"invalid .rite/config.yaml: {loaded.message}"])
        config = loaded

    tracked = pattern_scan.list_tracked_files(root)
    if isinstance(tracked, pattern_scan.ScanError):
        return GateReport(errors=[tracked.message])

    user_config_path: Path | None = None
    candidate = root / config.publish_gate.gitleaks_config
    if candidate.exists():
        user_config_path = candidate

    all_findings: list[Finding] = []

    # 1. gitleaks: maintained secret-detection ruleset over full git history
    #    (or the push range). Covers current + historical file content.
    history = gitleaks_runner.scan_history(
        root, binary, config_path=user_config_path, log_opts=rev_range
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

    all_findings.extend(
        pattern_scan.scan_content(root, tracked, all_patterns, source="rite-pattern")
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
        return GateReport(errors=errors, files_scanned=len(tracked))

    # Dedupe: the same finding can legitimately surface twice (e.g. gitleaks'
    # default ruleset run both standalone and via the user's extended config,
    # if the user's config also sets useDefault).
    deduped: dict[str, Finding] = {}
    for f in all_findings:
        deduped.setdefault(f.fingerprint, f)
    merged = list(deduped.values())

    suppression_path = root / DEFAULT_SUPPRESSION_PATH
    suppressions = supp_mod.parse(suppression_path)
    if isinstance(suppressions, supp_mod.SuppressionError):
        return GateReport(errors=[suppressions.message], files_scanned=len(tracked))

    blocking, suppressed = supp_mod.apply(merged, suppressions)
    stale = supp_mod.find_stale(suppressions, merged)

    blocking, pre_existing = _split_off_pre_existing(root, rev_range, blocking)

    return GateReport(
        findings=blocking,
        suppressed=suppressed,
        pre_existing=pre_existing,
        stale_suppressions=stale,
        files_scanned=len(tracked),
    )


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
    still_blocking = [f for f in blocking if not f.file or f.file in touched]
    pre_existing = [f for f in blocking if f.file and f.file not in touched]
    return still_blocking, pre_existing


def format_report(report: GateReport) -> str:
    """Human-readable summary — used by both the standalone `__main__` CLI
    and (once wired) the `rite publish check` command."""
    lines: list[str] = []
    if report.errors:
        lines.append("ERROR — the gate could not complete:")
        for e in report.errors:
            lines.append(f"  {e}")
        return "\n".join(lines)

    lines.append(f"scanned {report.files_scanned} tracked file(s)")
    if report.findings:
        lines.append(f"\n{len(report.findings)} finding(s) — BLOCKING:")
        for f in report.findings:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            commit = f" ({f.commit[:8]})" if f.commit else ""
            lines.append(f"  [{f.rule_id}] {loc}{commit} — {f.description}")
            lines.append(f"    match: {f.match_preview}")
            lines.append(f"    fingerprint: {f.fingerprint}")
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
    if report.suppressed:
        lines.append(f"\n{len(report.suppressed)} finding(s) suppressed (with reason)")
    if report.exit_code == EXIT_CLEAN:
        lines.append("\nclean")
    return "\n".join(lines)
