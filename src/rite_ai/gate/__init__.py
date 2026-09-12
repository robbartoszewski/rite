"""Publish gate — deterministic, zero-token pre-publish scanning.

Boundary note (2026-09-09): this module directory is owned by the publish-
gate work. It does not touch `src/rite_ai/cli/main.py` — the CLI entrypoint,
claims, credentials, and status are owned by other sessions. The public
interface below is what a `rite publish check` command (and a `rite
credential`-style hook-install command, if wanted) should call.

    from pathlib import Path
    from rite_ai.gate.gate import run_gate, format_report, EXIT_CLEAN

    report = run_gate(Path.cwd())            # full history scan
    report = run_gate(Path.cwd(), rev_range="origin/main..HEAD")  # scoped
    print(format_report(report))
    raise SystemExit(report.exit_code)        # 0 clean, 1 warn, 2 fail, 3 error

    from rite_ai.gate.hook import install_pre_push_hook
    install_pre_push_hook(Path.cwd())         # for `rite init`

    from rite_ai.gate.suppression import append as append_suppression
    append_suppression(root / ".rite" / "gitleaksignore", fingerprint, reason)

See `gate.py` for the exit-code contract and `GateReport` fields, and this
package's module docstrings for the two verified gitleaks behaviours
(commit-message blind spot, NUL-byte binary detection) the design works
around.
"""

from rite_ai.gate.gate import (
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_FAIL,
    EXIT_WARN,
    GateReport,
    run_gate,
)

__all__ = [
    "run_gate",
    "GateReport",
    "EXIT_CLEAN",
    "EXIT_WARN",
    "EXIT_FAIL",
    "EXIT_ERROR",
]
