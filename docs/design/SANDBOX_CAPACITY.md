# The worker cap counts other projects' sandboxes — revision 2

**Revision 1 proposed excluding `agent: idle` sandboxes from the cap. That is
wrong, and a live counter-example was sitting in the measurement.**

| Revision 1 said | Actually |
|---|---|
| `agent: idle` means nothing is running, so it is litter | It means no agent process is executing *right now*. One of the six is `~/PapugaAI`'s Worker **w1** — the dogfood running next door — 15h old, `Changes: yes`, `Agent: idle`, and its Owner considers it busy |
| Excluding idle sandboxes fixes the cap | It would let rite start a Worker alongside one that is mid-task. Review round 1 predicted exactly this; the dogfood supplied the instance |

yoloAI does not document what `agent` distinguishes, and `yoloai help` has no
topic for it. So the field cannot carry a liveness decision, and this design
stops trying to make one — the same conclusion the claim-expiry work reached,
arrived at from the other direction.

## The measurement, re-read

```
name                          status   agent   changes   workdir belongs to
rite-claim-a-71978-be9133     active   idle    no        a test fixture, session 814aad81
rite-claim-b-71978-f64b3c     active   idle    no        a test fixture, session 814aad81
rite-e2e-74627-62ec8f         active   idle    yes       a test fixture
rite-kct-9303d6               active   idle    yes       ~/PapugaAI — the live dogfood's w1
rite-push-73427-ca9757        active   idle    yes       a test fixture
rite-sign-73950-be92c3        active   idle    yes       a test fixture
```

**Not one of them belongs to the project whose cap they fill.** That is the
whole defect, and it has nothing to do with liveness.

## The actual bug: a per-project cap enforced against a machine-wide count

`SandboxConfig.max_concurrent_workers` is **per project** — it lives in that
project's `.rite/config.yaml`. SPEC §2.5.9 scopes it per Manager session.
`count_active_sandboxes` counts every sandbox on the machine whose name starts
with `rite-`, across every project and every leftover probe.

rite already solved this collision once, for a more obviously destructive
case. `sandbox_name` (§8.10) carries the project — `rite-acme-3f9a2c-w1`, not
`rite-w1` — with a docstring that names the exact hazard:

> "on a machine running two projects — which is what rite is for — the old
> name collided outright … `rite sandbox destroy w1` in one project would
> destroy the other project's worker."

The names were fixed. **The count was not.** So the same cross-project
collision survives in the one place it presents as capacity rather than as a
mistake: the run hits `5 active / max 5`, nothing is wrong with the project,
and the workaround is to raise the cap.

## The fix

**Count the sandboxes belonging to this project.** `rite-<project_slug>-*`,
using the prefix `sandbox_name` already builds. No liveness judgement, no new
vocabulary, no field whose meaning is undocumented.

Measured effect here: this project's count goes **6 → 0**, correctly, because
none of the six is its Worker.

Three details that decide whether it is safe:

1. **Legacy names must still count.** `legacy_sandbox_name` is `rite-<worker>`
   with no slug, and `existing_sandbox_name` already looks for both forms. A
   sandbox started for this project by an older rite must keep counting, or
   the cap silently under-counts the very Workers it exists to bound. Match
   the project prefix **or** a legacy name matching one of this project's
   configured Workers.
2. **Non-dict entries must not crash.** `count_active_sandboxes` calls
   `entry.get(...)` directly on each list element; a non-dict entry raises
   `AttributeError` today. `_count_named` already guards with `isinstance`.
   Fix it in passing — it is a latent crash in the same function.
3. **`CountUnavailable` still refuses.** Unchanged: a count that cannot be
   established must not become a plausible zero.

## What this gives up, said plainly

Counting machine-wide was an **accidental** bound on total sandboxes per
machine, and narrowing removes it: two projects at `max_concurrent_workers: 5`
can now run ten sandboxes between them.

That bound was never the one being configured — the key is per project and the
spec scopes it per Manager — but it was real, and on one laptop ten sandboxes
is a different proposition from five. **If a machine-wide limit is wanted it
should be its own key**, set once per machine rather than emerging from a
per-project number being applied to the wrong denominator. Recorded as a
follow-up rather than invented here.

## The litter is still worth reporting

The fix makes the cap correct; it does not clean anything up, and six
abandoned sandboxes holding 4.5MB and four sets of unapplied changes are still
there. `rite doctor` should name them — **with `changes: yes/no` per
sandbox**, because that is precisely what decides whether `yoloai destroy` is
safe — and print the command without running it.

rite must never destroy one. Four of the six hold unapplied changes, one of
them belongs to a project that is actively running, and this session found
that out by reading metadata. A tool that tidied on this evidence would have
destroyed a live Worker's work tonight.

## What would show this wrong

- **A project's own Workers stop being counted**, because their names do not
  match the prefix — the under-count failure, which is worse than the
  over-count it replaces. Detail 1 is the guard, and the part to review.
- **Ten sandboxes on one laptop** turns out to matter more than the wrong
  denominator did.
