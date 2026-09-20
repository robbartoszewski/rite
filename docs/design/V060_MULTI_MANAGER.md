# Multi-Manager, 0.6.0 — Robert's design, recorded

## ⚠ SUPERSEDED on 2026-09-20 — the shape below was reversed

**This document records Robert's earlier position. He changed it, explicitly,
later the same conversation, and the later answer is what SPEC §9.14.9 and
D-79 carry.**

Asked directly — "separate workspaces inside one root, or separate roots?" —
he answered: **"subdirectories inside one root. Strictly separated — one
misbehaving manager shouldn't be able to mess with others by accident."** He
answered it having already been given the argument below, so this is a
decision taken with its counter in front of him, not one taken without it.

**What changed the reasoning, and it is a fact about the code rather than a
preference.** Separate roots mean separate claim ledgers. Two Managers on one
machine would then **silently not see each other's claims** — and the
coordination layer cannot cover for that today: `claims_channel()`
(`coordination/identity.py:167`) returns `(None, "")` unless BOTH
`coordination.managers` and `coordination.remote` are set, so a project that
has configured neither gets no cross-root claim visibility at all. Two
sessions editing one path is the failure this project hit twice in
twenty-four hours, and separate roots would have made it the default rather
than the accident.

**The argument below is not deleted, because it names a real cost that the
decision accepts rather than refutes.** "A second protocol that has to be
kept in agreement with the first, and the two would drift" is true of a
shared root, and it is now a known debt rather than an avoided one. Whoever
builds `<root>/.rite/managers/<name>/` should expect drift between the
per-Manager boundary and the state layer's own protocol, and should be
looking for it.

**How the two documents came to disagree is worth recording too**, because it
is this project's own recurring defect: SPEC §9.14.9 was written without its
author having read this file, a day after this file was written. A conclusion
drawn from what was in hand rather than from what exists — the same root as
inferring absence from the calls you happen to make.

---


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

---

## Carried into 0.6.0 from 0.5.1 — engineering, not design

Two items deferred out of 0.5.1 deliberately. Neither blocks the design
above; both are things the next person to work in this area should know
before they spend an afternoon rediscovering them.

### 1. The real-tmux tests are load-sensitive and nondeterministic — OPEN

Two files are affected, and **the nondeterminism is proven independent of
any code change**:

- `test_manager_endings_and_resume.py::TestLivenessUnderRemainOnExit::test_a_dead_pane_is_not_alive_even_though_the_session_exists`
  — fails on `still_exists.returncode == 0`, "remain-on-exit did not hold
  the session".
- `test_loop_start_really_starts.py::test_doctor_names_a_running_loop` (and,
  in one run, two of its siblings) — fails on
  `"loop: running as rite-loop-" in result.output`: the loop session was
  started and `rite doctor` did not see it.

**What was measured on 2026-09-20, so the next person does not repeat it:**

| run | tree | result |
|---|---|---|
| either file alone, 2–3× | both trees | passes every time |
| full suite | pristine `origin/main` | 3757 passed, 0 failed |
| full suite, run 1 | `origin/main` + Manager-identity | **3 failed** (loop file) |
| full suite, run 2 | same, after test-cleanup fix | 3774 passed, 0 failed |
| full suite, run 3 | run 2's tree **+ 172 lines of markdown** | **1 failed** (loop file) |

⚠ **The last row is the control that matters.** Run 3's only difference
from run 2 is five `.md` files. Markdown cannot change how `rite doctor`
detects a tmux session, so the same code passed and failed across two runs.
Whatever this is, it is not a code defect introduced by either change — it
is timing, and it only appears under full-suite load.

**For the first of the two, start at `_keep_pane_after_exit`.** It runs `set-window-option` with
`check=False`, so a call that fails under load is silently ignored and the
next assertion is the first thing that notices. `ec3199c` changed that
function on the same day, and this module's own docstring already records
that the capability probe beside it "has now got in its own way five times",
one of which was *"the command died before the option was set"* — the same
shape as a set-option that does not land in time.

For the second, the common factor is that both tests assert on a session
being *visible* shortly after `start` returns, and `settled_alive` gives
that only a bounded number of tries. A shared root cause across both files
is plausible and unproven.

⚠ **Not cleared, and deliberately not called flaky-and-harmless.** What is
established is that it is nondeterministic under load, not that it is
harmless: both assertions are about rite failing to see a session that
exists, which is the same shape as the `eu:west` defect — a confident,
wrong answer about whether something is running. If that can happen under
test load it can happen on a loaded laptop. Treat it as open, and start by
making the silent failures loud rather than by re-running until green.

### 2. `journal.instructions()` still spells out `--manager`

Since 0.5.1 a Manager session carries `RITE_MANAGER` and `--manager` defaults
to it, so the instruction text handed to a Manager now tells it to pass an
argument it no longer needs. Harmless and redundant.

Left alone because the wording is pinned by assertions in
`tests/test_manager_journal.py`, and rewording text that other tests assert on
is not worth doing for a redundancy. Do it with the next deliberate change to
that text.
