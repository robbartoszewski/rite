"""A Worker's SLOT outlives its claims, and nothing said so (SCRUM-70).

§1's must-fix row: *"`release --force` reports success but the slot stays
held."*

🔴 **The two facts that were conflated.** A Worker holds two things, and they
are released by different commands:

- its **claims** on paths, which `rite release` clears;
- its **slot** against `sandbox.max_concurrent_workers`, which only
  `yoloai destroy` clears — `count_active_sandboxes` says so itself: *"a
  stopped-but-not destroyed sandbox counts; only `destroy` removes one."*

So `rite release --force` printed "force-released 2 claim(s)" and the next
Worker still could not start, because the cap was still full. And `rite
status` printed "**no active claims**" for a project whose every slot was
occupied — the most reassuring thing it can say, about the state that stops
all work. Both are true sentences about claims, read as sentences about
capacity.

**What this reports.** A Worker whose sandbox EXISTS (any status but `not
found`) holds a slot. Saying so beside a release and in `rite status` is the
whole fix: nothing here destroys anything, because which sandbox to destroy is
not rite's inference to make — a stopped sandbox still holds the Worker's copy
of the work.

⚠ **"Could not ask" is not "no slots held."** yoloAI unreachable is reported as
itself, for the reason `SandboxListing` carries that distinction at all: a
project told "no slots held" when rite could not look would be told its
capacity is free at the one moment it cannot know.

⚠ **One `yoloai ls --json`**, taken by the caller and passed in
(`sandbox.list_sandboxes`), so this costs nothing where a listing already
exists — the supervise boundary shares one with `reconcile`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HeldSlot:
    """One Worker occupying a slot, and whether it still holds claims too."""

    worker: str
    status: str
    """The sandbox status holding the slot: `active`, `idle`, `stopped`…"""
    claims: int = 0

    @property
    def idle(self) -> bool:
        """A slot held by a Worker with no claims left.

        ⚠ This is the shape SCRUM-70 is named for: the work is done or taken
        away, the paths are free, and the slot is still gone. A slot held by a
        Worker that IS still claiming paths is ordinary and is not a fault.
        """
        return self.claims == 0

    def line(self) -> str:
        if self.idle:
            return (
                f"{self.worker}: its sandbox is {self.status} and it holds NO "
                "claims, so the work is done or taken away and the slot is "
                "still occupied. It counts against "
                "sandbox.max_concurrent_workers until the sandbox is "
                "DESTROYED — releasing claims does not free it"
            )
        return (
            f"{self.worker}: its sandbox is {self.status}, holding a slot and "
            f"{self.claims} claim(s)"
        )

    def how_to_free_it(self, manager: str = "") -> str:
        """What to run. A Manager cannot destroy a sandbox from inside its own
        (B9), so it ASKS (SCRUM-59); the Owner's door is the direct command."""
        if manager:
            return (
                f"`rite request stop {self.worker}` frees the session, "
                f"`rite request destroy {self.worker}` frees the slot"
            )
        return (
            f"`rite sandbox destroy {self.worker}` frees the slot; "
            f"`rite sandbox stop {self.worker}` does not"
        )


def held_slots(
    root: Path, workers: list[str], *, listing=None, claims=None
) -> list[HeldSlot] | str:
    """Every Worker of this project holding a slot, or why it cannot be told.

    A string is always "could not tell", never "none" — see the module
    docstring. `listing` and `claims` are injected so this is pure and so a
    caller that already has them pays for neither.
    """
    root = Path(root)
    if listing is None:
        from rite_ai.sandbox import list_sandboxes

        listing = list_sandboxes()
    if not getattr(listing, "known", False):
        return (
            "rite could not ask yoloAI which sandboxes exist, so it cannot say "
            "which slots are held: "
            f"{getattr(listing, 'unknown', 'no reason given')}"
        )

    if claims is None:
        from rite_ai.claims.ledger import ClaimsLedger

        try:
            claims = ClaimsLedger(root / ".rite" / "claims.json").list_claims()
        except Exception:  # noqa: BLE001 - unreadable ledger: cannot tell
            return (
                f"{root / '.rite' / 'claims.json'} could not be read, so rite "
                "cannot say which held slots still have claims behind them"
            )
    per_worker: dict[str, int] = {}
    for claim in claims:
        name = getattr(claim, "worker", "")
        per_worker[name] = per_worker.get(name, 0) + 1

    from rite_ai.sandbox import legacy_sandbox_name, sandbox_name

    found: list[HeldSlot] = []
    for worker in workers:
        try:
            names = {sandbox_name(worker, root), legacy_sandbox_name(worker)}
        except Exception:  # noqa: BLE001 - an unsafe name has no sandbox
            continue
        status = listing.status_of(names)
        if not status.known or status.value == "not found":
            continue
        found.append(
            HeldSlot(
                worker=worker,
                status=status.value,
                claims=per_worker.get(worker, 0),
            )
        )
    return sorted(found, key=lambda s: s.worker)


def idle_only(slots: list[HeldSlot] | str) -> list[HeldSlot]:
    """Just the slots held by a Worker with no claims — SCRUM-70's shape.

    A "could not tell" string yields nothing: a caller that acts on idle slots
    must not act on a question nobody answered.
    """
    return [] if isinstance(slots, str) else [s for s in slots if s.idle]
