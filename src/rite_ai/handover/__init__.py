"""Continuous handover snapshot (SPEC §9.10.1, D-35).

`perform_handover` (`rite_ai.lifecycle`) marks a TRANSITION — it fires on
`stop`, a heartbeat timeout, or (Phase 2) a lease expiry, and answers "why
did this happen?" This module answers a different question, continuously:
"what is the state right now?" A session that dies between transitions
leaves whatever transition-triggered handover last existed — stale by up to
one detection window. Closing that gap means writing a snapshot on every
scheduled cycle, unconditionally, not only at a transition.

Ephemeral state, not a message (§3.3.3's boundary): each session's snapshot
is overwritten every write, carrying no historical value in isolation —
only the latest one matters.

**One snapshot per session, keyed by name** (`.rite/handover/<name>.json`),
which is the shape §9.10.1 already specifies for Phase 2
(`managers/<name>-handover.json`). Phase 1 wrote a single unkeyed
`.rite/handover.json` on the reasoning that there is one Manager — but
`perform_handover` is called per WORKER, so every worker wrote the same
file and the last one to stop erased the rest. Three workers handing over
their own tickets left a snapshot naming one of them, with no field saying
which, and a fresh session reading it to reconstruct state (§9.10.1's
"read the latest handover snapshot before evaluating orientation") saw one
arbitrary worker and no sign the others existed.

A legacy unkeyed `.rite/handover.json` is still read while no keyed
snapshot exists, so a project written by an older rite does not lose its
snapshot on upgrade — and stops being read once anything keyed replaces
it, since the two are otherwise indistinguishable and the listing showed
the same handover twice.

Writing this is a file write, not an LLM turn — cheap enough to call on
every watchdog cycle (§3.5) — but the CONTENT (ticket, progress, next step,
blockers) is authored by whatever session is running, not synthesized by
a mechanical script the way the watchdog's checks are. There is
deliberately no automatic scheduler wired up here yet (same gap as the
watchdog's own OS-level cron/launchd wiring, §3.5) — `rite handover write`
is the real, callable mechanism; something (a human, or eventually `rite
start`'s scheduled tasks) has to actually call it on a cadence.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rite_ai.label import project_name
from rite_ai.state import write_atomic

SNAPSHOT_FILENAME = "handover.json"  # legacy unkeyed path, still read
SNAPSHOT_DIRNAME = "handover"
LEGACY_OWNER = ""  # the name a legacy unkeyed snapshot is reported under


@dataclass
class HandoverSnapshot:
    project: str = ""
    worker: str = ""
    ticket: str = ""
    progress: str = ""
    next_step: str = ""
    blockers: list[str] = field(default_factory=list)
    timestamp: float = 0.0
    # Why this snapshot could not be read, when it could not be. Never
    # written to disk — derived by `_load` from the file in front of it.
    # See `_load` for why an unreadable snapshot is a SNAPSHOT and not a
    # `None`.
    unreadable: str = ""

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def age_seconds(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.timestamp)

    def describe_age(self, now: float | None = None) -> str:
        """`2026-09-12 01:05:14 (3h 1m ago)`.

        The absolute time alone was all any reader got, and this is the
        one file whose entire audience is a session that has just started
        and has no idea what time the previous one stopped. `rite
        handover show`'s own help says "read this at session startup".
        Three hours dead and three seconds old rendered identically in
        form, so telling them apart meant knowing the current time and
        doing the subtraction — and the difference between them is the
        difference between "that session is working on this" and "that
        session died holding this".

        `format_duration` is the same words the watchdog uses for a stall
        and the scheduler for a tick gap, so one reader learns one format.
        """
        from rite_ai.duration import format_duration

        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        return f"{stamp} ({format_duration(self.age_seconds(now))} ago)"


def _legacy_snapshot_path(root: Path) -> Path:
    return root / ".rite" / SNAPSHOT_FILENAME


def _snapshot_dir(root: Path) -> Path:
    return root / ".rite" / SNAPSHOT_DIRNAME


def _snapshot_path(root: Path, worker: str = "") -> Path:
    """One file per session. An empty `worker` is the single-coordinator
    case and gets a reserved name rather than the legacy unkeyed path, so
    that reading never has to guess whether a bare `handover.json` was
    written by this rite or an older one."""
    return _snapshot_dir(root) / f"{worker or '_owner'}.json"


def write_snapshot(
    root: Path,
    ticket: str = "",
    progress: str = "",
    next_step: str = "",
    blockers: list[str] | None = None,
    worker: str = "",
) -> Path:
    """Overwrite THIS session's snapshot — every call replaces that
    session's previous one entirely (ephemeral state; no append, no
    history). Other sessions' snapshots are untouched, which is the point:
    a shared file meant every worker's handover erased the one before it."""
    snapshot = HandoverSnapshot(
        project=project_name(root),
        worker=worker,
        ticket=ticket,
        progress=progress,
        next_step=next_step,
        blockers=list(blockers or []),
    )
    path = _snapshot_path(root, worker)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(snapshot)
    # Derived at read time from the file itself; writing it would put a
    # permanently-empty field in every snapshot on disk.
    payload.pop("unreadable", None)
    write_atomic(path, json.dumps(payload, indent=2) + "\n")
    return path


