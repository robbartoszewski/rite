"""Coordinator redundancy pool (SPEC §2.5, D-26–D-29).

Managers and the Owner are still ordinary Claude Code sessions that need
a human to start (§2.2/§2.3) — unlike on-demand sandboxed Workers
(§5.3), nothing about `mcp serve`'s no-approval spawn path applies to
them. This module keeps a small, FIXED pool (`pool.coordinator_standby`,
default 2) of already-started, idle coordinator sessions so a dead
Owner/Manager has a warm replacement ready instead of waiting for a
human to notice and start one.

**A pooled session is a real backgrounded process, not a data record.**
Mechanism: a detached tmux session (verified real, on this machine —
`new-session -d`, `has-session`, `kill-session` all behave exactly as
documented). Liveness is an OS-level check on the session process
(`tmux has-session`) — no prompt sent into the pooled session, no LLM
turn, exactly the zero-cost probe §2.5.2/§2.5.3 require.

**Retiring a slot is `archive`, and it is the other half of `fill`.**
A slot whose session died *without running its shutdown hook* — killed,
crashed, machine restarted — leaves two things behind: a `pool.json`
record that still looks like capacity, and, if that session had been
promoted onto real work, a claims-ledger entry under its name that reads
as live work being done by nobody. `archive` retires the record and
releases the orphaned claims together, because doing either alone is
what produces a false alarm: release without retiring and the next
`fill` recycles the slot name; retire without releasing and the claim
outlives the only record that could explain it.

`archive` will only ever touch a slot it has *proved* dead — the same
OS-level probe as `probe`, failing continuously for
`pool.archive_after_minutes`. It refuses outright when it cannot run
that probe (no tmux), because "I could not check" must not read the same
as "it is dead" for an operation that force-releases another session's
claims.

**Promotion is out of scope here, deliberately.** §2.5.1 describes "the
pool supplies a warm session" on Owner/Manager death — but *who* takes
over, and what they do first, is a judgement call a human makes by
attaching to the pooled session's tmux window (`tmux attach -t <name>`),
not something this module automates. This module's job ends at keeping a
real, named, live session ready to attach to.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.config.models import PoolConfig
from rite_ai.state import (
    CorruptStateError,
    locked,
    read_json_state,
    write_atomic,
)

STATE_FILENAME = "pool.json"
ARCHIVE_FILENAME = "pool-archive.jsonl"
CLAIMS_FILENAME = "claims.json"
DEFAULT_COMMAND = "claude"


def _tmux_binary() -> str | None:
    return shutil.which("tmux")


@dataclass
class PoolSlot:
    name: str
    created_at: float
    last_live_at: float | None  # None until the first successful probe
    # The claims-ledger identity this session works under once it is
    # promoted onto real work. Recorded at `fill` time so that a slot
    # found dead later can still be connected to the claims it died
    # holding — without this the two records have no link at all and an
    # orphaned claim is indistinguishable from live work.
    worker: str = ""
    # First moment a probe found this slot's session gone; cleared the
    # moment it is seen alive again. This is the honest "dead since"
    # clock: unlike `last_live_at` it measures the SESSION's state, not
    # how recently anyone got round to probing.
    unreachable_since: float | None = None


def _state_path(root: Path) -> Path:
    return root / ".rite" / STATE_FILENAME


def _read_state(root: Path) -> list[PoolSlot]:
    """Raises `CorruptStateError` rather than reporting an unreadable pool
    as an empty one. Reading empty here is not merely inaccurate: `fill`
    tops up to target from what it believes is live, so a corrupt state
    file makes it start a full set of brand-new sessions on top of the ones
    already running, and the dead-slot records that are the only surviving
    link between a crashed session and the claims it left behind are gone
    for good."""
    path = _state_path(root)
    data = read_json_state(path, default=None)
    if data is None:
        return []
    if not isinstance(data, dict):
        raise CorruptStateError(
            path, f"expected a JSON object, got {type(data).__name__}"
        )
    slots = data.get("slots", [])
    return [
        PoolSlot(
            name=s["name"],
            created_at=s["created_at"],
            last_live_at=s.get("last_live_at"),
            # A record written before slots carried an identity worked
            # under the slot name itself, which is what `fill` stamps —
            # so that is the correct reading of an older file, not a
            # guess.
            worker=s.get("worker") or s["name"],
            unreachable_since=s.get("unreachable_since"),
        )
        for s in slots
        if isinstance(s, dict) and "name" in s and "created_at" in s
    ]


@contextmanager
def _maybe_locked_state(root: Path) -> Iterator[None]:
    """Serialise like `_locked_state`, but never bring `.rite/` into
    existence to do it.

    `probe` is documented as read-only, and a stray `.rite/` in a
    directory that is not a rite project makes `rite init` refuse to
    initialise it. With no `.rite/` there is also no state file and
    nothing for two probes to race over, so skipping the lock costs
    nothing."""
    if not _state_path(root).parent.is_dir():
        yield
        return
    with _locked_state(root):
        yield


def _locked_state(root: Path):
    """Exclusion around the pool state file.

    Every entry point here is a read-modify-write over one shared file,
    and none of them was serialised. Measured with eight concurrent
    updaters, ten of ten rounds kept only two of eight updates — the rest
    were read, modified and then overwritten by a writer working from a
    stale read.

    ⚠ **Correcting the claim this shipped with**, which said two
    concurrent `fill`s would each see an empty pool and each start a full
    complement of sessions, spending quota no cleanup recovers. Driven
    against real tmux, that does not happen: `slot_name` is
    deterministic, so every filler computes the same names, and `tmux
    new-session` refuses a name that already exists. Three concurrent
    fills of a three-slot pool produced three sessions, not nine.

    What they did produce was two exit-1 failures reading `started 0 of 3
    needed, then failed: duplicate session ...` about a pool that was in
    fact filled correctly — and, since a failing `fill` still writes the
    state it computed, a stale write racing the winner's. So the cost is
    lost records and a misleading failure, and what stands between rite
    and the doubled sessions is tmux's own refusal to reuse a session
    name, not anything rite does. Under the lock all three fills succeed:
    one starts the sessions, the other two report a full pool.

    Held on a sidecar, not on the state file — see
    `rite_ai.state.locked`."""
    return locked(_state_path(root))


def _write_state(root: Path, slots: list[PoolSlot]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "slots": [
            {
                "name": s.name,
                "created_at": s.created_at,
                "last_live_at": s.last_live_at,
                "worker": s.worker or s.name,
                "unreachable_since": s.unreachable_since,
            }
            for s in slots
        ]
    }
    write_atomic(path, json.dumps(payload, indent=2))


def slot_name(root: Path, index: int) -> str:
    """Deterministic per-project, per-slot tmux session name — stable
    across a `fill`/`probe` cycle so re-running either finds the same
    sessions rather than accumulating orphans."""
    # The project's NAME first, then a path hash (§8.10). This was the
    # whole resolved path — unique, but `tmux ls` then showed
    # `rite-pool-Users-someone-code-backend-0` and the project name a
    # human is looking for never appeared. Uniqueness was never the
    # problem; readability was.
    from rite_ai.label import project_slug

    return f"rite-pool-{project_slug(root)}-{index}"


def is_tmux_session_alive(name: str) -> bool:
    binary = _tmux_binary()
    if binary is None:
        return False
    # ⚠ `=` is tmux's EXACT-match prefix, and it is not decoration. `-t`
    # resolves by exact match, then fnmatch, then PREFIX — so `has-session
    # -t rite-pool-foo-1` succeeds when only `rite-pool-foo-10` exists, and
    # `slot_name` numbers slots exactly that way. A pool of eleven reads
    # slot 1 as alive because slot 10 is, so a dead slot is never refilled
    # and `pool status` reports liveness nothing observed — the failure
    # `_settled_alive` exists to prevent, one layer up. Measured:
    # `has-session -t lead` -> 0 and `-t =lead` -> 1, with only `leader` up.
    proc = subprocess.run(
        [binary, "has-session", "-t", f"={name}"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    return proc.returncode == 0


@dataclass
class FillResult:
    ok: bool
    message: str
    started: list[str] = field(default_factory=list)


def _settled_alive(name: str, tries: int = 10, pause: float = 0.2) -> bool:
    """Is the session STILL there after the window, not merely at some point.

    `tmux new-session -d -s <name> <command>` exits 0 when tmux managed to
    CREATE the session. It says nothing about whether `<command>` then ran.
    A command that is missing, unauthenticated or broken exits on its first
    line, tmux tears the session down, and the exit code has already been
    0 for some milliseconds.

    Measured on this module before this existed: `fill(count=2)` with a
    command that exits immediately returned `ok=True`, printed "pool at 2/2
    — 2 session(s) started", and wrote both slots to `pool.json` with
    `unreachable_since=None`. A second later `tmux ls` showed neither. The
    state file recorded liveness that nothing had ever observed, and `rite
    pool status` then read it back as fact — so the user got a closed loop:
    status says run fill, fill says success, status says 0/2.

    This is `loop/session.py`'s `_settled_alive`, arrived at there after the
    same defect and the same fix, deliberately kept identical in shape. The
    two modules do not share it because they check different liveness
    primitives; if a third one appears, that is the moment to lift it out.

    Polled rather than slept once, and required at EVERY poll rather than
    any: the first version of the loop's check returned True on the first
    successful poll, so a session that died at 0.3s was seen alive at 0.2s
    and reported started — the defect it was written to catch, one layer in.
    """
    for _ in range(tries):
        time.sleep(pause)
        if not is_tmux_session_alive(name):
            return False
    return True


def fill(
    root: Path,
    config: PoolConfig,
    count: int | None = None,
    command: str = DEFAULT_COMMAND,
    max_slots: int | None = None,
) -> FillResult:
    """Top up the pool, one filler at a time — see `_fill_locked`."""
    with _locked_state(root):
        return _fill_locked(root, config, count, command, max_slots)


def _fill_locked(
    root: Path,
    config: PoolConfig,
    count: int | None,
    command: str,
    max_slots: int | None,
) -> FillResult:
    """Top up to `count` (default: `config.coordinator_standby`) live
    slots. Existing live slots count toward the target — this tops up
    the gap, it does not replace what is already warm.

    Dead slots are kept in the state file rather than dropped. They do
    not count toward the target and their names are never handed to a
    new session — a dead record is the only surviving link between a
    session that died without running its shutdown hook and the claims
    it left behind, and recycling its name would silently re-attribute
    those claims to the live session that inherited it. `rite pool
    archive` is what removes them, once it has proved they are dead.

    `max_slots` is a ceiling on the target, and it is not decoration: every
    slot runs `command`, which defaults to `claude`. Measured before this
    check existed, `rite pool fill --count 500` issued 500 `tmux
    new-session ... claude` calls without a prompt or a pause — 500 live
    Claude sessions from one typo, against the weekly quota that is the
    scarce resource here. Spent quota is the one kind of damage in this
    module that no cleanup gets back.

    Refused, not clamped, matching `schedule.check_worker_cap`: silently
    starting 5 when 500 was asked for hides the mistake, and the caller
    asked for something it should learn was wrong."""
    binary = _tmux_binary()
    if binary is None:
        return FillResult(
            False, "tmux not found — install it to use the coordinator pool"
        )

    moment = time.time()
    target = count if count is not None else config.coordinator_standby

    if target < 0:
        return FillResult(False, f"refused: negative target ({target})")
    if max_slots is not None and target > max_slots:
        return FillResult(
            False,
            f"refused: target of {target} pooled session(s) exceeds "
            f"sandbox.max_concurrent_workers ({max_slots}). Every slot runs "
            f"`{command}`. Raise the cap in .rite/config.yaml if this is "
            "really intended — refused rather than clamped so the number you "
            "asked for is not silently changed.",
        )

    live: list[PoolSlot] = []
    dead: list[PoolSlot] = []
    for slot in _read_state(root):
        if is_tmux_session_alive(slot.name):
            live.append(replace(slot, unreachable_since=None))
        else:
            dead.append(
                replace(
                    slot,
                    unreachable_since=(
                        slot.unreachable_since
                        if slot.unreachable_since is not None
                        else moment
                    ),
                )
            )

    # Both lists reserve their names: a live slot is obviously in use,
    # and a dead one still owns its identity until it is archived.
    taken = {s.name for s in live} | {s.name for s in dead}

    started: list[str] = []
    index = 0
    while len(live) < target:
        while slot_name(root, index) in taken:
            index += 1
        name = slot_name(root, index)
        proc = subprocess.run(
            [binary, "new-session", "-d", "-s", name, command],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
        if proc.returncode != 0:
            _write_state(root, live + dead)
            detail = proc.stderr.strip() or proc.stdout.strip()
            return FillResult(
                False,
                f"started {len(started)} of {target} needed, then failed: {detail}",
                started,
            )
        if not _settled_alive(name):
            # NOT recorded, and the name NOT burned. A slot is kept as dead
            # so its name still links a departed session to the claims it
            # left behind — but a session whose command never ran cannot
            # have claimed anything, so there is nothing to link and
            # reserving the name would cost a name per failed attempt for
            # no benefit. Three failed runs would otherwise consume six.
            #
            # ⚠ The limit, because the signal is narrower than the reasoning
            # above needs it to be. `_settled_alive` proves the session was
            # not alive at every poll. It does NOT prove the command never
            # ran: "never started" and "started, claimed a path, died at
            # 1.4s" produce the same answer, and only the first is safe to
            # forget. Two conditions, one symptom, and this takes the cheap
            # reading deliberately — a session that claims inside ~2.2s is
            # not a realistic sequence, and the cost of the careful reading
            # is a burned slot name on every failed attempt for ever.
            #
            # So: if an orphaned claim is ever found with no pool record
            # behind it, this is where it came from. That is the whole
            # reason the sentence is here rather than in a commit message.
            _write_state(root, live + dead)
            return FillResult(
                False,
                f"started {len(started)} of {target} needed. tmux created "
                f"`{name}` and `{command}` exited immediately, so the session "
                "was gone before it could be used — `tmux new-session` "
                "reports whether a session was CREATED, not whether the "
                "command in it is running.\n"
                f"  check it with: {command}\n"
                "  nothing was recorded for this slot",
                started,
            )
        now = time.time()
        live.append(
            PoolSlot(
                name=name,
                created_at=now,
                last_live_at=now,
                worker=name,
                unreachable_since=None,
            )
        )
        taken.add(name)
        started.append(name)
        index += 1

    _write_state(root, live + dead)
    # Name what was actually started. `fill` launches real detached
    # sessions on this machine, and §2.5.1's takeover path is a human
    # attaching to one of them — a bare "pool at 2/2" reports neither
    # that anything was launched nor the one thing needed to reach it.
    message = f"pool at {len(live)}/{target}"
    if started:
        listed = "\n".join(f"  started {name}" for name in started)
        message = (
            f"{message} — {len(started)} session(s) started\n{listed}\n"
            f"  attach with: tmux attach -t {started[0]}"
        )
    return FillResult(True, message, started)


@dataclass
class PoolStatus:
    target: int
    live: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    warn: bool = False
    message: str = ""


def probe(root: Path, config: PoolConfig, now: float | None = None) -> PoolStatus:
    """Probe the pool, one prober at a time — see `_probe_locked`."""
    with _maybe_locked_state(root):
        return _probe_locked(root, config, now)


def _probe_locked(
    root: Path, config: PoolConfig, now: float | None = None
) -> PoolStatus:
    """Mechanical, zero-token liveness check (§2.5.2/§2.5.3) — no prompt
    reaches any pooled session. Updates and persists `last_live_at` for
    every slot found alive; a slot whose lease has not been refreshed
    within `pool.lease_expiry_minutes` is reported stale even if the tmux
    session technically still exists, per §2.5.2's "not refreshed within
    the expiry window drops out of the live count.\""""
    moment = now if now is not None else time.time()
    lease_seconds = config.lease_expiry_minutes * 60
    slots = _read_state(root)
    live: list[str] = []
    stale: list[str] = []
    updated: list[PoolSlot] = []

    for slot in slots:
        if is_tmux_session_alive(slot.name):
            updated.append(replace(slot, last_live_at=moment, unreachable_since=None))
            lease_expired = (
                slot.last_live_at is not None
                and moment - slot.last_live_at > lease_seconds
            )
            if lease_expired:
                stale.append(slot.name)
            else:
                live.append(slot.name)
        else:
            stale.append(slot.name)
            # Keep the record, and start (or leave running) its
            # "dead since" clock — `rite pool archive` reads that clock,
            # so a probe is what makes a slot eventually archivable.
            updated.append(
                replace(
                    slot,
                    unreachable_since=(
                        slot.unreachable_since
                        if slot.unreachable_since is not None
                        else moment
                    ),
                )
            )

    # Do not create `.rite/` just to record that there is no pool. This
    # command is documented as a read-only probe, and a stray `.rite/`
    # in a directory that is not a rite project makes `rite init` refuse
    # to initialise it.
    if updated or _state_path(root).is_file():
        _write_state(root, updated)

    target = config.coordinator_standby
    warn = len(live) < target * config.warn_threshold
    message = ""
    if warn:
        needed = target - len(live)
        message = (
            f"coordinator pool at {len(live)}/{target} — {needed} more needed, "
            "run `rite pool fill`"
        )

    return PoolStatus(target=target, live=live, stale=stale, warn=warn, message=message)


# --- Archiving stale slots (and the claims they died holding) ---


@dataclass
class ArchivedSlot:
    name: str
    worker: str
    unreachable_seconds: float
    released_claims: int = 0
    claim_paths: list[str] = field(default_factory=list)


@dataclass
class ArchiveResult:
    ok: bool
    message: str
    archived: list[ArchivedSlot] = field(default_factory=list)
    # Dead, but not dead long enough yet — named so a human can see the
    # archiver considered them rather than wonder why nothing happened.
    waiting: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def released_claims(self) -> int:
        return sum(a.released_claims for a in self.archived)


def _archive_log_path(root: Path) -> Path:
    return root / ".rite" / ARCHIVE_FILENAME


def archive(
    root: Path,
    config: PoolConfig,
    after_minutes: int | None = None,
    ledger: ClaimsLedger | None = None,
    now: float | None = None,
    dry_run: bool = False,
) -> ArchiveResult:
    """Archive dead slots, one archiver at a time — see `_archive_locked`.

    ⚠ **Deliberately manual, and asked about: no, the scheduler tick must
    not run this.** Nothing unattended releases the claims a dead pooled
    coordinator was holding, which looks like a gap until you name what
    closing it would mean — an unattended process force-releasing claims
    from a session it has decided is dead. rite already took the opposite
    decision one level down: a claim never expires on its own, because
    rite cannot tell a crashed session from a session thinking hard, and
    auto-releasing a path a live worker is editing is worse than leaving
    a stale one lying around. The liveness probe here is better evidence
    than a heartbeat timeout, but "better evidence" is not the same as
    "safe to act on while nobody is watching". A human runs this."""
    with _locked_state(root):
        return _archive_locked(root, config, after_minutes, ledger, now, dry_run)


def _archive_locked(
    root: Path,
    config: PoolConfig,
    after_minutes: int | None,
    ledger: ClaimsLedger | None,
    now: float | None,
    dry_run: bool,
) -> ArchiveResult:
    """Retire slots whose sessions are provably gone, and release the
    claims they died holding.

    The staleness rule, stated in one place: **a slot is archivable when
    the OS-level liveness probe has failed for it continuously for at
    least `pool.archive_after_minutes`** (overridable per-run with
    `after_minutes`). Two halves, both load-bearing:

    - *Provably* — a slot whose session answers the probe is never
      archived, whatever any timestamp says. Archiving force-releases
      another session's claims; doing that to a session that is still
      working is a worse failure than leaving a stale record around.
    - *Continuously* — the clock is `unreachable_since`, set by the first
      probe that found the session gone and cleared the moment it is seen
      alive again. A slot that flickers does not accumulate time toward
      archiving.

    When tmux is unavailable the probe cannot run at all, and every slot
    would look dead. This refuses rather than believing that. (`probe`
    deliberately does the opposite and reports such slots stale: for
    *reporting*, "cannot verify" should read as "do not count on it";
    for a *destructive* operation it must read as "do nothing.")

    The orphaned-claim release is scoped to the dead slot's own worker
    identity — not to its claimed paths — so a live session holding an
    overlapping path is never caught up in it.
    """
    moment = now if now is not None else time.time()
    threshold = (
        after_minutes if after_minutes is not None else config.archive_after_minutes
    ) * 60

    if _tmux_binary() is None:
        return ArchiveResult(
            False,
            "tmux not found — cannot verify whether any session is really "
            "gone, so nothing was archived",
            dry_run=dry_run,
        )

    if ledger is None:
        ledger = ClaimsLedger(root / ".rite" / CLAIMS_FILENAME)

    slots = _read_state(root)
    keep: list[PoolSlot] = []
    archived: list[ArchivedSlot] = []
    waiting: list[str] = []
    records: list[dict] = []

    for slot in slots:
        if is_tmux_session_alive(slot.name):
            keep.append(replace(slot, last_live_at=moment, unreachable_since=None))
            continue

        dead_since = (
            slot.unreachable_since if slot.unreachable_since is not None else moment
        )
        unreachable_for = moment - dead_since
        if unreachable_for < threshold:
            # Start the clock if this is the first sighting, then leave it.
            keep.append(replace(slot, unreachable_since=dead_since))
            waiting.append(slot.name)
            continue

        worker = slot.worker or slot.name
        held = ledger.claims_for(worker)
        paths = [p for claim in held for p in claim.paths]
        entry = ArchivedSlot(
            name=slot.name,
            worker=worker,
            unreachable_seconds=unreachable_for,
            released_claims=len(held),
            claim_paths=paths,
        )
        archived.append(entry)
        records.append(
            {
                "timestamp": moment,
                "slot": slot.name,
                "worker": worker,
                "created_at": slot.created_at,
                "last_live_at": slot.last_live_at,
                "unreachable_since": dead_since,
                "unreachable_seconds": unreachable_for,
                "reason": (
                    f"unreachable for {int(unreachable_for // 60)}m "
                    f"(threshold {threshold // 60}m) — session ended without "
                    "running its shutdown hook"
                ),
                "released_claims": [
                    {"paths": c.paths, "ticket": c.ticket} for c in held
                ],
            }
        )

    if dry_run:
        # A dry run reports and writes nothing at all — not even the
        # "dead since" stamps, which is why the clock is `probe`'s job
        # and not this function's alone.
        return ArchiveResult(
            True,
            _archive_message(archived, waiting, dry_run=True),
            archived=archived,
            waiting=waiting,
            dry_run=True,
        )

    from rite_ai.coordination.identity import claims_channel

    pool_layer, pool_machine = claims_channel(root)
    for entry in archived:
        if entry.released_claims:
            # Published too: an archived slot's claims must stop blocking
            # other machines, and nothing else will take them back.
            #
            # `force_release` rather than `release`, so this lands in
            # `force-releases.jsonl` with who did it and why. It did not, and
            # the consequence was that "why did my claim disappear" depended
            # on which subsystem removed it — a human release was in the
            # audit trail and an automatic one was only in the pool archive.
            #
            # `worker=` is what makes the swap safe: `force_release` matches
            # by path and would otherwise release whoever holds them, which
            # in the window between reading the slot and releasing could be a
            # live worker that claimed an identically-named path.
            ledger.force_release(
                worker=entry.worker,
                by="rite pool archive",
                reason=f"slot {entry.name} was unreachable and has been archived",
                layer=pool_layer,
                machine=pool_machine,
            )

    if records:
        path = _archive_log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")

    _write_state(root, keep)
    return ArchiveResult(
        True,
        _archive_message(archived, waiting, dry_run=False),
        archived=archived,
        waiting=waiting,
    )


def _archive_message(
    archived: list[ArchivedSlot], waiting: list[str], dry_run: bool
) -> str:
    if not archived:
        base = "nothing to archive"
        if waiting:
            base += (
                f" — {len(waiting)} slot(s) unreachable but not yet past the threshold"
            )
        return base
    claims = sum(a.released_claims for a in archived)
    verb = "would archive" if dry_run else "archived"
    message = f"{verb} {len(archived)} stale slot(s)"
    if claims:
        released = "would release" if dry_run else "released"
        message += f", {released} {claims} orphaned claim(s)"
    if waiting:
        message += f"; {len(waiting)} not yet past the threshold"
    return message


def read_archive(root: Path) -> list[dict]:
    """The durable record of what `archive` retired and why — append-only,
    the answer to "this slot and its claim were here yesterday"."""
    path = _archive_log_path(root)
    if not path.is_file():
        return []
    records = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
