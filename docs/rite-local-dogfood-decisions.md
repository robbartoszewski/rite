# The rite-local dogfood: what is settled, and what is not

**Status as of 2026-09-18, late evening. No overnight run was launched. No real
Claude Worker session has ever been started.**

This is the one document to read before starting a rite-local dogfood run. It
replaces the need to read the three session writeups it is drawn from
(`RITE-LOCAL-DOGFOOD-BLOCKED.md`, `RITE-LOCAL-DOGFOOD-PLUMBING.md`,
`RITE-LOCAL-DOGFOOD-PHASE1.md`, all in `~/AI/rite-dogfood/`), though they hold
the raw command output behind each claim here.

Everything below was executed. Nothing is inferred from reading source.

---

## 1. What the dogfood was meant to be, and what it can actually be today

The goal was **rite orchestrating its own Workers unattended**: the Owner picks
a `scheduled` ticket and assigns it to a Manager, the Manager hands it to a free
Worker, the Worker works, nobody watches.

> ⚠ **CORRECTED 2026-09-19 against the tree. Both reasons below are now
> false; the conclusion is mostly not.** Kept rather than rewritten, because
> which half went stale is the useful part — the callers were built, and the
> thing that still does not happen is a different thing from the thing that
> had no caller.
>
> - **Owner → Manager HAS a caller.** `manager_views` and `assign_to_manager`
>   are invoked from the scheduler tick (`scheduler/__init__.py:641,681`).
>   It is gated, not absent: `coordination.assign_unattended` defaults to
>   **false** (`config/models.py:295`), Q9's middle answer. A reader acting
>   on the old text would build a caller that exists; the actual step is
>   setting a config key and enrolling the machine in `.rite/machine`.
> - **`ManagerMonitor` IS given a backend and a schedule** —
>   `scheduler/__init__.py:468-476` passes `backend=board` and
>   `schedule=project.config.schedule`.
> - **What genuinely does not happen is the SPAWN, and it is deliberate.**
>   `rite loop` reports what it would start and starts nothing: "Nothing in
>   this module writes, spawns or spends", `would_dispatch`, "DRY RUN —
>   nothing was started". §2.5's "nothing spawns a session automatically"
>   still holds, and a session costs quota, which is the one damage no
>   cleanup reverses. So the conclusion below — that a run needs a driver —
>   survives; its reason changes from "unwired" to "wired, and deliberately
>   stopping one step short of spending money".

**That chain does not exist in running code.** Not slow, not misconfigured — it
has no caller:

- **Owner → Manager has no caller.** `manager_views`, `choose_manager` and
  `assign_to_manager` are built and tested, and nothing invokes them.
  `rite board assign` is a different thing: it sets the ticket backend's own
  assignee field, while rite routes on the worker-name label.
- **Manager → Worker is built and never given a backend.** `distribute()` works
  and is called from `ManagerMonitor._distribute`, but the scheduler constructs
  `ManagerMonitor(...)` with no `backend=` and no `schedule=`, and `_distribute`
  returns immediately when `self.backend is None`.

**Assignment is a command, not a loop.** `rite sandbox start <worker> --ticket
ABC-12` sends a Worker its opening prompt. Nothing in rite picks a ticket and
starts a Worker by itself — §2.5 says so outright, and `rite pool fill` fills
coordinator standby slots, not Workers. The generated Worker `CLAUDE.md` even
says "You are usually started with a ticket ID".

**So a run today needs a driver script**: loop over ready tickets, pick a free
worker, call `rite sandbox start`. Be honest about what that is — *a script
orchestrating rite's Workers*, not *rite orchestrating its own Workers*.

Closing that gap is **Q9 + RL-T32 + RL-T33**, which **Reserve J is building
now** (see §6). Until it lands, a run exercises Phase 1 only.

---

## 2. The Phase-1 path works end to end — measured

A throwaway two-module project was driven all the way through, in real seatbelt
sandboxes built the way `rite_ai.sandbox.start_worker` builds them (`.rite:rw`
shared, `workers/<w>:copy-all` private). Never in `~/AI/rite`.

| step | result |
|---|---|
| assignment = worker-name label | `rite board label 2 w1` → `2: labelled w1` |
| Worker reads its ticket from inside a sandbox | title, labels, body, over `gh` |
| claim, from inside a sandbox | `claimed 1 path(s) for w1` |
| **second worker claims the same path** | **refused, exit 4** |
| **second worker widens to the parent directory to get around it** | **refused, exit 4** — `engine overlaps engine/README.md` |
| heartbeat through the shared mount | `heartbeat recorded for 'w2'` |
| `git commit` inside a sandbox | succeeded |
| `git push` to GitHub from inside | exit 0, branch landed on the remote |
| `rite status` | workers, claims with ticket and age, STALLED, coordination cost |

**The claim invariant holds, including against the obvious workaround.** A
Worker that cannot claim `engine/README.md` cannot get it by claiming `engine`
instead. Both refusals were exit 4. That mount split — ledger shared, checkout
private — is what makes it hold.

