# Changelog

## 0.5.1 (unreleased)

### A Manager can record what it noticed — `rite start --record-issues` (BETA)

Feedback about how rite is *working* has only ever existed where a human
was watching. An unattended run produced silence: whatever the session
noticed went into a pane's scrollback and died with it.

With `--record-issues`, a Manager writes what it noticed to files under
`.rite/managers/<name>/journal/`, using two new commands:

- `rite journal observe` — something behaved differently from what the
  docs, or a tool's own output, claimed.
- `rite journal retrospective` — what a ticket or a review round cost,
  what changed, and whether the change would have been caught elsewhere.

**Off by default, and genuinely off.** A Manager started without the flag
is not told the journal exists, so it spends nothing reflecting. Marked
beta because the entry format will change.

**An entry without an anchor is refused** — a commit SHA, a file and line,
a command with its output, a ticket id, or a named log file with a
timestamp. An issue log containing events that did not happen is worse
than no log, so the format makes an uncheckable entry impossible to write
rather than asking for a careful one. `observed` and `inferred` are
separate fields for the same reason.

**A retrospective has nowhere to record a verdict**, deliberately. The
obvious measure is inverted: a review round ending "fix these three
things" produces a commit, one ending "this design would force-release
live Workers, start again" produces nothing — so judging a round by its
output ranks bad reviewing above good.

**Nothing in rite reads these files**, and nothing collects, uploads or
transmits them. `rite start --record-issues` prints the directory so
whoever is running the session knows where to copy from; there is no
export command and none is planned.

**Known gap, stated rather than discovered:** an entry must *have* an
anchor, and an anchor that is present is not verified to resolve — so a
plausible but invented commit SHA is accepted today.

### Fixed before release: `days:` was parsed but ignored by the loop and scheduler

The day dimension above is enforced at `rite sandbox start`, and four other
places that evaluate "how many Workers right now" did not pass the day at
all. On a schedule of `Mon-Fri → 3` and `Sat-Sun → 0`, at Saturday noon,
`rite loop` and the scheduler reported capacity for **3** while
`start_worker` correctly refused — the same kind of disagreement the
advisory schedule produced, one layer up. Found and fixed before the
release, and there is now a check that fails if any caller evaluating a
real moment omits the day again.

### Telling a human quitting from an agent finishing is now measured, not reasoned

A Manager session exits with status 0 whether the agent finished or a
person typed `exit`, so rite asks tmux whether anybody was attached. That
check shipped in v0.5.1's development with its TRUE direction unverified —
no test harness could produce a genuinely attached client. It can now: a
tmux pane is a real terminal, so attaching from inside one gives a real
client on a real tty. Confirmed True while attached, False before and
after.

### A project alias and a Manager can no longer silently be the same word

`rite start <word>` matches Manager names before aliases, so an alias that
shares a name with a declared Manager never resolved — the Manager started
instead, with no message. `rite projects add` now refuses such an alias,
and `rite doctor` reports collisions that appeared later because somebody
committed a Manager role, naming both ways out. It is not refused at `rite
start`: the Manager name is committed and shared, the alias is local, so
the collision lives on one machine and the config it would reject is
correct.

### Ctrl-C stops a Manager and its session; `rite manager stop` recovers an orphan

Ctrl-C on `rite start <manager>` now ends the supervisor **and** the
Manager's session, in one action — previously it halted the restarts and
left a live session spending quota. Reaching `--sessions` or `--minutes`
still leaves the session alive on purpose: a ceiling is an accounting
limit and you may be mid-conversation.

`rite manager stop <name>` is for the orphan case only — the supervising
process died (a crash, a closed laptop, a killed terminal) and the session
is still running with nothing watching it. It is a separate command rather
than `rite stop <manager>` because `rite stop [DIRECTORY]` already exists,
resolves registered aliases, and acts on the board.

Both clear the Manager's instance record, so a stopped Manager no longer
leaves a phantom that makes the next `rite start` think it is running.

### `rite start <Manager>` takes two bounds, and both are required

`--sessions` caps how many provider sessions a run may start; `--minutes`
caps how long it may keep starting them. Neither has a default, because
measurement showed neither suffices alone: sessions that end instantly
reach the count with the clock untouched, and sessions of realistic length
reach the clock after three with the count untouched (SPEC D-82).

`--minutes` is new. The duration bound was already recorded and passed to
the supervisor before this release — by a call site that always passed
zero, because nothing set it.

