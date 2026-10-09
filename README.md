# rite

> **Tickets in. Draft pull requests out.**

`rite` takes a ticket off your board, plans it, has a **different model than
wrote the plan** approve it before a line is written, builds it, checks the
finished work against the ticket's own definition of done, and opens the pull
request. You start a run and it works the board for the budget you gave it,
asking you questions in Slack rather than in a terminal you have to sit and
watch.

It runs on your hardware, on your engine: Claude for the judgement, a GPU
Worker on your own box for the implementation, or both in one fleet.

**The point is where your hours go.** The work that needs you concentrates
into settling requirements, making the calls and reading what came back, while
the fleet keeps moving in between.

## What it does that a prompt cannot

- **A staged pipeline rite's own code drives.** Definition, plan, plan review,
  approach, work, recomposition verify, delivery. Each move goes through one
  guard, and a stage cannot be entered until the artifact it claims already
  exists — so a model cannot skip a stage or reorder two. A `CLAUDE.md` that
  says "review before you open a PR" is a request; this is not.
- **Plan review by an independent model.** `rite plan approve <ticket>` is
  honoured only from a Manager that holds the plan-review duty, is **not** the
  plan's author, and runs a **different model** from the author — and it fails
  closed when rite cannot place the author. A rejection goes back to the
  planner with your reasons, and that loop is bounded.
- **Nothing is delivered on a piece-by-piece pass.** The composed work has to
  pass the ticket's own agreed check; each subtask passing its own is not the
  ticket working.
- **Claim exclusion is measured, not asserted.** A four-minute soak of six
  concurrent Workers: 132,321 grants, zero lost and zero held twice. The same
  harness against the previous lock produced 309 lost updates in 10 seconds.
- **Your board and Slack are the interface.** JIRA or GitHub Issues for the
  queue; a Manager's questions and your replies go through Slack if you wire
  it up.
- **A secret scan runs on pre-push and in CI**, over full history. Suppressing
  a finding takes a written reason — there is no global off switch.
