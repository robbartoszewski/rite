# What a `-p` Manager may DO — the permission mode is rite's parameter now

**Status: DECIDED by Robert, 2026-09-21.** Recorded, not proposed. Nothing
is built and no launch path was touched.

> `--permission-mode acceptEdits` as default,
> `--dangerously-skip-permissions` as opt-in

— and the opt-in is configured **per Manager**.

⚠ **`acceptEdits` has NOT been observed working.** See "The gap that
matters now" below. The decision is made; the verification is not done.

Second scope created by Finding B's Option 1, and a sibling of
`CREDENTIAL_HANDLING_FOR_UNATTENDED_RUNS.md`: that one is about the token
`claude -p` needs to *authenticate*, this one about the permission it needs
to *act*. They arrive from the same choice and should be read together.

---

## What was measured

Tonight's acceptance run, recorded here because it is the evidence:

**`claude -p` has no tool permissions by default.** The Manager read its
prompt, said it could not write a file without approval, and exited 0. The
supervisor read that as a clean finish — correctly, by its own rules, since
the process ended with status 0 and nobody was attached — and resumed.

**A working loop around a Manager that cannot act.** Every mechanism this
release built behaved exactly as designed: the session started, the prompt
arrived, the ending was classified, the next cycle began. The only thing
absent was the work.

⚠ This is the release's own defect class arriving one level up. Each part
reported success about the thing it measures, and nothing measured whether
the Manager could do anything.

## What settles it

Robert supplied the documentation. Resuming from a terminal **without**
`-p` restores the permission mode the session was in — and that restoration
explicitly **excludes** `-p`, along with the session picker and `/resume`.

The consequence is the finding:

> **There is no path under Option 1 where a User grants permissions once,
> interactively, and the unattended cycles inherit them.**

So **every `-p` invocation must be passed `--permission-mode` or
`--dangerously-skip-permissions` explicitly, every cycle.** It cannot be
inherited from an earlier interactive session, and it cannot be deferred to
the user "setting it up once".

**That makes the permission mode a parameter rite has to own**, in the same
way `--resume <id>` became one: not a thing that happens to be right, but a
value composed at the launch site and passed on every cycle.

## ⚠ Both halves, because neither alone is true

**It is not purely a regression introduced by `-p`.** An *interactive*
Manager running unattended would hit a permission prompt with nobody there
to answer it, and hang. That is equally useless, and this project already
has the phrasing for exactly this shape, from the keychain hang
(`tests/conftest.py`): *"a prompt is not an exception. It is the absence of
an answer"* — no output, no timeout, no failure.

**And it is not "this was always fine".** What `-p` changed is real: the
failure moved from something that quietly did not work into a **decision
somebody must make deliberately**. Before, the mode was invisible and the
run hung; now the mode is absent and the run completes having done nothing.
The second is worse to diagnose and better to fix, because it forces the
question into the open where it can be answered once.

Recorded this way deliberately: read as "new bug" it looks like a reason to
back out `-p`, and read as "always been fine" it looks like nothing needs
deciding. Neither is the case.

## ⚠ The security asymmetry, stated plainly

This is what makes `--dangerously-skip-permissions` a **real** choice rather
than an obvious one:

| | where it runs |
|---|---|
| **Worker** | inside a yoloAI sandbox, with an explicit `--env` list and a stated reachable set (SPEC §5.3) |
| **Manager** | **unsandboxed**, as a plain `tmux new-session` with `cwd` = the project root, on the user's own machine |

A Worker granted broad permissions is bounded by the sandbox. A Manager
granted broad permissions is bounded by nothing but the user's filesystem
permissions. The same flag means two different things in the two places,
and only one of them has a container under it.

That is not an argument against skipping permissions for a Manager — an
unattended Manager that must stop and ask is not unattended. It is an
argument that the choice must be **stated and owned**, not defaulted into by
whoever writes the launch line.

## The decision, and what a builder needs with it

**Safe by default, dangerous by deliberate act.** `acceptEdits` lets a
Manager do the work it was started for without anybody having to think
about it first; the broader mode is a separate, knowing choice.

⚠ Deliberately not phrased as "the mode that lets it do anything". **The
delta between the two is not established** — see "Why the opt-in exists" —
and a justification resting on that delta being large would be resting on
something nobody has measured.

### 1. `--permission-mode acceptEdits` is the default

Passed on every `-p` invocation, every cycle, because nothing carries it
between them (see "What settles it"). A Manager that can edit the repo it
was aimed at is a Manager that can do its job; that is the ordinary case
and it needs no ceremony.

### 2. `--dangerously-skip-permissions` is opt-in

⚠ **The opt-in must be a deliberate act, not a convenience**, and that is a
constraint on whatever mechanism implements it rather than a detail of it.
A key a user copies out of an example and forgets is not a decision they
made. Whatever the shape — config key, flag — the test is whether somebody
who has it switched on would say so if asked.

The asymmetry above is the whole reason: a Worker granted broad permission
is bounded by its sandbox, and **a Manager is bounded by nothing but the
user's own filesystem permissions.** The dangerous mode is a decision about
the user's own machine, and it should read like one.

### 3. The opt-in is configured per Manager

