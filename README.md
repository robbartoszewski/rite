# rite

> **Set it running in the evening, review it in the morning — the rite way.**

`rite` runs many Claude Code sessions against one codebase without them
colliding: each works as a named *worker* and claims the files it is about to
touch, and the second worker to claim the same ones is refused.

The point is where your hours go. Once you have it set up, the work that
needs you concentrates into a couple of hours — settling requirements, making
the calls, reviewing what came back — while the sessions keep going in
between. rite is both halves of that: the CLI that hands out the claims, and
the `CLAUDE.md`, agents and commands it installs, which are what tell your
sessions to claim and who to ask.

- **Claim exclusion is measured, not asserted.** A four-minute soak of six
  concurrent workers: 132,321 grants, zero lost and zero held twice. The same
  harness against the previous lock produced 309 lost updates in 10 seconds.
- **A secret scan runs on pre-push and in CI**, over full history. Suppressing
  a finding takes a written reason — there is no global off switch.
- **Workers can run sandboxed, and on macOS `rite init` turns the setting on
  by default.** `rite sandbox start <name>` runs a worker under Seatbelt
  (macOS's `sandbox-exec`) through [yoloAI](https://yoloai.dev), which `rite
  init` offers to install. A session you open yourself is not sandboxed.
  Inside the sandbox, outbound network is not restricted and Claude Code skips
  permission prompts. Every worker gets every credential the project holds;
  `rite add worker --scoped-token` gives one its own GitHub token in place of
  the shared one. On other platforms `rite init` leaves sandboxing off. On
  Docker, a dogfood run found file locking does not lock, so two workers can
  be granted the same path: run one worker there.
- **61 numbered decisions** in [`SPEC.md`](SPEC.md), each with the question it
  answers and the reasoning.

**If you run one session at a time you do not need this.** Several machines
can share one project — they elect an Owner and the role moves on its own when
a machine stops — but that shipped in 0.4.0 and has never been run on two
physical machines over a real network. Treat it as implemented and unproven.

*This sentence said "one machine only" until 0.5.0, which was false from the
moment 0.4.0 shipped, and was contradicted 250 lines below by this same file.*

## How work moves through rite

Spec, plan, tickets, implementation. The last step — a worker taking a ticket
to a merged PR — is built. In a sandbox, its parts have each been run, but not
yet one worker taking a ticket all the way through. The first three are wholly or partly
yours; each step's heading line says which.

Commands starting with `/` are typed into a Claude Code session; `rite …`
commands run in a terminal at your project root. Two kinds of session appear
below:

- **Your Dispatch session** — the Claude Code session you talk to, opened at
  your project root; `rite init` ends by telling you to start one. It runs as
  the role you picked at `rite init` (by default the Owner, the role that owns
  the ticket board). You use it to write tickets and decide which worker takes
  which.
- **A worker session** — Claude Code opened in `workers/<name>/`, a directory
  holding that worker's own clone of each repository, which
  `rite add worker <name>` creates. Its `CLAUDE.md` tells it how to work a
  ticket.

You open both yourself, or start a sandboxed worker with `rite sandbox start`
(step 4).

**1. Spec** — yours to write; rite points workers at it

You write the design, or you already have one. `rite init` looks for
`SPEC.md`, `DESIGN.md`, `ARCHITECTURE.md` or a `docs/`, `adr/`, `rfcs/` or
`design/` directory and offers to point workers at what it finds;
`rite spec add <path>` adds one later, for workers created after that.

On a spec too large to read whole, `rite spec index` turns it into addressable
units and `rite spec slice 5.3` prints just that section, what it cites and the
sections everything depends on — around 9% of rite's own 4000-line spec. It
refuses specs a slice cannot help: a short or densely interlinked document is
cheaper read whole. When a slice was not enough, `rite handover write
--spec-fallback 5.3` records it, and `rite spec status` reports how often that
happens — the only signal that the slices need to be bigger.
Worker sessions get the path, never a copy, and read what their ticket needs.
rite reads the spec when you ask it to — `rite spec index` maps it into
addressable units, `slice` and `show` hand a Worker one, and `verify` refuses
to call a digest current once the spec has moved under it. What it does not do
is read it on its own or check it against the code, so it cannot tell you
whether the spec still describes what you built. A sandboxed worker cannot read
files at the project root, so it sees a spec only when the spec lives inside a
module; keep one it should read in a module's repository.

**2. Plan** — yours; rite has no planning step

You decide what gets built first and what can run side by side, usually by
talking it through in your Dispatch session. The tickets you write next, and
what blocks what between them, are the plan as rite sees it.

**3. Tickets** — rite helps write one at a time; putting a whole plan on the
board is yours

In your Dispatch session, `/refine "export invoices as CSV"` drafts one
ticket a fresh session could start cold: the paths it touches, a definition of
done, and a `Verify` section naming the command that proves it. It checks
your board (JIRA or GitHub Issues) for a duplicate first, and implements
nothing. If the ticket is not on your board yet, file it from a terminal. On
JIRA, record what blocks what as a link; GitHub Issues has no link type rite
can set.

```bash
rite board create "Export invoices as CSV" --description "<what /refine drafted>"
rite board link ABC-19 ABC-12      # JIRA: ABC-19 is blocked by ABC-12
```

**4. Implementation** — built

Give each worker its own clones:

```bash
rite add worker alpha            # creates workers/alpha/
```

Open a worker session in `workers/alpha/` and tell it which ticket to work —
"work ABC-12". Its `CLAUDE.md` walks it through the rest: `rite prepare` to
sync its clones, `rite claim` on the paths before touching them, your
project's own test and lint commands, `/review` (reviewer agents against a
checklist), a PR, and `rite release` after the merge. Several worker sessions
can run at once: if one claims a path that overlaps a path another holds, rite
refuses the claim, and each worker's instructions say not to touch paths
another worker holds.

If sandboxing is on (the default on macOS), start the worker from the project
root instead of opening it yourself — a session you open yourself is not
sandboxed, whatever the setting says. Before the first one, once per project
(these need rite v0.2.0 or later):

```bash
claude setup-token            # prints a long-lived Claude login token
rite credential set claude    # paste it: a sandbox cannot use your keychain login
rite credential set github    # a token with Contents and Pull requests read/write
```

and install GitHub's `gh` CLI, which git inside the sandbox is set to
authenticate through, using that token; `gh` needs no login of its own. A commit pushed from inside a sandbox this way has been
measured reaching GitHub. Sandboxed pushes run the repository's own hooks,
never your global ones, so a global pre-push hook such as a secret scan does
not run there. `rite doctor`
reports a missing Claude login as a problem. Then, per ticket:

```bash
rite sandbox start alpha --ticket ABC-12     # GitHub Issues: --ticket 42
                                            # no board: --prompt "<what to do>"
```

Start prepares the worker's workspace first and refuses one it cannot prepare,
saying what to do. The ticket arrives as the session's opening prompt, so you
don't need to attach. After the prepare summary, start prints the sandbox's
name and a `yoloai attach <name>` command for watching the session or typing
to it (detach with `Ctrl-b d`). That session's first screen shows the credentials
passed in, in plain text, so don't share or record it; `rite sandbox pane`
shows it with them redacted.

The worker edits a copy of its workspace that is discarded with the sandbox,
so its work survives only as a pushed branch. Its `CLAUDE.md` tells it to push
after every commit; anything it has not pushed is gone with the sandbox, and
`rite sandbox destroy` refuses while its copy holds such work. A module whose origin
is a local directory rather than a URL cannot be pushed from a sandbox. Its
`CLAUDE.md` takes it through the PR, the merge and `rite release`, so it has
finished when `rite status` no longer lists its claims; then
`rite sandbox destroy alpha`. If its session ends before that, merge the PR
yourself and run `rite release --worker alpha`.
[Starting a sandboxed worker](docs/guide.md#starting-a-sandboxed-worker) has
the rest.

When you come back, `rite status` lists each worker and the paths it has
claimed; then read the PRs.

If you would rather not come back to find out, `rite loop start` watches the
queue in the background and writes what it sees to a log — every couple of
minutes, one word for why the run is where it is: everyone busy, someone free
but the files they need are held, the schedule allows nobody this hour, or
nothing is waiting. One of those words is **deadlocked**: work waiting, nobody
able to take it, and the holders look gone. That one will not clear on its
own, so the loop stops and prints exactly what to release.

**It starts nothing.** It tells you the run has stalled and what is holding
it; you still start the next worker.

## Example

```console
$ rite claim backend/src/billing --worker alpha --ticket ABC-12
claimed 1 path(s) for alpha

$ rite claim backend/src/billing --worker beta --ticket ABC-19
claim failed: path contention
  backend/src/billing overlaps backend/src/billing (held by alpha)
```

## Quickstart

`rite init` asks a few questions, then writes `.rite/`, the `CLAUDE.md` and
`.claude/` your sessions run from, and the secret scan in both places.

```bash
cd your-project
rite init
rite add worker alpha        # a checkout of its own, under workers/alpha/

# What a worker session runs, in order, over ticket ABC-12 (step 4 above)
rite prepare --worker alpha                            # sync that checkout
rite claim backend/src --worker alpha --ticket ABC-12   # before touching anything
rite heartbeat --worker alpha --ticket ABC-12           # "still alive"
rite release --worker alpha                            # after the PR merges

rite status                  # what is happening now
rite doctor                  # tools and credentials — non-zero on problems
```

`rite status` and `rite doctor` change nothing you would notice, with two
exceptions worth naming rather than rounding off. Once a coordination remote
is configured, doctor pushes and then deletes one throwaway branch there to
check that force-push is allowed. And `rite status` takes a lock file inside
`.rite/` while it reads the coordinator pool — measured, not assumed: a
`rite status --no-board` in a fresh project leaves `.rite/pool.json.lock`
behind. Neither touches your code. `rite help` tours the rest.

## Install

**Not on PyPI yet.** Needs `uv` or `pipx`, `git`, Python 3.11+, and a
signed-in Claude Code. rite hands sessions your environment, so an exported
`ANTHROPIC_API_KEY` is inherited — which Claude Code [bills per token rather
than to your subscription](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan).
Unset it to run on your plan. Sandboxed workers need a little more: a Claude
login token stored with `rite credential set claude` (step 4 above), a GitHub
credential stored with `rite credential set github` and GitHub's `gh` CLI for
pushing, and rite installed as below, under `~/.local`. A `rite`
installed anywhere else in your home directory, such as a project virtualenv,
cannot run inside a sandbox.

```bash
curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.5.1/install.sh | sh
```

*That is a `curl | sh` for a tool that scans your repo for secrets, so two
slower paths — verify the checksum first, or clone and read it — are in
[`docs/install-notes.md`](docs/install-notes.md), with the reasoning. Nothing
phones home.*

**Upgrading rite does not update a project's generated files.** `CLAUDE.md`,
the slash commands, the agents, the review checklist and the CI workflow are
written when you run `rite init`, and a new rite ships improvements to all of
them. After upgrading, in each project:

```bash
rite update --files-only --dry-run   # what would change, and what it won't touch
rite update --files-only             # apply it
```

It never overwrites your edits: a generated section you have changed is kept
and reported, with the difference, and replaced only if you name it
(`--take-rite "<section>"`). One section is derived rather than authored —
`## Project spec` is rebuilt from `.rite/` every time, which is how an older
worker receives a newer one — so notes of your own belong in
`.rite/spec-notes.md`, which rite never generates and never rewrites.
`rite doctor` says whether a project is behind.

## Planned — not built

**This section has been wrong before, in the direction that costs you most.**
Until 0.5.0 it listed multi-machine coordination as unbuilt; that shipped in
0.4.0, and somebody who believes a feature is absent does not go looking for
it. What follows is what is genuinely not built, checked against the code.

- **The loop works the queue.** `rite loop` watches it and says why it is
  stopped — it prints the Worker it would start and does not start one.
  Two ways to close that. Dispatching mechanical subtasks to local models is
  still unwired — but only the half that would run one: nothing outside
  `rite_ai/local/` calls the runner or the harness, and there is no command
  that executes a subtask on a local model. The rest is wired and reachable:
  assignment routes duties through the local duty router, and `rite doctor`
  probes a configured `local:<class>` endpoint rather than assuming it.
  *This bullet said "the pieces are in the code, nothing calls them" until
  0.5.1, which was false in both directions — and the same stale claim had
  already been corrected once in `docs/rite-local-dogfood-decisions.md` section 8
  without anyone propagating it here.* Dispatching Claude sessions
  **unattended** remains forbidden by SPEC §9.12, on purpose, because it
  spends your quota while nobody is watching. *Attended* dispatch arrived in
  0.5.1 as `rite start <manager>`, which keeps a Manager session going in
  your own foreground terminal under two ceilings you typed — so the gap is
  now narrower than this section used to claim, and it is the loop that
  still starts nothing. So the loop closes
  *"nobody noticed the queue had stalled"* and not *"nobody is doing the
  work"*.
- **Questions routed to whoever owns the area.** What is built: you list
  people and their areas in the project config, and that table is printed
  into the Owner's instructions — a heading and a list of names against
  tags. What is not: any instruction telling the Owner to *use* it, and any
  mechanism in rite that matches a question to a person, breaks ties, or
  reroutes when that person's machine stops responding. SPEC §4 is written in
  the present tense and describes that mechanism. So today the table is
  reference material a session may or may not act on, which is less than
  either "built" or "not built" suggests.

  *Kept visible because how this line was arrived at is worth more than the
  line. It read "not built" (false — the config and the table ship), was
  corrected to "a duty the Owner carries out" (also false — the generated
  file prints a heading and a list of names, with no sentence telling anyone
  to use it), and only then to what is above. Three passes, each correction
  made by someone opening the generated output instead of reading the source
  or the previous description. The version that survived is weaker than
  either confident claim, and that is the usual shape: the true answer to
  "is this built?" is often "partly, and less usefully than it sounds".*
- **Nothing notices a rejected push of a Worker's work.** (`rite doctor` does
  probe whether the coordination remote accepts a push, by pushing and
  deleting a throwaway branch — a different thing.) Practical consequence,
  since this
  is the moment people turn on branch protection: scope the rule to your
  default branch. Blocking pushes to feature branches means a Worker's work
  exists only inside a sandbox that is later destroyed.

