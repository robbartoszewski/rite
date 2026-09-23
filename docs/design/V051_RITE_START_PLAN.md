# `rite start <name>` — implementation plan for v0.5.1

Written against SPEC §9.14 as revised on 2026-09-20 (§9.14.9), **not** against
the original §9.14, which modelled a provider as a command argument and was
withdrawn. Read §9.14.9 first if the two appear to disagree.

Estimate up front: **7–10 sittings**, and the spread is two open questions
(items 0 and 6). Nothing here is research; the spread is integration surface.

⚠ **Item 0 blocks items 2 and 6 and is not mine to answer.** Read it first.

## What already exists, and is not in the estimate

Confirmed in the tree rather than assumed:

- **`ManagerRole`** (`config/managers.py`) — `name`, `engine`, `duties`,
  `preset`, `endpoint`, `model`, `agent`. `engine` matching `local:<class>`
  is already the D-72 shape.
- **`PRESETS`** — `lead`, `planner`, `executor`, with duty sets.
- **`manager_roles:` is parsed** from `config.yaml` (`parse.py:391` via
  `parse_managers`). The profile half of §9.14.9's item 4 exists today.
- **`loop/session.py`** — starting a named detached tmux session, refusing a
  duplicate, `_settled_alive`, and a status/stop pair. **This is the model to
  follow and in places to call**, not to reinvent.
- **`local/`** — `duty_router`, `engine_probe`, `harness`, `decomposition`.

## The work

**0. ⚠ DECISION REQUIRED: one root with subdirectories, or separate roots? —
0 sittings, blocks 1–3 sittings of others.**

`docs/design/V070_MULTI_MANAGER.md` and SPEC §9.14.9 record **opposite**
shapes, a day apart, neither citing the other, both attributed to the same
source. V060: "separate roots per Manager... each Manager owns its own
project root and its own `.rite/`", rejecting a shared `.rite/` by name as "a
second protocol... the two would drift" and calling that the load-bearing
decision. §9.14.9: subdirectories under one root.

**This is not a relocation difference.** Under separate roots, item 2's
`<root>/.rite/managers/<name>/` is discarded rather than moved, and item 6's
shared-loop question dissolves — two roots each have their own loop, and
coordination is the state layer that already exists and already has a
conformance suite. Building either costs the other.

*Recorded as D-79. Nothing below item 1 should start until it is answered.*

**1. Manager instances in `.rite/user/` — 1 sitting.**
Profiles are committed; instances are not. A record per running Manager:
name, pid, tmux session, started-at, the ceiling it was given. Reuses
`state.write_atomic` and the `locked()` sidecar. §9.14.9 item 4.
*Risk: low. This is a file format and two functions.*

**2. Per-Manager directories — 1–2 sittings.**
`<root>/.rite/managers/<name>/`, created on start. This is D-77's unblock and
§5.4's boundary becoming statable. **The 1–2 spread is whether existing state
moves now or later**: creating the directory is small; relocating heartbeats,
outbox and the rest is §5.4.6's "shared by accident" list and touches every
reader. **Recommend: create the directory and put only NEW state in it;
relocate in 0.6.0.** Moving state in a patch release is how you break a
project that upgrades mid-run.

**3. `rite start <name>` — 2.5 sittings.**
Resolution order and the 0/1/2+ rule from D-78, with the 2+ case listing
names. Three concrete facts from the code that the first draft of this plan
left implicit:

- **`start_cmd` already resolves its argument** through
  `_resolve_directory_or_alias` (`cli/main.py:~5758`), which tries a path,
  then a §8.9 alias, then **exits 1**. A Manager name must be intercepted
  BEFORE that call, or every Manager name hits the existing refusal.
- **Bare `rite start` does not start the loop** — `lifecycle/commands.py`
  reports its pid and deliberately does not start it (§9.14.0 records this as
  an AMENDMENT, not a reuse). So "starts the loop" is new code on the named
  path, and the bare path must stay untouched, which §9.14.0 requires.
- **`loop/session.py` is hard-wired to the loop**, not a generic starter:
  `session_name` builds `rite-loop-<slug>` and `start` shells
  `{command} loop run --watch`. Item 3 can CALL it for the loop half; item 4
  needs a new session function FOLLOWING its pattern, not a call into it.