### Known: telling a finished Manager from a crashed one is unreliable on tmux 3.4

rite asks tmux for a session's exit status. On tmux 3.4 that answer comes
back empty some of the time — measured, in one CI run, absent for a probe
and present for a real session minutes later. Where it is absent the
ending is "unknown", and an unknown ending never resumes: the Manager runs
one session and stops. Safe, and not the feature working. `rite start`
warns when its probe could not read a status.

### Fixed: a clean Manager exit could not be told from a crash on Linux

`#{pane_dead}` and `#{pane_dead_status}` do not arrive together, so a
session that finished normally read as "unknown" and the supervisor
stopped rather than resuming. The check now waits for the status; if it
never arrives the answer is still unknown, which still does not resume.

### Fixed: a Manager name is now checked against what it BECOMES, not just where it goes

A Manager name is a path segment *and* a tmux target, and only the first
was checked. `eu:west` and `v2.0` both passed — `:` is tmux's window
separator and `.` its pane separator — so tmux created a session under a
name no later lookup could resolve. Measured: `rite start` reported that
the session "started and exited immediately" **while a real session was
running**, `rite manager stop` reported success having killed nothing, and
the liveness check answered confidently that it was not alive. In
production that pane runs `claude`, so the failure mode was a paid,
detached session with no instance record and a recovery command that could
not see it.

Names are now validated by an **allowlist** — ASCII letters, digits, `.`,
`_`, `-` — rather than a list of characters to reject, because `:` would
not have been on a reject list either. The stricter tmux-target rule, which
additionally refuses `.`, is passed explicitly at every call site that
takes a Manager name, rather than being the default for every caller —
context *file* names come through the same function, and `database.md`
must keep its dot. A name that was accepted before and is refused now was
already broken; it just failed later and less visibly.

### Fixed: the ending path asks the Manager's own pane, and reads the kill signal

Three defects from a hostile review of how a session's ending is
determined, each reproduced against real tmux before the fix:

- **`-t <session>` answers about the session's *active* pane.** Attach and
  run `tmux split-window`, exit that scratch shell, and `ending` reported
  `finished` while the Manager's own pane was still running its process.
  `start` now records the Manager's `#{pane_id}` and every later question
  names that pane.
- **`#{pane_dead_signal}` carries `kill` in the same call that leaves
  `#{pane_dead_status}` empty**, so an OOM-killed or externally killed
  Manager read as "cannot tell" rather than as a fault.
- **Whether a human was attached** is asked of the session rather than
  inferred.

### A Manager can read its own name — `RITE_MANAGER`

`rite start <manager>` now sets `RITE_MANAGER` on the tmux session, so a
process inside it can determine which Manager it is. Previously the name
reached the session only as prompt text, which the supervisor sends on the
**first** session and deliberately never again — so after a resume nothing
on the machine could answer the question.

`--manager` on `rite journal observe` and `rite journal retrospective` is
therefore no longer required: inside a Manager's own session it defaults to
that Manager. An explicit `--manager` still wins, and outside a session the
command refuses rather than guessing a name — an entry filed under the
wrong Manager is worse than one that was refused.

⚠ **Needs tmux 3.2 or newer** (for `new-session -e`). On an older tmux the
session still starts and `rite start` says that the identity is unavailable
and that commands inside it need `--manager` spelled out.

### Fixed: an anchor of invisible characters is no longer an anchor

`str.strip()` removes Python-whitespace only, so U+200B ZERO WIDTH SPACE,
U+200C ZWNJ and U+2800 BRAILLE PATTERN BLANK all passed the anchor refusal
— measured. An entry whose anchor renders blank is worse than the invented
SHA the known gap below describes, because at least an invented SHA looks
like something a reader will try to check.

The rule is now positive rather than a blacklist: an anchor must contain at
least one alphanumeric character. A first attempt blacklisted Unicode
categories and missed U+2800 on its first run, whose category is `So`.

**Why this release exists, stated plainly: v0.5.0 shipped a schedule that
looks enforced and is not.** A user opens `config.yaml`, sees windows and
worker counts, and has no way to discover that `rite sandbox start` ignores
them — and v0.5.0's changelog says nothing, because the entry explaining it
was written after the tag. That is the silent-wrong-belief defect v0.5.0's
own notes are largely about, shipped inside the release that files it. It is
the reason this is prompt rather than convenient.

