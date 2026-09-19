# Phase 2 — claimable tickets

Decomposed 2026-09-13 from `IMPLEMENTATION_PLAN.md` P2.0–P2.7 and SPEC §2.4,
§3.3, §3.4. Sized for **one worker, one sitting** — the plan's own units are
3–7 days each, which is not claimable.

**Not on the board yet** — no JIRA site is configured on this machine; the
recorded safe write target is `RT`, never `SCRUM` or `RW`.

Every ticket cites D-numbers rather than restating them. If a ticket and a
decision disagree, the decision wins and the ticket is the bug.

---

## ⚠ READ FIRST — spec drift against the published code

The Phase 2 plan predates the rename, the credential redesign and the CLI
review. Four findings, checked against `origin/main` today:

1. **Every path in P2.1–P2.6 is wrong.** The plan says `src/rite/coordination/`,
   `src/rite/owner/lease.py`, `src/rite/manager/`, `src/rite/claims/global.py`,
   `src/rite/routing/`. The package was renamed to **`rite_ai`**. Read every
   `src/rite/…` as `src/rite_ai/…`.
2. **`src/rite_ai/claims/global.py` should not be a new file.** Phase 1 shipped
   `claims/ledger.py` with `ClaimsLedger`, sidecar `flock` locking
   (`state.locked()`) and a force-release audit trail. P2.5 extends that, and
   the cross-machine design must reconcile with its on-disk schema rather than
   invent a parallel one.
3. **P2.6's premise no longer holds.** It assumes expertise routing is a code
   module. Phase 1 solved expertise **agent-side**: `claude_gen.py` renders an
   expertise table into the Owner's `CLAUDE.md` and the session routes on it.
   There is no router to add a seam to. See P2-6 below — it needs a decision,
   not an implementation.
4. **§10.4 is NOT stale** — it already describes the per-project namespace
   (`<namespace>/jira_token`) the credential redesign shipped. Phase 2's
   multi-machine credential story is settled and needs no ticket.

`perform_handover` (`lifecycle/commands.py:400`) exists and its docstring
already names the Phase-2 lease-expiry caller. Signature:
`(root, worker=None, reason="clean shutdown", ticket="")`.

---

## ⚠ What the spec does NOT settle

49 decisions covering a protocol is not an implementable spec. Where a ticket
below is marked **[NEEDS DECISION]**, the worker should implement the rest,
raise the question, and keep moving — not stall.

- **`config.yaml` has no `managers:` list.** §2.4 says "Managers are listed in
  priority order in `config.yaml`", and `ProjectConfig` has no such field.
  Nothing specifies its shape. → **P2-0a**.
- **No coordination-repo remote anywhere in config.** Nothing says how a
  machine learns which repo to coordinate through. → **P2-0a**.
- **Owner lease duration has no config key.** The plan says "default 15-minute
  lease, configurable". `PoolConfig.lease_expiry_minutes` is explicitly NOT it
  (SPEC:399 — that is the local pool readiness lease). → **P2-0a**.
- **Skew tolerance is a hardcoded 60s in the plan** (D-42). Configurable or
  not is unspecified.
- **`promotion-request.json` has no schema.** §2.4 describes the protocol, not
  the file.
- **Message-log commit format is unspecified.** "Ordinary commits for
  decisions, blockers, handovers, promotion events" — no subject convention,
  so nothing can parse them back.

---

## Dependency order

```
P2-0  config schema ─────────────► blocks everything
        │
        ▼
P2-1a interface ──► P2-1b git CAS ──► P2-1c full-state merge
                          │
        ┌─────────────────┼──────────────────┬─────────────┐
        ▼                 ▼                  ▼             ▼
   P2-1d round-trip  P2-1e doctor      P2-4a heartbeat  P2-5a publish
                                                          claims
        ┌─────────────────────────┐
        ▼                         │
   P2-2a lease file ──► P2-2b promotion ──► P2-2c lost-ack ──► P2-2e demotion
                              │                                     │
                              ▼                                     ▼
                        P2-2d split-brain                    P2-3c handover
                                                              on promotion
```

