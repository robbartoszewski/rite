# Changelog

## 0.5.0 (unreleased)

**The headline, stated so it cannot be read as more than it is: rite now
watches the queue and says why it is stopped. It still does not start
sessions.** Every Worker begins because a human, or a Manager session a human
is sitting with, typed a command. The gap this release closes is *"nobody
noticed the run had stalled"*, not *"nobody is doing the work"*.

### Enhancements

- **`rite loop` watches the queue and names why it is not moving.** A cycle
  reads the schedule, every Worker's checkout, the claim ledger and the board,
  and ends in one word: **idle** (nothing waiting), **saturated** (work
  waiting, everyone busy), **blocked** (work waiting, someone free, but the
  files it needs are held), **deadlocked** (same, and the holders look gone),
  **closed** (the schedule allows nobody this hour), plus **ready** and
  **unknown** — the last meaning the board, ledger or schedule could not be
  read, which is a state that needs a name rather than a silence. **Three of
  them stop the run:** `idle` because there is nothing to do, and
  `deadlocked` and `unknown` because continuing would reprint the same line
  every two minutes until morning while nothing moves. The other four sleep
  and look again. `rite loop run` answers once and exits with a code you can
  branch on; `rite loop start` runs it in the background under tmux;
  `rite loop status` asks tmux what is actually running and names any claim
  whose holder has gone quiet; and `rite loop stop` asks it to drain rather
  than killing it. **It starts nothing** — SPEC §9.12 forbids anything unattended from opening a Claude
  session, so the loop prints the Worker it would start and does not start it.
- **`/rite-start` in the Claude app, matching `rite start` in the terminal.**
  A session had to be told what to work on, in a paragraph the user composed
  from memory and differently every time. Now there is one thing to type: it
  orients the session, reads the board and the claims, takes the next ready
  ticket, and hands off to the standing instructions that keep it moving
  across tickets. `rite start` prints the command, so finding either half
  finds the other. **It does not make a session permanent** — it starts work,
  and nothing restarts a session that stops.
- **A blocked ticket stops looking takeable.** Until now, a ticket refused
  because another Worker held its files was indistinguishable from one nobody
  had picked up, so the next session picked it up and was refused in turn.
  A refusal is now written down with the path and the holder, and the loop can
  tell a queue (everyone busy) from a wall (nobody can proceed).
- **rite refuses to hand a Worker a command whose failure it cannot see.**
  `pytest | tail -3` reports `tail`'s exit code; so does anything ending in a
  filter, `|| true`, or `&`. `rite doctor` names these, and — because doctor
  is a command somebody chooses to run — the warning is also written into the
  instructions file the session actually loads, beside the command, saying
  what a green result there proves. This is in the release because it happened
  here three times in one night, once pushing a commit to `main` with the
  failing test named in the same output.
- **A claim whose holder is gone is named, with the command to release it —
  and is never released automatically.** rite could already detect this and
  did nothing with it; a 9.3-day-old claim with no heartbeat ever was still
  blocking a live Worker. Two review rounds killed automatic release: a Worker
  heads-down for thirty minutes looks exactly like a dead one by this signal,
  and guessing wrong puts two sessions on one file, which is the thing the
  ledger exists to prevent. Reporting is correct on every input; releasing
  stays a human decision.
- **`rite release --force` says what it skipped and who holds it.** It matches
  paths exactly, so a near-miss used to be silent. It now names the
  overlapping claim it left behind and the Worker holding it. (`--worker` is
  still ignored with `--force`, as its own help text says; narrowing a forced
  release to one holder exists in the ledger and is not reachable from the
  command line.)
- **Two sandbox caps, because one machine can run several projects.** A
  project's concurrent-Worker limit now counts that project's sandboxes rather
  than every `rite-` sandbox on the machine, and a separate machine-wide limit
  bounds the total whoever is asking. The old behaviour let an unrelated
  project's Workers exhaust yours.
- **`rite status` and `rite doctor` show the loop**, and doctor now probes
  rather than assumes: it names a module that is itself a rite project, and
  checks a local Manager's engine up front instead of failing every subtask.

### Fixes

- **An unreadable heartbeat is no longer read as a dead Worker.** Missing,
  corrupt and unreadable all collapsed into one answer, and that answer was
  "maximally stalled" — the least safe reading of "I could not check".
