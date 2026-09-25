"""The standup a check-in opens with: anchors, not prose (plan § K4).

⚠ **Three things were reported done in the last release that had never
run.** A standup is otherwise the most efficient channel there is for
unverified claims, so this one is COMPOSED BY RITE from records, and every
line names something a reader can check:

* **Observed by rite**: commits (`git log`, SHA and subject), Worker
  sandboxes started and stopped and board moves (`.rite/events.jsonl`,
  written where rite saw each succeed), each supervised cycle and how it
  ended, and what the engine refused (the Manager's check-in ledger). The
  sandboxes started in the period are also looked up at the check-in itself.
* **Stated by the Manager**: `rite checkin note --anchor … --observed …`,
  through the journal's anchor floor. Labelled as the Manager's, for the
  reason C10 gave journal entries a `recorded_from`: a reader must be able
  to tell evidence from a claim.

Nothing here summarises. A line like "sorted out the Worker problem" cannot
be produced, because there is no field it could come from.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path

FIRST_CHECKIN_LOOKBACK = 24 * 3600
"""How far back the first check-in ever looks, when there is no earlier one
to start from. Said in the standup's first line, not implied."""

MAX_COMMITS = 30


def _hhmm(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().strftime("%a %H:%M")


def _commits(root: Path, since: float) -> tuple[list[str], str]:
    """`(lines, problem)`. Every ref this checkout knows, so a Worker's
    branch counts once it has been fetched. What was never fetched is not
    seen, and the standup says which command it ran so that is checkable."""
    try:
        done = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "--all",
                f"--since=@{int(since)}",
                "--format=%h%x09%s",
                f"--max-count={MAX_COMMITS + 1}",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"`git log` could not run: {e}"
    if done.returncode != 0:
        why = (done.stderr or "").strip().splitlines()
        return [], f"`git log` failed: {why[-1] if why else done.returncode}"
    lines = []
    for row in done.stdout.splitlines()[:MAX_COMMITS]:
        sha, _, subject = row.partition("\t")
        lines.append(f"- commit {sha} {subject}")
    if len(done.stdout.splitlines()) > MAX_COMMITS:
        lines.append(
            f"- more than {MAX_COMMITS} commits — the rest: "
            f"`git log --all --since=@{int(since)}`"
        )
    return lines, ""


def _sandbox_state(names: list[str]) -> list[str]:
    """Where each sandbox started in the period is NOW, asked of yoloAI."""
    if not names:
        return []
    from rite_ai.sandbox import CountUnavailable, list_rite_sandboxes

    listed = list_rite_sandboxes()
    if isinstance(listed, CountUnavailable):
        return [
            f"- sandbox {n}: could not ask yoloAI at this check-in "
            f"(`yoloai ls` — {listed.reason})"
            for n in names
        ]
    by_name = {e.name: e for e in listed}
    out = []
    for n in names:
        entry = by_name.get(n)
        if entry is None:
            out.append(f"- sandbox {n}: not listed by `yoloai ls` at this check-in")
        else:
            out.append(
                f"- sandbox {n}: {entry.status or 'listed'} at this check-in "
                "(`yoloai ls`)"
            )
    return out


def digest(
    root: Path, manager: str, since: float, now: float | None = None
) -> list[str]:
    """The standup's lines, since `since` (0.0: no earlier check-in)."""
    from rite_ai.managers import checkins
    from rite_ai.reporting import events

    now = time.time() if now is None else now
    if since > 0:
        start = since
        head = f"Standup since {_hhmm(since)}, the last check-in:"
    else:
        start = now - FIRST_CHECKIN_LOOKBACK
        head = (
            f"Standup since {_hhmm(start)} — there is no earlier check-in, so "
            "this covers the last 24 hours:"
        )

    observed: list[str] = []
    commits, problem = _commits(root, start)
    observed.extend(commits)
    if problem:
        observed.append(f"- commits unknown: {problem}")

    started: list[str] = []
    for e in events.since(root, start):
        kind = e.get("event")
        when = _hhmm(float(e.get("at") or 0))
        if kind == "sandbox-started":
            ticket = f", ticket {e['ticket']}" if e.get("ticket") else ""
            observed.append(
                f"- Worker {e.get('worker')} started in sandbox "
                f"{e.get('sandbox')}{ticket} ({when})"
            )
            started.append(str(e.get("sandbox")))
        elif kind in ("sandbox-stopped", "sandbox-destroyed"):
            verb = kind.split("-", 1)[1]
            observed.append(f"- sandbox {e.get('sandbox')} {verb} ({when})")
        elif kind == "board-move":
            by = f" by {e['by']}" if e.get("by") else ""
            observed.append(
                f"- ticket {e.get('ticket')} moved to {e.get('status')!r} "
                f"through `rite board move`{by} ({when})"
            )
    observed.extend(_sandbox_state(list(dict.fromkeys(started))))

    notes: list[str] = []
    for e in checkins.ledger(root, manager):
        if float(e.get("at") or 0) <= start:
            continue
        kind = e.get("event")
        if kind == "cycle":
            observed.append(
                f"- cycle {e.get('number')}, session {e.get('session')}: "
                f"{e.get('ending')} ({_hhmm(float(e.get('started_at') or 0))}"
                f"–{_hhmm(float(e.get('at') or 0))})"
            )
        elif kind == "refusal":
            observed.append(
                f"- refused by the engine in cycle {e.get('number')}, session "
                f"{e.get('session')}: `{e.get('command')}`"
            )
        elif kind == "note":
            # One line each: a note's second line would be a line of the
            # standup with no anchor on it.
            said = " ".join(str(e.get("observed") or "").split())
            anchor = " ".join(str(e.get("anchor") or "").split())
            notes.append(f"- {said} [anchor: {anchor}]")

    lines = [head, "", "Observed by rite:"]
    if observed:
        lines.extend(observed)
    else:
        lines.append(
            f"- nothing: `git log --all --since=@{int(start)}` found no commits, "
            "and .rite/events.jsonl and this Manager's checkins/ledger.jsonl "
            "have nothing since then"
        )
    if notes:
        lines += ["", "Stated by the Manager — rite did not verify these:", *notes]
    # N2 (plan § N, SPEC §6.6.2): wired in here by N. Nothing in K reads it.
    from rite_ai import phrases

    lines += phrases.standup_lines(events.since(root, start))
    return lines