- **Workers can run sandboxed, and on macOS `rite init` turns the setting on
  by default.** `rite sandbox start <name>` runs a worker under Seatbelt
  (macOS's `sandbox-exec`) through [yoloAI](https://yoloai.dev), which `rite
  init` offers to install. A session you open yourself is not sandboxed.
  Inside the sandbox, outbound network is not restricted and Claude Code skips
  permission prompts. Every worker gets the project's GitHub credential and
  its engine's own login, and nothing else; `rite add worker --scoped-token`
  gives one its own GitHub token in place of the shared one. How well one
  sandbox's process environment is kept from other processes running as you
  depends on the sandbox provider, and is being addressed upstream. On other
  platforms `rite init` leaves sandboxing off. On Docker, a dogfood run found
  file locking does not lock, so two workers can be granted the same path: run
  one worker there.
- **101 numbered decisions** in [`SPEC.md`](SPEC.md), each with the question it
  answers and the reasoning.

## The fleet this release is built for

**A Claude Manager, a Claude Worker, and a GPU Worker.** Claude does the
orchestration and the reviewing; the Workers implement, with the token-heavy
work pushed onto hardware you already own. That is the shape that is supported
and the one to start from.

```bash
rite add worker alpha                        # a Claude Worker
rite add worker gpu1 --engine local:small --agent goose \
  --endpoint http://localhost:11434 --model qwen3:8b \
  --context-window 32768                     # a GPU Worker on your own box
```

A local Worker must declare its `--context-window` (at least 32,768). Ollama
serves every model at 4,096 tokens by default, which is smaller than the
agent's own system prompt, so rite refuses a local Worker that does not say,
and pins the window it was given into the model on the server.

⚠ **An all-local fleet runs, but it is slow** — hours per ticket on a single
32 GB GPU, including the first claim and kickoff. It is a documented
limitation, not the recommended setup, and a local loop that closes on its own
is the direction rather than something this release ships.

**If you run one session at a time you do not need this.** Several machines
can share one project's claims (since 0.4.0) — they elect an Owner and the
role moves on its own when a machine stops — but that has not yet been run on
two physical machines over a real network. Treat it as implemented and
unproven. Several *Managers* working together (routing and replies) is one
machine, one project root, in this release; across machines is planned for
0.8.0.

## How work moves through rite

Spec, plan, tickets, implementation. The first two are wholly or partly yours;
from the ticket onward rite drives it through stages it will not let a model
skip. Each step's heading line says which is which.

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
sections everything depends on — about 3% of rite's own 7,100-line spec. It
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

**2. Plan** — the order is yours; the per-ticket plan is rite's

You decide what gets built first and what can run side by side, usually by
talking it through in your Dispatch session. The tickets you write next, and
what blocks what between them, are the release plan as rite sees it.

Once a ticket starts, the plan for *that ticket* is rite's own stage, not
yours: a Manager holding `decompose` authors it, and it has to be approved by
a Manager that did not write it and runs a different model before any work
begins. You can drive those by hand — `rite local decompose`, `rite plan
approve <ticket>`, `rite plan reject <ticket> --reason-file <path>` — and a
run drives them for you, one stage per cycle.

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

**4. Implementation** — built, and driven through stages

Give each worker its own clones. A Claude Worker needs no flags; a GPU Worker
declares its engine:

```bash
rite add worker alpha            # creates workers/alpha/
rite add worker gpu1 --engine local:small --agent goose \
  --endpoint http://localhost:11434 --model qwen3:8b --context-window 32768
```

Whatever a Worker runs, the ticket moves through the same recorded stages —
definition, plan, plan review, approach, work, recomposition verify,
delivery — and rite's own code refuses to let one be skipped or reordered.
`rite status` shows where each ticket is.

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

and install GitHub's `gh` CLI: rite pushes and opens the pull request with
that token on your machine (`rite deliver`), and the worker never holds it. A commit pushed from inside a sandbox this way has been
measured reaching GitHub. Sandboxed pushes run the repository's own hooks,
never your global ones, so a global pre-push hook such as a secret scan does
not run there. `rite doctor`
reports a missing Claude login, or a missing GitHub token, as a problem, and
`rite sandbox start` refuses to start a worker that could not push its work:
no GitHub token, no `gh`, or a token GitHub says cannot push to the
repository (asked of GitHub without pushing anything). An `ssh`
remote works too; inside the sandbox it is used over HTTPS.

**Contributing through a fork?** Register your fork as the module and give
the token Contents and Pull requests read/write on the fork, and nothing
else. The worker pushes its branch to your fork, and **you** open the pull
request to the upstream. That is the point, not a gap: a diff going to
someone else's project should be read by you before its maintainer sees it,
and with a fine-grained token that is enforced rather than promised —
GitHub does not let one open a pull request on a repository you are not a
member of. Don't hand the worker a classic token to get round it: one that
can open that pull request can also write to every repository you can.
Then, per ticket:

```bash
rite sandbox start alpha --ticket ABC-12     # GitHub Issues: --ticket 42
                                            # starts only on a REFINED ticket
                                            # not a ticket yet: --prompt "<what to do>"
                                            #   files an unrefined chore, starts nothing
```

Start prepares the worker's workspace first and refuses one it cannot prepare,
saying what to do. The ticket arrives as the session's opening prompt, so you
don't need to attach. After the prepare summary, start prints the sandbox's
name and a `yoloai attach <name>` command for watching the session or typing
to it (detach with `Ctrl-b d`). A question the Worker asks reaches you in
Slack, and your reply in that thread is carried back to it, so you need not
attach to answer. That session's first screen shows the credentials
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
rite init                    # questionnaire; writes .rite/, CLAUDE.md, .claude/
rite doctor                  # tools and credentials — non-zero on problems

# The fleet this release is built for
rite add worker alpha        # a Claude Worker, its own checkout under workers/
rite add worker gpu1 --engine local:small --agent goose \
  --endpoint http://localhost:11434 --model qwen3:8b --context-window 32768

# Give a Manager a budget and let it work the board
rite start lead --sessions 3 --minutes 90

rite status                  # where every ticket and claim is
```

Plan review is the one stage that waits for a person or another Manager:

```bash
rite plan approve ABC-12
rite plan reject  ABC-12 --reason-file reasons.md
```

What a Worker session runs itself, over ticket ABC-12 — useful to know when
you are driving one by hand:

```bash
rite prepare --worker alpha                             # sync that checkout
rite claim backend/src --worker alpha --ticket ABC-12   # before touching anything
rite heartbeat --worker alpha --ticket ABC-12           # "still alive"
rite release --worker alpha                             # after the PR merges
```

`rite status` and `rite doctor` change nothing you would notice, with three
exceptions worth naming rather than rounding off. Once a coordination remote
is configured, doctor pushes and then deletes one throwaway branch there to
check that force-push is allowed. `rite doctor --network`, and only with that
flag, posts one message to your Slack broadcast channel to check it delivers.
And `rite status` takes a lock file inside
`.rite/` while it reads the coordinator pool — measured, not assumed: a
`rite status --no-board` in a fresh project leaves `.rite/pool.json.lock`
behind. Neither touches your code. `rite help` tours the rest.

## Running a Manager

A Manager is a long-running session that works your board and talks to you,
over Slack if you set it up. `rite init` offers to declare one (`lead`, on
the Owner machine), and `rite add manager <name> --preset <preset>` declares
more, without opening `.rite/config.yaml`; then run `rite doctor`, which checks
what was written.

One Manager on Claude, the place to start:

```yaml
coordination:
  managers: [lead]
```

```bash
claude setup-token                 # a Claude Manager needs a token of its own
rite credential set claude         # paste it
rite start lead --sessions 3 --minutes 90
```

A Manager's git uses your global config, from inside its sandbox. If you
sign commits, rite turns signing off for the Manager's commits and says so.
A global `core.hooksPath` is not bypassed: the Manager's commits and pushes
fail on its hooks until you opt in, and `rite doctor` gives the one-line fix and what it costs.

**The supported shape for this release is a Claude Manager with a Claude
Worker and a GPU Worker** — the local model does the implementing, not the
orchestrating. See [the fleet this release is built for](#the-fleet-this-release-is-built-for).

A Claude Owner with a *local secondary Manager* is a different arrangement,
also one machine and one project root. It works, and it is the right place to
look if you want a local model carrying routed work rather than tickets:

```yaml
coordination:
  managers: [lead, helper]
  manager_roles:
    - {name: lead, preset: lead}          # Claude; the Owner, it holds 'route'
    - {name: helper, engine: 'local:small', preset: executor,
       endpoint: 'http://localhost:11434/v1', model: 'qwen3:8b', agent: goose,
       context_window: 32768}
```

`helper` needs Goose and Ollama with the model pulled. A local Manager must
declare its `context_window` (at least 32768): `rite start` refuses one that
does not, and rite pins the window into the model it runs, so Ollama's own
default does not decide it. Run each Manager in
its own terminal (`rite start lead …`, `rite start helper …`). The Owner hands
work down with `rite route --ticket RT-12 helper --from-file <draft>`, the text in a file it wrote (every route names its
ticket; work you asked for in chat becomes a chore ticket first); while that work is unfinished its
supervisor waits, spending no session, and starts the Owner's next session,
within the same `rite start`, when the reply arrives. **A local model's report is not trusted:** in testing,
`qwen3:8b` often did not reply at all, and more than once reported a step as done that
had failed. rite checks every reply in a separate session before the Owner
reads it (CONFIRMED, CONTRADICTED or COULD NOT TELL), and the Owner is told
to check too. The checker is also a model and can be wrong. The
[guide](docs/guide.md#declaring-a-manager) has the details.

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
curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.7.0/install.sh \
  | RITE_VERSION=v0.7.0 sh
```

⚠ **Give `RITE_VERSION` explicitly.** `install.sh` still defaults its
`VERSION` to `v0.6.0` (SCRUM-89), so a bare `| sh` installs 0.6.0 whichever
tag you fetched the script from — and 0.6.0 has none of the staged pipeline or
the independent plan review above. Until that default is fixed, the variable
is what decides which version you get.

*That is a `curl | sh` for a tool that scans your repo for secrets, so two
slower paths — verify the checksum first, or clone and read it — are in
[`docs/install-notes.md`](docs/install-notes.md), with the reasoning. Nothing
phones home.*

Check what landed:

```bash
rite --version        # expect 0.7.0
rite doctor           # tools and credentials — non-zero on problems
```

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

From 0.5.1, also run `rite credential import-keychain` once: 0.6.0 keeps
credentials in a 0600 file and no longer reads the keychain. The
[changelog](CHANGELOG.md) lists every step.

## Planned — not built

What follows is what is genuinely not built, checked against the code.

- **A run that starts itself.** Nothing scheduled ever opens a Claude
  session — SPEC §9.12 refuses it on purpose, because it would spend your
  quota with nobody reachable. You start a run with `rite start <manager>
  --sessions N --minutes M` and it works the board inside that budget; the run
  is your own process, so Ctrl-C ends it and so does closing the terminal.
  `rite loop` reports a stalled queue rather than starting the Worker it names.
  *(Driving subtasks on a local model is no longer in this list: `rite local
  step` runs one, and a run drives the local tier a stage per cycle.)*
- **Questions routed to whoever owns the area.** What is built: you list
  people and their areas in the project config, and that table is printed
  into the Owner's instructions — a heading and a list of names against
  tags. What is not: any instruction telling the Owner to *use* it, and any
  mechanism in rite that matches a question to a person, breaks ties, or
  reroutes when that person's machine stops responding. SPEC §4 is written in
  the present tense and describes that mechanism. So today the table is
  reference material a session may or may not act on, which is less than
  either "built" or "not built" suggests.
- **Nothing notices a rejected push of a Worker's work.** (`rite doctor` does
  probe whether the coordination remote accepts a push, by pushing and
  deleting a throwaway branch — a different thing.) Practical consequence,
  since this
  is the moment people turn on branch protection: scope the rule to your
  default branch. Blocking pushes to feature branches means a Worker's work
  exists only inside a sandbox that is later destroyed.

**Built since this list last claimed otherwise:** several machines on one
project, with Owner election and failover (0.4.0 — see [the
guide](docs/guide.md)); watching the queue (0.5.0); and, in 0.7.0, the staged
pipeline, the independent-model plan review, and a GPU Worker driven under any
Manager (SCRUM-72).

## Why you might not want it

**A Manager runs on your machine with your network access, behind an allowlist and a sandbox that are guard rails, not containment.**
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
approval. It runs inside a sandbox — a GUARD RAIL against mistakes, not
containment. See the limitations printed below.
```

⚠ **Read that as written.** The sandbox bounds FILES, not capability, and it
is a set of holes that were looked for and closed rather than a proof of
containment. Signalling processes outside the sandbox is closed on macOS,
and on Linux only on kernel 6.12 or newer: not on a stock Ubuntu 24.04
kernel. Reaching the **tmux server**, which runs outside the sandbox and
would run anything sent to it unconfined, is closed on macOS and **open on
Linux**, where the sandbox is weaker (see the release notes).

So treat a Manager as having your own file and network access, because a
determined one does. `git` runs hooks, `python -c` runs anything, and the
network is not confined at all — seatbelt has no network isolation. **What
the profile buys is that a mistake stays inside the project**, which is
worth having and is not the same as containment. On macOS, Workers are
bounded more tightly: they run in their own sandbox with no tmux server
outside it to reach through. **On Linux, Workers are not sandboxed by
default.**

To change the list, edit your own `.claude/settings.json` — add to
`permissions.allow` to widen it, or `permissions.deny` to narrow it. rite
rewrites its own file from code on every run and never touches yours. A
Manager can write yours, though, so a steered one can widen its own list for
later runs; review changes to that file as you would code. If
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
exhausts sooner than Max. Nor does it end gracefully: rite does not read a
worker session's exit status, so a worker that runs out stops where it stands, claim
still held until you `rite release` it.

**Nothing opens a session unless you are there.** rite sets up the workspace
and the config; starting Claude is your explicit action — opening a session,
typing `rite sandbox start`, or `rite start <manager>`. This is still true
with `rite loop` running: the loop watches the queue and reports, and the
Worker it says it would start is one you start. Nothing rite runs
*unattended* opens a Claude session, because anything scheduled that could
would be spending your quota with nobody watching.

`rite start <manager>` starts a Manager session and starts the next one when the last finishes cleanly — so it opens
sessions you did not individually type. What keeps the promise above true is
that it runs in the **foreground**, in your own terminal: it is your process,
you can attach to the session and watch it, and Ctrl-C ends the run. With no
flags it runs until you stop it; give `--sessions` (how many) and `--minutes`
(how long) together to bound the whole run instead. Nothing about it is
scheduled and nothing survives your shell.

**A lone Manager needs a ticket backend to run more than once.** With no board configured, `rite start` runs one session to help you set one
up. A lone Manager does not start another, whatever `--sessions` says. Where
several Managers share the project, routed work can start more sessions
within `--minutes`, and each one is said.

A bare `rite start <manager>` **continues that Manager's last session** — the
work it did yesterday is reachable today — and `--fresh` starts a new one
instead. If the project's board has changed since that session began,
`rite start` refuses and asks you to choose, because the session would keep
working from the old board. Nothing to continue is not an error: a first run, or a session the
provider has forgotten, starts fresh and says which.

**No gates on your code.** Your sessions run your tests and linters — that is
what rite tells them to do — but rite does not read the results, so there is
no coverage threshold, no accessibility pass, and no opinion on your test
strategy.

**A local Manager must declare its context window.** Ollama serves every
model at 4096 tokens by default, which is smaller than an agent's own system
prompt — so a Manager on that default fails in ways that look like a model
unable to call tools and an agent losing its conversation history, rather
than like a setting. So rite refuses a local Manager with no
`context_window` (at least 32768), whatever its agent, and pins the declared
window into the model it runs; `rite doctor` says which window each Manager
gets. See the guide.

**A Manager runs on Claude Code, or on Goose for a local model.**
`CLAUDE.md` and `.claude/agents/` are first-class here rather than behind a
provider abstraction. A local model tier for Workers (`local:<class>`) is
designed, parsed by the config and probed by `rite doctor`; what is missing
is the half that runs a subtask on one. Other tools are added one at a
time rather than behind a general abstraction: a Cursor adapter is planned
for 0.7.0.

**Workers are interchangeable, so there is no capability routing.** Every
worker holds the same project-scoped credentials, so assignment picks
whichever is free rather than whichever *can*.

**Platforms.** Built and tested on **macOS 26.2**. One step there has not
yet run end to end: a sandboxed worker taking a ticket all the way through
(step 4). **Linux is thinner**, tested on Ubuntu 24.04 (ARM64): Claude and
Goose Managers run inside a sandbox that is weaker than macOS's, and have
been observed working together as Owner and secondary; Workers are not
sandboxed by default. Several
Managers share a project on **one machine, one project root**. Windows is
not attempted.

## Documentation

[`docs/guide.md`](docs/guide.md) — roles, per-worker checkouts, handover,
credentials, adopting an existing repo, uninstalling, the roadmap.
[`SPEC.md`](SPEC.md) — the design document, written for someone building it.

## License

MIT
