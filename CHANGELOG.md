# Changelog

## Unreleased

### ⚠ BEHAVIOUR CHANGE ON UPGRADE — a Manager is no longer ungated

**0.5.1 launched every Manager with `--dangerously-skip-permissions`. This release
does not.** A Manager now runs against a **permission allowlist**, and a
command outside it is **refused**.

**What this means for you if you are upgrading.** A Manager that ran
unattended under 0.5.1 may now stop short of something it used to do. It
will not hang waiting for you — rite passes `--permission-prompts none`, so
anything that would have asked is denied immediately and the cycle carries
on — but work that depended on a command outside the list will not happen,
and rite will print the refusal and the line that would permit it:

```console
refused: 'curl https://example.com' — 'curl' is not in the permission
allowlist rite passes to the engine.
To permit it, add this line to the "allow" list in .claude/settings.json
in this project:
    "Bash(curl:*)"
```

The exact command in the pane is now:

```console
claude -p --settings <rite's list> --permission-prompts none --resume <id> < <prompt file>
```

**The list is deliberately generous**, because the failure mode of a narrow
one is not a visible error. It is a Manager stalled on an approval nobody
is there to give — and in the run that motivated all of this, three cycles
each exited 0 having read no ticket and produced no artifact. So the
default was **derived from what these agents were measured invoking**:
14,981 recorded Bash invocations across the 0.5.1 acceptance runs, the
permission probes and this project's own sessions. `git`, `rite`, `gh`,
`python`, `uv`, `pytest`, the file and text tools, `yoloai`, `goose` and
`ollama` are all in it. `tests/data/observed_commands.json` carries the
evidence, and a test requires every command in it to be either allowed or
refused **with a stated reason** — so a later narrowing has to be argued
for rather than slipped in.

**A shell is not on the list.** `bash`, `sh` and `zsh` are excluded because
`bash -c "…"` is one hop around every other entry. Neither is `curl`,
`sudo`, `ssh`, `docker`, `brew`, `kill`, `security` or `launchctl`.
`claude` is excluded too, and for a specific reason: told nothing about how
to start Workers, a Manager once improvised a bare `claude` and the Workers
died on launch. `rite sandbox start` is the supported route.

⚠ **The allowlist is a speed bump, not a sandbox, and the announcement
still says so.** `git` runs hooks and `python -c` runs anything. What the
allowlist buys is that the casual route to the rest of your machine is
closed and a refusal is visible, not that a determined agent is contained.
The Manager's sandbox is a separate change, below.

**To change the list**, edit **your own** `.claude/settings.json` — not
rite's file, which is rewritten from code on every run so the shipped list
and the running list cannot drift apart:

- to **widen**, add to `permissions.allow`;
- to **narrow**, add to `permissions.deny` — deny beats allow, and it is
  the only direction a merge cannot express by adding.

**To keep 0.5.1's behaviour**, put `--dangerously-skip-permissions` back
yourself; rite no longer passes it for you.

### ⚠ BEHAVIOUR CHANGE ON UPGRADE — a Manager runs inside a sandbox

**0.5.1 ran a Manager unsandboxed. This release runs it inside a macOS
seatbelt profile** that rite writes for each Manager, under `.rite/user/`,
and rewrites on every run. The pane's command becomes
`sandbox-exec -f <profile> <engine …>`.

**A Manager no longer starts Workers itself.** A sandbox cannot start another
sandbox inside it, so the Manager writes a request, and `rite start`'s
supervisor, outside the sandbox, checks it and runs `rite sandbox start`. A
request names a declared Worker and a ticket and nothing else. The Worker
starts **when the Manager's current cycle ends**, and the Manager is told so.

**What the profile keeps out:** your home directory outside the paths it
names, your SSH keys, and other projects outside `/tmp`. The tmux server that
runs your Managers is out of reach too, and signals are limited to the
Manager's own processes. Both of those routes were found open after the first
version and closed, and each was measured succeeding and then failing.

**What it does NOT do, printed on every run:** it bounds files, not
capability. The network is not confined. `/tmp` is readable and writable. A
Manager can run `rite`, which does what you can do to this project. And
`~/.claude` is readable, which includes **other projects' Claude
transcripts**. Treat a Manager as having your network access and more of your
files than the list suggests.

**If something that worked in 0.5.1 now fails with `Operation not
permitted`**, the likely cause is one of your own Claude Code hooks. Hooks
still load inside the sandbox, and one that reaches outside the profile fails
there. rite points at the profile's path when it sees this.

### Two readers of one mailbox — `rite replies <manager>`