---

## 3. The two pieces of plumbing that were fixed tonight

### rite on PATH is now 0.4.0

It was **0.2.0** — a `uv tool install` from the `v0.2.0` tag, predating Phase 2
entirely, with no `coordination/` package at all. Anything a sandboxed Worker
ran would have been pre-Phase-2 code while the changelog said otherwise.

Reinstalled non-editably from the remote:

```
uv tool install --force git+https://github.com/robbartoszewski/rite.git@8d19410
 - rite-ai==0.2.0   + rite-ai==0.4.0
```

**Verified from inside a seatbelt sandbox**, not from a shell — the whole point
was that the two differed:

```
--- rite --version   -> rite, version 0.4.0
--- command -v rite  -> ~/.local/bin/rite -> ~/.local/share/uv/tools/rite-ai/bin/rite
--- ls .../rite_ai/  -> ... coordination coordination_cost ... local phase.py spec ...
```

### The dogfood board is GitHub, private

`.rite/config.yaml` had `ticket_backend.type: none`, and `none` returns
`BackendError`. Since assignment *is* a board label, there was nothing to write
on.

Now: **`type: github`, `repo: robbartoszewski/rite-dogfood-board`** — private,
issues enabled, no code.

**No credential is needed from Robert.** `GitHubBackend` shells out to `gh` and
reads no rite credential at all; `gh` was already authenticated as
`robbartoszewski` with `repo` scope. `rite credential list` reports
`github_token` **set** for this project and *nothing missing*. JIRA was the
alternative and was rejected: it needs `jira_email` + `jira_token`, and its
board would be the real `BEN` project — dogfood tickets do not belong on a
production board.

**Verified by a full round trip**, not by reading config: create → label
`w1 scheduled` → show → `list --label w1` hit → `list --label w2` empty →
unlabel → close. `label()` created both labels itself on the fresh repo, so the
first `rite board create -l <worker>` is not a no-op.

---

## 4. Three traps that will waste your evening if you do not know them

### GitHub's label index lags writes by ~20 seconds

`rite board list --label w1` returned the ticket *after* it had been unlabelled
and closed; twenty seconds later it returned `no tickets`, and `gh issue view`
confirmed `labels: []`. The reverse also happened: a `list --label` straight
after a `label` returned nothing.

**Any driver that labels a ticket and then queries for the label needs a retry.
It is not a rite bug and it will read as one.**

### `yoloai exec` does not inherit the sandbox's `--env`

A process started with `yoloai exec` sees none of the variables passed to
`yoloai new --env` — those reach the agent's own session. So an exec'd probe
sees no `RITE_PROJECT_ROOT`, no `GH_CONFIG_DIR`, no `GITHUB_TOKEN` and **no
`GIT_CONFIG_*`**, and then:

- `rite board` reports *"not a rite project"* — it is not ignoring
  `RITE_PROJECT_ROOT`; it never received it.
- `gh` cannot start — *"failed to load config … operation not permitted"*.
- `git commit` fails with *"failed to write commit object"*, because the host's
  global `commit.gpgsign=true` + an SSH key under `~/.ssh` is unreadable inside.

All three were nearly filed as blockers. None is one. `sandbox_git_environment()`
already injects `commit.gpgsign=false`, `tag.gpgsign=false`,
`core.hooksPath=.git/hooks` and a `gh auth git-credential` helper; with the
variables re-exported inside the exec, everything succeeds. Probe scripts that
get this right are in `~/AI/rite-dogfood/phase2/probe_*.py`.

### Claims are local-only, and `rite claim` does not say so

`claims_channel()` returns `(None, "")` unless **both** `managers` and `remote`
are configured. Neither is, here. So claims never consult another machine — and
`rite claim` prints the same `claimed N path(s)` line either way.

On a single-machine run that is correct, and it is the mode every measurement
above was taken in. **It would matter silently the day a second machine joins.**

The neighbouring Phase-2 pieces are gated the same way rather than
broken-and-pretending: `_coordination_tick` returns `[]` when `not
config.managers or not config.remote`, and `ManagerMonitor._distribute` returns
when `self.backend is None`. Nothing on the Phase-1 path depends on any of them.

---

## 5. What a run tonight would and would not test

**Would:** spec → plan → tickets → implementation; worker fungibility (every
Worker gets every module — verified in generated output, not in source, because
a previous release shipped a changelog that said otherwise); the claim invariant
under real parallelism; heartbeat and STALLED reporting; commit, push and PR
from inside a sandbox; the board as the assignment surface; rite's CLI under
real use.

**Would not:** Owner→Manager assignment, Manager→Worker distribution,
cross-machine claims, leases, election, failover, refusal and reroute, the duty
router — all of Phase 2's coordination.

---

## 6. The one thing that is still completely unexercised

**No real Claude Worker has ever run this loop.** Every probe to date used
yoloAI's `idle` agent, deliberately, so that no quota was spent and nothing was
left running. The stretch from *"the opening prompt arrives"* to *"the agent
reads CLAUDE.md, claims its paths, does the work, pushes"* has never happened.