**Built since this list last claimed otherwise:** several machines on one
project, with Owner election and failover (0.4.0 — see [the
guide](docs/guide.md)); watching the queue (0.5.0, above).

## Why you might not want it

**A Manager runs with no permission gate, unsandboxed, on your machine.**
`rite start <manager>` launches Claude Code against a **permission
allowlist**: a generous list of command families the Manager may run
without asking, and a refusal for anything else. Nothing waits for an
approval — rite passes `--permission-prompts none`, so a command outside
the list is denied at once and the cycle carries on rather than hanging on
a prompt nobody is there to answer.

**The list is generous on purpose.** A Manager that must stop and ask is
not running unattended: the gated modes were measured refusing a Manager
the ability to run `rite` or `gh` at all — three cycles, no ticket read, no
work done. So the default was derived from what these agents were measured
invoking rather than from a plausible-looking set, and it covers `git`,
`rite`, `gh`, `python`, `uv`, `pytest`, `yoloai`, the file and text tools,
and the local-tier binaries. rite states the grant every run rather than
leaving it to be discovered:

```console
permissions: Manager 'planner' may run 77 allowlisted command families
(permissions.json); anything else is REFUSED rather than queued for
approval. It still runs unsandboxed in this project's directory, on this
machine, with your own file and network access — the allowlist narrows what
it reaches for, not what it could reach.
```