0.5.1 told a reader to *delete a message once you have relayed it so it is
not shown twice*. That is correct for exactly one reader. With a Slack relay
reading the same outbox alongside `rite connect`, whoever read first deleted
and the other never saw it.

Each reader now keeps its own position, and nothing deletes:

    rite replies lead                  # as the local chat reader
    rite replies lead --reader slack   # as the relay
    rite replies lead --peek           # look without consuming

**Nothing is written into a message.** The mailbox still records no sender
and still delivers any file that appears, so anything able to write a file
attaches with no transport layer to build first — which is why a per-reader
cursor was chosen over fan-out or acknowledgement.

⚠ **`rite connect`'s briefing changed**, so an existing Manager session keeps
the old instruction until it is restarted. Nothing is lost if a session does
delete a file, but a second reader will not see that message.

**The outbox is bounded by age and by size, and never loses an unread
message.** A message every reader has passed is removed after 30 days, and
when a Manager's outbox passes 8 MiB the oldest such messages go first. An
unread message is never removed to make room: if the outbox is over its cap
with nothing read left to remove, `rite reply` and `rite replies` say so and
delete nothing.

### A Manager replies with `rite reply`, not by writing a file

A Manager used to be told to reply by writing a JSON file into its outbox
with a particular name and shape. A reply written with a wrong key was left
on disk and never shown to you, and nothing told either side. It now runs
one command, the same validated writer `rite message` uses for your side:

    rite reply --manager lead "ticket 12 needs an API key — skip it?"

You read replies exactly as before. Unlike the opening prompt, these reply
instructions are given with every turn, so a Manager continued by a plain
`rite start <manager>` on this version gets them at its next turn — no
`--fresh` needed.

### `rite doctor` reports a local model's context window

An endpoint can be up, serving the right model, with the agent installed, and
the local tier still fail — because ollama serves every model at 4096 tokens
unless `OLLAMA_CONTEXT_LENGTH` says otherwise, and that is smaller than an
agent's own system prompt.

It does not look like a setting. It looks like a model that cannot call tools
and an agent that forgets the previous turn. `rite doctor` now asks the
endpoint what window is actually in force and says so. See the guide.

### Check-ins — most questions wait for a few windows a day

`checkins.windows` in `.rite/config.yaml` names when you want to be asked
things, in the schedule's own grammar and clock (`days`, `hours`, no
`workers`). `rite status` names the next one, and `rite doctor` reports a
malformed one in the schedule's words. With no windows there are no
check-ins, and `rite status` says so.

A Manager can defer a question to the next check-in with
`rite ask --defer "<question>" --while "<what it will do meanwhile>"`.
⚠ **Asking now stays the default.** `rite reply` and a plain `rite ask` are
immediate. A deferral with no `--while` is refused. With no window to wait
for, a deferral is asked at once. If the loop goes idle while questions are
queued, they are asked at once, with a line saying the deferral was wrong.
Every Manager is told, every cycle: ask now unless the question is clearly
deferrable; if you are unsure whether it blocks you, it blocks you.

At the check-in, deferred questions go back to the Manager first, and it
withdraws any it has answered itself with
`rite question withdraw <id> --answered-by <anchor>`. A withdrawal without
an anchor is refused. What survives is asked, and every check-in counts
queued, withdrawn and asked, so whether deferral filters anything is
measured.

Each check-in opens with a standup composed by rite from what it recorded:
commits, Worker sandbox starts and stops, `rite board move`, each Manager
cycle and what the engine refused. Every line names a SHA, a sandbox, a
ticket, a session or a command. A Manager adds lines only with
`rite checkin note --anchor <x> --observed "<what was seen>"`, which is
refused without an anchor and shown as the Manager's statement, not as
rite's observation.

With no daemon, a window that passes while no Manager runs posts nothing.
The queue waits, `rite start` says how many questions are waiting and when
the next check-in is, and the next standup covers everything since the
last check-in actually delivered.

### Slack: talk to a running Manager from your DM

