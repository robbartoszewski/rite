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


def _cmd_check(argv: list[str]) -> int:
    root = _find_root(Path.cwd())
    report = run_gate(root)
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

    root = _find_root(Path.cwd())
    lines = sys.stdin.read().splitlines()

    if not lines:
        # No refs on stdin can legitimately happen (e.g. a push that deletes
        # nothing and updates nothing matched by refspec) — nothing to scan,
        # nothing to block.
        print("rite publish gate: nothing to scan")
        return 0

    worst = 0
    for rev_range in compute_pre_push_ranges(lines):
        report = run_gate(root, rev_range=rev_range)
        print(f"rite publish gate — {rev_range}")
        print(format_report(report))
        worst = max(worst, report.exit_code)

    return worst


def _cmd_install_hook(argv: list[str]) -> int:
    root = _find_root(Path.cwd())
    force = "--force" in argv
    result = install_pre_push_hook(root, force=force)
    print(result.message)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(
            "usage: python -m rite_ai.gate {check|pre-push|install-hook}\n"
            "  check         scan full history, exit per the gate contract\n"
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