That is the half the dogfood exists to test. One ticket with one real Worker
would settle it. It costs quota, so it is Robert's call.

---

## 7. State of the ticket backlog

`RL-T32` ("start, stop and count a `local:*` Manager"), `RL-T33` ("assignment
consults the duty router") and `RL-T34` are **filed** in
`.docs/RITE_LOCAL_TICKETS.md`, each with a rationale, a scope and a "Done when".

> ⚠ **That file is not in the repository.** `.docs/` is the first line of
> `.gitignore`, and `RITE_LOCAL_TICKETS.md` has never been committed on any
> branch — it exists only on Robert's machine. **A fresh clone does not get the
> RL backlog.** It was left that way deliberately: `.docs/` is an intentionally
> ignored scratch area, and force-adding its contents to a public repository is
> Robert's decision, not a sweep's. He has made that call selectively before —
> the `preregistration/phase1` branch carries three `.docs/` files that were
> added with `git add -f`.
They were excluded from an unattended run on dependencies, not on absence: T32
needs RL-T3 and RL-T6, T33 needs RL-T5, T34 needs RL-T11 and RL-T13.

RL-T6 has since landed on `main`. Reserve J is building the Q9 / RL-T32 / RL-T33
wiring now.

---

## 8. Decisions taken, and why

| decision | why |
|---|---|
| **GitHub, not JIRA, for the board** | `gh` was already authenticated; `GitHubBackend` needs no rite credential at all. JIRA needs two credentials and its board is the real `BEN` project. |
| **A new private repo, not `robbartoszewski/rite`** | The rite repo is public. A dogfood board there means public issues. |
| **Install from the remote SHA, not from a working tree** | `~/AI/rite` had live, uncommitted work in it; building from a tree nobody had committed would make "which code ran?" unanswerable. |
| **Verify inside a sandbox, never from a shell** | The host shell and a sandboxed Worker resolved *different* `rite` binaries. A check run in the wrong place measures a proxy, not the property. |
| **`idle` agent for every probe** | Proves the mechanism without spending quota and without leaving a Claude session running unattended. |
| **Do not launch** | ⚠ *Reason corrected 2026-09-19; the decision stands.* The original — "a harness whose top half has no caller" — is the same stale claim as §1 above, and it was two screens away from it in this document. The callers exist. **The decision survives on the half that was never about wiring:** three Workers overnight spend quota, and spent quota is the one damage no cleanup reverses (§5.1.1). Launching is Robert's call and nothing here makes it. |
| **Track `.rite/config.yaml`** | The repo's own `.gitignore` re-includes it (`!.rite/config.yaml`) and `rite credential list` says *"commit it: a name, never a value"*. It carries names and hostnames, no secret values, and a fresh clone otherwise has no idea the board exists. It is **not** a `PROJECT_MARKERS` file (those are `.rite/brief.yaml` and `.rite/modules.yaml`), so tracking it does not turn a fresh clone of rite into a rite project. |

Its `site: example.atlassian.net` and `projects: {workers: ABC}` fields are
leftovers from the JIRA attempt and are unused by the GitHub path. They were
left in rather than silently edited, so that switching back does not need them
re-derived. The file carries names, hostnames and thresholds only — it was read
end to end and scanned before being committed to a public repo.

> ⚠ **One consequence, verified rather than assumed.** `.rite/config.yaml`
> already exists as an *untracked* file in the existing `~/AI/rite` checkout.
> Git refuses a pull that would write over an untracked file **even when the
> bytes are identical** — tested: *"untracked working tree files would be
> overwritten by merge … Aborting"*. So the next `git pull` in an existing
> `~/AI/rite` will abort once until someone runs `rm .rite/config.yaml` first.
> Nothing is lost: what lands is byte-identical to what is deleted. A **fresh
> clone is unaffected**.

---

## 9. Where things are

| what | where |
|---|---|
| this document, in the repo | `docs/rite-local-dogfood-decisions.md` on `main` |
| the three source writeups | `~/AI/rite-dogfood/*.md` — **local only**: `~/AI/rite-dogfood` is not a git repository, so nothing in it can be pushed. That is why this document was copied into the rite repo. |
| sandbox probe scripts | `~/AI/rite-dogfood/phase2/probe_*.py` |
| the board | `https://github.com/robbartoszewski/rite-dogfood-board` (private; default branch `main`, zero open issues) |
| the backlog | `.docs/RITE_LOCAL_TICKETS.md` — **committed**, with the rest of `.docs/`. This row said "local only, ignored, never committed" and a commit 21 minutes later falsified it; the row is corrected rather than deleted, because "never" was a claim about the future and the shortest-lived one in this document. |
| Phase-1 pre-registration | branch `preregistration/phase1` — `.docs/rite-on-rite-phase1-preregistration.md`, `.docs/run-parameters.md`, `.docs/carried-limitations-register.md` |