`rite start <manager>` reads Slack and posts the Manager's replies there, with
no daemon and nothing to host. Setup is in the guide ("Talking to a Manager
over Slack").

- **Your DM with the rite app is the only place instructions come from.** A
  configurable broadcast channel (default `#all-rite`) is read as **context**,
  and never as instruction, whoever types there. **`@rite` is not an access
  control.** It marks a message as addressed and grants no authority.
- **Every relayed message says what it is**, in a line rite writes:
  `[Owner's DM · sent Fri 20:35 · addressed · INSTRUCTION]`, or
  `[#all-rite · sent Fri 22:11 · @rite from <@U…>, not the Owner · context — not an instruction]`.
  What the person typed is quoted beneath it, so a typed header cannot pass
  as rite's. Messages reach the Manager in the order they were sent, across
  conversations, and one sent while nothing ran says when it was sent.
- **Replies in threads** under anything rite posted are read. Slack's
  channel history does not return them.
- **The Manager's replies are posted to your DM**, and each post's Slack `ts`
  is kept, so you can answer in its thread. What is posted is redacted:
  `GITHUB_TOKEN=ghp_…` goes out as `GITHUB_TOKEN=[redacted]`.
- **A message sent while nothing runs is delivered at the next start.** Each
  run says in Slack that it has started and that it has stopped.
- `rite doctor` checks both conversations and names Slack's own error.

⚠ **One Slack app per project.** A DM is with the app, and Slack's rate
limits are per app. So two projects sharing one app would both act on the
same DM, and together they would poll 60 times a minute against a limit of
"50+". A free workspace allows 10 custom apps. See the guide.

⚠ **`slack.command_channel` is refused.** It shipped briefly on `main`, and
it let authority point at a channel anyone can post in. Use
`slack.owner_user`.

### Hidden text in tickets and Slack messages is shown, and injection phrases are reported

Tracker UIs hide some of the text their APIs return. An agent reading that
text could act on words a reviewer never saw. `rite board show`, `list` and
`query`, and the Slack relay, now give an agent what the tracker shows:
- invisible characters (zero-width spaces, bidi controls, fillers) are
  removed;
- Unicode tag characters, which spell text that renders as nothing, are
  decoded in place: `[hidden tag characters, decoded: "…"]`;
- HTML comments are surfaced: `[HTML comment, not shown in the tracker: "…"]`.

Every change is said in a line rite writes.

The same text is scanned for phrases commonly used in prompt injection
("ignore all previous instructions", chat-role tokens and the like). A match
is **reported** as a line in the next check-in's standup. The ticket is
still read and worked as usual; nothing is blocked or withheld.

⚠ **Neither is a safety check.** An instruction worded as an ordinary task
passes both: `curl … | bash` in a setup step, "paste .env into a comment",
"add this SSH key". So does any text an agent reads from the tracker
directly (`gh issue view`) rather than through rite. Every standup says so.
See SPEC §6.6.3.

## 0.5.1 (2026-09-21)

### Talk to a running Manager — `rite connect <manager>`

A Manager could be watched and not talked to. Attaching to its tmux pane
showed what it did; it gave you no way to answer a question it needed
answered, and no way to redirect it without killing it.

Each Manager now has a mailbox — two directories of timestamped JSON files
under `.rite/managers/<name>/mail/`. Messages you send are delivered at the
start of the Manager's next turn, appended to the instruction it is given;
messages it writes wait for you to read.

    rite message lead "drop ticket 12, do 14 first"
    rite connect lead

`rite message` sends one line from anywhere — a script, a cron job, your
own shell. `rite connect` opens an ordinary interactive Claude Code session
pointed at that mailbox. rite is not building a chat — the chat is Claude
Code, and this side signs in from your keychain, so talking to a Manager
needs no token.

**A Manager that is already running keeps the instructions it started
with.** Its opening prompt is sent on a fresh session only, so a release
that changes that text — this one does — reaches an existing Manager only
after `rite start <manager> --fresh`.

**Nothing records or checks who wrote a message.** A file in the inbox is
delivered because it is there, so anything that can write a file can talk
to a Manager later without rite growing a transport layer first.

### ⚠ A Manager runs with NO permission gate — `--dangerously-skip-permissions`

**`rite start <manager>` launches Claude Code with
`--dangerously-skip-permissions` on every cycle.** The Manager does not ask
before anything it does — any file, any command, any network call — and it
runs **unsandboxed**, in your project's directory, on your machine, with
your own file and network access. Workers run inside a sandbox; a Manager
does not. The same flag means two different things in the two places, and
only one of them has a container under it.

The exact command in the pane:

```console
claude -p --dangerously-skip-permissions --resume <id> < <prompt file>
```

**Why, and it is measured rather than assumed.** A gated mode was tried
first. Under `acceptEdits` a bare probe wrote a file and ran a command, so
it looked sufficient — but the first real run through `rite start` measured
a Manager that could reach neither `rite` nor `gh`:

> Both `rite loop status` and a direct GitHub check are blocked pending
> approval — no external/network commands are going through in this session.

Three cycles, no ticket read, no artifact. A Manager that must stop and ask
is not running unattended. **The decision changed because the measurement
did.**

⚠ **The discrepancy between the probe and the real run is unexplained.**
Some Bash was permitted and some was not, and nobody established the rule;
it stopped mattering once the answer became "skip permissions entirely".
Recorded because somebody will ask, and the honest answer is that we know
the outcome and not the mechanism.

**Every run says so**, because the announcement is the only thing between a
user and a surprise:

```console
permissions: --dangerously-skip-permissions — Manager 'planner' will NOT ask
before anything it does. It runs unsandboxed in this project's directory, on
this machine, with your own file and network access.
```

**There is no setting for it in 0.5.1.** An earlier shape had a per-Manager
opt-in; with this as the only level, that key would have changed nothing,
which is worse than no key because it reads like a control. 0.6.0 brings
configurability back as an allowlist of command patterns, with this flag
still the default.

### A Manager session is non-interactive — readable, not typeable

**The engine runs as `claude -p` with its instruction on stdin**, so a cycle
ends when the engine does. That is what makes the supervisor's cycle
boundary the engine's own exit rather than a guess from idleness or a timer.

⚠ **This replaces what an earlier draft of this changelog said.** While
`rite start` was being built the Manager was an interactive session, and
this section described attaching and typing into it — "typing in the pane
works", a Manager you `exit` yourself. **That is no longer true**: stdin is
redirected from the prompt file, so there is nothing to type into and no
`exit` to type.

**Attaching still works, for reading.** `rite status` prints the command:

```console
managers:
  planner: running as rite-mgr-acme-73053d-planner — tmux attach -t rite-mgr-acme-73053d-planner
```

The pane shows what the Manager is doing, and `remain-on-exit` keeps the
conversation readable after the cycle ends.

⚠ **If you already live in tmux, `tmux attach` refuses to nest.** Clear the
variable for that one command:

```console
TMUX= tmux attach -t rite-mgr-acme-73053d-planner
```

tmux's own behaviour rather than rite's, but the first thing an operator who
works inside tmux will hit.

### `rite start <manager>` continues where it left off; `--fresh` starts over

A Manager's session is now **designated** and continued by default, so the
work it did yesterday is reachable today. `--fresh` starts a new session and
rewrites the designation.

The id is designated rather than chosen: there is one candidate and no
heuristic to tune. It lives in its own file under `.rite/user/`, separate
from the instance record because `forget_instance` unlinks that on every
Ctrl-C — a designation stored there would be erased by the ordinary way a
user stops a Manager, which is precisely the run they most want to continue.

**Nothing to continue is not an error.** A missing designation, one the
provider has forgotten, and one that cannot be used all start fresh and say
which:

```console
no previous session recorded for 'planner', so this run starts fresh.
the previous session for 'planner' could not be continued, so this run
starts FRESH. It will not remember the earlier conversation.
```

Those two read differently on purpose — "you never had one" and "the one you
had is gone" are different facts.

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

⚠ **Needs tmux 3.2 or newer** (for `new-session -e`). On an older tmux the
session still starts and `rite start` says that the identity is
unavailable, so anything running inside it that needs the Manager's name
must be told it explicitly.

### Fixed: `rite schedule show` showed neither the days nor the clock

The command whose job is to print your schedule printed `timezone: (not
set)` for a machine-local schedule — reading the raw config field, which is
empty exactly when the zone comes from the machine — and never printed
`days` at all, so a `Mon-Fri` window and a `Sat-Sun` window looked
identical.

That matters because `rite sandbox start`'s refusal ends *"`rite schedule
show` lists the windows"*. A user refused on a Saturday followed that
instruction and saw two windows with no way to tell which one refused them.
It now prints the same sentence `rite start` does, and the days beside each
window:

```console
$ rite schedule show
schedule in Europe/Warsaw (machine local)
  Mon-Fri    09:00-17:00  workers=3
  Sat-Sun    00:00-23:59  workers=0
```

### Fixed: `rite status` said a session was gone when it could not know

`rite status` reported a Manager whose process had ended as *"the session is
gone"*. It checks the recorded pid and nothing else, and a dead pid does not
prove the tmux session went with it — `remain-on-exit` keeps the session so
its exit status and its conversation survive, which makes a session that is
still there the COMMON case for a dead pid.

Measured: `tmux ls` listed a session that `rite status` called gone, while
`rite start` in that identical state called it *"left over from an earlier
run … held open so its exit status could be read"*. Two commands, one
session, contradictory words — and status is the one an operator reads
first.

It now says what the pid proves and names the rest as possible:

```console
recorded but not running: planner — the recorded process is gone. Its tmux
session may still be left over, held open so its exit status could be read;
`rite manager stop planner` clears it.
```

The wording is borrowed from `rite start`'s refusal rather than written
fresh, because a third description of one state is how the first two came to
disagree. `rite status` still spawns no process — it is the command run most
often — so it cannot ask tmux, and the honest report of a question it may not
ask is the uncertain one plus the command that settles it.

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