v0.5.0 was tagged against its release FINDINGS rather than against its
assigned feature scope. `rite start <name>` and the refined schedule were
both in scope and neither was in the tag. The schedule half is corrected
here; `rite start <name>` is being planned and reviewed before it is built.

### Enhancements

**The schedule can express a working week, and something now enforces it.**
`schedule.windows` entries take an optional `days` — `Mon-Fri`, `Sat,Sun`,
`Fri-Mon` (day ranges wrap, as the hours already did at midnight). A window
with no `days` means every day, which is what every window written before
this release meant, so **an existing schedule keeps its exact meaning and
needs no migration.**

Robert's shape now parses and evaluates:

```yaml
schedule:
  windows:
    - {days: "Mon-Fri", hours: "09:00-17:00", workers: 3}
    - {days: "Mon-Fri", hours: "17:00-09:00", workers: 1}
    - {days: "Sat-Sun", hours: "00:00-23:59", workers: 0}
```

⚠ **The schedule was ADVISORY in v0.5.0 and every release before it. If
you configured one and believed it was enforced, it was not — including in
v0.5.0, which shipped this very paragraph's absence.** `workers_at` was correct and was
read by the loop's verdict and the scheduler tick; `rite sandbox start`
checked the flat `sandbox.max_concurrent_workers` and never consulted it. So
a project configured for zero Workers at the weekend started one anyway
whenever anything asked, while `rite loop status` reported `closed` — the
schedule allows nobody — about a Worker that was running. Two true sentences
that disagreed. From 0.5.1 `start_worker` refuses outside the window and names when
it next opens.

**`schedule.timezone` is now optional and defaults to the machine's own
clock.** The hours express human working hours, and "nine to five" means the
operator's day. ⚠ **A container or CI runner with no timezone configured
resolves to UTC** — an operator in Warsaw writing 09:00-17:00 would get a
fleet running two hours off with every individual number looking right — so
`rite start` prints which clock it resolved: `schedule in Europe/Warsaw
(machine local)`. Note also that the schedule is committed config
interpreted locally, so a shared config gives each machine its own hours.
That is intended — each operator works their own day — and is worth knowing
the first time a colleague's fleet runs on a different schedule from yours.

**Time not covered by any window is zero Workers**, not the flat cap and not
unbounded. Unchanged behaviour, documented because it is the value most
projects meet first and never configure.


### Notes for existing projects

**Nothing you configured changes meaning.** A `schedule.windows` entry with
no `days` still means every day, and a project with no schedule is
unaffected — `workers_at` returns 0 for an empty schedule, so the new
refusal is skipped rather than refusing everything.

**One behaviour does change, deliberately.** `rite sandbox start` now
refuses outside a configured window where it previously started a Worker. If
you have a schedule and have been relying on starts working at any hour,
that is the change to know about — and the refusal names when the window
next opens.

## 0.5.0 (2026-09-20)

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
- **rite warns when a command's failure would be invisible.**
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

- **The publish gate blocked every push it promised not to block.** A finding
  that predates your push, or a suppression entry that no longer matches
  anything, was reported as "not blocking" and then returned exit code 1 —
  which aborts a git push. So adopting rite on a repository with any
  pre-existing finding made pushing impossible, and one stale suppression
  entry stopped every push and every release run until someone deleted it.

  The cause was one integer answering two different questions: *did the gate
  find something* and *should this proceed*. They are now separate. A warning
  exits 0 and is still reported; `rite publish check --strict` makes warnings
  blocking for callers who want that, and the generated CI workflow uses it,
  because in CI something nobody has looked at is worth failing over while
  stopping an unrelated push is not.

  **Two tests covered this and both asserted the exit-code constant rather
  than the outcome, so they passed for the entire life of the bug.** They now
  assert that the push proceeds, that the warning is still reported, and that
  `--strict` blocks.
- **Four commands accepted a path where they wanted a name, and three of
  them escaped the project.** Demonstrated through the CLI, not inferred:
  `rite context add ../../FILE.md …` followed by `rite context remove
  ../../FILE.md` **deleted a file outside the project**; `rite heartbeat
  --worker ../../NAME` overwrote an arbitrary `.json`; and `rite remove
  worker ..` **emptied the entire repository** — `workers/..` is the project
  root, and the unsaved-work guard could not help, because it inspects the
  target's direct children that are git repositories and a project root has
  none. It truthfully reported nothing at risk about a directory holding
  everything, then `rmtree` ran and the traceback arrived after the files
  were gone. Registering a module named `../../X` is the fourth: it creates a
  directory outside the tree from the library, though the CLI happens to
  refuse it for an unrelated reason.

  All four are now validated in the library rather than at the command
  layer, because a guard that only exists in the CLI leaves the same bad
  state reachable from anything else that calls in.
