# Does a rite claim expire when its holder dies? — revision 2

**Revision 1's guard would have force-released live Workers.** Review found it
and was right. Corrections up front, because they change what gets built:

| Revision 1 said | Actually |
|---|---|
| "sandbox absent" is evidence the holder died | `"not found"` with `known=True` is the **normal** state for any Worker not launched by `start_worker` — `sandbox.enabled` false, started by hand, never sandboxed. The guard was satisfied by default |
| The three conditions together are safe | A Worker heads-down for 30 minutes without calling `rite heartbeat` satisfies all three. Nothing beats on a Worker's behalf |
| `detect_stalls` provides a "starting, not dead" backstop | It does not. `not_started` only excludes holders with **no claim**, so any claim-holder that has not beaten reports `seconds_silent=inf` immediately. The protection rested entirely on claim age |
| Claims belong to Workers | Managers claim through the same ledger, `Claim` has no role field, and `rite claim --worker` takes any string. The loop could force-release the Manager running it |

## The measurement still stands

On `67b2631`: a 9.3-day-old claim, holder with no heartbeat ever, no sandbox.
`detect_stalls` reports `seconds_silent=inf`; the claim is still held; a live
worker is refused the path. **rite detects the death and does nothing about
it** — a working detector whose only consumer is a human reading `rite status`.

Bentora's claims were immortal because the identifier could not resolve.
rite's identifiers agree; rite just never acts.

## The design, rebuilt around what rite can actually witness

**Absence is not evidence.** A sandbox that is not there tells you nothing
about whether a session is alive, because most Workers never had one. So the
mechanism splits in two, by what rite can honestly claim to know.

### Always: report. Never releases anything.

Any claim older than the stall threshold whose holder has not beaten is
**named** — in `rite status`, and in the loop's cycle — with the exact command
to release it:

```
claims: engine/parser.py held by ghost for 9.3 days, no heartbeat ever
  → `rite release --worker ghost --force --reason "session gone"`
```

This is correct on every input, needs no guard, and converts the actual
failure — invisible, permanent, silently narrowing the run — into a visible
one with a one-line fix. **It is the whole of the release-critical fix.**
Everything below is an optimisation on top of it.

### Only on positive evidence: release automatically.

rite may auto-release exactly when it can witness the death rather than infer
it from absence:

1. **rite started the session itself**, recorded in `.rite/dispatch-intents.json`
   (L-3) or a successor — so there is a specific sandbox rite knows existed;
2. **and that sandbox is now gone**, with `known=True`. `known=False` is "could
   not ask" and is not a yes (EXC-3's fourth rule);
3. **and the holder is a registered Worker** (`project.workers`), never an
   arbitrary name — which is what keeps a Manager's own claims out of it;
4. **and the claim predates** the stall threshold.

A hand-started session that dies is **not** covered, and the report above is
what covers it. Saying rite cannot tell is better than guessing, and a guess
here costs two sessions on one path — the thing the ledger exists to prevent.

### Mechanics

- Release through `force_release(paths, by, reason)`, with `paths` taken
  **exactly** from `claims_for(worker)`. It matches by path with directory
  nesting, so anything broader can collaterally release a live worker's
  nested claim.
- Capture `claims_for(worker)` **before** releasing: `force_release` returns
  a count, not what it released, and "reported, always" needs the detail.
- **One audit trail.** `pool.archive` already force-releases a dead session's
  claims — on a stronger signal than anything here, `is_tmux_session_alive` —
  but via plain `release()`, so it lands in the pool archive and not in
  `force-releases.jsonl`. Today "why did my claim disappear" depends on which
  subsystem removed it. Fix that in the same change.
- The threshold is `heartbeat.interval_minutes × stall_threshold` = **30
  minutes** at the defaults. That is *eligibility*, not time-to-release:
  nothing is released until a loop cycle or a human looks, so the 9.3-day
  scenario remains fully possible whenever the loop is not running. Say so
  rather than implying a bound.

### A defect this uncovered, worth its own fix

`read_heartbeat` collapses missing, corrupt and malformed into `None`, so a
transiently unreadable heartbeat reads as **maximally stalled** — the opposite
of safe, and the same conflation `worker_sandbox_status` already avoids with
`known=False`. Anything that acts on stalls needs the third answer.

## What would show this wrong

- **A live Worker's claim is released.** The expensive failure. Now guarded by
  positive evidence rather than by absence, and the guard is the part to
  review hardest.
- **The report fires constantly.** A 30-minute threshold on Workers that beat
  irregularly makes a line people learn to scroll past — which is how this
  class of defect survives.
- **It never fires.** A week with a dead holder and nothing said.