**Strictly serial (the critical path):**
`P2-0a → P2-1a → P2-1b → P2-1c → P2-2a → P2-2b → P2-2c → P2-2e → P2-3c`
— nine tickets. Everything else hangs off it.

### ⚠ The opening had zero parallel work — and worker count was not the lever

Two independent walks of the **Blocked by** lines agree: through `P2-0`,
`P2-1a` and `P2-1b` there was nothing else claimable, because every off-path
ticket sat behind `P2-1b`. Three workers, four or ten makes no difference.
Simulating three workers — one on the critical path, two taking off-path work,
one ticket per sitting — the original set idles **7 worker-sittings**, three of
them with BOTH off-path workers idle at the start.

**The fix is not more tickets, it is binding consumers to the interface
instead of to git.** §3.3.5 and D-20/D-21 already put the state layer behind
an interface with substitutable backends ("Redis or S3 can substitute... all
available via the interface"). So `P2-3a`, `P2-4a`, `P2-5a`, `P2-1d` and
`P2-2a` do not need git CAS. They need the **interface**, which is `P2-1a`.

`P2-1a` therefore ships three things, and that is what unblocks the run:
the interface, a local backend, and a backend-agnostic **conformance suite**
the git backend must later pass. Building consumers against an interface
before its production backend exists is what D-20 is for.

Re-simulated: **1 idle worker-sitting**, in the final round as the run winds
down. The critical path also shortens from nine tickets to eight, because
`P2-2a` binds to the interface and only `P2-2b` (promotion, where a lost
update actually causes split-brain) needs `P2-1c`.

**This dissolves the "do not spend P2-1d/P2-1e early" constraint.** That was
derived from a set where they were the only off-path work at `P2-1b`/`P2-1c`.
Under this structure the `P2-1c` window holds `P2-3a`, `P2-4a` and `P2-5a`,
so `P2-1d`/`P2-1e` can be taken when they come up without stranding it.

**Honest caveat:** consumers built against the local backend are complete and
tested, but their cross-machine behaviour is only exercised once `P2-1b`/
`P2-1c` land. The conformance suite in `P2-1a` is what bounds that risk, which
is why it is in the same ticket rather than deferred.

**Available from minute one:** P2-0b, P2-0c, P2-0d.
**Unlock at P2-1a (the interface):** P2-1d, P2-2a, P2-3a, P2-4a, P2-5a.
**Unlock at P2-0a:** P2-1e.
**Unlock at P2-2b:** P2-2d, P2-4d.
**Independent of all of it:** P2-6 (blocked on a decision), P2-STRETCH.

Workers are fungible, so this ordering lives here and in each ticket's
**Blocked by** line — not in who picks it up.

---

## Tickets

### P2-0a — Coordination config schema  ⛔ blocks the critical path  [NEEDS DECISION]
Add to `ProjectConfig`: a priority-ordered `managers:` list, a coordination
remote, a state-branch name, an Owner-lease duration, and a skew tolerance.
Parse, serialise and round-trip them (`test_config_roundtrip_is_total.py`
walks `dataclasses.fields()`, so a new section is covered automatically).
**Decisions:** §2.4, D-19, D-20, D-42.
**Not settled:** every field name and default. Propose, record in the ticket,
and raise — do not let the naming block the branch.

### P2-0b — `owner-lease.json` and `managers/<name>.json` schemas  ·  no blocker  ·  parallel  [NEEDS DECISION]
The two state files everything else reads and writes. Fields for the lease are
named in §2.4.1 (owner, acquired, expires, priority); the Manager status file
is not specified beyond §3.4's prose. P2-1a binds to these, which is why they
come first rather than being invented inside it. **Output: the schemas plus
their (de)serialisers**, proposed and raised — not a stall.

### P2-0c — Synchronised-burst harness for cross-machine tests  ·  no blocker  ·  parallel
The fixture P2-2d and P2-1c both need, built once and first. Model on
`test_blast_radius_concurrent.py`, which records that desynchronised workers
never revisit the window the bug lives in — a broken lock passed the first
version of that soak. No protocol code required, so it is available from
minute one.

### P2-0d — `promotion-request.json` schema + message-log commit convention  ·  no blocker  ·  parallel  [NEEDS DECISION]
Two gaps listed above, ticketed because they are real artifacts needing no
code from P2-0a. §2.4 describes the promotion protocol but not the file; the
message log is "ordinary commits for decisions, blockers, handovers, promotion
events" with no subject convention, so nothing can parse it back.
**The output is a written proposal plus the parser/serialiser it implies** —
propose, implement against the proposal, raise the question. Do not stall.

### P2-1a — State-layer interface, local backend, conformance suite  ·  Blocked by: P2-0a, P2-0b  ⛔ the keystone
`read_state`, `write_state` (CAS), `append_message`, `read_messages` (§3.3.5).
**Three deliverables, deliberately one ticket:** the interface; a local
filesystem backend; and a backend-agnostic conformance suite that any backend
must pass. D-20/D-21 already require substitutable backends, so the local one
is not test scaffolding — it is the second implementation the abstraction
exists for, and it is what lets every consumer be built and tested before git
CAS exists. **Decisions:** D-20, D-21, §3.3.5.

### P2-1b — Git CAS via `--force-with-lease`  ·  Blocked by: P2-1a, P2-0c
Must pass P2-1a's conformance suite unchanged.
State branch, always one commit, force-pushed. Use the explicit
`--force-with-lease=state:<oid-or-absent>` refspec form so the first-ever
write to a brand-new branch is still atomic (§2.4.2 step 1). Fetch before
write, retry on conflict. **Decisions:** D-19, §2.4.2.

### P2-1c — Read-merge-write the FULL state  ·  Blocked by: P2-1b
A force-push replaces the ref's tree; a delta-only push silently destroys
other Managers' files. Merge the whole state before every push.
**Decisions:** §2.4.2's own warning. **This is a data-loss bug if skipped** —
give it a concurrent test, not a unit test.

### P2-1d — Unknown keys round-trip  ·  Blocked by: P2-1a  ·  parallel
`managers/<name>.json` must preserve fields it does not recognise. An older
Manager writing back only known fields silently erases a newer one's
declaration — same shape as P2-1c. Free now, expensive later.

### P2-1e — `rite doctor` probes force-push permission  ·  Blocked by: P2-0a  ·  parallel
Many hosts' branch-protection defaults reject force-push permanently, which
otherwise fails as a silent "race loss" rather than a loud misconfiguration.
Follow `doctor`'s existing convention: report, exit non-zero, name the fix.

### P2-2a — `owner-lease.json` + renewal loop  ·  Blocked by: P2-1a
Fields: owner, acquired, expires, priority. Renewal on a timer.
**Decisions:** §2.4.1, D-16, D-17.

### P2-2b — Promotion with clock-skew tolerance  ·  Blocked by: P2-2a, P2-1c
Fetch → check expired **with margin** → write → CAS push. Compare against
`expires + skew_tolerance`, never the bare boundary. NTP-synced clocks are a
documented precondition. **Decisions:** D-42, §2.4.1.

### P2-2c — Lost ack must not cause a false stand-down  ·  Blocked by: P2-2b
On push rejection, re-fetch and re-read before concluding you lost. Stand down
only if the re-read lease genuinely names someone else. **Decisions:** §2.4.2
step 5.

### P2-2d — Split-brain adversarial test  ·  Blocked by: P2-2b, P2-0c  ·  parallel
Two simultaneous promotions → exactly one winner, one explicit loser. Model it
on `test_blast_radius_concurrent.py`: synchronised bursts, not sleep loops —
that file records that desynchronised workers never revisit the window the bug
lives in, and a broken lock passed the first version of that soak.

### P2-2e — Graceful demotion  ·  Blocked by: P2-2c
Promotion request → incumbent finishes **one operation** → transfers → releases.
**Decisions:** D-43 defines "operation" precisely (one backend write or one
board-state transition; a four-call sequence may hand over between any two).
Do not re-derive it.

### P2-3a — Owner reads Manager status, assigns by label  ·  Blocked by: P2-1a  ·  parallel
**Decisions:** §2.3, D-18.

### P2-3b — Stalled-Manager detection → `perform_handover`  ·  Blocked by: P2-3a
D-14's **second** trigger. Reuse the existing function; do not write a second
handover path.

### P2-3c — On promotion, hand over the OUTGOING Owner's own work  ·  Blocked by: P2-2e
D-14's **third** trigger, with `reason='owner lease expired, promoted by
<manager>'`. **Every prior version of the plan, the shipped docstrings and the
decisions register all dropped this** — D-14's row says so explicitly. It is
on the critical path for that reason.

### P2-4a — Manager heartbeat to coordination repo  ·  Blocked by: P2-1a  ·  parallel
Include an in-flight task **count** (one integer). Capacity is unenforceable
without it and retrofitting means the Owner cannot trust the field until every
machine upgrades. **Decisions:** §3.4, D-17.

### P2-4b — Manager reads assignments, distributes to Workers  ·  Blocked by: P2-4a
Reuse the `scheduled` label convention. **Decisions:** §2.7, D-44.

### P2-4c — A Manager can refuse an assignment  ·  Blocked by: P2-4b
Full, shutting down, missing a module. Core needs this regardless of the
stretch goal.

### P2-4d — Manager monitors lease, promotes when next in priority  ·  Blocked by: P2-2b  ·  parallel

### P2-5a — Publish Worker claims to the coordination repo  ·  Blocked by: P2-1a  ·  parallel
Not via the ticket backend — no rate limit, atomic push semantics. Extend
`ClaimsLedger`; do not fork it. **Decisions:** D-19, §5.2.

### P2-5b — Claim check reads local + published  ·  Blocked by: P2-5a

### P2-5c — Expire claims from offline machines  ·  Blocked by: P2-5b
Based on claim timestamp and the Manager's last heartbeat push.
**Note:** force-releasing another session's claims is destructive and Phase 1
requires attribution and a reason (`force_release(paths, by, reason)`, audit
trail). Cross-machine expiry must not bypass that.

### P2-6 — Expertise routing  [DECISION STATED — needs a yes/no]  ·  independent

**The decision, precisely:** does Phase 2 move expertise routing into code, or
keep it agent-side and use the state layer only to publish the table?

Phase 1 has no router. `claude_gen.py:291` renders an expertise table into the
Owner's `CLAUDE.md` and the session routes on it. D-6 covers what happens when
an expert is unavailable; nothing covers *where routing executes*.

- **(A) Keep it agent-side.** Phase 2 publishes the expertise table through the
  state layer so every machine's generated `CLAUDE.md` carries the same one.
  No new module, no new config surface. Cost: routing is not programmatically
  queryable and cannot be asserted in a test.
- **(B) Move it into code.** A resolver maps (ticket, tags) → expert.
  Deterministic and testable. Cost: a new module and config surface, and it
  encodes as a lookup table a judgement the session already makes.

**Recommendation: (A).** The only thing Phase 2 genuinely adds is that the
table must be the SAME on every machine — a publication problem the state
layer already solves, not a routing problem. Routing itself is judgement about
who knows what, which is what sessions are good at and lookup tables are bad
at; D-1's reasoning cuts this way too, since the thing worth spending code on
is the mechanical part, not the judgement.

**If (A):** this becomes an ordinary ticket — "publish the expertise table via
the state layer, render it into every machine's `CLAUDE.md`" — blocked by
P2-1a, off the critical path, no decision outstanding.
**If (B):** it needs its own decomposition and should not enter this run.

### P2-STRETCH — Capability and permission routing  🟡 not core
Explicitly out of the critical path (P2.0, marked stretch 2026-09-12). Its
seams are already ticketed above where they are free: P2-1d (round-trip),
P2-4a (in-flight count), P2-4c (refusal), P2-3a (Manager-owns-Worker in
coordination state). Build none of it now.