**Not a rite-wide setting.** Different Managers doing different work warrant
different trust — a `planner` that reads and proposes and a Manager that
edits and pushes are not the same risk, which is why `ManagerRole` already
carries `duties`.

It lives with the other per-Manager attributes in the gitignored
per-Manager configuration (`V060_SESSION_CONTINUITY.md`), not in committed
`manager_roles`. A trust level is per-person and per-machine: a committed
`--dangerously-skip-permissions` would impose one operator's risk appetite
on every clone of the project. That is the same objection as a committed
session id and a worse one, because it is not merely wrong elsewhere — it is
dangerous elsewhere.

## Why the opt-in exists, when nobody has inventoried what it adds

Robert's reasoning, and the point is that it does not depend on knowing the
delta:

> a project may include some custom acceptance gates

A project's own hooks or gates can block things the default does not cover.
The escape hatch earns its place because *a project can need one*, not
because the extra permissions have been enumerated and found large. **He was
explicit that he does not need to know what they are at this stage**, and
that is a coherent position rather than a deferral: the opt-in exists for a
case that is real whether or not the delta is ever measured.

This is why nothing in this note argues from the size of the delta. If
somebody later inventories it and finds it small, the opt-in is unaffected —
a custom gate that blocks a Manager is still a custom gate that blocks a
Manager.

## Objection raised and closed: shell execution under `acceptEdits`

**Raised:** that `acceptEdits` may permit shell execution, not only file
edits, and that this would be a surprise hiding inside a safe-sounding
default.

**Closed by Robert, and the reasoning is stronger than the objection:** a
Manager that cannot run `git`, tests or a build **is not a Manager**. Shell
execution is a **requirement of the role**, not an unexpected extra that
came along with the mode.

That inverts what the verification below means. If a Manager under
`acceptEdits` turns out to run commands as well as write files, that is the
decision working rather than a complication in it — the run would be
confirming the mode does what the role needs. Recorded so the objection is
not re-raised as though it were open: it was raised, and it was answered.

## Recommended, not decided — this one is mine

**rite should STATE the mode it launched with, every run.**

A user should never be unsure what their Manager was permitted to do. Same
reasoning as the loud timezone fallback: D-48 was relaxed to let the zone
default, and that was acceptable only because the default is announced —
*"a default that is never stated is the same silent-wrong-clock D-48 was
written against"* (`schedule/__init__.py`). A permission mode is a larger
fact about a run than a timezone.

It also does work that nothing else does. Without it, a Manager that
*chose* not to act and a Manager that was *not allowed* to act produce the
same visible result — which is precisely the confusion the acceptance run
below took a night to resolve.

Recorded as a recommendation rather than a decision because Robert set the
default, the opt-in and where it lives; he has not ruled on this.

**Open documentation item, also mine: this note should state plainly what
`acceptEdits` actually permits.** The name says "edits", and a reader could
reasonably conclude their Manager is confined to writing files — which, per
the objection closed above, it is not. An earlier draft of this very
document made that assumption in its own opening sentence and had to be
corrected, which is the argument for writing it down rather than trusting
the name.

A docs-accuracy point, not a reason to revisit anything. It needs somebody
to establish what the mode permits and say so here in a sentence.

## ⚠ The gap that matters now: `acceptEdits` is unverified

**Nobody has watched a Manager write a file under `acceptEdits` and have
the cycle end cleanly.**

Tonight's acceptance run measured the **absence** of permissions — a Manager
that said it could not write and exited 0. It did not measure the presence
of them. The decision above is a well-reasoned answer to what that run
found; it is not evidence that the answer works.

That is the same gap as everything else tonight, in its final form: **a
mechanism existing is not a mechanism observed.** `window_seconds` was
passed and never enforced. `running_instances` and `pid_alive` existed with
zero callers. `forget_instance` existed with zero callers. The resume path
was tested only through an injected starter, so no test ever created a
second real tmux session and the duplicate-session collision survived to be
found by hand. Each was correct-looking code that nothing had watched do
its job.

**What would close it**, and it is one run: a Manager launched with
`--permission-mode acceptEdits`, given a prompt that requires **writing a
file AND running a command** — `git status` or the test suite will do —
observed to have done both, with the cycle ending `finished` and the
supervisor resuming.

Both halves, because of the objection closed above: shell execution is a
requirement of the role, so a run that only proves file writes leaves the
half a Manager needs most unmeasured. Until somebody has seen that, "the
Manager can act" is a claim about a flag rather than about the Manager.

## What would show this wrong

- A permission mode that **does** survive into `-p` by some path not in the
  documentation Robert supplied. That would collapse the finding, and it is
  the first thing to try to falsify.
- `acceptEdits` not being sufficient for ordinary Manager work — a duty
  that needs to run a command or push, say, and stalls under it exactly as
  the acceptance run stalled with no permissions at all. That would not
  reopen the default; it would mean the opt-in is reached more often than
  "deliberate act" suggests, and that is worth knowing early.
- Evidence that a Manager with NO tool permissions is still useful for some
  duty — a reviewer that only reads, say. Per-Manager configuration already
  allows for that, so this would be a case for a third mode rather than an
  objection to the two.
