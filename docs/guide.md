# rite — guide

Everything that does not fit on the [README](../README.md) front page. This
is the reference half: the role model, adopting a repo that already has
`.rite/`, credentials, what runs unattended, and how to take it back out.

## The model

Three roles, flat hierarchy — one Owner, one or more Managers, each with its
own Workers:

- **Worker** — one session, one task. Has its own workspace
  (`workers/<name>/`) so it never shares a checkout with anything else, and
  behaves like a real developer: pulls, branches, opens PRs, merges — it
  doesn't get told what to do by hand each time.
- **Manager** — runs a Worker or several, reports up, makes the calls a
  Worker shouldn't make alone. On a single-developer setup this is often the
  same session as the Owner.
- **Owner** — owns the board and the project-wide ticket queue. One per
  project.

A session holding one of these roles is a **Dispatch** — the session you
actually talk to, which assigns work, creates tickets and watches its
Workers. rite's own output uses the word, so it's worth having: "start a
Dispatch session" means "open Claude Code here", nothing more exotic.

Every role reads and writes the same durable state — file claims, the
handover snapshot, the ticket board — so a session that dies costs nothing
more than the time it takes a human to notice and start a replacement.

## Handed a repo that already has `.rite/` in it

Someone else ran `rite init`; you're joining. Don't run it again — it offers
to wipe the project's config, not to add you to it. Install rite, then, in
this order:

1. **Read `CLAUDE.md` at the project root.** Your role, the module map with
   each module's install/build/test/lint commands, and the conventions every
   session here follows. It points at `.rite/context/INDEX.md` and
   `.rite/kb/INDEX.md` rather than inlining them — read the indexes, then
   only the file you actually need.
2. **`rite doctor`** — is this healthy *on your machine*. Credentials are
   per-machine and the pre-push hook lives in your own `.git/hooks` rather
   than in the commit, so a fresh clone is expected to have findings. Read
   the list, not the exit code.
3. **`rite handover show`** — what the last session *on this machine* was
   doing and where it stopped. Start from it instead of re-deriving it.
4. **`rite status`** — claims, workers, what's blocked or stalled, again for
   **this machine**.

**In the Claude app, `/rite-start` does 3 and 4 and then starts working.** It
is the app half of `rite start`, and it exists because `CLAUDE.md` cannot
start anything: it is context, loaded before the session reads anything, and
something still has to be typed. Without the command the thing typed is a
paragraph you compose from memory, differently every time.

It takes the next ready ticket whose files nothing else holds and keeps going
across tickets. **It does not make the session permanent.** Nothing restarts a
session that stops — not the command, and not `rite loop`, which watches the
queue and starts no sessions. When a session ends, you start the next one.

