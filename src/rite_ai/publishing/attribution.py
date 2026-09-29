"""How every commit a Worker's work reaches the world in says who made it.

**Robert's form, 2026-09-29.** A body line crediting rite, and one trailer
crediting Claude:

    🤖 Generated with rite (https://github.com/robbartoszewski/rite)

    Co-Authored-By: Claude <noreply@anthropic.com>

⚠ **No model version, deliberately.** Security-flagged work can run on a
downgraded model, so "Claude Opus 5.5" would sometimes be false — a claim in
permanent public history. "Claude" is always true. No `rite` trailer and no
rite email: rite is credited by the body line's URL.

⚠ **Added by rite, never remembered by a Worker.** Two places build the
commits that leave: the Worker's own commits, made inside its sandbox, which
carry a `prepare-commit-msg` hook rite installs in each clone before the
sandbox starts (`install_hooks`; `git commit --no-verify` does not skip that
hook), and the squashed commit `rite deliver` builds on the host
(`deliver._squash_message`), which goes through `credit` directly.

Claude Code adds `Co-Authored-By: Claude <noreply@anthropic.com>` to its own
commits already, so both paths add the body line BEFORE the trailer block and
never repeat a line that is there: a second copy, or the body line landing
after the trailers (which turns them back into text), is the failure mode.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

RITE_URL = "https://github.com/robbartoszewski/rite"
"""rite's repository, as its `origin` names it (checked 2026-09-29)."""

BODY_LINE = f"🤖 Generated with rite ({RITE_URL})"
TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"

_TRAILER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: \S")
_HOOK_MARK = "# rite: attribution.install_hooks"


def credit(message: str) -> str:
    """`message` with `BODY_LINE` and `TRAILER`, each exactly once, the body
    line as a paragraph before the trailer block."""
    lines = message.rstrip("\n").split("\n")
    if BODY_LINE not in lines:
        # The trailer block is the last paragraph, if every line of it is one.
        start = len(lines)
        while start > 0 and lines[start - 1].strip():
            start -= 1
        block = [line for line in lines[start:] if line.strip()]
        has_block = (
            bool(block)
            and start > 0
            and all(_TRAILER_LINE.match(line) for line in block)
        )
        at = start if has_block else len(lines)
        insert = [BODY_LINE, ""] if has_block else ["", BODY_LINE]
        lines = lines[:at] + insert + lines[at:]
    if TRAILER not in lines:
        tail = lines[-1] if lines else ""
        if tail == BODY_LINE or not _TRAILER_LINE.match(tail):
            lines.append("")
        lines.append(TRAILER)
    return "\n".join(lines) + "\n"


def _hook_script() -> str:
    """A `prepare-commit-msg` hook doing what `credit` does, in POSIX sh and
    awk: a sandbox has git, sh and awk, and not necessarily this rite. A hook
    that was already there is kept and run first."""
    return (
        "#!/bin/sh\n"
        f"{_HOOK_MARK} — every Worker commit credits rite and Claude.\n"
        'before="$(dirname "$0")/prepare-commit-msg.before-rite"\n'
        'if [ -x "$before" ]; then "$before" "$@" || exit $?; fi\n'
        'msg="$1"\n'
        f"body={shlex.quote(BODY_LINE)}\n"
        f"trailer={shlex.quote(TRAILER)}\n"
        'if ! grep -qxF "$body" "$msg"; then\n'
        '  awk -v body="$body" \'\n'
        "    { line[NR] = $0 }\n"
        "    END {\n"
        "      n = NR; while (n > 0 && line[n] ~ /^[[:space:]]*$/) n--\n"
        "      s = n; while (s > 0 && line[s] !~ /^[[:space:]]*$/) s--\n"
        "      block = (n > 0 && s > 0)\n"
        "      for (i = s + 1; i <= n; i++)\n"
        "        if (line[i] !~ /^[A-Za-z][A-Za-z0-9-]*: [^ ]/) block = 0\n"
        "      for (i = 1; i <= n; i++) {\n"
        '        if (block && i == s + 1) { print body; print "" }\n'
        "        print line[i]\n"
        "      }\n"
        '      if (!block) { print ""; print body }\n'
        '    }\' "$msg" > "$msg.rite" && mv "$msg.rite" "$msg"\n'
        "fi\n"
        "exec git interpret-trailers --in-place --if-exists addIfDifferent "
        '--trailer "$trailer" "$msg"\n'
    )


def install_hooks(workdir: Path) -> list[str]:
    """Install the hook in every clone of a Worker's workspace. Returns the
    problems; the caller refuses to start on any, since a Worker whose
    commits could go out uncredited is what this prevents."""
    problems = []
    clones = [workdir] if (workdir / ".git").is_dir() else []
    clones += sorted(p for p in workdir.iterdir() if (p / ".git").is_dir())
    for clone in clones:
        hooks = clone / ".git" / "hooks"
        hook = hooks / "prepare-commit-msg"
        try:
            hooks.mkdir(parents=True, exist_ok=True)
            if hook.exists() and _HOOK_MARK not in hook.read_text(errors="replace"):
                hook.rename(hooks / "prepare-commit-msg.before-rite")
            hook.write_text(_hook_script())
            hook.chmod(0o755)
        except OSError as exc:
            problems.append(f"{clone.name}: {exc}")
    return problems
