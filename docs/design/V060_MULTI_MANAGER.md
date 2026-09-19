# Multi-Manager, 0.6.0 — Robert's design, recorded

**Recorded, not derived.** This is Robert's shape for 0.6.0 multi-Manager,
written down before it gets lost in a transcript. Nothing here is built, and
nothing here has been reviewed yet — the open questions at the end are mine
and are for whoever picks it up, not objections to the design.

Status of the file itself: `.docs/` is gitignored and its committed copies are
force-added and stale, so **this file does not reach a fresh clone**. That
ownership decision is open (see the v0.5.0 close-out). If it is resolved by
un-ignoring `.docs/`, this arrives on its own; if by dropping `.docs/` from
the repo, this needs a home.

---

## The shape

**Separate roots per Manager, coordinating through the shared state layer.**
Each Manager owns its own project root and its own `.rite/`, and Managers
reach each other only through the state layer that 0.4.0 already substitutes
(git remote, local filesystem, or a socket-served key-value store — the same
conformance suite runs against all three).

**Same-machine is a special case of that, not its own design.** Two Managers
on one box are two roots talking through the same layer, exactly as if they
were on two machines. This is the load-bearing decision: the alternative —
a separate "local Managers" path with shared memory or a shared `.rite/` —
would be a second protocol that has to be kept in agreement with the first,
and the two would drift. One protocol, one conformance suite.

**What stays genuinely machine-specific is a short list, and none of it is
protocol.** Two things:

- **resource contention** — CPU, memory, disk, the worker cap, quota burn;
- **correlated failure** — one machine dying takes all its Managers with it,
  which independent machines do not do.

Both are **scheduling inputs**, not protocol concerns. They change *how many
Workers a Manager should be given right now* and *how much to trust a set of
heartbeats that all went quiet together*. They do not change how Managers talk
to each other. Keeping them on the scheduling side is what lets same-machine
stay a special case rather than a fork.

## Heterogeneous Managers on one machine

A Claude Manager, a Cursor Manager and a local-model Manager can share a
machine and have very different capabilities. The existing axis already
handles it: **engine × duties**.

- engine gains `cursor` beside `claude`, and `local:<class>` for a local model
  of a given class;
- duties stay what they are — the `plan-review` / `decompose` / `step-review`
  / `integrate` / `execute` vocabulary the duty router already uses, which
  targets Managers rather than Workers.

So "what can this Manager do" is answered by its engine and the duties it
holds, and routing does not need to know what kind of thing is behind it.
That is the same separation that makes the local tier's router work today.

---

## Open questions, for whoever builds it

Not objections — the places where building will decide something this note
does not.

1. **Correlated failure is named as a scheduling input, and the mechanism for
   noticing it is not.** Today a Manager's death is detected by heartbeat
   lapse, one holder at a time. Several Managers on one machine going quiet
   together is exactly the case a per-holder timeout reads wrong — it looks
   like several independent deaths, and whatever acts on it acts several
   times. This is the same argument that killed Manager-posted heartbeats
   (see `MANAGER_POSTED_HEARTBEATS.md` §3): correlated failure defeats a
   timeout because the timeout assumes silence is evidence about **one**
   holder.

2. **The worker cap now has three denominators, not two.** v0.5.0 landed
   per-project and machine-wide counts. Per-Manager is a third, and a machine
   running three Managers needs all three to agree on what "full" means.

3. **`local:<class>` needs its classes named before it is a routing key.**
   A capability a routing decision depends on has to be observed rather than
   declared (EXC-3). `engine_probe` already probes a local engine for doctor;
   whatever `<class>` means has to come from that probe, not from config.

4. **Two roots on one machine share more than the design says.** The same
   keychain, the same tmux server, the same yoloAI sandbox namespace, the
   same `~/.rite`. v0.5.0 found defects in the second and third of those
   (orphaned tmux sessions; a leaked `rite-selftest-*` sandbox), and
   `HRM-*` in the dogfood tickets is about the first. Same-machine being a
   special case of the protocol does not make it a special case of the host.