- **A leftover loop lock could wedge the loop permanently, and said nothing
  useful about it.** The symptom: `rite loop start` refuses with *"another
  loop holds this project's lock"* while `rite loop stop` answers *"no loop is
  running"* — two commands, two true-sounding answers, and no way to
  reconcile them. The cause: `.rite/loop.lock` is a project file and survives
  a reboot, the machine can hand that process number to something unrelated,
  and rite treated "a process with that number exists" as "the loop is
  running". The old remedy printed by `start` was `rite loop stop`, which
  never touches the lock, so the one command that fixes it — deleting the
  lock — appeared nowhere. Now both commands name the stale lock, say which
  two things it could be, and print the exact path to remove. rite still
  cannot tell a recycled number from a loop you started by hand, and says so
  rather than guessing: deleting a live loop's lock is the worse error.
- **`rite loop start` reported success when nothing was running.** It read
  tmux's exit code, which answers "was a session created", not "is it still
  there a second later". It now polls and reports why a session died.
- **A lost-ref race on Linux was reported as a permanent refusal**, because
  Linux git and macOS git describe the same condition in different words. Only
  the Linux CI run could have caught this.
- **The CLI advertised a credential model the spec retired**, and warned about
  a token without checking it was this project's.
- **A test file that collected nothing counted as passing.** The suite now
  fails on a file that contributes no tests, not only on one named wrongly.
- **The guide told readers that cross-machine failover was not built.** It
  shipped in 0.4.0. Three other documents contradicted themselves and are
  fixed; a fourth is flagged as a decision rather than quietly resolved.
- **Lint was red in CI and nobody was running it locally.** Fixed in
  `7b023b5`, whose commit message records that it had been red since
  `8d19410`. That span is this repository's own note rather than something
  re-checked for these release notes: the Actions history for that window has
  since scrolled out of the API's reach, and an earlier draft of this line
  asserted the span as though it had been verified. Saying which claims were
  measured and which were inherited is the whole point of this section.

### Groundwork, not a feature yet

Work began on running subtasks through local models, to do cheap mechanical
steps without spending Claude quota: a durable decomposition record, a router
that sends a pipeline stage to whichever **Manager** holds the duty that stage
needs, a verify runner and a committer, and one subtask taken start to finish
without trusting what the agent says about it.

**The pieces exist; the wiring does not.** Nothing calls the subtask runner
and there is no command that drives the pipeline, so you cannot turn this on.
One piece is already load-bearing: `rite doctor` probes a local Manager's
engine, so it fails up front rather than on every subtask. It is described
here because it is in the code, not because it is a feature.

### Notes for existing projects

