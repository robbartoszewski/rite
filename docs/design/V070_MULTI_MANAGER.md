# Multi-Manager, 0.7.0 — Robert's design, recorded

⚠ **MOVED TO v0.7.0 on 2026-09-23, and this file was renamed with it.** It
was `V060_MULTI_MANAGER.md`. Robert had misremembered his own earlier
decision; asked again, he confirmed **"multi-manager is out, Slack is in"** —
which restores the scope he set two days earlier. v0.6.0 is fixes + Slack +
local models; multi-Manager and Cursor are v0.7.0.

Renamed rather than left, because a filename claiming a release is a claim a
reader believes before they open the file, and this release spent two days
clearing exactly that class of stale artefact. **The decisions inside are
unaffected** — SPEC §9.14.9 and D-79 still carry them, and the superseded
"separate roots" shape below is still superseded.

⚠ **The "Carried into 0.6.0" section near the end is still 0.6.0 work.**
Those items were never multi-Manager work; they are in
`V060_RELEASE_PLAN.md`'s carried table and ship in v0.6.0 regardless of where
this document went.

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

⚠ *UPDATED 2026-09-25: the status paragraph below is stale. This file is
tracked under `docs/design/` and reaches a fresh clone. v0.7.0 planning for
multi-Manager, including this note's open questions, is in
`V070_RELEASE_PLAN.md`, track MM.*

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

Five items deferred out of 0.5.1 deliberately. None blocks the design
above; all are things the next person to work in this area should know
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

### 3. The test suite and a live Manager share one tmux server — OPEN

**This is a candidate mechanism for item 1, and it is testable.** Every
`tmux` invocation in the suite (65 of them) and every one in the product
uses the DEFAULT socket, because both call the bare binary with no `-S`.
So `pytest` and a Manager started by `rite start` land on the same server,
as do two suites running at once on a developer's machine.

**Evidence that this is not hypothetical:**

- A stray `rite-loop-looptest-*` session was found on the shared server
  during a run of `test_manager_endings_and_resume.py`, which creates no
  such session — it belonged to another suite running concurrently.
- A control run elsewhere had a **five-file markdown-only diff flip a
  tmux-detection test**. A documentation change cannot affect tmux; a
  neighbour on the same server can.

Item 1 says the nondeterminism is "proven independent of any code change"
and that a shared root cause is "plausible and unproven". This is the
most likely shared root cause, and it explains the shape item 1 names —
rite failing to see a session that exists, or seeing one it should not.

**The fix is one autouse fixture and no call-site changes.** `TMUX_TMPDIR`
relocates the socket directory and tmux resolves it itself, so setting it
once for the pytest process covers the tests AND the code under test,
which inherit it. Measured: a session created under a private
`TMUX_TMPDIR` is invisible to `tmux ls` on the default socket, and a bare
`subprocess.run(["tmux", "ls"])` with only the variable set reaches the
private server. `conftest.py` already isolates `RITE_CLAUDE_PROJECTS_DIR`,
git config, the OS keychain and network credentials this way; tmux is the
one shared global on that list that is not isolated.

⚠ **One constraint that will cost an hour if it is rediscovered.** A Unix
socket path is capped near 104 bytes, and the path becomes
`$TMUX_TMPDIR/tmux-<uid>/default`. pytest's `tmp_path_factory` basetemp is
far too long — measured, it fails with `error connecting to … (File name
too long)`. Use a short dedicated `mkdtemp`, not `tmp_path`.

**Deferred from 0.5.1 on purpose:** it has no user-visible effect and its
blast radius is every real-tmux test, which is not a change to land hours
before a tag.

### 4. `session_exists` is not a general tmux predicate

`session_exists("eu:west")` is **False for a session that exists**.
`has-session -t =eu:west` reads `eu:west` as session `eu`, window `west`,
so the exact-match prefix does not save it.

