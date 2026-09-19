# Run parameters — rite-on-rite, Phase 1

Companion to `rite-on-rite-phase1-preregistration.md` §8.4, which lists the
slots this file fills. Recorded **before the run**. A slot marked OPEN is not
yet settled; the two marked BLOCKING stop the run from starting.

**Created:** 2026-09-13, before any worker has started on `phase-2`.

---

## Settled

| Slot | Value | Provenance |
|---|---|---|
| **Worker count** | **3** | Settled decision — conserve quota while still exercising parallel work. Consequence recorded in pre-registration §8.5. |
| **Expected concurrency** | **≥2 sustained, from `P2-1c` onward** | §8.5. Concurrency is structurally unavailable until `P2-1b` lands — no off-critical-path ticket is unblocked before then, at any worker count. Concurrent-minutes accrued before `P2-1c` is expected to be zero and is **not** a scheduling failure. |

---

## OPEN — to be filled before the run starts

### BLOCKING — the run does not start without these

- **§6.3 mitigation in force** — `fix landed (D1)` / `PATH wrapper`:
  **UNDETERMINED as of 2026-09-13.**
  The fix exists as `4473e75` ("Stop a module that is a rite repo from
  capturing its own workers") on `fix/nested-project-root`: the project marker
  becomes a FILE (`.rite/brief.yaml` or `.rite/modules.yaml`) rather than the
  `.rite/` directory, plus a `RITE_PROJECT_ROOT` override, a `_gate_root()`
  split so `rite publish check` still resolves the repository, and
  `tests/test_nested_project_root.py` asserting the property against real
  directories. It is **not on `main` and not pushed**.
  **Re-check immediately before the run and record the answer here.** A run on
  the fix tests rite; a run on the shim tests rite plus scaffolding, and the
  write-up must say which it was (§6.3).

- **Named off-critical-path tickets assigned to a second worker (W1-PRE)** —
  **OPEN.** §8.5 identifies `P2-1d` and `P2-1e` as the only off-path work that
  unlocks at `P2-1b`, and therefore the natural pair; they are not yet
  assigned. A run that starts without them named does not start (§9, W1-PRE).

### Required for the write-up to be interpretable

- **Wrapper checksum** — OPEN. Applies only if the `PATH` wrapper is the
  mitigation in force; recorded in `provenance.jsonl` beside `V010_CHECKSUM`.
- **Verification that a worker clone resolves to the experiment root** under
  the chosen mitigation — OPEN. Assert the property from inside a worker
  clone; do not trust the arrangement.
- **State root filesystem type** (the `flock` question, C8) — OPEN.
- **Whether the sandbox mount boundary isolates `_find_project_root`** (§6.4)
  — OPEN. Test it; do not assume it.
- **Whether a real JIRA board is available** (C2) — OPEN. `PHASE2_TICKETS.md`
  records *"no JIRA site is configured on this machine; the recorded safe
  write target is `RT`, never `SCRUM` or `RW`."* If that still holds at run
  start, the board half of the Phase 1 Definition of Done is unexercised and
  C2 stays open.
- **`V010_CHECKSUM`** — OPEN.
- **Experiment state root path** — OPEN.
- **`RELAY_VERIFIED`** — bidirectional, dated, verifying session named — OPEN.
