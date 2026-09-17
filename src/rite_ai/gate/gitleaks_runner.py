"""Subprocess wrapper around the gitleaks binary.

SPEC §11: "Built on gitleaks — don't reimplement secret detection." This
module's only job is invoking gitleaks correctly and translating its JSON
report into `Finding` objects; it does not implement any detection logic
itself.

Two verified, load-bearing facts about gitleaks 8.30.1 that shape this file:

1. `gitleaks detect` (full git-log mode, no `--no-git`) scans every commit's
   diff — which means it sees a file's content via whichever commit
   introduced or last touched it, without needing a separate working-tree
   pass. So one full-history `detect` run covers both "current file content"
   and "content that was later removed but still lives in history" (the
   scenario SPEC §11.1 opens with).

2. `gitleaks detect` does **not** scan commit message text, at all, by
   default. Verified directly: a GitHub-token-shaped string placed only in a
   commit message (never in file content) produced zero findings. There is
   no flag in `gitleaks detect --help` (8.30.1) to change this. Since SPEC
   §11.2 explicitly requires commit messages in scope, and gitleaks itself
   won't do it, this module relays commit message text through gitleaks by
   writing it to a temp file and running `gitleaks dir` against that file —
   reusing gitleaks' actual regex/entropy engine rather than reimplementing
   it, just handing it text it wouldn't otherwise look at. Verified working:
   the same GitHub-token string, relayed this way, produces a real finding.
   (A NUL-byte-delimited relay file was tried first and silently produced
   zero findings — gitleaks treats any file containing a NUL byte as binary
   and skips it outright. The relay format here uses newline delimiters only,
   for exactly this reason — see `_dump_commit_messages`.)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rite_ai.gate.findings import (
    Finding,
    content_digest,
    iter_commit_messages,
    redact,
)


@dataclass
class ScanError:
    message: str


def find_gitleaks_binary() -> str | None:
    return shutil.which("gitleaks")


def _run_gitleaks_json(
    args: list[str], cwd: Path, binary: str
) -> list[dict] | ScanError:
    """Run gitleaks with a forced JSON report to a temp file and --exit-code 0
    so the process's own exit code always distinguishes "gitleaks ran" (0)
    from "gitleaks itself failed" (non-zero) — findings are read from the
    report file, never inferred from the exit code. This is the guard against
    the exact failure this gate must prove it doesn't have: a tool that exits
    non-zero to stderr while stdout/the report stays empty, silently read as
    "clean"."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        report_path = Path(tmp.name)
    try:
        full_args = [
            binary,
            *args,
            "--no-banner",
            "--exit-code",
            "0",
            "-f",
            "json",
            "-r",
            str(report_path),
        ]
        try:
            proc = subprocess.run(
                full_args,
                cwd=cwd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return ScanError(f"failed to run gitleaks: {e}")

        if proc.returncode != 0:
            return ScanError(
                f"gitleaks exited {proc.returncode}: {proc.stderr.strip()[:500]}"
            )

        if not report_path.exists():
            return ScanError("gitleaks produced no report file")

        raw = report_path.read_text()
        if raw.strip() == "":
            return []  # gitleaks writes an empty file, not `[]`, on a clean scan
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return ScanError(f"gitleaks report was not valid JSON: {e}")
        if not isinstance(data, list):
            return ScanError("gitleaks report was not a JSON array")
        return data
    finally:
        report_path.unlink(missing_ok=True)


def _to_finding(entry: dict, source: str) -> Finding | None:
    if not isinstance(entry, dict):
        return None
    secret = str(entry.get("Secret") or entry.get("Match") or "")
    try:
        return Finding(
            rule_id=str(entry["RuleID"]),
            description=str(entry.get("Description", entry["RuleID"])),
            file=str(entry.get("File", "")),
            line=int(entry.get("StartLine", 0)),
            commit=entry.get("Commit") or None,
            match_preview=redact(secret),
            source=source,
            digest=content_digest(secret) if secret else "",
        )
    except (KeyError, ValueError, TypeError):
        return None


def scan_history(
    root: Path,
    binary: str,
    config_path: Path | None = None,
    log_opts: str | None = None,
) -> list[Finding] | ScanError:
    """Full (or range-scoped, via `log_opts`) git-history scan using gitleaks'
    own default ruleset. Covers file content across all of history, including
    content since removed from HEAD."""
    args = ["detect", "--source", str(root)]
    if config_path is not None:
        args += ["--config", str(config_path)]
    if log_opts:
        args += ["--log-opts", log_opts]
    result = _run_gitleaks_json(args, cwd=root, binary=binary)
    if isinstance(result, ScanError):
        return result
    findings = [_to_finding(e, "gitleaks") for e in result]
    return [f for f in findings if f is not None]


def _dump_commit_messages(
    root: Path, dest_dir: Path, rev_range: str | None = None
) -> list[tuple[str, int, int]]:
    """Write every commit's message to one text file, newline-delimited
    (never NUL — see module docstring). Returns [(sha, start_line, end_line)]
    so a finding's report line can be mapped back to a commit.

    `rev_range` scopes this to a `git log`-style revision range (e.g.
    `"<remote-sha>..<local-sha>"`) instead of full history — used by the
    pre-push hook to stay fast regardless of total repo history size."""
    messages = iter_commit_messages(root, rev_range)
    if isinstance(messages, str):
        # `_dump_commit_messages` has no error channel of its own; its
        # caller turns an empty result into a clean scan, so a git failure
        # must not look like "no commits".
        raise RuntimeError(messages)

    out_path = dest_dir / "commit-messages.txt"
    offsets: list[tuple[str, int, int]] = []
    line_no = 1
    with out_path.open("w") as out:
        for sha, body in messages:
            lines = body.splitlines() or [""]
            start = line_no
            for line in lines:
                out.write(line + "\n")
                line_no += 1
            offsets.append((sha, start, line_no - 1))
    return offsets


def scan_commit_messages(
    root: Path,
    binary: str,
    config_path: Path | None = None,
    rev_range: str | None = None,
) -> list[Finding] | ScanError:
    """Relay every commit message through gitleaks (see module docstring for
    why this exists) and map findings back to their source commit."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        try:
            offsets = _dump_commit_messages(root, tmp_dir, rev_range=rev_range)
        except (OSError, subprocess.TimeoutExpired) as e:
            return ScanError(f"failed to dump commit messages: {e}")

        args = ["dir", str(tmp_dir)]
        if config_path is not None:
            args += ["--config", str(config_path)]
        result = _run_gitleaks_json(args, cwd=tmp_dir, binary=binary)
        if isinstance(result, ScanError):
            return result

        findings: list[Finding] = []
        for entry in result:
            base = _to_finding(entry, "gitleaks-commit-msg")
            if base is None:
                continue
            sha = _commit_for_line(offsets, base.line)
            findings.append(
                Finding(
                    rule_id=base.rule_id,
                    description=base.description,
                    file="<commit message>",
                    line=_line_within_commit(offsets, base.line),
                    commit=sha,
                    match_preview=base.match_preview,
                    source=base.source,
                    digest=base.digest,
                )
            )
        return findings


def _commit_for_line(offsets: list[tuple[str, int, int]], line: int) -> str | None:
    for sha, start, end in offsets:
        if start <= line <= end:
            return sha
    return None


def _line_within_commit(offsets: list[tuple[str, int, int]], line: int) -> int:
    for _sha, start, end in offsets:
        if start <= line <= end:
            return line - start + 1
    return line