⚠ **That last sentence is the important one: this is a speed bump, not a
sandbox.** `git` runs hooks and `python -c` runs anything, so a Manager is
still unsandboxed in your project's directory with your own file and
network access. Workers are different: they run inside a sandbox. A Manager
does not.

To change the list, edit your own `.claude/settings.json` — add to
`permissions.allow` to widen it, or `permissions.deny` to narrow it. rite
rewrites its own file from code on every run and never touches yours. If
that is not a trade you want on a given machine, do not run
`rite start <manager>` there — Workers, the loop and everything else are
unaffected.

**It runs on Pro; what it is *for* may not.** Starting a worker needs no more
than a signed-in Claude Code, plus a token from `claude setup-token` if it
runs sandboxed. But rite neither meters nor throttles — workers
spend your Claude Code quota in parallel, so N of them burn it at roughly N
times one session's rate, against a quota [shared with Claude on a rolling
window](https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code).
The plan you need scales with how many workers you run and for how long; Pro
exhausts sooner than Max. Nor does it end gracefully: rite never reads a
session's exit status, so a worker that runs out stops where it stands, claim
still held until you `rite release` it.

**Nothing opens a session unless you are there.** rite sets up the workspace
and the config; starting Claude is your explicit action — opening a session,
typing `rite sandbox start`, or `rite start <manager>`. This is still true
with `rite loop` running: the loop watches the queue and reports, and the
Worker it says it would start is one you start. Nothing rite runs
*unattended* opens a Claude session, because anything scheduled that could
would be spending your quota with nobody watching.