**On a fresh clone, `rite handover show` is empty and `rite status`'s claims,
workers and handover sections are — and that is not a bug.** (The rest of
`rite status` still reports: the project, its modules, the coordinator pool,
and this machine's burn rate.) Claims, heartbeats and handover snapshots are
runtime state: gitignored by design
(SPEC §8.11), never committed, meaningful only on the machine that wrote
them. So **until coordination is configured**, `rite status` cannot see a
colleague's claims and will not stop you claiming a path they are already
working on. Cross-machine claims *are* built — they shipped in 0.4.0 — but
they stay inert until `coordination.managers` and `coordination.remote` are
both set and this machine names itself in `.rite/machine`, so a single-machine
project keeps claiming locally and offline. (This paragraph said "designed and
not built" until 0.5.0, which was the third copy of that sentence in rite's
own documents and the last one found. Fixing a wrong status in one document
leaves it in the others, and nothing goes looking.) On a shared project that
is not configured for it, ask them. On your own machine across sessions,
both commands carry exactly what they say.

### Credentials

**Start here:**

```
rite credential list
```

It prints what this project needs, the keychain account each one lives under,
and what is missing — with the exact command to fix each gap. That is the
answer to *"how do I give rite a GitHub token?"*; you do not have to know the
key's name in advance.

Credentials are **per project**. The secret goes in your OS keychain; the
project's *namespace* is recorded in `.rite/config.yaml`, which is committed.
The keychain account is `<namespace>/<key>`, so two projects on one machine can
hold two different JIRA identities — which a single shared `jira_token` could
not.

```
rite credential set jira_token             # this project
rite credential set github_token --global  # machine-wide fallback
```

Because `config.yaml` holds a **name and never a value**, someone cloning the
project runs `rite credential list` and sees exactly which credentials to set,
without anyone having shared a secret — the `.env.example` pattern.

Already have `jira_token` set globally from before? It keeps working: a project
with no scoped entry falls back to the machine-wide one. That fallback is never
silent — it says so and names the account it used — and
`rite credential migrate jira_token` copies it into this project.

**What this does and does not protect.** Per-project names stop *accidental*
cross-project use. They are **not a security boundary**: keychain access is
per-user, not per-process, so any unsandboxed process running as you can read
every entry rite has stored, whatever it is named. A Worker you open yourself
is unsandboxed whatever `sandbox.enabled` says — only `rite sandbox start`
sandboxes one — so that is the default path.

The sandbox is the only enforcement, and it is blunter than you might expect: a
seatbelt-sandboxed Worker **cannot read the keychain at all** — not another
project's entry, and not its own. That is measured, not assumed (SPEC §10.3),
and it is *why* a sandboxed Worker's token is handed to it through `--env`
rather than fetched. Inside a sandbox, "not found" means "cannot check".

## The schedule — when Workers may run

`schedule.windows` in `.rite/config.yaml` says how many Workers may run at a
given hour, on given days. From 0.5.1 it is **enforced**: `rite sandbox start`
refuses outside a window and names when the next one opens. Before 0.5.1 it
was advisory, and a project configured for zero Workers at the weekend
started one anyway.

```yaml
schedule:
  timezone: Europe/Warsaw        # optional; see below
  windows:
    - {days: "Mon-Fri", hours: "09:00-17:00", workers: 3}
    - {days: "Mon-Fri", hours: "17:00-09:00", workers: 1}
    - {days: "Sat-Sun", hours: "00:00-23:59", workers: 0}
```

**`days`** takes `Mon`, a range `Mon-Fri`, or a list `Sat,Sun`. Ranges wrap,
so `Fri-Mon` is Friday, Saturday, Sunday and Monday — the same way `hours`
already wrapped at midnight. A name it does not recognise is refused with
what it expected, rather than being skipped.

**A window with no `days` means every day**, which is what every window
written before 0.5.1 meant, so an existing schedule keeps its exact meaning
and needs no migration.

**`hours`** is a half-open `HH:MM-HH:MM` range and may wrap midnight
(`17:00-09:00` is the evening plus the following morning).

**Time no window covers is zero Workers** — not the flat cap, not unbounded.
This is the value most projects meet first and never configure: with the
example above, Monday 20:00 is not in any window, so it is zero.

**`workers` is checked against `sandbox.max_concurrent_workers`, and a
window asking for more is REFUSED rather than quietly reduced.** There is no
"lower of the two wins": `rite doctor` reports the window as a problem, so a
schedule that cannot be honoured says so instead of running smaller than you
wrote and letting you believe otherwise.

### Which clock it is on

**`timezone` is optional and defaults to your machine's own clock**, because
a schedule expresses human working hours and "nine to five" means the
operator's day.

⚠ **A container or CI runner with no timezone configured resolves to UTC.**
An operator in Warsaw writing `09:00-17:00` would get a fleet running two
hours off with every individual number looking correct. So rite states the
clock rather than assuming you know it. `rite schedule show`:

```console
$ rite schedule show
schedule in Europe/Warsaw (machine local)
  Mon-Fri    09:00-17:00  workers=3
  Sat-Sun    00:00-23:59  workers=0
```

`(machine local)` means the field was unset and your machine answered;
`(from config)` means you named it. A `timezone` that is not a known zone is
**not silently replaced** — it falls back to machine-local and says which
value it ignored:

```console
schedule in Europe/Warsaw (machine local — schedule.timezone 'Not/AZone' is
not a known timezone and was ignored)
```

`rite start <manager>` prints the same sentence, so the clock is stated at
the moment you spend something.

⚠ **A committed schedule is interpreted on each machine separately.**
`config.yaml` is shared, and with no `timezone` set each operator's fleet
runs on their own local day. That is intended — each person works their own
hours — and it is worth knowing the first time a colleague's Workers start
three hours before yours. Set `timezone` explicitly if you want one clock
for everybody.

### When it refuses

```console
$ rite sandbox start alpha
the schedule allows 0 Workers right now (schedule in Europe/Warsaw (machine
local)). Next open: <when>. Refused rather than started — `rite schedule
show` lists the windows, and raising the count is a config change.
```

A project with **no** schedule is unaffected: an empty schedule reports zero
windows and the refusal is skipped rather than refusing everything.

### Check-in windows — when you want to be asked

`checkins.windows` says when you want to be asked things. It is the
schedule's grammar without `workers`, read by the same parser, on the same
clock (`schedule.timezone`, or your machine's own):

```yaml
checkins:
  windows:
    - {days: Mon-Fri, hours: "09:00-10:00"}
    - {days: Mon-Fri, hours: "14:00-15:00"}
    - {days: Mon-Fri, hours: "20:00-21:00"}
```

`rite status` names the next one, or says a window is open and when it
closes:

```console
check-ins: next at Mon 09:00 (in 59h55m) (schedule in Europe/Warsaw (machine local))
```

**No windows means no check-ins**, and `rite status` says so rather than
printing nothing. `rite doctor` reports a malformed window in the words it
uses for a malformed schedule window, followed by what it costs: no check-in
happens there.

A window that wraps midnight reads `days` the way the schedule does, by the
weekday of each minute. So `{days: Mon-Fri, hours: "23:30-00:30"}` on a
Friday stops at midnight, as a schedule window with the same keys would.

### Questions that can wait for a check-in

A Manager asks you things with `rite reply`, and that stays immediate. It can
also **defer** a question to your next check-in, but only by naming what it
will do meanwhile:

    rite ask --defer "rename --out to --output?" --while "tickets 8 and 9, which do not touch the CLI"

⚠ **The rule every Manager is given, in these words: ask now unless the
question is clearly deferrable; if you are unsure whether it blocks you, it
blocks you.** Deferring a question that blocks it idles a Manager until the
next check-in, which can be hours away. Asking you one that could have waited
costs you thirty seconds. So every doubt ends in asking:

- a deferral with no `--while` is refused ("then it blocks you — ask now")
  and nothing is queued;
- with no check-in window configured, or none that parses, a deferral is
  asked at once and says why;
- if the loop runs out of work while questions are queued, at least one was
  blocking after all. They are asked at once, with a line saying the deferral
  was wrong, in the message and in `rite start`'s output.

A deferred question waits in `.rite/managers/<name>/checkins/queue/`.

**At the check-in it is re-read before it is asked.** At the first cycle
boundary inside a window, the Manager's instruction carries what it deferred,
with one directive: withdraw any you can now answer yourself. A withdrawal
must say where the answer came from, through the same anchor rule as the
journal:

    rite question withdraw q3fa9c1 --answered-by "docs/adr/0004-storage.md:12 chooses SQLite"

What is not withdrawn is asked when that cycle ends, whatever the ending.
Every check-in counts what the filter did, so whether deferral filters
anything is measured, not assumed:

```text
Deferred questions since the last check-in: 2 queued, 1 withdrawn by the Manager before asking, 1 asked now.
  withdrawn q5f9f54: answered by docs/adr/0004-storage.md:12 chooses SQLite
```

### The standup each check-in opens with

At the first cycle boundary inside a window, a check-in is prepared, and it
goes out as one message when that cycle ends: the standup, the deferral
counts, and the questions that survived. A run that stops on its loop's
verdict inside a window sends its check-in instead of skipping it.

⚠ **The standup carries anchors, not prose.** rite composes it from what it
recorded, and every line names something you can check:

```text
Observed by rite:
- commit a6e0d8f Add the notes the Worker will need
- Worker alpha started in sandbox rite-k4proj-a26a8d-alpha, ticket T-1 (Fri 21:34)
- sandbox rite-k4proj-a26a8d-alpha: active at this check-in (`yoloai ls`)
- cycle 1, session rite-mgr-k4proj-a26a8d-lead: finished (Fri 21:35–Fri 21:35)

Stated by the Manager — rite did not verify these:
- the notes the Worker needs are in NOTES.md [anchor: a6e0d8f]
```

What rite records, and where it records it:

- commits: from `git log --all --since`. Work a Worker pushed and this
  checkout never fetched is not seen.
- Worker sandbox starts, stops and destroys: `.rite/events.jsonl`, written
  when `yoloai` reports success.
- board moves made through `rite board move`, with the column the ticket
  actually landed in: also `.rite/events.jsonl`.
- each Manager cycle, how it ended, and what the engine refused: the
  Manager's own `checkins/ledger.jsonl`.

A Manager adds lines only through
`rite checkin note --anchor <SHA, file:line, ticket, sandbox> --observed "<what was seen>"`.
**A note with no anchor is refused**, and a note is shown as the Manager's
statement, never as something rite observed. The first check-in ever covers
the previous 24 hours and says so.

## What runs on its own

Nothing rite runs unattended starts a Claude session.

`rite scheduler install` registers one periodic job. That job checks whether
sessions are still alive and, when your schedule drops to zero workers, writes a
handover. No LLM call, and no session spawned, ever.

This is deliberate. Anything scheduled that could start a session would be
spending tokens while nobody is watching.

**One thing that job is not: purely local.** The handover posts a comment and a
label to your ticket board — a network write, made while you're away. If the board
is unreachable it queues locally and retries rather than losing anything, but if
you'd rather nothing touched a shared board unattended, don't install the
scheduler.

Sessions start only when you type a command: `rite pool fill`, which is refused
above `sandbox.max_concurrent_workers`, or `rite sandbox start <worker>`.

## Watching the queue

```
rite loop start          # in a tmux session, a cycle every two minutes
rite loop status         # is one running, since when, and what to type
rite loop stop           # ask it to finish the cycle it is in and exit
```

**What it does: looks, and tells you what it sees.** Each cycle it reads the
schedule, the claims ledger, your workers' checkouts and the board, then says
what it would do. **What it does not do: start anything.** It spawns no
session and spends no quota, which is why it can run unattended without
contradicting the section above.

That distinction is the point rather than a limitation. The thing this
replaces is a person checking `rite status` every twenty minutes to find out
whether the queue has stalled.

### The verdicts, and why there are seven of them

A cycle ends in one word, and the difference between them is what makes the
loop worth running:

| | |
|---|---|
| `idle` | nothing on the board is waiting. **Stops the loop** |
| `saturated` | work is waiting and every worker is busy. A queue, not a fault |
| `blocked` | work is waiting, a worker is free, and the paths it needs are held by someone still working |
| `deadlocked` | same, except the holders look gone. **This will not clear on its own**, so the loop **stops** and prints what to release |
| `closed` | your schedule allows no workers at this hour **on this day** (§2.7.3). Since 0.5.1 a window can carry `days:`, so a whole day can be closed — this row said "this hour" when the hour was the only dimension |
| `ready` | a worker is free and there is safe work for one |
| `unknown` | the board, the ledger or the schedule could not be read. **Stops the loop** — this is the state that must not be silent, because "could not check" and "nothing to do" look identical in a log |

"Nothing happened" would have been an honest summary of four of those and a
useless one. `saturated`, `blocked`, `closed` and `ready` are why it keeps
going; `idle`, `deadlocked` and `unknown` are why it stops, for three
different reasons — finished, stuck, and could-not-tell.

*This table said "five" and named `idle` as the only verdict that stops the
loop until 0.5.0, which was wrong on both counts and contradicted its own
`deadlocked` row two lines down.*

### Two costs, stated rather than engineered around

**It dies with your tmux server, and it does not survive a reboot.** Nothing
is registered with cron or launchd, so after a restart `rite loop status`
says "not running" rather than having quietly restarted itself. If you want
it back, start it again.

**`rite loop stop` does not kill it.** It writes a drain signal; the loop
finishes the cycle it is in, takes no new work, and exits — up to one cycle,
usually seconds. Killing it mid-cycle is what leaves a claim held by a
process that no longer exists, which is the mess this whole area exists to
prevent.

One loop per project, enforced twice: tmux refuses a duplicate session name,
and a pid lock catches anything that gets past that — a second terminal, a
script, or `rite loop run --watch` by hand. A git worktree is refused
outright, because `.rite/` is tracked and each worktree therefore has its own
claims ledger while sharing one board.

Its output goes to `.rite/loop.log`, rotated like the scheduler's. `rite loop
run` on its own prints one cycle and exits, which is the way to see what it
thinks without leaving anything running.

## Which clock your schedule runs on

A schedule says when Workers may run:

```yaml
schedule:
  timezone: Europe/Warsaw     # optional
  windows:
    - days: "Mon-Fri"
      hours: "09:00-17:00"
      workers: 3
```

**The timezone is optional.** Leave it out and the hours mean your own
machine's local time — "nine to five" is your working day, and on a project
you run from one machine you should not have to name your own timezone.

**rite tells you which clock it picked.** `rite start` prints a line like:

    schedule in Europe/Warsaw (machine local)

`machine local` means no timezone was configured and this machine's clock is
being used. `from config` means the schedule named a zone and that zone is
in use.

⚠ **Set a timezone when more than one machine reads the schedule.** The
schedule lives in `.rite/config.yaml`, which is committed — so everyone
gets the same file, and without a timezone each machine reads it against
its own clock. The same window then means different hours in different
places. At Friday 23:00 UTC, a `09:00-17:00 Mon-Fri` schedule with no
timezone allows 3 Workers in Los Angeles (Friday 16:00) and 0 in Tokyo
(Saturday 08:00) — the day itself is different. One IANA name in the config
removes that entirely.

⚠ **A timezone that is set but misspelled is an error, not a fallback.**
`Europe/Lodnon` is not a timezone. rite does not stop, but it says so
loudly rather than quietly using your machine's clock:

    schedule in Europe/Warsaw (machine local — schedule.timezone
    'Europe/Lodnon' is not a known timezone and was ignored)

`rite doctor` reports it too. This is deliberately different from leaving
the field out: an empty field is a choice, a misspelled one is a mistake,
and before 0.5.1 the two printed the same sentence.

**A typo in `days:` behaves the same way** — `Mon-Fry` is not a day range,
so that window is ignored entirely and contributes no Workers. `rite
doctor` names the typo and says the window was dropped.

## Starting a sandboxed worker

### Before the first one

1. **Store a Claude login for sandboxes, before the first worker starts.** A
   sandboxed session cannot use the login in your macOS keychain; without a
   stored token a sandboxed worker starts, reports that its login has expired,
   and does nothing. In the project root:

   ```bash
   claude setup-token            # prints a long-lived token
   rite credential set claude    # paste it at the hidden prompt
   ```

   `rite credential set claude` needs rite v0.2.0 or later (`rite --version`).
   An older rite does not know `claude` and offers `--allow-unknown`, which
   stores a key that no worker is ever given. rite keeps the token in the
   keychain for this project and passes it into each sandbox it
   starts as `CLAUDE_CODE_OAUTH_TOKEN`, whichever shell you start it from;
   `rite doctor` reports it missing as a problem. Inside the sandbox, Claude
   Code's banner says "API Usage Billing" even with a subscription token:
   yoloAI hands the session its login through a local proxy, and Claude Code
   labels any login it receives that way as API billing, so the label says
   nothing about how you are charged. An exported
   `CLAUDE_CODE_OAUTH_TOKEN` also reaches sandboxes started from that shell,
   and a stored token takes its place. yoloAI's agent list
   (`yoloai help agents`) also names `ANTHROPIC_API_KEY` for Claude; an
   exported key is billed per token.
2. **Install rite where the sandbox can read it.** A sandboxed worker runs
   `rite claim` and `rite heartbeat` with the `rite` on its PATH, and the
   sandbox can read only some of your home directory. `install.sh` installs
   under `~/.local` (through `uv tool` or `pipx`), which it can read; so can
   Homebrew and system locations. A `rite` in a virtualenv anywhere else fails
   inside the sandbox with a permission error (measured with a virtualenv
   under the home directory).
3. **Give it a way to push.** A worker's work leaves the sandbox by being pushed
   (below), so store a GitHub credential for the project with
   `rite credential set github` — a token with Contents and Pull requests
   read/write on the module repositories — and install GitHub's `gh` CLI. rite
   passes the credential in as `GITHUB_TOKEN` and tells git inside the sandbox
   to authenticate github.com through `gh auth git-credential`, which reads
   that variable, so `gh` needs no login of its own; the keychain helper git
   would otherwise use is unreadable there. Commits made in a sandbox are not
   signed: rite turns signing off inside it, because they are the agent's
   commits, not yours. A module whose origin is a local directory cannot be
   pushed from a sandbox; start names each one. A commit pushed from inside a sandbox this
   way has been measured reaching GitHub. Sandboxed pushes run the repository's own hooks, never your
   global ones: rite sets `core.hooksPath` to `.git/hooks` inside the sandbox,
   because a global hooks directory under your home directory cannot be read
   there and made every push fail. If you rely on a global pre-push hook, such
   as a secret scan, it does not run for a sandboxed worker's pushes.

### Starting it

Give the worker its work when you start it. Once the sandbox is running,
nothing in rite can type into the session — `rite sandbox pane` only reads it.
Start prepares the worker's workspace first, as `rite prepare` does; if a
module has uncommitted changes or cannot be updated, it stops and says what to
do (`--allow-dirty` starts on the checkout as it is instead). Run this from the
project root:

```bash
rite sandbox start alpha --ticket ABC-12     # opening prompt: "Work ticket ABC-12."
rite sandbox start alpha --ticket 42        # a GitHub issue, by its number
rite sandbox start alpha --prompt "Add a CSV export to the invoices page."
```

`--ticket` is for work on your board, by its key there; `--prompt` sends its
text as written. With neither, the session starts idle until someone attaches.
After the prepare summary, start prints:

```text
sandbox 'rite-myproject-3f9a2c-alpha' started
  watch or step in: yoloai attach rite-myproject-3f9a2c-alpha
```

The worker starts on its own. `yoloai attach <name>` opens the session so you
can watch it or type to it; yoloAI's own hint for leaving it running is
`Ctrl-b d` to detach.

**Its first screen shows your credentials in plain text.** On macOS, yoloAI
launches the agent by typing a command into the session's shell, and that
command carries every credential rite passed in (`export GITHUB_TOKEN='…'`).
Don't share, record or screenshot a terminal attached with `yoloai attach`.
`rite sandbox pane` prints the same screen with those values replaced by
`[redacted]`, so a Claude session reading it never receives them.

### The next ticket

A worker's sandbox is started once per ticket. Its instructions take it
through the PR, the merge and `rite release`, so it has finished when
`rite status` no longer lists its claims. Then `rite sandbox destroy alpha`, and
start it again with the next ticket; starting it while the old sandbox still
exists is refused. If its session ends before the merge, merge the PR yourself
and run `rite release --worker alpha`.
`rite sandbox status alpha` reports whether the sandbox is running, not whether
the ticket is done.

### What the worker can reach

- **A copy of its own `workers/<name>/`, gitignored files included.** rite asks
  yoloAI for a full copy (`:copy-all`): its default copy leaves out gitignored
  files and nested repositories inside a git repository, which in a rite
  project is the worker's whole checkout. So a module's `.env` or other ignored
  local files reach the sandbox too. The worker works on this copy, not on the
  directory itself, and its work leaves by pushing
  its branch to origin. The copy is discarded with the sandbox: anything not
  pushed is gone. `rite sandbox stop` warns, and `rite sandbox destroy` refuses
  without `--force`, while the copy holds uncommitted changes or commits on no
  remote, naming them. `yoloai apply` is not part of this.
- **The project's `.rite/`, writable**, because claims and heartbeats are
  written there. yoloAI mounts whole directories, so this is more than claims:
  a sandboxed worker can rewrite the project's config, the commands
  `modules.yaml` records (which `rite prepare` prints and workers are told to
  run), the publish gate's suppressions, other workers' heartbeats (hiding a
  stall), and messages in the handover outbox, which rite later posts to your
  board with your credentials.
- **Read-only, each local repository one of its clones fetches from**, so git
  inside can fetch from it. For a module registered without a URL that is the
  project root's own checkout of the module, so those files are readable — and
  read-only, so it cannot be pushed to. Only a module with a URL origin can
  push from a sandbox.

Nothing else in the project is mounted. Other workers' directories are neither
readable nor writable from inside, and a spec kept at the project root rather
than inside a module is not visible to the worker. If a clone fetches from a
directory that contains the worker's own, such as the project root itself,
start says it cannot be mounted.

## A Manager needs a token in your environment

**`rite start <manager>` runs the engine non-interactively**, so a session
ends when its turn does and the supervisor can tell finishing from crashing
by reading an exit status. A non-interactive engine cannot stop and ask you
to log in, so it needs a credential it can read without you:

    claude setup-token                 # prints a long-lived token
    export CLAUDE_CODE_OAUTH_TOKEN=... # in the shell you run rite from

    rite start <manager> --sessions 3 --minutes 90

**rite reads it from the environment and never handles it.** The pane
inherits your shell's environment, so the token reaches the engine without
rite storing it, logging it, or putting it on a command line — there is
nothing for rite to redact, because rite never has the value. Do not pass
it to rite as an argument and do not put it in `.rite/config.yaml`.

Without it, `rite start` refuses before spending a session and tells you
so. That refusal is different from the one you get when a token is present
but the engine still could not authenticate: rite cannot tell a bad
credential from one the engine failed to read, and it says so rather than
guessing.

## A local model needs a context window you have to set

**This one is worth reading before you debug anything else**, because when it
is wrong it does not look like a setting. It looks like a model that cannot
call tools and an agent that forgets what you just told it.

Ollama serves **every** model with a 4096-token context window unless you say
otherwise, whatever the model itself supports. `qwen3:32b` declares 40960 and
tool support, and you still get 4096. An agent's system prompt and tool
schemas are bigger than that before your task is added — measured, opencode
sends about 31KB on the wire and Goose about 19KB — so the window is full
before the work starts.

    export OLLAMA_CONTEXT_LENGTH=32768   # then restart the ollama server

**32768 is the lowest value measured to work**, not a tuned minimum. On rite's
own five-task benchmark at 4096, Goose scored 4/5 and opencode 0/5 with every
task timing out; at 32768 both scored 5/5. Nothing in between was measured, so
a smaller window may well be fine — but it has not been shown to be.

**What it looks like when it is wrong**, so you recognise it rather than
chasing it:

- a model that declares tool support, accepts the request, and returns empty
  content **with no error at all**;
- a file that never gets written, while the agent explains what it would have
  written;
- a resumed session answering "that is not in the conversation history" about
  something you told it one turn ago;
- tasks that run to a timeout without finishing.

**`rite doctor` tells you.** It asks the endpoint what window the model is
actually being served with and says so:

    manager planner: its model is being served with a 4096-token context
    window, below the 32768 measured to work...

It reports **unknown** rather than guessing when it cannot tell — the model is
not loaded yet, or the endpoint is LM Studio, llama.cpp or vLLM rather than
Ollama, none of which expose this through the OpenAI-compatible API.

## Talking to a Manager over Slack

`rite start <manager>` can listen to Slack and post the Manager's replies
there. There is no daemon: **while `rite start` runs, Slack is read. While it
doesn't, nothing is reading.** `rite connect` keeps working alongside it. Each
reader has its own position in the mailbox, so each sees every reply.

**Who can instruct the Manager is decided by where a message is typed**
(SPEC §9.16):

| where | what it counts as |
|---|---|
| **your DM with the rite app** | an **instruction**. Only you and the app are in it |
| **the broadcast channel** (default `#all-rite`) | **context**, whoever types it, `@rite` or not |
| a thread under something rite posted | whatever the conversation it is in counts as |

`@rite` tells the Manager a message is meant for it. **It does not give
anyone authority.** Anyone in the workspace can type it, so a mention in the
broadcast channel is still context. Each message reaches the Manager with a
line rite writes, saying which of these it is.

**Set it up** once per project:

1. Create a Slack app with the bot scopes `channels:history`, `chat:write`
   and `im:history`. Under **App Home**, allow users to send messages in the
   Messages tab. Install it, and `/invite @rite` into the broadcast channel.
2. `rite credential set slack` stores the bot token (`xoxb-…`).
3. In `.rite/config.yaml`:

   ```yaml
   slack:
     owner_user: U0123ABCD        # your member ID: profile → ⋮ → Copy member ID
     broadcast_channel: '#all-rite'
   ```

   With no `owner_user` Slack is **broadcast-only**, and nothing typed in
   Slack instructs anyone.
4. `rite doctor` posts one line to each conversation, reads it back, and
   names Slack's own error if either fails: `missing_scope`,
   `not_in_channel` or `channel_not_found`.

**When no Manager is running**, a message you send waits in Slack. At the
next `rite start` it is delivered at the Manager's first turn, with a line
in the terminal saying how many arrived while it was stopped. Each run posts
a line when it starts and another when it stops, so the last thing in your
DM tells you whether anything is listening. The one exception is a
`rite start` that is killed outright: it cannot post its stop line.

**A check-in goes to your DM, and is mirrored to the broadcast channel.**
The standup and the questions that survived are one message. It is posted
in your DM, where a reply in its thread reaches the Manager as an
instruction answering that check-in. A copy goes to the broadcast channel
for everyone else to read, and replies under the copy reach the Manager as
context, whoever types them. With no `owner_user`, the check-in is posted to
the broadcast channel only, and says that answers there cannot instruct:
answer with `rite message <manager> "…"` instead.

**The first run does not replay history.** Turning Slack on starts reading
from that run's start line, and replies already in the mailbox stay in
`rite replies` rather than being posted.

## Keeping a project's generated files current

`rite init` writes `CLAUDE.md`, `.claude/commands/`, `.claude/agents/`, the
review checklist and the CI workflow, and `rite add worker` writes each
worker's `CLAUDE.md`. Upgrading rite replaces the binary and touches none of
them — so a project initialised months ago runs today's rite against the
instructions and commands of whatever version created it. That is how a fix to
generated content fails to reach the projects that need it.

```bash
rite update --files-only --dry-run   # the plan, plus a diff for anything contested
rite update --files-only             # apply
```

**Your edits are safe, and that is the point.** Every generated section of
`CLAUDE.md` carries a hidden marker recording what rite wrote there:

- a section you have not touched is replaced with the current version
- a section you edited is kept byte-for-byte and reported, with the difference
- a section this version adds is inserted; one rite does not generate is never
  touched, and neither is your own text above the first heading
- a copied file (command, agent, checklist) is replaced only when it is
  byte-identical to something a release shipped; anything else is yours

Take rite's version of something it left alone by naming it:

```bash
rite update --files-only --take-rite "Role: Owner"
rite update --files-only --take-rite review-checklist.md
```

**Projects created before markers existed** (anything initialised with rite
0.3.0 or earlier) are handled too, and this is the case that matters most,
because it is every project a tester already has. rite recognises its own
older text two ways. A section some release wrote the same way for every
project is matched by hash, so the match is exact. A section that is fixed
guidance around a line of your project's own details — your Role section,
naming your ticket backend — is matched against what that release wrote with
the project-specific runs left open: every word of the guidance has to be
there verbatim, and each open run may only stand for about as many lines as
the release itself put there. Either way the section is refreshed, so a
correction written after your project was created reaches it.

Sections that are mostly your project's own data — Project spec, What this is,
Modules — are not attributable at all, and are reported rather than guessed
at. The one place this infers rather than proves is worth stating: if you
edited the project-specific line inside an otherwise-untouched generated
section, the refresh regenerates that line from `.rite/`, because nothing
distinguishes your version of it from rite's. Edit `.rite/` and let the file
follow.

**Two corrections to this section, both found in review before 0.5.0.** It
said a dry run "shows every such line before anything is written" — it does
not. A section refreshed by pattern rather than by hash is recorded with an
empty diff, so the dry run prints one line saying it *would* refresh and
shows you nothing of what changes. Read the file, not only the dry run, if
that distinction matters to you.

And `## Project spec` was listed above as reported rather than rewritten
while it was in fact regenerated in full on every refresh — so prose added
there was destroyed with no diff and no prompt.

**That section is derived, and since 0.5.0 it says so.** It regenerates from
`.rite/` every time, unconditionally, which is what lets a Worker created by
an older rite receive a newer spec section at all. Notes of your own go in
`.rite/spec-notes.md`, which rite never generates and never rewrites. The
"never overwrites your edits" promise is unconditional again because there
is nothing of yours in the derived half — not because refresh got cleverer
about guessing.

**A command or agent rite no longer ships is named, not deleted.** Refreshing
walks the files this version ships, so one a past release wrote and this one
withdrew would otherwise stay in `.claude/` for ever, offered to Workers and
maintained by nothing. It is reported with its path; deleting it is yours to
do, and a command your own team wrote is never mentioned, because only bytes a
release actually shipped are named.

**The CI workflow's pin moves with it.** A workflow written by an older rite
keeps installing that rite in CI, which is the layer SPEC §11.5.1 calls
load-bearing. Refreshing it updates the pin; an edited one is left alone, and an
absent one is reported rather than reinstated.

**rite's `.gitignore` lines are topped up.** The block rite writes grows between
releases, and a project missing a line tracks runtime state it should not — what
`rite doctor` calls "git tracks runtime state". The refresh appends only the
lines this version ships that your file lacks: nothing is rewritten, reordered
or removed, and a project that committed its knowledge base stays that way. One
consequence worth knowing: a rite line you deleted deliberately comes back.

**The pre-push hook is not part of this.** `rite publish install-hook` installs
it and `rite doctor` says when the gate will not run on push, so a files
refresh does not write inside `.git/`.

## If the pre-push hook doesn't install

`core.hooksPath` — set globally by some teams and by some tooling — redirects
git's hooks wholesale, and `.git/hooks` is then ignored completely. A gate
written there is executable, correct, and never runs.

rite checks where git will actually look before installing, and refuses rather
than leaving you a hook that reports itself installed and silently does nothing.
If you see that warning, pick one:

```bash
# point this repo back at its own hooks, then install
git config --local core.hooksPath .git/hooks
rite publish install-hook

# ...or add this line to the pre-push hook in your redirected hooks directory
exec rite publish pre-push
```

`rite publish install-hook` also covers the ordinary case of a project that was
initialised before you wanted the hook — re-running `rite init` won't do it, as
that offers to rewrite the project's config instead. `rite publish install-ci`
is the same thing for the CI workflow, and for the same reason: a project
initialised before this existed has no workflow, and `rite init` is not the way
to get one. Neither command replaces a file it didn't write without `--force`,
and `install-ci` tells you whether whatever is already there actually runs
`rite publish check` — a workflow someone else wrote that calls it covers you
just as well.

Either way, `.github/workflows/publish-gate.yml` runs the same gate on every
push and pull request, and no local git config can switch that off. That is
why a red run there is worth reading as publish-blocking rather than as
untidiness: the hook can be disarmed on your machine without you doing
anything and with no signal that it happened; the workflow cannot.

**Check that it is actually there before relying on it.** `rite init` writes
it, but not into a non-git directory, and never over a file already at that
path — and a project initialised before this existed has none. `rite init`
says which of those happened at the time, and `rite publish install-ci` will
tell you now and install one if there isn't.

Two limits on it, stated rather than implied. It goes into the **project
root** repo only — the gate reads its scan patterns and its suppressions from
`.rite/`, which lives there, so a module repo built alone in CI would re-flag
everything the project already suppressed with a reason and go red for no
reason. That means a module repo pushing to its own remote gets nothing from
this workflow: its only rite layer is the pre-push hook, which is a narrower
scan (what's being pushed, not full history) and is subject to the same
`core.hooksPath` problem. Give that repo its own `.rite/` and copy the
workflow in if you want a gate there. The second limit: this is the GitHub
Actions form — `rite publish check` is the whole gate, so any CI that can
install rite and run one command can run it.

### When it blocks something that is fine

Test fixtures, documentation examples and placeholder paths trip the rules
that exist to catch the real thing. The answer is one line per finding, with
a reason — there is no flag that turns the gate off.

`rite publish check` prints, under each finding, the line to copy:

```
  [rite-pattern] tests/fixtures.py:38 rite-hardcoded-macos-home-directory-path: /Use***ser/
    fingerprint: -:tests/fixtures.py:rite-hardcoded-macos-home-directory-path:sha256-bdcd1f87…
To accept one of these, add a line to .rite/gitleaksignore:
  <fingerprint>  # why this one is safe
```

Paste the fingerprint, add ` # ` and the reason. The reason is required: an
entry without one is an error, not a silently ignored line. Nothing creates
that file for you — the first suppression does.

Two things follow from a fingerprint naming the matched TEXT rather than a
line number. Moving the code does not break the entry, but changing the
matched string does, and the gate then reports the old entry as stale and
blocks on the new string — deliberately, because a fixture token replaced by
a real one should not inherit the old reason. And a second occurrence of the
same string, same rule, same file is covered by the same entry; the gate says
so ("one entry covers 2 findings") rather than letting it pass unremarked.

If you ever remove a suppressed string from the repo, delete its entry in the
same change. The gate reports it stale, which is your reminder.

### Getting rid of it

`rite init` writes into your repo, so here is how to take it back out. There
is no `rite uninstall`; it is all files, and this is the list:

```bash
rite scheduler uninstall               # first, while .rite/ still exists
rm -rf .rite/ workers/ .claude/agents/ .claude/commands/
rm -f CLAUDE.md .github/workflows/publish-gate.yml
# then delete the block `rite init` appended to .gitignore (it is labelled)
```

**The pre-push hook needs a look rather than an `rm`.** rite refuses to
overwrite a hook it did not write, so a `.git/hooks/pre-push` may well be
yours — open it, and delete it only if it contains `rite publish pre-push`.
Same in each module repo. And if your git uses `core.hooksPath`, rite never
wrote into `.git/hooks` at all; the line to remove is in that directory
instead.

`workers/` holds each Worker's own clone of your module repos — check nothing
unmerged is in there before deleting it.

Three things live outside the project and survive all of the above:

- **A scheduler registration, if you installed one.** `rite scheduler
  uninstall` — and run it *first*, while `.rite/` still exists. Otherwise a
  launchd agent or crontab line keeps waking every few minutes against a
  project directory you have emptied.
- **`~/.rite/`** — the cross-project dispatch hub, and a registry of which
  credentials exist and when they were last set. The registry holds names and
  timestamps, never values. The values are in your OS keychain, under the
  service `rite`, with item names scoped to the project that owns them —
  `<namespace>/jira_token` (see *Credentials* below). `rite credential list`
  shows them and `rite credential remove <name>` deletes them.
- **The tool** — `uv tool uninstall rite-ai`, or `pipx uninstall rite-ai`.

## What's built

Everything above — claims, workspace prep, the generated config, ticket
backends (JIRA and GitHub Issues, or none), a continuous handover snapshot,
a cheap non-LLM watchdog, a user-set Worker schedule, burn-rate reporting
from your own Claude Code transcripts, a small standby pool of coordinator
sessions, and optional process sandboxing for Workers via
[yoloAI](https://yoloai.dev) (installed separately; on macOS `rite init` sets
`sandbox.enabled` on by default, and Workers are sandboxed only when started
with `rite sandbox start` — read the caveats in [Roadmap](#roadmap) first).

Since 0.4.0, **several machines can run one project**: they elect a single
Owner through a coordination repository, the role moves on its own when a
machine stops, and a returning higher-priority machine asks for it back
rather than seizing it. This guide said the opposite until 0.5.0 — that
leader election was "designed but not implemented" — which is worth
mentioning because it is the failure a stale guide actually causes: a reader
believing a feature is absent does not go looking for it.

And in 0.5.0: **the loop** ([Watching the queue](#watching-the-queue)),
**`/rite-start`** — one thing to type in the Claude app to get a session
oriented and working the board, matching `rite start` in the terminal — a
**claims report** that names a claim whose holder has gone quiet rather than
leaving it to be discovered when somebody is refused, and a **worker cap that
counts this project's sandboxes** rather than every one on the machine.

## Roadmap

What this doesn't do yet — being straight about it rather than implying
otherwise:

- **Claude-native, on purpose.** `CLAUDE.md`, `.claude/agents/`, and Claude
  Code sessions are first-class concepts here, not hidden behind a provider
  abstraction. rite does not coordinate any other AI tool, and there's no
  plan to add one — an abstraction layer would weaken every integration
  point to the lowest common denominator that Claude Code's actual session
  and config model doesn't need.
- **The loop watches; it does not work the queue yet.** `rite loop` reads the
  board, your workers and the schedule every couple of minutes and tells you
  what it would do. It starts nothing. Two layers would close that, and
  neither is wired: dispatching to a local-model tier, whose pieces exist in
  the code — a decomposition record, a duty router, a verify runner, a
  committer — with nothing calling them and no command to drive them; and
  dispatching Claude sessions, which spends quota unattended and is a
  decision rather than a task. So the
  loop closes the "nobody noticed the queue stalled" gap and not the "nobody
  is doing the work" one.
- **A claim whose holder died is reported, never released.** rite can tell
  that a worker has gone quiet; it cannot tell a crashed session from one
  thinking hard, and releasing a path under a live worker is worse than
  leaving a stale claim. So it names the claim, the holder, and the command —
  and waits for you. The same applies to leftover sandboxes: `rite doctor`
  lists them and says which hold unapplied changes, and destroys nothing.
- **Nothing notices a rejected `git push`.** rite's own code never pushes; a
  Worker's push is plain `git` inside its sandbox, and rite does not classify,
  retry, or report the result. If a branch-protection rule starts refusing
  pushes, the Worker's behaviour is whatever that session decides and rite
  will not tell you. Scope any such rule to your default branch: blocking
  feature-branch pushes means work exists only inside a sandbox that is later
  destroyed.
- **Burn-rate reporting is account-wide, not per-project.** It reads your
  own `~/.claude/projects/` transcripts across every project on the
  machine — because that's how Anthropic's weekly quota actually works —
  and reports the raw rate and totals. It deliberately reports **no
  percentage of quota and no exhaustion warning**: nothing local exposes the
  size of an Anthropic weekly quota, so any such percentage would be measured
  against a number you supplied rather than against your real limit. (It does
  split the total into cache reads versus new tokens, as a share of the
  total — a different thing.) It never recommends a Worker count or throttles
  anything. **Two things you set control concurrency**, not one: the
  schedule's per-window worker count, and `sandbox.max_concurrent_workers`.
  A start is refused when either says no.

  *This said "the schedule you set is the only thing that controls
  concurrency", which was wrong twice over. In v0.5.0 the schedule controlled
  nothing at the start path — `rite sandbox start` never consulted it, so a
  project set to zero workers at the weekend started one anyway while the
  loop reported `closed`. Since v0.5.1 it is enforced, and "only" is still
  wrong because the cap is enforced too. A sentence claiming one mechanism
  where there are two is how someone sets a schedule and believes it is
  doing work it is not.*
- **Sandboxing needs a real security review before you'd trust it with
  untrusted work.** On macOS `rite init` sets `sandbox.enabled` on by default
  (it asks, and the default answer is Yes), and Workers are sandboxed only when
  started with `rite sandbox start`. Four things are worth knowing before you
  use it. SPEC §5.3 has the detail, and says which of them were
  checked against the installed tool and which are second-hand:
  - **Every rite-managed sandbox has unrestricted outbound network**, on every
    backend. Not because the backends can't isolate — some can — but because
    rite never asks: there is no network setting in `.rite/config.yaml` and no
    flag is ever passed. Checked in the code, and it's the claim that matters;
    the backend you pick only changes what rite *could* have asked for.
  - **On Docker, file locking doesn't lock.** A dogfood run found `flock`
    silently succeeding twice on the same file inside a Docker sandbox — so two
    Workers can be granted the same path and both told "claimed", which is the
    one thing claims exist to prevent. `rite doctor` detects it and prints
    `file locking: DOES NOT WORK`; it warns rather than refusing. Docker is
    also the only backend that could contain network egress, so the two pull
    against each other: on Docker, run one Worker.
  - **A sandboxed Worker still runs in bypass-permissions mode.** yoloAI
    launches the agent as `claude --dangerously-skip-permissions` — it's in the
    installed binary and in its base image. That's the point; the sandbox is
    what makes the permissive case safe. But the bypass is *contained*, not
    removed, and it's better to hear it here.
  - **Prefer `rite sandbox destroy` over `rite sandbox stop`** once a Worker's
    token is no longer wanted on a machine: `stop` preserves the sandbox's
    state, and a dogfood session reports the token being among what it keeps
    (not reproduced here — SPEC §5.3.3 records what is and isn't established).
    `destroy` clears the sandbox, with the Worker's copy of its checkout; it
    refuses while that copy holds work on no remote, unless given `--force`.

## What rite deliberately doesn't do

Not the roadmap above — these are boundaries, and they are not moving.

**Accessibility, internationalisation and performance tooling.** All three are
real review concerns, and all three are settled by *running the thing*: an axe
pass over a rendered page, a missing-translation report out of a built bundle,
a p95 from a load test. Your sessions run those commands — that is what the
`CLAUDE.md` rite generates tells them to do — but nothing reads the output
back, so rite cannot see the result, and anything it reported here would be a
generated line asserting a fact nobody measured, which is precisely what its
own review checklist forbids. These belong in `.rite/review-checklist.md` as lines your
reviewers work, and in the module's own CI where the build actually happens.
**Coverage thresholds** are the same call for a different reason: a percentage
measures which lines executed, not whether anything would have noticed them
breaking. The checklist line that says *delete the code under test, or break
the property itself, and confirm the check goes red* asks the question the
percentage only gestures at, and a reviewer can answer it. If you want a
number gating your PRs, your test runner already has one.