def has_content(
    ticket: str, progress: str, next_step: str, blockers: list[str] | None
) -> bool:
    """Whether a proposed write would record anything at all.

    Every write replaces that session's snapshot entirely, so a write with
    no content is a deletion that reports success. Measured: a worker
    recorded `DEF-12`, 60%-done progress, a next step and the open blocker
    "needs DB credentials"; the next scheduled call lost its arguments,
    printed `handover snapshot written for alpha`, exited 0, and left five
    `(none)` lines where the blocker had been. The docstring tells callers
    to run this "on a schedule (every few minutes)", which is a great many
    chances for a wrapper to drop its arguments.
    """
    return bool(
        ticket.strip()
        or progress.strip()
        or next_step.strip()
        or [b for b in (blockers or []) if str(b).strip()]
    )


def _unreadable(root: Path, path: Path, worker: str, detail: str) -> HandoverSnapshot:
    """A snapshot that stands for a file this module could not parse.

    The file's own mtime is the timestamp, because it is the one true
    thing left: it says when that session last wrote, which is most of
    what a reader wanted from the snapshot anyway.
    """
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = 0.0
    return HandoverSnapshot(
        project=project_name(root),
        worker=worker,
        timestamp=stamp,
        unreadable=f"{path}: {detail}",
    )


def _load(root: Path, path: Path, worker: str) -> HandoverSnapshot | None:
    """Parse one snapshot file.

    **An unreadable file comes back as a snapshot, not as `None`.** It used
    to return `None`, every caller dropped it, and a truncated
    `handover/alpha.json` therefore made `rite handover show` print `no
    handover snapshot recorded yet` — the exact words it prints when
    nothing has ever been written — while an open blocker sat inside the
    file. `rite start` omitted the handover line entirely and said
    `ready`. The watchdog, whose job is to notice a blocked worker
    unattended, stopped seeing that worker's blocker at all.

    `rite_ai.state`'s module docstring already settles this, for the
    claims ledger: "An empty file is a real state that means 'nothing is
    claimed'. A corrupt file means 'I do not know what is claimed', and
    the two must never render the same." The write half of handover was
    converted in that sweep; the read half was not.

    A `None` return is reserved for a file that is no longer there at all
    — genuinely nothing, and the caller's glob has already moved on.
    """
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        return _unreadable(root, path, worker, f"could not be read: {e}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return _unreadable(root, path, worker, f"invalid JSON: {e}")
    if not isinstance(data, dict):
        return _unreadable(
            root, path, worker, f"not a JSON object ({type(data).__name__})"
        )
    return HandoverSnapshot(
        # A snapshot written before this field existed belongs to the
        # project whose directory it was found in.
        project=data.get("project") or project_name(root),
        worker=data.get("worker", worker),
        ticket=data.get("ticket", ""),
        progress=data.get("progress", ""),
        next_step=data.get("next_step", ""),
        blockers=list(data.get("blockers", [])),
        timestamp=data.get("timestamp", 0.0),
    )


def read_snapshots(root: Path) -> list[HandoverSnapshot]:
    """Every session's snapshot, newest first.

    Returns a list because there is one per session: with several workers
    on a project, "what is the state right now?" has as many answers as
    there are live sessions, and collapsing them to one was how two of
    three workers' handovers disappeared.
    """
    found: list[HandoverSnapshot] = []
    directory = _snapshot_dir(root)
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            name = path.stem
            snap = _load(root, path, "" if name == "_owner" else name)
            if snap is not None:
                found.append(snap)
    legacy = _legacy_snapshot_path(root)
    if not found and legacy.is_file():
        # Written by an older rite, and read ONLY while nothing keyed
        # exists. Reading it unconditionally showed the same handover
        # twice after an upgrade — once under its worker's name and again
        # as "(unnamed session)" — because the first keyed write leaves
        # the superseded file sitting there. The unkeyed file carries no
        # worker, so there is no way to tell it apart from the keyed copy
        # of itself; the moment anything keyed exists it is stale by
        # definition. Left on disk rather than deleted: retiring a user's
        # state file is not this function's decision to make.
        snap = _load(root, legacy, LEGACY_OWNER)
        if snap is not None:
            found.append(snap)
    return sorted(found, key=lambda s: s.timestamp, reverse=True)


def read_snapshot(root: Path) -> HandoverSnapshot | None:
    """The most recent session's snapshot, or `None` when nothing has been
    recorded yet — a missing snapshot is not an error, it just means
    proceed to the orientation table as normal (SPEC §9.10.1's carve-out).

    Callers that render for a human should use `read_snapshots`: this one
    answers for a single session and cannot say that others exist.
    """
    snapshots = read_snapshots(root)
    return snapshots[0] if snapshots else None
