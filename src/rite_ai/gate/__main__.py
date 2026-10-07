"""Standalone entry point: `python -m rite_ai.gate check|pre-push`.

`rite publish check` and `rite publish pre-push` (`rite_ai.cli.main`) ARE wired
now and are the primary, pipx-safe UX — this module is the fallback path:
CI that shells out to the module directly (`.github/workflows/publish-gate.
yml` runs `python -m rite_ai.gate check`) and `python -m rite_ai.gate install-hook`
for installing the pre-push hook outside of `rite init`. Both this module's
`_cmd_pre_push` and the CLI's `publish pre-push` share the same range logic
(`rite_ai.gate.hook.compute_pre_push_ranges`), so they can't drift apart the way
the two hook installers once did (Stage 2 #3).

Exit codes match `gate.EXIT_*` exactly — see that module for the contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rite_ai.gate.gate import format_report, run_gate
from rite_ai.gate.hook import install_pre_push_hook


def _find_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return start


def _roots() -> tuple[Path, Path]:
    """`(scan_root, config_root)` — the same split the `rite` CLI makes.

    This module had only the first, so in a `rite prepare` layout it ran
    with whatever `parse_config` returns for a file that is not there: a
    default `ProjectConfig`, no error, every declared scan pattern dropped
    and every suppression ignored. That is not a cosmetic divergence —
    `GATE_INVOCATIONS` lists this spelling and rite's own CI workflow uses
    it, so it is the gate on the layer local configuration cannot switch
    off.
    """
    from rite_ai.cli.main import _gate_config_root, _gate_root

    return _gate_root(), _gate_config_root()


def _range_option(argv: list[str]) -> tuple[str | None, str | None]:
    """`(rev_range, problem)` from `--range A..B`, or `(None, None)`.

    Refused rather than guessed at: a range this cannot parse would silently
    become a full scan, and a reader of the output would have no way to tell
    which they got.
    """
    # ⚠ BOTH SPELLINGS. Matching only the two-word form meant
    # `--range=origin/main..HEAD` was dropped in silence and ran a FULL scan
    # printing nothing — exactly what this function's docstring promises not
    # to do. Found by review, 2026-10-07, measured: 1555 commits scanned under
    # a flag asking for 11.
    joined = [a for a in argv if a.startswith("--range=")]
    if joined:
        value = joined[0].split("=", 1)[1].strip()
    elif "--range" in argv:
        at = argv.index("--range")
        if at + 1 >= len(argv):
            return None, "--range needs a value, like --range origin/main..HEAD"
        value = argv[at + 1].strip()
    else:
        return None, None
    if not value or ".." not in value:
        return None, (
            f"--range {value!r} is not a revision range: it needs the "
            "`A..B` form, as `git log` takes it"
        )
    return value, None


def _cmd_check(argv: list[str]) -> int:
    """Scan, by default the whole history (see `_run_gate`'s docstring).

    ⚠ **`--ci-range` narrows it to the commits THIS run is responsible for
    (SCRUM-39)**, which is what a per-pull-request required check should
    judge. Without it, gitleaks scans every commit the repository can reach
    and `fetch-depth: 0` has fetched every ref — so one branch's leak failed
    the gate on every other open pull request (measured live, 2026-10-02,
    #183 blocking #184). `rite_ai.gate.ci_range` decides the range and falls
    back to the full scan whenever it cannot establish one, so a narrowed
    scan is never a guess.

    The chosen range is PRINTED either way. A green gate is a claim about
    which commits were looked at, and a reader is entitled to see it.
    """
    root, config_root = _roots()
    rev_range, problem = _range_option(argv)
    if problem:
        print(f"rite publish gate: {problem}", file=sys.stderr)
        return 2
    if "--ci-range" in argv or any(a.startswith("--ci-range=") for a in argv):
        if rev_range is not None:
            print(
                "rite publish gate: pass --range or --ci-range, not both — "
                "they would answer the same question differently",
                file=sys.stderr,
            )
            return 2
        import os

        from rite_ai.gate.ci_range import range_for_ci

        chosen = range_for_ci(os.environ, root)
        print(f"rite publish gate: {chosen.why}")
        rev_range = chosen.rev_range
    elif rev_range is not None:
        print(f"rite publish gate: scanning {rev_range}")
    report = run_gate(root, rev_range=rev_range, config_root=config_root)
    print(format_report(report))
    return report.exit_code


def _cmd_pre_push(argv: list[str]) -> int:
    """Reads git's pre-push protocol from stdin: one line per pushed ref,
    `<local ref> <local sha1> <remote ref> <remote sha1>`. Scans each pushed
    range; a hook fails (nonzero exit) if any range fails.

    This is the standalone-module fallback path (this file's own module
    docstring explains why it exists); the pipx-safe primary path is
    `rite publish pre-push` (`rite_ai.cli.main`), which calls the same
    `compute_pre_push_ranges` this does."""
    from rite_ai.gate.hook import compute_pre_push_ranges

    root, config_root = _roots()
    lines = sys.stdin.read().splitlines()

    if not lines:
        # No refs on stdin can legitimately happen (e.g. a push that deletes
        # nothing and updates nothing matched by refspec) — nothing to scan,
        # nothing to block.
        print("rite publish gate: nothing to scan")
        return 0

    worst = 0
    for rev_range in compute_pre_push_ranges(lines):
        report = run_gate(root, rev_range=rev_range, config_root=config_root)
        print(f"rite publish gate — {rev_range}")
        print(format_report(report))
        worst = max(worst, report.exit_code)

    return worst


def _cmd_install_hook(argv: list[str]) -> int:
    root = _find_root(Path.cwd())
    force = "--force" in argv
    # SCRUM-76: the hook carries the project whose rules it should trust,
    # resolved the same way the gate resolves it (`_roots`).
    _scan_root, config_root = _roots()
    result = install_pre_push_hook(root, force=force, project_root=config_root)
    print(result.message)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "usage: python -m rite_ai.gate {check|pre-push|install-hook}\n"
            "  check         scan full history, exit per the gate contract\n"
            "    --range A..B  scan only that range\n"
            "    --ci-range    scan only what this CI run introduces\n"
            "  pre-push      scan the range read from stdin (git pre-push protocol)\n"
            "  install-hook  install .git/hooks/pre-push",
            file=sys.stderr,
        )
        return 2

    cmd, rest = argv[0], argv[1:]
    if cmd == "check":
        return _cmd_check(rest)
    if cmd == "pre-push":
        return _cmd_pre_push(rest)
    if cmd == "install-hook":
        return _cmd_install_hook(rest)

    print(f"unknown command: {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
