# rite local — estimate, and why 32 is not the denominator (2026-09-18)

Started from the capacity simulation in `RITE_LOCAL_TICKETS.md`, not from a
fresh guess. Re-costed for **one sequential lane**, which is the current
constraint.

## The headline

| | |
|---|---|
| Ticket set as written | 32 tickets ≈ **33 worker-sittings** (the simulation: 11 rounds × 3 workers, one idle round) |
| RL-T0 is two sittings by its own text | +1 → **34** |
| Missing wiring tickets (below) | **+3 to +5**, and they are on the critical path |
| **Sequential total** | **≈ 37–39 sittings** |
| Critical path as written | `RL-T0 → RL-T6 → RL-T9 → RL-T30 → RL-T11 → RL-T13`, plus feeders T3, T4, T5, T8 ≈ **10 sittings** |

**In one lane the critical path stops being the floor and becomes a subset.**
Eleven rounds only ever meant "with three workers". Sequentially the number to
plan against is the total, and the parallel structure buys nothing — the
simulation itself says the plan is worker-bound from round 3, with 10–13 tickets
ready and three taken. One worker means that ready queue is just a queue.

## What changed since the tickets were written

`RITE_LOCAL_TICKETS.md` opens with "almost nothing here can start today"
because six Phase 2 prerequisites were unbuilt. **Phase 2 has since merged and
shipped in v0.4.0.** On `origin/main` now: `src/rite_ai/coordination/` carries
`state_layer.py`, `local_backend.py`, `git_backend.py`, `heartbeat.py`,
`assignment.py`, `refusal.py`, `election.py` and more. So the "stuck at round 3"
premise is gone, and the plan is dependency-open.

Two corrections to the blocker table, from the tree rather than the doc:

- **P2-0a/P2-0b landed only partly.** `managers:` is a **list of names**
  (`CoordinationConfig.managers: list[str]`). RL-T3 is therefore an extension
  with backwards compatibility against projects already on v0.4.0, not the
  greenfield schema its blocker line implies. That is a bigger ticket than
  written, and it is the one five others wait on.
- ~~P2-4a's in-flight count is not there~~ **WRONG, corrected 2026-09-18.**
  It exists: `publish_heartbeat(layer, name, *, workers, in_flight, ...)` in
  `coordination/heartbeat.py`, and `in_flight` appears in six coordination
  modules. The original check grepped `coordination/__init__.py` alone and
  concluded from one file what was true of neither. RL-T5 and RL-T6 are not
  blocked on it, and the estimate loses nothing — but the claim was repeated
  twice before it was checked, which is the cost worth remembering.

## Robert's six questions block particular tickets, not the start

| Q | Blocks | Can work proceed? |
|---|---|---|
| Q7 `managers:` schema | RL-T3, and 5 tickets behind it | Yes — §0 gives the shape to proceed on (`engine`, `duties`, `preset`, `questions`) |
| Q1 one machine or several | Whether cross-machine Phase 2 core is a dependency | Moot now: it is built |
| Q2 does the PM run Claude | RL-T2, RL-T14 | Both expressible; only the docs ticket truly waits |
| Q6 SPEC §4 expertise routing | `team`/`org` profiles, PM business questions | Yes — `solo` only, as designed |
| Q3 plan review on every decomposition | RL-T8's shape | Yes — every one, meanwhile |
| Q13 who may be Owner | RL-T21 | Yes |

**Genuinely blocked: about four tickets** (RL-T2, the business half of RL-T14,
part of RL-T21, the final shape of RL-T8). Nothing blocks starting.

## The denominator is wrong, and it is the same gap Phase 2 had

Phase 2's set was missing a wiring ticket per mechanism: nine integration
defects, each a mechanism that shipped complete, correct, tested and called by
nobody. **This set has the same hole, and it is visible in the ticket text.**

**RL-T6 (harness core) says, in its own words: "no edits to the pool or sandbox
paths, so this does not collide with CLI work."** Nothing else in the 32 adds
those edits. So after RL-T6 through RL-T13 are all done, the pipeline exists and
**nothing starts it**: today a Manager session begins at `rite start` (the pool)
or `rite sandbox start`, and both spawn `claude`. A `local:*` Manager has no
start path at all.

**Progress on these, 2026-09-18 (from `origin/main`, not from memory):**

- (2) **done.** Assignment consults the duty router, and the Owner's
  assignment path is called from the scheduler tick behind Q9's switch. Q9 is
  answered as Robert answered it — `coordination.assign_unattended`, default
  false, with the three unambiguity rules always on.
- (1) **half done.** The monitor is constructed with `backend=`/`schedule=`
  and says when it did not try; nothing yet STARTS a `local:*` Manager —
  `pool.fill` spawns one command for every anonymous slot, and a Manager role
  is a named identity with an engine. That mismatch is the remaining work, and
  it is bigger than a flag.
- (3) **not started.**

At least three wiring tickets are missing, all on the critical path:

1. **Start a `local:*` Manager.** Whatever `rite start` and the pool do for a
   Claude Manager, something must do for a harness Manager — spawn it, count it
   against the worker cap, stop it, and report it in `rite status`.
2. **Call the duty router.** RL-T5 builds routing; no ticket changes the
   existing assignment path (`coordination/assignment.py`, `rite claim`) to
   consult duties. Built and uncalled is exactly Phase 2's failure.
3. **Notify the `integrate` holder.** RL-T13 is "integrate handoff", but what
   puts the composed branch in front of a Claude session or a person — a board
   move, a ticket comment, a pull-list entry — is not in any ticket.

Likely fourth: **capacity accounting**. `pool.json` and the worker cap are
written against Claude sessions; a local Manager's Workers have a different cost
and a different failure mode, and nothing reconciles them.

**Why the gap is structural rather than careless.** The collision map optimises
for parallel claimability: keep logic in new packages, touch shared files only at
a call site. Integration is precisely where claims collide — `cli/main.py`, the
pool, `assignment.py` — so a set written to avoid collisions writes the
integration out. The ticket review already caught one instance (RL-T30, "compose
the branch … nothing built the branch RL-T11 verifies"). The same review pass
did not ask the question of the pipeline as a whole.

**Recommendation:** before starting RL-T3, add the wiring tickets explicitly and
put each one immediately after the mechanism it wires, not at the end. The cost
is +3 to +5 sittings; the cost of not doing it is a complete local tier that no
project can turn on, discovered at the point someone tries.

## Confidence

The simulation's per-ticket unit is one sitting and is not defended anywhere;
RL-T6 and RL-T3 are visibly larger than RL-T20 or RL-T22. Treat 37–39 as the
shape rather than the number, and expect the variance to live in RL-T3 (schema
with back-compat), RL-T6 (the harness), and whatever RL-T0 concludes — if no
adopted agent holds the contracts, RL-T6 becomes "build an agent loop" and the
ticket set says that roughly doubles it.