- **Upgrading rite does not update your project's files.** `rite update
  --files-only --dry-run` shows what would change; `rite update --files-only`
  applies it.
- **The loop is opt-in and starts nothing.** Nothing enables it for you, and
  `coordination.assign_unattended` remains off by default.
- **If you rely on the concurrent-Worker cap, re-check your number.** It now
  counts this project's sandboxes rather than the machine's, so the effective
  limit may be higher than it was.

### How this release was verified

**Two platforms, and neither covers the other.**

- **Linux, CPython 3.11–3.13**, by CI on every commit (`ubuntu-latest`).
- **macOS 15 (darwin 25.2.0), CPython 3.14.3**, by a local run.

CI is Linux-only, so macOS rests entirely on the local run; the local run is
one Python, so 3.11–3.13 rest entirely on CI. Read either alone and you will
overstate the coverage.

**Linux CI was red for six consecutive commits and is now green.**
`tests/test_spec_5_3_4_is_what_the_cli_says.py` failed on a runner because
its fixture called `rite init` and inherited whatever sandboxing default the
machine produced — enabled on macOS, empty on Linux, where yoloAI's backends
do not exist. The test asserted through a credential list that was therefore
empty. Fixed in `6ea45ae` by having the fixture STATE the sandboxing it
needs instead of accepting what the machine offers. Green since, on every
commit: `6ea45ae`, `014044d`, `1738a7d`, `09393f4`, `6425916`, `67c113e`.

**Do not tag until the Linux run for the tagged commit is green.** That
instruction outlives the failure that prompted it and is kept for the same
reason it was written: it is checkable. "CI was green recently" is not the
same claim — six commits is a run of luck until the tagged one is checked,
and this section previously asserted a CI state that had stopped being true
before anybody re-read it.

The accurate sentence for the local half is "3434 passed, 1 skipped, on
macOS/py3.14", not "the suite is green" — the second implies a matrix, and
this one has a platform that only CI covers.

**And that local run did NOT exercise the macOS keychain.** It was run with
`PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`, because a test reaches
the real keychain and macOS blocks it on an authorization dialog — at 0% CPU,
with no output, indefinitely. Nulling the backend puts the run in the state a
Linux runner is already in, which is why the suite passes there and why CI
cannot see this at all. So the keychain path is covered by **neither** half of
the two-platform split, and anything claiming otherwise would be wrong. Filed
as `HRM-1`..`HRM-4`.

The skip is `tests/test_release_checksums.py`'s two tag-dependent tests, which
derive the tag they need from `VERSION` and so cannot run between bumping the
version and creating the tag. They run again the moment the tag exists and
must pass before the release is published.

**Why the two-platform split earns its place on this codebase.** Three
defects this week were invisible on macOS and caught only by the Linux run:
the coordination state layer classified a lost ref race as a permanent
refusal, because Linux git and macOS git report the same condition with
different strings; two doctor tests asserted exit 0 while their fixtures left
sandboxing enabled; and the credential-list failure above. All three are the
same shape — a value whose vocabulary you only know from the platform in
front of you.

**How this boundary was found, which is the part worth keeping.** It was not
designed. An earlier draft of this section asserted there was no CI at all —
the Actions allowance was exhausted for the private repositories in the same
workspace, and nobody checked that this one is public and therefore
unaffected. Four commits went onto a red `main` while a local macOS run was
reported as verification, because a premise nobody checked reads exactly like
a fact. The two-platform split above is what was found by getting it wrong.

A release that documents how its verification boundary was discovered is more
useful than one stating it as though it had always been known: the second
invites you to trust the boundary, the first tells you what kind of mistake
produced it and therefore what kind to look for next.

**Least confirmed, in order:** the tmux loop lifecycle (`rite loop
start/status/stop`), whose tests use the real binary and therefore *skip*
where tmux is absent, so on a runner without tmux they silently do not run;
claim exclusion, which rests on `flock` and varies by filesystem; and sandbox
capacity counting, which parses `yoloai ls --json` from a macOS-first tool.
Most of the rest is filesystem and JSON handling with no platform surface —
**which is a reason to expect it holds rather than evidence that it does.**

## 0.4.0 (2026-09-18)

### Enhancements

- **Several machines can run one project (Phase 2).** They elect a single
  Owner through the coordination repository, and the role moves on its own
  when a machine stops: the Owner holds a lease it has to renew, the next
  Manager in priority order promotes once that lease expires, and a returning
  higher-priority Manager asks for the role back rather than seizing it, so
  work is never interrupted mid-operation.
- **The state layer is substitutable, and that is demonstrated rather than
  claimed.** Compare-and-swap is per key against an opaque version, with no
  git vocabulary in the interface — no oids, refs, or fetch-then-push — so
  another store can take git's place. The same conformance suite runs
  unchanged against three backends: the git remote, a local filesystem, and a
  socket-served key-value store with no trees, refs or merges, standing in for
  Redis. Git remains the default because it needs nothing a team does not
  already have; nothing in the design requires it.
- **Claims are safe across machines.** `rite claim` checks what other machines
  hold before granting a path and publishes what it granted, so two Workers on
  different machines cannot edit the same file. Releasing publishes too, so a
  finished path stops blocking the fleet immediately rather than when
  something else happens to notice.
- **A stalled machine no longer strands its work.** When a Manager's heartbeat
  lapses, the Owner hands its tickets back and expires the claims it held —
  once, with an audit record on the coordination log.
- **`rite doctor` shows the fleet:** who holds the Owner role and until when,
  every Manager as alive, stalled or unknown, a handover that has been asked
  for and not happened, and the last few coordination events. It also reports
  coordination settings that cannot work — managers with no remote, a
  duplicate name that makes priority ambiguous, a lease already expired when
  written — rather than leaving a half-configured block to do nothing quietly.
- **`rite status` says what this machine's last coordination pass concluded**,
  stamped with its age, read from local state without touching the network.
- **A spec too large to hold can be read a part at a time.** `rite spec index`
  turns a registered spec into addressable units — numbered sections, slug
  paths, and each row of a decision register — and says whether splitting it is
  worth anything before a token is spent: on rite's own 4,000-line spec a
  Worker loads 9% of it at p90, and a short or densely interlinked spec is
  REFUSED, because reading it whole costs less. `rite spec slice <unit>` prints
  what a ticket needs: the unit, what it cites, and the sections everything
  depends on. `/spec-digest` writes a reviewed unit per section under
  `.rite/spec/`, `rite spec show` hands one to a Worker, and `rite spec verify`
  is the gate that refuses to call a digest current when the spec has moved
  under it.
- **A slice that was not enough is counted, because otherwise it is invisible.**
  A Worker that had to read the whole spec anyway records it with
  `rite handover write --spec-fallback <unit>`, and `rite spec status` reports
  fallbacks against retrievals. No data is reported as no data, never as 0%: a
  feature nobody used and a feature that always worked are opposite readings.
  The verdict is also qualified when most units cite nothing — measured across
  nine specs written without a citation gate, slices come out SMALLER there,
  and that is an empty reference graph rather than a good decomposition.

### Notes for existing projects

- **Nothing changes for a single-machine project.** Coordination stays off
  until `coordination.managers` and `coordination.remote` are set and this
  machine names itself in `.rite/machine`. Without that it does not publish,
  elect, or touch another machine's work.
- The coordination cache under `.rite/` collects its own garbage and stays
  bounded. A self-hosted coordination remote holds roughly a fortnight of
  unreachable objects before git's own housekeeping clears them.

## 0.3.0 (2026-09-16)

### Enhancements

- `/spec` writes the project's `SPEC.md` from its brief: it asks the few
  follow-ups the brief warrants, writes Problem, Scope and Non-goals,
  Architecture, Decisions and Open questions, and registers what it wrote. The
  decision register is mandatory, so a ticket saying "implement per D-3"
  resolves to something.
- `rite start` ends by saying what phase the project is in and what the next
  step is, and every generated `CLAUDE.md` carries the phase table itself
  rather than pointing at one it does not contain.
- `rite spec add` and `rite spec remove` rewrite the spec section of the
  `CLAUDE.md` files rite generated, so a spec added after `rite init` reaches
  the Owner and the Workers created before it.
- Follow-up answers go into the spec rather than an `enriched:` block in
  `brief.yaml` that nothing ever read; `rite doctor` reports one it finds so
  earlier projects can move theirs.

### Bug fixes

- Fixed `rite doctor` crashing when yoloAI is installed but will not run. No
  check can end the command in a traceback now: each one is reported as a
  problem instead.
- Fixed `rite init` not asking whether this machine is the Owner or a Manager
  when the project already has a spec or source code.
- Fixed the assignment guidance an Owner session reads: Workers are
  interchangeable and map to a workspace, not to a module.
- Fixed a missing `gh` being reported neutrally when Workers are sandboxed and
  every push from a sandbox depends on it.

## 0.2.0 (2026-09-14)

### Enhancements

- Added a new `rite init` path for projects with an existing spec or source
  code: it asks about both first, and offers the branch, project kind,
  description and frameworks it detects.
- Added per-module commands in `modules.yaml`, and detection for Swift and
  Xcode projects and Node's package manager.
- Sandboxed Workers now take part in coordination — they claim, send
  heartbeats and push their branch — and receive their ticket when started
  with `rite sandbox start --ticket`. Sandboxing is on by default where a
  verified backend exists, and `rite doctor` checks it by running a sandbox.
- Workers are pointed at the project's existing spec (`rite spec add`,
  `rite spec remove`).
- Added `rite credential set claude` to store the Claude login a sandboxed
  Worker uses, and `rite board show` to read a single ticket.

### Bug fixes

- Fixed project resolution when a module is itself a rite project; a bare
  `.rite/` directory no longer counts as a project.
- Fixed credentials appearing in `rite sandbox pane`.
- Fixed JIRA access from sandboxed Workers.
- Fixed `sandbox.enabled` changing how Worker credentials are scoped.
- Fixed README and guide claims that didn't match shipped behaviour.

## 0.1.0 (2026-09-13)

- First public release.
