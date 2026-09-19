# Should the Manager post heartbeats for its Workers? — a read, with measurements

**The proposal:** *"Why don't we have the Manager post heartbeats for itself
and for workers? That means a worker doesn't have to stop what it's doing in
order to post a heartbeat. We would need to make the timeout generous though —
so a busy Manager doesn't miss it by accident."*

**Short answer: the problem is real and the mechanism as stated makes it
worse. The fix is not who posts, it is what the heartbeat asserts.** Both
concerns raised against it hold up under measurement — one of them more
strongly than expected — and there is a third that neither of us named.

---

## 1. The problem is real, and it is the reason "silent ≠ dead" is unresolved

Nothing writes a heartbeat on a Worker's behalf. `rite heartbeat` is a command
the agent must remember to run, between turns, while doing something else. A
session deep in a long tool call genuinely cannot — and `detect_stalls` has no
way to tell that from a dead one.

That is not theoretical here. It is exactly why the claim-expiry work this
week could only ever **report** and never release: two review rounds killed
automatic release precisely because a Worker heads-down for thirty minutes is
indistinguishable from a corpse by that signal. So the proposal is aimed at
the right target.

---

## 2. Concern one — it changes what a heartbeat asserts. **Confirmed, with numbers.**

Today a heartbeat nominally means *"the worker is alive and working"*.
Manager-posted, it means *"the Manager believes this worker's sandbox
exists"*. Those are different claims and only the first is what anything
downstream reads it as.

**Measured on this machine, 2026-09-19:**

```
6 rite- sandboxes, all agent:idle (nothing running inside any of them)
4 of the 6 holding unapplied changes
1 of the 6 is another project's LIVE worker
```

Under Manager-posted heartbeats keyed on sandbox existence, **all six would
have been publishing healthy heartbeats.** Four of them are abandoned work.
So the concern is not merely that the assertion weakens — it is that the
change converts the exact population we are trying to detect from *silent*
into *apparently maintained*. Silence is at least legible. A confident wrong
answer is not.

This is the strongest argument against the mechanism as stated, and it is
stronger than "it might make the orphan case worse": on tonight's numbers it
would have made it worse, four times out of six.

## 3. Concern two — it moves the single point of failure. **Confirmed, and it is the corruption case.**

If the Manager dies, every Worker goes silent simultaneously, including ones
genuinely running. A timeout-based release would then free paths under live
Workers — two sessions editing one file, which is the single thing the ledger
exists to prevent.

Worth being precise about why this is worse than today's failure mode. Today,
N Workers fail independently; a Manager's death is *correlated* failure, and
correlated failure defeats a timeout because the timeout's whole assumption is
that silence is evidence about **one** holder.

The generous timeout the proposal calls for makes this worse rather than
better, in a way that is easy to miss: a longer timeout means a larger window
in which a dead Manager's Workers look fine, so the blast radius at expiry is
bigger and arrives all at once.

## 4. Concern three, which neither of us named — **the attestation has no author**

A Worker-posted heartbeat is self-evident: the thing that is alive says so.
A Manager-posted one is hearsay, and rite currently has nowhere to record
*whose* hearsay. `ManagerStatus` carries `workers` and `in_flight`; a
Worker's heartbeat record carries `worker`, `timestamp`, `ticket`, `message`.
Nothing carries "posted by", so a Manager-posted heartbeat is
indistinguishable from a Worker-posted one after the fact.

That matters because the first question during an incident is "who said this
worker was alive, and on what basis" — and the record would not be able to
answer it. **The same defect class this week has been about**: a value whose
provenance is discarded at the moment it is written.

---

## 5. What would make it work — and it is close to what was already proposed

*"The Manager attesting to something it checked rather than something it
assumes, and the heartbeat carrying what was verified."* That is right, and
the measurements sharpen it into three requirements:

1. **Attest to a checked fact, and name the check.** "Sandbox present" is the
   assumption that fails four times out of six. What distinguishes the live
   Worker from the five orphans is that something is *running inside* it —
   which yoloAI answers in a field rite does not currently read for this
   purpose, and whose vocabulary is undocumented, so it needs probing before
   it can be trusted (EXC-3: probe, don't declare).
2. **Carry the evidence, not just a timestamp.** `"Manager attests; sandbox
   present, agent process alive, verified 3m ago"` is actionable.
   `"Manager attests"` is not. The record needs a `posted_by` and a
   `verified` field, or the provenance problem in §4 ships with it.
3. **Never let an attested heartbeat authorise a release.** It may keep a
   path held; it must not free one. That asymmetry is what contains the
   correlated-failure case: a dead Manager then causes *stalling*, which is
   visible and recoverable, rather than *releasing*, which is corruption.

**And one thing that should not change:** the timeout. Making it generous is
treating the symptom. The reason the current timeout is unreliable is that it
measures the wrong thing — an agent's memory to call a command — and a longer
window makes a wrong measurement later rather than better.

---

## 6. My recommendation

**Do not build "the Manager posts heartbeats".** Build **"a heartbeat says who
observed what, and when"** — which is a smaller change, is useful immediately
to the Worker-posted case, and makes the Manager-posted case *expressible*
rather than *assumed*:

- add `posted_by` and `observed` to the heartbeat record;
- a Worker posting for itself sets `posted_by=self`, unchanged in meaning;
- a Manager may then post with `posted_by=<manager>, observed="sandbox
  present; agent running"` — and every consumer can weigh it differently,
  because it can finally tell them apart;
- releases continue to require a human, as decided this week.

That sequencing also means the risky half can be measured before it is
trusted: run Manager attestation alongside Worker heartbeats for a while and
count how often they disagree. If they never disagree, the attestation is
carrying no information; if they disagree often, we have learned exactly which
of the two is lying, and that is the fact this whole area has been missing.

**Where I think the concern was slightly too strong:** "it may make the orphan
case worse" reads as a risk; on tonight's measurements it is not a risk but a
prediction, and it should be stated that way. That is the argument being
*more* right than claimed, not less.