*Risk: medium, and it is the alias collision — with an asymmetry the first
draft missed. `dispatch.add_project` checks only `alias in
registry.projects` and has no knowledge of Manager names. But `manager_roles`
is COMMITTED and the dispatch registry is PER-MACHINE, so the same
`config.yaml` can collide on one teammate's machine and not another's.
Refusing at registration (D-71) therefore cannot be a purely local check, and
that wrinkle needs settling inside item 3.*

**4. The Claude adapter — 2 sittings.**
A NEW tmux session function following `loop/session.py`'s pattern — its own
name, `is_alive`, `_settled_alive` and lock. Launch `claude` in tmux with the
generated prompt; on exit, `--resume
<session-id>` while the foreground process lives (§9.14.6); stop on the loop's
verdict per §9.14.4 including `closed`. **The ceiling is a session count plus
a wall-clock window** (D-69) — not spend, because §2.6.1 says rite cannot read
the quota.
*Risk: medium. The session-id capture is the fiddly part and it is the one
thing no existing code does.*

**5. Attachability, and printing how to reach it — 0.5 sittings.**
`tmux attach -t <name>`, printed AT START (§9.14.3). §9.14.3 records that the
loop's own precedent fails this — it prints the attach line only in `loop
status` — so this is a correction, not a copy.

**6. ⚠ The open question, and the reason for the spread — 1–3 sittings.**
**What does the loop do when several Managers run concurrently?** §9.14.9 item
1 says a project may run `lead`, `planner` and `executor` at once. The loop is
one per project, its verdicts are project-wide, and its capacity model is
`sandbox.max_concurrent_workers` — a single number. Three Managers sharing one
loop and one capacity number is not specified anywhere.
*If the answer is "one loop, Managers coordinate through claims" this is 1
sitting. If it is "a loop per Manager" it is 3 and it touches the lock, the
log and the verdict set.* **This needs deciding before item 3 is built, not
during.**

⚠ **Item 0 may dissolve this question rather than answer it.** Under separate
roots each Manager has its own loop by construction, and there is nothing to
share. The first draft of this plan claimed the question was unanswered
anywhere; that was wrong — it is answered in `V070_MULTI_MANAGER.md`, by a
design this plan had not read.

**7. Tests — 1.5 sittings.**
Real tmux, following `test_loop_start_really_starts.py`, which exists because
mocked tests certified a facade. Plus the 0/1/2+ matrix, the duplicate
refusal failing CLOSED (D-74), and the ceiling refusing at zero.

## Ordering

**0 (decide) → 6 (decide, or observe it dissolved) → 1 → 2 → 3 → 4 → 5**,
with 7 throughout rather than at the end.

Item 0 first because it can discard item 2 entirely and dissolve item 6. Item
6 next because it can invalidate item 3's shape. Items 1 and 2 before 3
because `start` writes the instance record they create.

**Items 3, 4, 5 and 7 are unaffected by item 0** and could start immediately
if the decision is slow — the resolution order, the 0/1/2+ rule, the adapter
and the attach line are the same under either coordination model. That is the
fallback if item 0 needs to wait for Robert.

## What this plan does NOT include

- **Relocating existing `.rite/` state** — item 2's recommendation defers it.
- **The `local` adapter.** D-64 puts it second and §9.14.2 keeps the interface
  unstable until it exists; it is 0.6.0 by Robert's roadmap.
- **Resolving §9.14.7's compliance contradiction.** rite still prompts for,
  keychains and reads back the Claude token, which requirement 3 forbids. **The
  adapter in item 4 inherits that**, and shipping it does not make it worse —
  but it does not fix it either, and the measurement that would settle it
  (whether the inheritance channel persists the token) is still untaken.
- **An unattended overnight Manager.** Out of scope per §9.14.6, not deferred.

## Confidence

Items 3, 4, 5 and 7 are well-bounded and unaffected by item 0. Item 4's
session-id capture is the likeliest overrun. **Items 0 and 6 are not
estimates, they are decisions with estimates attached** — and if it lands on "a loop per Manager" the total moves
to 9–12 and item 3 should be replanned rather than stretched.