*This paragraph said "nothing starts a session for you" until 0.5.1, and
`rite start <manager>` made that false.* That command starts a Manager
session and starts the next one when the last finishes cleanly — so it opens
sessions you did not individually type. What keeps the promise above true is
that it runs in the **foreground**, in your own terminal: it is your process,
you can attach to the session and watch it, Ctrl-C ends the run, and it stops
at two ceilings you had to type — `--sessions` (how many) and `--minutes`
(how long), neither of which has a default. Nothing about it is scheduled and
nothing survives your shell.

A bare `rite start <manager>` **continues that Manager's last session** — the
work it did yesterday is reachable today — and `--fresh` starts a new one
instead. Nothing to continue is not an error: a first run, or a session the
provider has forgotten, starts fresh and says which.

**No gates on your code.** Your sessions run your tests and linters — that is
what rite tells them to do — but rite does not read the results, so there is
no coverage threshold, no accessibility pass, and no opinion on your test
strategy.

**Several machines work, and have not been run on several machines.** Owner
election and failover shipped in 0.4.0 and are exercised by the suite,
including against three interchangeable state backends. What has not happened
is two physical machines on one project over a real network. Treat it as
implemented and unproven rather than as either.

**A local model tier needs `OLLAMA_CONTEXT_LENGTH` set.** Ollama serves every
model at 4096 tokens by default, which is smaller than an agent's own system
prompt — so the tier fails in ways that look like models unable to call tools
and agents losing conversation history, rather than like a setting. `rite
doctor` reports the window actually in force. See the guide.

**Claude is the only agent rite drives today, deliberately.** `CLAUDE.md`
and `.claude/agents/` are first-class here rather than behind a provider
abstraction. *This said "no other tool is planned", which contradicted the
"Planned — not built" section eighty lines above in this same file:* a local
model tier (`local:<class>`) is designed, ticketed, parsed by the config and
probed by `rite doctor`, and what is missing is the half that runs a subtask
on one. No other **hosted** provider is planned.

**Workers are interchangeable, so there is no capability routing.** Every
worker holds the same project-scoped credentials, so assignment picks
whichever is free rather than whichever *can*.

**Tested on macOS 26.2**, where everything above has been run end to end,
except a sandboxed worker taking a ticket all the way through (step 4).
Linux is implemented but unverified on real hardware. Windows is not
attempted.

## Documentation

[`docs/guide.md`](docs/guide.md) — roles, per-worker checkouts, handover,
credentials, adopting an existing repo, uninstalling, the roadmap.
[`SPEC.md`](SPEC.md) — the design document, written for someone building it.

## License

MIT