- **`rite update` destroyed anything you wrote under `## Project spec`**,
  while `README.md` promised rite "never overwrites your edits". That section
  is regenerated from `.rite/` on every refresh, which is what delivers a
  newer spec section to a Worker an older rite created — so the rewrite is
  correct and the promise was the thing that was wrong.
  **`.rite/spec-notes.md` is now the place for notes of your own**: rite
  never generates it and never rewrites it, and the generated section names
  it. The promise is unconditional again because nothing of yours lives in
  the derived half.
- **19 tests had never run in CI.** They cover `rite loop start` for real
  rather than against a mock, they skip when tmux is absent, and the runner
  had no tmux — so a skip reported the same green as a pass. That is how
  `rite loop start` shipped as a command printing success while starting
  nothing: it passed every test it had. CI installs tmux now, and the tests
  fail loudly instead of skipping if it ever goes missing there again.
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
- **The publish gate reported "clean" on files it could not open.** Any path
  outside ASCII came back from git quoted and octal-escaped, was used as a
  path, failed to open, and was skipped with no error channel — while the
  count beside it said the file had been scanned. A secret in `café.py` passed
  where the same secret in `cafe.py` blocked. Worse in the "is this from this
  push" check, where an escaped name meant a secret **this push added** was
  demoted to "already in the repository". Fixed three ways: exact parsing,
  skips are now reported rather than silent, and the scanned count no longer
  includes files nothing read.
- **`rite sandbox pane` leaked tokens when asked for colour.** Redaction
  tolerated a line break between a value's characters but not an ANSI escape,
  and a colour reset also broke the `export NAME=` match — so `--ansi`, a flag
  the guide recommends, could print what README and the guide both promise it
  replaces.
- **`rite sandbox destroy` switched off yoloAI's own safety check** on the
  ordinary path, not just under `--force`, leaving rite's guard as the only
  thing between the command and unpushed work — and that guard answered
  "nothing at risk" whenever it could not look. Two checks that fail
  independently beats one check trying to be certain.
- **`rite prepare` reported "up to date" without contacting a remote.** The
  remote check read command output without reading its exit code, so a locked
  index, a half-finished clone or a renamed remote all read as "this module
  has no remote" — and a Worker then built on stale code believing it was
  current.
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

### Known, and not fixed in this release

- **Credentials are visible to other local accounts while a Worker starts.**
  rite passes each project credential to the sandbox as a command-line
  argument, which puts it on the machine's process table for the few seconds
  `yoloai new` runs. Measured rather than assumed: on macOS a non-root user
  can read the full argument list of processes owned by other users, so this
  is not limited to the account that already owns the credentials. **On a
  single-user machine there is no practical exposure; on a shared or
  multi-user machine, treat any credential given to rite as readable by every
  local account.** There is no fix inside rite today — the sandbox tool offers
  no way to pass a secret off the command line — and the designed remedy is
  for Workers to fetch credentials through a validated channel instead of
  having them injected. Recorded here because a release that knows this and
  does not say it is worse than the defect itself.

### Notes for existing projects

- **Suppression entries for non-ASCII filenames need rewriting.** The gate
  used to record such paths in an escaped form; it now records them exactly.
  An existing entry in `.rite/gitleaksignore` for a file whose name is not
  plain ASCII will stop matching — the finding returns as blocking and the old
  entry is reported as stale. Re-copy the line the gate prints. Entries for
  ASCII filenames, which is almost all of them, are unaffected.
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

**The split is asymmetric, and one concrete case shows why that matters more
than the sentence above suggests.** A fix in this release parses file paths
that are not valid UTF-8. Those names cannot exist on APFS — macOS refuses to
create one — and are ordinary on ext4. So the author, working on a Mac, was
*structurally incapable* of testing the case: not careless, not hurried,
unable. A second reviewer found it by reading, and the first version of that
fix would have turned a silently-skipped file into a crash on Linux.

That is the argument for a second pair of eyes stated as a fact rather than a
principle: there are defects the person who wrote the code cannot reach, and
no amount of care by that person closes them. It is also the fourth
platform-split defect in this release, and the third where the platform that
would have caught it is not the platform the author was standing on.

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

The accurate sentence for the local half is "3494 passed, 3 skipped, on
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
