# v0.7.1 — the dogfood fixes: scope, design, order

**Status: PLAN, 2026-10-05, for Robert's review.** No code fix is on this
branch; it carries this plan and the spec amendments that go with it. Base:
`main` at `0118273` (v0.7.0a9 at `29d02d4`).

**Goal.** A dogfood where *every known issue is fixed* and a **real end-to-end
run on a throwaway app** passes: a Claude Manager plus one Claude Worker and one
GPU Worker take real tickets from the board to delivered pull requests, with no
human running host commands.

## 1. Scope (the must-fix set)

| Key | What | Size |
|---|---|---|
| SCRUM-59 | Manager can't run Worker lifecycle or gate diagnosis from its sandbox | M |
| SCRUM-64 | A restarted Manager doesn't reconcile with ground truth | M–L |
| SCRUM-72 | A local/GPU Worker is never driven under a Claude Manager | L |
| SCRUM-69 (+45, 22) | Relay text depends on a shell heredoc (temp-file EPERM, doubled end line) | S–M |
| SCRUM-21 | `rite reply`/`ask` can't thread under the Owner's message | S |
| SCRUM-39 | CI publish-gate scans every fetched branch, not the PR's range | S |
| SCRUM-62 | A Worker can edit its own gate's suppressions | S–M |
| SCRUM-70 | `release --force` reports success but the slot stays held | S |
| SCRUM-71 | The issues journal misses the failures that matter | M |
| SCRUM-73 | A Done ticket still labelled `ready-to-work` is offered again (caused this run's KAN-28 replay) | S |

**Folded in, not separate tickets.** SCRUM-45 and the residual of SCRUM-22 are
the transport SCRUM-69 replaces (§3.4). The `mkdir`/`echo … > $(date)` request
line the Manager was refused on (journal, 2026-10-04 10:48Z) goes with
SCRUM-59's `rite request`. A Worker started with a `CLAUDE.md` older than
`rite done` (journal, 10:56Z) becomes a refusal at start, in SCRUM-64.

**Recommended in, Robert to decide (§5):** SCRUM-68 (release tags turn CI red).
The next RC re-cut hits it again.

## 2. What the code says (scoping, 2026-10-05)

**SCRUM-59 (M).** There is a working precedent three times over:
- the Manager files a Worker start into `state/requests/`, honoured by
  `supervise._honour_worker_requests` → `broker.honour`;
- deliveries go through `publishing/requests.honour_deliveries`;
- `rite chore` is a CLI the Manager runs that writes its request in Python, so
  nothing reaches the shell.

Every host-side op already exists: `sandbox.stop_worker`, `destroy_worker`,
`restart_worker`, `worker_sandbox_status`, and `gate.run_gate`. Outcomes return
by `telling.tell_manager`. The allowlist already permits the absolute `rite`
path (`permissions._running_rite_rules`), so a CLI needs no allowlist change.
What is missing is the request kind, the CLI, the honour step, and the three
prompt texts that still teach `echo … > $(date).json`.

**SCRUM-64 (M–L).** `rite start` reads none of `claims.json`, heartbeats, publish
records or `watched.json`. Recovery (`recovery.recover_stalled_workers`) acts
only on watchdog stalls, and `left_unattended` is written at exit and released
by nobody. Reconciliation runs host-side, so detection does not need SCRUM-59;
the Manager acting on what it finds does.

**SCRUM-72 (L).** A local Worker's turn runs only through `take_one_step`, which
needs an **approved plan**: there is no single-turn path. The driver is wired
only `if role.is_local` (`cli/main.py:10217`). In a Claude-plus-GPU fleet no
Manager can author the plan:
- `lead` lacks the `decompose` duty;
- the Claude proposer is "not wired yet" (`decompose._proposer_for`);
- RL-67 requires the author to be a Manager;
- RL-6 requires a reviewer on a different engine.

Separately, `rite sandbox start` pastes a Claude prompt into a local Worker's
idle sandbox (`main.py:8226`, `sandbox/__init__.py:1678`). Once a Worker is
placed, the executor itself (`worker_step.placement_for`, #202) does not check
the Manager's engine.

## 3. Design decisions

### 3.1 Lifecycle requests (SCRUM-59)
- **`rite request {stop|destroy|restart|status|gate} <worker> [--ticket <id>]`**, run
  by the Manager and modelled on `rite chore`. It writes `{op, worker, ticket?}`
  into `state/lifecycle/` with Python, so no `$()` or `>` ever reaches the
  engine.
- `supervise` honours the requests at the cycle boundary, between deliveries and
  Worker starts, and the result returns as an inbox note. `status` and `gate`
  put their findings in the note.
- **The boundary is not widened.** The Manager gets no credential store, no
  `~/.yoloai` and no git temp-cache.
- **Refused by construction:**
  - `destroy` with `force` (no field exists to carry it);
  - a Worker belonging to another Manager.
- **Shared with recovery:** restarts use recovery's backoff (`recovery.json`).
- **Counts as progress:** the new request kind is added to `progress.footprint`
  and `COORDINATION`, so a session that only files requests is not "idle".
- The three prompt texts and the deliver gate-refusal note switch to the CLI.
  The refusal note carries the gate's findings directly.

### 3.2 Reconciliation (SCRUM-64)
A new `managers/reconcile.py`: a pure planner plus an injected applier, the
shape of `recovery.py`. It runs once at start (after `pending.sync`) and
throttled per cycle.

For each of the Manager's Workers it compares:
- claims
- the live sandbox (`unknown` means do nothing)
- a handback
- the publish record
- the watched PR and the origin branch
- the board
- a recorded-but-absent sandbox

**Rule:** release a claim **only** when the sandbox is known gone **and** the
work is delivered, merged or handed back. Every action goes through SCRUM-59's
executor, and every outcome is told to the Manager so it stops escalating stale
facts.

At start it also:
- refuses a Worker whose `CLAUDE.md` predates the running release's handback
  protocol, or refreshes it;
- runs `reap_dead_selftest_sandboxes`, which its own docstring already claims
  happens.

### 3.3 Local Workers under any Manager (SCRUM-72)
- **The driver keys off Workers, not the Manager.** The supervise loop drives
  the local tier for every ticket held by a **local Worker** of this Manager,
  whatever the Manager's engine. `_local_tier_tickets` filters to those tickets,
  so a Claude Manager's own tickets are never decomposed.
- **Who authors and who approves.** The recommended answer changes **no RL
  rule**:
  - a declared **local Manager with the `planner` preset** (`decompose`, on the
    same Ollama model as the Worker) authors the Level 1 plan;
  - the Claude Manager, which holds `plan-review` in the `lead` preset,
    approves with `rite local approve`.

  That keeps Level 1 at Manager tier (DECOMPOSER_DESIGN §1.3). The author is a
  Manager (RL-67), and the reviewer is a different engine (RL-6). The planner
  is declared in config. *Inferred, to confirm first while building:* the
  Claude Manager's loop can drive the planner's decompose step, so the planner
  needs no `rite start` of its own. If it cannot, the planner runs its own
  loop, and the fleet has one more process. Alternatives are in §5.
- **No prompt for a local Worker.** `sandbox start`/`restart` pass no prompt
  and no `--resume`, and the output says the local tier drives the Worker.
- **The watchdog must not read an idle sandbox as stalled.** A local Worker's
  sandbox idles between subtasks and never heartbeats. Its liveness becomes
  "has a plan step pending, or is running one".
- **The Manager waits on the plan and its steps, not on `rite done`.** Its
  prompt says so (inferred from the scoping; to confirm while building).

### 3.4 Relay transport (SCRUM-69, absorbing 45 and 22)
The Manager writes its text to a file with its own Write tool and runs
`rite reply|ask|route --manager <m> --from-file <path>`. There is no heredoc,
no temp file the shell must create, and no end line to double. The heredoc
form stays accepted while the prompt teaches only the file form. Paths are
limited to the Manager's own scratch directory.

### 3.5 Light fixes
- **SCRUM-21:** `--message <ts>` on `rite reply`/`ask`, carried to `_post(thread=)`.
- **SCRUM-39:** the CI gate scans the PR's own range.
- **SCRUM-62:** delivery refuses a Worker branch that touches gate suppression
  files, and routes the change to the Owner. The project root's suppression
  file stays Owner-only.
- **SCRUM-70:** `release --force` says the slot is still held while a sandbox
  lives, and offers `rite request stop`. `rite status` stops saying "no active
  claims" for a Worker holding a slot.
- **SCRUM-71:** a defined set of recordable events, written by the supervisor
  itself:
  - relay or delivery refusals
  - recovery and restart
  - reconciliation actions
  - gate refusals
  - a slot held by an idle sandbox
- **SCRUM-73:** readiness excludes Done and Closed statuses whatever the labels
  say.

## 4. How it is built

**One integration branch, `feat/v0.7.1-dogfood-fixes`, one PR at the end.**
1. Each fix lands on the branch as **its own reviewed commit**, with a fast
   targeted test subset run locally. That subset is the touched tests plus
   `test_update_refresh`, the boundary tests and the Manager-prompt tests.
2. When the set is complete: one PR, the **full CI matrix**, plus the **on-Mac
   real-Claude suite**.
3. Fix everything they find in one pass, then merge.
4. **Re-cut the RC tag**, regenerate the section and template histories (unless
   SCRUM-68 makes that automatic), and install it.
5. Run the throwaway-app end-to-end (§4.2).

### 4.1 Order, by dependency
1. **SCRUM-69** (transport). The Manager's own commands must work before
   anything it is asked to do can.
2. **SCRUM-59** (lifecycle requests). The executor everything below relies on.
3. **SCRUM-64** (reconciliation). Builds on 59.
4. **SCRUM-72** (local Workers). Needs §5's decision. It also uses 59's
   start/stop path, and 64's liveness rule for idle local sandboxes.
5. **SCRUM-70, 71, 73.** These touch the same supervisor seams, so they land
   right after 64.
6. **SCRUM-21, 39, 62.** Independent; can land at any point, in parallel
   sessions.
7. **SCRUM-68**, if in: before the re-cut.

### 4.2 The acceptance gate: a throwaway app, end to end
A fresh repository with a small app, a fresh rite project and a fresh board
project.

**Fleet:**
- a Claude `lead`;
- a local `planner` (only if §5 goes that way);
- one Claude Worker;
- one GPU Worker (`qwen3.8` at a 32k window, the patched yoloAI first on PATH).

**Tickets:**
- two ordinary tickets for the Claude Worker;
- one ticket for the GPU Worker;
- one ticket whose definition of done needs an answer from the Owner.

**Passes when, with no host command run by a person:**
- every ticket reaches a delivered PR;
- the Owner's answer reaches its Worker;
- a killed Worker sandbox is recovered by the Manager (through SCRUM-59);
- a Manager restart mid-run reconciles without escalating (SCRUM-64);
- the journal records each induced failure (SCRUM-71);
- no Done ticket is offered again (SCRUM-73).

This gate is the last step before the RC is re-cut. It is not a test in CI.

## 5. Decisions for Robert
1. **SCRUM-72 authorship.**
   - **(a) recommended:** a declared local `planner` Manager authors and the
     Claude `lead` approves. No RL change.
   - **(b)** the Claude `lead` authors and a local reviewer approves. It needs
     the unwired Claude proposer adapter, so more code, and it spends Claude
     quota on every plan.
   - **(c)** the GPU Worker plans its own ticket. That relaxes RL-67 and
     contradicts DECOMPOSER_DESIGN §1.3. Not recommended.
2. **SCRUM-68 in v0.7.1?** Recommended yes: the re-cut otherwise needs the same
   follow-up PR we needed for a9.
3. **Is SCRUM-22 closed** once SCRUM-69 lands? #179 and #189 fixed its
   reporting, and its transport residual is §3.4.

## 6. Estimate (revised 2026-10-05)
**The compressed 2–3 days (one PR) does not hold.** That figure assumed SCRUM-72
was wiring and SCRUM-59 was small. Scoped against the code, 72 needs an
authorship decision and a plan/approve path for a fleet that has never run one,
and 64 has the highest risk of the set.

| Work | Agent wall-clock |
|---|---|
| SCRUM-69 transport | 0.5 day |
| SCRUM-59 lifecycle requests | 1 day |
| SCRUM-64 reconciliation | 1–1.5 days |
| SCRUM-72 local Workers under any Manager (after §5) | 1.5–2.5 days, much of it live Ollama turns measured in minutes |
| SCRUM-70, 71, 73 | 1 day |
| SCRUM-21, 39, 62 (parallelisable) | 1 day |
| SCRUM-68 (if in) | 0.5 day |
| Integration PR: full CI (about 35 min a cycle) + on-Mac real-Claude suite + one bulk-fix round | 0.5–1 day |
| Throwaway-app end-to-end gate, plus fixing what it finds | 1–1.5 days |
| Re-cut and install the RC | 0.25 day |

**Range: 7–10 working days with one integrator, or about 5–7 if SCRUM-21, 39
and 62 (and 70, 71, 73 once 64 lands) run in parallel worktrees.** The core
chain 69 → 59 → 64 → 72 is serial and sets the floor.

**Drivers, most uncertain first:**
- the SCRUM-72 authorship decision, and how well the local model plans;
- local-model turn time;
- SCRUM-64's "cannot tell" cases;
- CI cycle time;
- how much the end-to-end gate finds.
