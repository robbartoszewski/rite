# A work-seeking loop for rite — implementation plan, revision 3 (2026-09-19)

> ## ⚠ Built. This plan is now a record, not a queue.
>
> Read the code first; where they disagree, the code is right and this is the
> bug. What shipped differs from revision 2 in three ways that matter, each
> because building it proved the plan wrong:
>
> | Plan said | Shipped |
> |---|---|
> | **L-4 is a reap** — release claims whose holder is gone | It **reports** and never releases. rite cannot tell a crashed session from one thinking hard, and the cost of guessing is two sessions on one path. L-4 instead separates `blocked` (a live holder will let go) from `deadlocked` (nobody is coming back), which is the distinction the loop actually needed |
> | **L-5 is the surface over a working loop** | `rite loop start` was a **facade** — it printed "started", exited 0, and no session existed, because tmux exits 0 for a session whose command died instantly. Fixed; the tests now use real tmux, because every previous test mocked it |
> | **L-3 is a dispatch record** | It is an **intent** record covering only the seconds a dispatch is invisible. A ledger of running sessions drifts in the direction that stops work; observation cannot drift, because it reads the thing rather than a note about it |
>
> **Still unbuilt:** L-6 (local-tier dispatch, zero Anthropic quota, blocked
> on RL-T0's agent) and L-7 (Claude dispatch — spends quota, needs the §9.12
> amendment and Robert's decision). So the loop **observes and reports**; it
> does not yet dispatch, and "rite keeps itself going" is not true yet.



**Below this line is revision 2 as it stood before anything was built**, kept
because the reasoning is what the banner above is a correction to — and
because a plan with its wrong turns deleted teaches nothing. Revision 1 was
reviewed adversarially against the tree and **its central premise was wrong.**
What changed at the time, and why:

| Rev 1 said | Actually |
|---|---|
| The blocking rule is §2.5 | It is **§9.12**. §2.5 is the *coordinator pool*; Worker sessions are already carved out (§5.3.1) |
| "rite has a watchdog and an assigner, and **no executor**" | `src/rite_ai/local/` **is** an executor, and `local/__init__.py` says "Nothing here calls an Anthropic API" |
| The loop dispatches `claude` sessions | That is the most expensive available answer, and the last one to reach for |
| The claims ledger tells the loop who is busy | `start_worker` writes **no claim**. For minutes after dispatch the ledger says that Worker is free |

Verified at `origin/main`; every quote below was read out of the tree.

---

## 1. The gap, restated correctly

Tonight's symptom: *"the only way to make an Owner keep going is a human typing
'don't stop after BEN-191'."*

The spec already has a work-seeking executor, and it is not a rite daemon.
SPEC §2.7:

> "Workers start when a Manager has safe work to give one, never because a
> clock crossed a boundary with nobody watching."

and §5.3.1 has the Manager "create its Workers' sandboxes from within itself as
ordinary tool calls … the human approval happens once, at the Manager, rather
than once per Worker."

**So the executor is the Manager session, and the bug is that it stops.** Not
that rite lacks a daemon. A session finishes what it was asked and exits
because nothing standing tells it there is a next thing — `worker.yml`'s
`claude_instructions` shapes character, not work-seeking, and there is no
equivalent at the project level at all.

That reframing makes the cheapest fix the real one, and demotes the daemon from
"the feature" to "the mechanical support around it".

---

## 2. The rule that actually governs, quoted in full

SPEC §9.12, which §2.7 forwards to as "the general rule":

> "**Nothing rite runs unattended starts a Claude session.** … anything
> scheduled that could start a session would be spending tokens while nobody is
> watching … Sessions start only from a command a human types — today `rite
> pool fill` … and `rite sandbox start <worker>` … Both are explicit and **in
> the foreground**."

Three things follow, and revision 1 got all three wrong:

1. **The word is "Claude session".** A local-engine harness is not one. §9.12
   does not block an unattended local executor at all.
2. **"In the foreground" is load-bearing.** A tmux-detached loop is not in the
   foreground under any honest reading. Revision 1 proposed an amendment that
   quietly redefined "foreground" as "a human typed the start command once" and
   presented that as principle-preserving. It is not, and this revision says so
   rather than arguing it.
3. **No test resists the §2.5 amendment** (`tests/test_rehearsal_round7.py:73`,
   `tests/test_lifecycle.py:426` pin the tick's non-action, which nothing here
   touches). That silence was the signal that §2.5 was the wrong section, not
   that the amendment was safe.

---

## 3. Four layers, cheapest first. Ship 1–2, then decide.

### Layer 1 — Standing instruction. Zero quota, no amendment, closes tonight's gap.

The thing a human was typing by hand becomes generated text. A `## Working the
queue` section in the project `CLAUDE.md` (`cli/init/claude_gen.py`, inserted
into the `parts` list at `:130`) and the Worker equivalent
(`workspace/manage.py:720`): *this project works a queue; when you finish a
ticket, look for the next one; here is the command that tells you what is
ready.*

- Costs nothing. Spawns nothing. Amends nothing.
- Governed by the existing per-section checksum rules
  (`generated_sections.mark_sections`, `update/refresh.refresh_text`), so it
  lands as `inserted` on `rite update --files-only` and is thereafter refreshed
  only while unedited. If we ever rename it, `SUPERSEDED_HEADINGS`
  (`update/refresh.py:65`) needs the old name or projects get both copies.
- **Offers, never instructs.** It says the sweep exists and names the command.
  It does not say "run this now" — a generated file that starts spending quota
  on read is the exact surprise everything below is avoiding. It is also
  conditional in tone: what the project wants, not how a particular harness
  must do it.

**This alone would have prevented tonight's manual typing.** It ships first.

### Layer 2 — The mechanical loop, which starts no Claude session.

`rite loop run` (foreground) / `rite loop start` (tmux). Per cycle it:

- NAMES claims whose holder is gone, and releases none of them (§5);
- runs `distribute` — the *existing* Manager→Worker path, with Q9's three
  rules — rather than a second assignment path of its own;
- reports what is ready, what is blocked, what it did.

**It spawns nothing, so §9.12 is untouched** and no amendment is needed to ship
it. It is `scheduler-tick`'s judgement-free sibling with a cadence of its own,
and it is the honest version of "a command that runs in the background".

### Layer 3 — Local-tier dispatch. An executor that costs zero Anthropic quota.

`src/rite_ai/local/` already is one: `harness.run_subtask` "pulls the
assignment, prepares the workspace, hands the agent exactly one subtask and its
spec slice, **runs the verify ITSELF**, commits to a local branch, reports, and
releases". `runners.py`'s verifier and committer landed yesterday.

**Blocked on one thing: the `Agent`.** `harness.py` injects it as a Protocol
and `src/` constructs none, pending RL-T0. That is the whole gap between "rite
has a free executor" and "rite can work a queue for nothing".

For a user near a weekly cap this is the interesting layer, and revision 1
omitted it entirely while citing the design note it is written in.

### Layer 4 — Claude-session dispatch. Opt-in, foreground, and honestly amended.

Only if Robert wants it, and only with §9.12 amended in words that admit what
they change:

> **Proposed §9.12 amendment, stated plainly.** "In the foreground" currently
> means a human is present for each session start. This would widen it to "a
> human started a process whose only purpose is starting sessions, with a
> persisted ceiling they set". **That is a real weakening**: the human is
> present for the decision to run, not for each session. The compensating
> controls are the persisted budget (§6), the drain-stop (§7) and the fact that
> `rite loop status` names every session it started.

Not recommended for the first landing. Layers 1–3 close the actual gap.

---

## 4. The cycle, corrected

1. **Lock.** One loop per project root, pid-based, the shape
   `scheduler/lock.py` already uses — and for the same reason its docstring
   gives: the lock lives in the body so no caller can route around it.
   ⚠ **A tracked `.rite/` makes every git worktree its own project root**, so
   two worktrees hold two different locks and two different `claims.json` while
   pointing at one board. The loop refuses to run in a worktree whose `.rite/`
   is not the main checkout's, and says why.
2. **Window open?** `workers_at(schedule, minute)`. Zero is §2.7.3's clean
   stop: sleep to the boundary. ⚠ Read the boundary from
   `schedule-state.json`'s last count — the file `scheduler-tick` owns — rather
   than recomputing, or the two disagree about when the boundary happened.
3. **Capacity.** Not the claims ledger. `start_worker` writes no claim, so for
   the minutes between dispatch and the session's first `rite claim` the ledger
   reports that Worker free — and a 120s cycle re-dispatches it. Capacity is
   `min(schedule, cap) − (claims ∪ **the loop's own dispatch record**)`, where
   the dispatch record is a file in `.rite/`, written **before** the spawn.
   ⚠ `count_active_sandboxes` counts every `rite-` sandbox **machine-wide,
   across projects** (`sandbox/__init__.py:630`), so `start_worker` can refuse
   for a reason the loop cannot see. That refusal is an expected outcome, not a
   crash — see §5.
4. **Reap.** §5.
5. **Distribute.** Call `distribution.distribute`. Do not re-implement the
   board query: the pre-distribution filter is "tickets labelled with this
   Manager", and dispatching against it without letting `distribute` move the
   label leaves the ticket in the query for ever — the queue never drains and
   the same ticket goes out again next cycle.
6. **Dispatch** (layer 3 or 4 only).
7. **Sleep**, default 120s.
8. **Stop.** `--until-empty` exits when the board is empty **and** the dispatch
   record is empty — not when the claims ledger is, which is empty for minutes
   after a dispatch and would exit while a session was still booting.

---

## 5. Judgement — corrected, and with the position narrowed

**The loop still makes no model calls.** But revision 1 claimed every case has
a defensible mechanical answer, and the review found one that does not.

**The case a rule cannot decide: is this ticket workable at all?** A one-line
ticket, a ticket blocked on an unmerged PR, three tickets in a trench coat. The
mechanical answer — "it has a label and a slot, dispatch" — is wrong in the
expensive direction, because a whole session burns to discover it. rite's
architecture already puts that judgement in a Manager (`_assign_the_pool`
routes the `decompose` stage; `local/decomposition.py` is the artifact; RL-6
gates it). **The loop must not decide it.** The escape is not "call a model
from the loop" — it is layer 3, where the judgement happens in a tier that
costs nothing.

| What the loop finds | What it does |
|---|---|
| **Claim held, session gone** | **Reports it. Releases nothing.** ⚠ CORRECTED 2026-09-19 against the tree: this row planned `ClaimsLedger.force_release(by, reason)`, and what shipped (`claims/suspect.py`) only names the claim — "named, never released", killed by two review rounds because rite cannot tell a crashed session from one thinking hard, and the cost of guessing is two sessions on one path. The only `force_release` callers in `src/` are the human-invoked CLI path and the pool's worker swap; nothing in the loop reaps. `expire_offline_claims` is still not the mechanism, for the reason this row already gave |
| **`worker_sandbox_status` says `known=False`** | Nothing. It is the "could not ask" answer and its own docstring names the conflation with "not found" as the thing not to do |
| **`sandbox.enabled` is false** | Moot, and kept because the reasoning outlives the plan: status returns "not found" for every Worker, so a reap built on it would force-release every live claim in the project and publish it to the fleet. Nothing reaps, so nothing is disabled — but anything that ever does needs this guard |
| **Session alive, quiet** | Nothing; report its age. rite cannot see inside a session, and "no commit in 20 minutes" is a Worker thinking |
| **`start_worker` refuses on the machine-wide cap** | Expected, not a crash. Log it, do not count it as a dispatch, retry next cycle |
| **Sandbox name already in use** (a human ran `rite sandbox start` for that Worker) | Expected. Treat that Worker as busy for this cycle. ⚠ Revision 1 sent this to the catch-all, so a person doing the normal thing killed the loop |
| **Ticket dispatched N times** | Report it and skip it **for this run**, in memory. ⚠ Revision 1 wanted a board label; a label permanently drops a ticket from the queue and needs a human to undo, decided by a rule that cannot tell a bad ticket from a machine that rebooted twice |
| **Owner lease expired** | Nothing, and say so. Election is the tick's job |
| **Board unreachable** | Backoff, 3 cycles, then stop |
| **Crash mid-dispatch** | The dispatch record is written **before** the spawn, so the next run finds it and reconciles rather than losing the session |
| **Anything else** | Stop, print state, exit nonzero |

Nothing here interrupts. Escalation is pull — the outbox and `rite loop
status` — per RL-39: never occupy the channel a person would use to stop it.

---

## 6. Quota, corrected

- **Layers 1–3 spend zero Anthropic quota.** Layer 1 is text; layer 2 spawns
  nothing; layer 3 is the local tier.
- **Layer 4 costs one session per *dispatch*, not per ticket.** A handed-back
  ticket is dispatched again. Revision 1 said "per ticket" and undercounted.
- **Sessions are not tokens, and rite says so** (`budget/__init__.py:19`): rite
  cannot attribute tokens per project. Three long sessions are not cheaper than
  thirty short ones. A session count bounds *sessions*; it does not bound the
  thing the weekly cap measures, and the plan must not imply otherwise.
- **The budget is persisted to `.rite/`, not held in memory.** Revision 1's
  counter reset on every crash, stop/start, tmux kill and reboot, which makes
  "N for the life of the loop" true and useless against a weekly cap.
- **Default:** no default ceiling that is secretly a concurrency number.
  Revision 1 defaulted to `max(workers_at)` — a concurrency figure used as a
  total, which stops a `--forever` loop after ~3 dispatches, reads as broken,
  and pushes the user straight to `--max-sessions 50` where nothing bounds the
  week. Layer 4 requires an explicit `--max-sessions`; there is no default.

---

## 7. Consent, and stopping

Both constraints hold, unchanged:

1. **`assign_unattended` stays false and the loop never flips it.** If it is
   off, `rite loop start` says so once and runs on what a human assigned.
2. **Nothing schedules itself** — no cron, no launchd, no login item. Reboot
   ends it; `rite loop status` then says "not running".

**`rite loop stop` does not kill anything.** It sets the drain signal, the loop
finishes its cycle, refuses new work and exits. Two reasons: `pool/` has no
kill path *at all*, deliberately; and killing mid-dispatch produces a claim
held by a session that no longer exists — the §2.6 failure, caused by the stop
command. `ManagerMonitor` already takes `draining`, threaded through to
`refusal.refusal_reason` ("shutting down: …"), and **nothing in `src/` sets
it** — this is its first caller.

Prior art cited as before: `lifecycle.start`'s docstring, which reports rather
than performs the persistent, networked and quota-spending steps, and records
that the *spec* was amended rather than the code.

---

## 8. What gets built, in order

| # | Ticket | Layer | Spends |
|---|---|---|---|
| L-1 | LANDED (32e80f7): `## Working the queue` in project + worker `CLAUDE.md` | — | nothing |
| L-2 | LANDED (65c188d): `rite loop run --dry-run` — one cycle, decisions printed, nothing done | — | nothing |
| L-3 | LANDED (591e733): the dispatch record in `.rite/`, written before any spawn — an INTENT record covering the seconds a dispatch is invisible, not a ledger of running sessions | — | nothing |
| L-4 | LANDED, and not as a reap: `claims/suspect.py` names a claim whose holder is gone and releases nothing (88d01db). `force_release` gained a `worker=` filter separately (2b38592) | — | nothing |
| L-5 | LANDED: `rite loop run/start/status/stop` all exist. `start` was a facade that printed "started" and left no session — tmux exits 0 for a command that died instantly — fixed in a50dcbe, and `status` now asks tmux rather than a lock file (2aa76c1) | — | nothing |
| L-6 | Local-tier dispatch | 3 | nothing — **blocked on RL-T0's `Agent`** |
| L-7 | Claude dispatch + persisted budget + §9.12 patch | 4 | quota — **not started without Robert** |

L-1 alone closes tonight's gap. L-2's dry run is the review artifact — it would
have caught the claims-ledger error before anything spent.

⚠ **Read the table as a record, not a plan: L-1 through L-5 have all landed**
(checked against the tree 2026-09-19, not against this document). What is left
is L-6 and L-7, and they are the two that were always the hard part. A plan
whose finished rows still read as future work is how the next session rebuilds
something that exists — and this one had a corrections header at the top while
its body still described L-4 as a reap, which is worse than being merely
stale: a reader who starts at the body never learns the header disagrees.

---

## 9. Falsifiers (RL-45)

- **The standing instruction is ignored.** Sessions still stop after one
  ticket → layer 1 is not where work-seeking lives, and layer 2 becomes load-bearing.
- **The dry run disagrees with reality.** It says "would dispatch 3" and a real
  cycle does something else → the capacity model is still wrong.
- **The dispatch record leaks.** Entries for sessions that ended → reconciliation
  is wrong, and capacity will drift down to zero until someone deletes the file.
- **Somebody is surprised.** Anyone finds rite writing to a board, spending
  quota, or running after a reboot without having asked. → §7 failed, and that
  is the one failure tuning does not fix.