This costs nothing today, and the reason is worth stating rather than
assuming: a Manager name containing `:` is refused by `name_problem`
(`must_be_a_tmux_target=True`) — the same check that made `eu:west` and
`v2.0` stop being accepted names in 0.5.1 — so rite never creates one and
never has reason to ask about a stranger's.

**It is a landmine for the next caller.** The function reads like "does a
tmux session with this name exist", and it is only sound for names rite
itself validated. Anything that asks it about a name from outside rite —
an adoption feature, a doctor check that enumerates the server, a
multi-Manager view — gets a confident wrong answer of exactly the shape
item 1 warns about. Either document the precondition at the function, or
make it take a validated name type.

### 5. An unattended Manager cannot act without approval — OPEN, and NOT new

**Observed in the 0.5.1 acceptance run.** Three cycles ran against real
`claude`, resumed correctly and stopped on their ceiling — and the Manager
produced nothing. Its pane:

    ...le. This needs to be approved on your end before I can proceed.
    DONE-1
    Pane is dead (status 0, ...)

It was asked to write one file, said it needed approval, answered `DONE-1`,
and exited 0.

**This gap predates `-p` and predates this release.** `RITE_LOOP_PLAN.md`
records the assumption directly — §5.3.1 has the Manager create sandboxes
"as ordinary tool calls … the human approval happens once, at the Manager"
— which is only true while a human is at the Manager. Unattended, nobody
answers, and that is `DEFECT_CLASSES.md` class 15 in its original words: a
prompt is not an exception, it is the absence of an answer. An interactive
Manager would have reached the same wall and WAITED at it until the window
bound.

⚠ **What `-p` changed is the failure mode, and that part IS new.** Waiting
is visible — the session stays alive, `ending` says the pane is not dead,
nothing resumes. Refusing is not: the engine exits 0, `ending` reads
FINISHED, the supervisor resumes, and the run reports success having done
nothing. **A refusal that looks like a clean finish is worse than a hang**,
and it is the same shape as every other finding this release — a real
answer to the wrong question, believed because the check could not tell.

**Decided since, elsewhere.** `PERMISSION_MODE_FOR_UNATTENDED_RUNS.md`
carries Robert's answer — `--permission-mode acceptEdits` by default,
`--dangerously-skip-permissions` as a per-Manager opt-in — and is the home
for the decision. What stays here is the measurement it was decided on.

**Deliberately not fixed here.** The remedy is Claude Code's
`--permission-mode` / `--allowedTools`, and choosing what an unattended
agent may do to somebody's repository is a security decision that belongs
to Robert, not to a late fix for something else. Recorded with what was
measured so the decision can be made on evidence.

**What would make it detectable meanwhile:** nothing distinguishes "the
Manager did the work" from "the Manager declined and exited 0" today, and
that is the cheaper half of the problem.


### 6. An empty prompt file is launchable — OPEN (hardening, not a fix)

`_default_starter` defaults `prompt=""` and `start_session` writes
`prompt.txt` "even when empty", with no guard. `claude -p` with empty
stdin exits 1, so a caller that forgets the prompt produces a session that
dies at launch rather than a refusal naming the mistake.

That exact omission shipped once — the fresh fallback in `supervise`
launched without a prompt, and the run rite had announced as starting
fresh could not start. Fixed at the call site; the enabling condition is
still there for the next caller.

**Cheap guard available:** refuse to launch with an empty prompt, so the
failure is a sentence rather than a silent no-op. Deliberately not taken
during the 0.5.1 release window — it is hardening, and the defect it would
have caught was fixed directly.

### 7. The shared test starter records only the resume id — OPEN

`tests/test_manager_designated_session.py::_starter` appends `resume_id`
and swallows everything else into `**kw`. That is why the missing prompt
survived a green suite: the harness could not see the argument, so no
assertion built on it could either. The permission mode was invisible the
same way, and was missing from the same call.

⚠ **Other tests use that harness.** The same blind spot may be hiding
defects of the same shape — an argument added to one call site and not
another, with nothing able to observe the difference. Not investigated:
recorded so the next person looking at these tests knows the recorder is
the limit, not the coverage.
