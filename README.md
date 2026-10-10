# rite

> **Set it running in the evening, review it in the morning — the rite way.**

rite is for getting real work out of coding agents over hours you are not
watching.

- **It keeps going when an agent doesn't.** Agents stall, run past their
  clock, and report work they did not do. rite treats each of those as a
  condition to recover from rather than a verdict on the work: a turn cut off
  by a timeout is retried instead of recorded as a failure, a Manager in an
  unbounded run that has gone quiet for an hour is woken, and a subtask that
  could not start because another agent held the file waits for it instead of
  being abandoned.
- **It tells you what actually happened.** Every claim, refusal, stall and
  delivery is written down as *rite* observed it, not as the agent described
  it. On the local-model pipeline rite also runs the ticket's named command
  itself, and the exit code decides.
- **Your project stays on your machine.** There is no rite service. Claims,
  tickets and state are files in your repository and your home directory, and
  nothing phones home. Claude inference goes to Anthropic, as it does when you
  use Claude Code yourself; a local model's does not leave the machine.

**What it runs on today: Claude and Ollama.** A Manager — the long-running
session that works your board and talks to you — runs on Claude Code, or on a
local model through Goose. Workers, the sessions that implement tickets, run on
either. Adding a provider means writing it into rite, so you cannot add one
yourself; more are planned.

Agents collide when they share a codebase, so the floor under all of it is
file claims: each worker claims the paths it is about to touch, and the second
worker to claim the same ones is refused.

- **Claim exclusion, measured.** A four-minute soak of six
  concurrent workers: 132,321 grants, zero lost and zero held twice. The same
  harness against the previous lock produced 309 lost updates in 10 seconds.
- **A mixed fleet took a ticket from board to delivered branch**, on
  2026-10-09: a Claude Manager orchestrating, a local model authoring the plan,
  a GPU worker implementing, plan review by a different model, and rite's own
  verify deciding. That was the four-check happy-path smoke — one trivial
  ticket, no push, no pull request. The full gate has not passed.
- **A secret scan runs on pre-push and in CI**, over the commits being pushed
  or a pull request's own range — and over full history when that range cannot
  be established. Suppressing a finding takes a written reason; there is no
  global off switch.
- **Workers can run sandboxed, and on macOS `rite init` turns it on by
  default.** `rite sandbox start <name>` runs a worker under Seatbelt through
  [yoloAI](https://yoloai.dev), which `rite init` offers to install; a session
  you open yourself is not sandboxed, and other platforms are left off. A
  worker gets its engine's own login and no GitHub token — rite pushes from
  your machine. Read the sandbox's limits below before you rely on it.
- **115 numbered decisions** in [`SPEC.md`](SPEC.md), each with the question it
  answers and the reasoning.

**If you run one session at a time you do not need this.** Several Managers
working together is one machine, one project root, in this release. Sharing a
project's claims across machines is built but has never been run on two
physical machines; across-machine Managers are planned for 0.8.0.

## How work moves through rite

Spec, plan, tickets, implementation. The last step is built, and in this
release a worker took a ticket all the way to a delivered branch in the
project's own checkout (see the smoke above, and its limits). The first three are wholly or partly yours; each step's heading line says
which.

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
sections everything depends on — a small fraction of rite's own spec. It
refuses specs a slice cannot help: a short or densely interlinked document is
cheaper read whole. Worker sessions get the path, never a copy, and read what
their ticket needs.
rite reads your spec only when asked, and never checks it against the code —
so it cannot tell you whether the spec still describes what you built.

⚠ **A sandboxed worker cannot read files at the project root.** It sees a spec
only when the spec lives inside a module, so keep one a worker should read in
that module's repository.

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
checklist), and a branch or a PR as the ticket says. It does not merge and
does not release its own claim — the claim is held until the work lands, which
is what stops another worker changing the same paths. Several worker sessions
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
request to the upstream — a fine-grained token cannot open one on a
repository you are not a member of. Do not hand the worker a classic token to
get around that: one that can open the pull request can also write to every
repository you can.
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
so its work leaves as a branch rite collects. Whether a module's branch is
pushed is the project's publish setting, not the worker's decision: under
`commit` it never pushes, and rite brings the commits out of the sandbox
itself. Uncommitted changes are the one thing nothing collects, so its
`CLAUDE.md` tells it to commit as it goes, and `rite sandbox destroy` refuses
while its copy still holds work. A module whose origin is a local directory
rather than a URL cannot be pushed from a sandbox. The worker stops short of
the merge — merging is yours, or rite's own step that checks the green is on
exactly the commit being merged — and it leaves its claim held; `rite status`
no longer listing its claims is how you know the work landed, and then
`rite sandbox destroy alpha`.
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

## Example

```console
$ rite claim backend/src/billing --worker alpha --ticket ABC-12
claimed 1 path(s) for alpha — on this machine only

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
# it does NOT run `rite release`: the claim is released when the work lands

rite status                  # what is happening now
rite doctor                  # tools and credentials — non-zero on problems
```

Neither `rite status` nor `rite doctor` touches your code. Three things they
do touch: with a coordination remote configured, doctor pushes and deletes one
throwaway branch to check force-push is allowed; `rite doctor --network`, and
only with that flag, posts one message to your Slack channel to check it
delivers; and `rite status` leaves a `.rite/pool.json.lock` behind while it
reads the coordinator pool. `rite help` tours the rest.

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

A Claude Owner with a local secondary, the setup this release is for (one
machine, one project root):

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
curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.7.0/install.sh | sh
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

From 0.5.1, also run `rite credential import-keychain` once: 0.6.0 keeps
credentials in a 0600 file and no longer reads the keychain. The
[changelog](CHANGELOG.md) lists every step.

## Planned — not built

What follows is not built, checked against the code.

- **The loop does not start the work.** `rite loop` watches the queue and
  prints the Worker it *would* start, then starts nothing. So it closes
  *"nobody noticed the queue had stalled"*, not *"nobody is doing the work"*.
  Opening Claude sessions on a schedule stays forbidden by SPEC §9.12 on
  purpose — it would spend your quota with nobody watching — which is why
  `rite start <manager>` runs in your own foreground terminal instead.
- **Questions are not routed to whoever owns the area.** You can list people
  and their areas in the config, and the table is printed into the Owner's
  instructions, but nothing matches a question to a person, breaks ties, or
  reroutes when someone stops responding. SPEC §4 describes that mechanism in
  the present tense; it is not built.
- **Nothing notices a rejected push of a Worker's work.** So when you turn on
  branch protection, scope the rule to your default branch: blocking pushes to
  feature branches means a Worker's work exists only inside a sandbox that is
  later destroyed.

## Why you might not want it

**A Manager runs with your file and network access.** `git` runs hooks,
`python -c` runs anything, and outbound network is unrestricted. The sandbox
bounds files, not capability: what it buys is that a mistake stays inside the
project. Treat a Manager as able to do what you can do.

⚠ **A Worker's sandbox is escapable on macOS, in the yoloAI that ships
today.** A command handed to a tmux server running outside the sandbox runs as
you — your keychain, rite's credential store, `~/.ssh`, every project. The fix
is in yoloAI's profile and is not released; it has been measured working only
in a locally patched build. Until that ships, run Workers only with a patched
yoloAI first on `PATH`, or treat every Worker as able to act as you.
**On Linux, Workers are not sandboxed by default**, and the Manager sandbox is
weaker there too.

**Nothing waits for an approval.** rite passes `--permission-prompts none`, so
a command outside the Manager's allowlist is refused at once rather than
hanging on a prompt nobody is there to answer. The list covers what these
agents were measured invoking — `git`, `rite`, `gh`, `python`, `uv`, `pytest`,
`yoloai`, the file and text tools — and rite prints the grant on every run.

```console
permissions: Manager 'planner' may run 77 allowlisted command families
(permissions.json); anything else is REFUSED rather than queued for
approval. It runs inside a sandbox — a GUARD RAIL against mistakes, not
containment. See the limitations printed below.
```

**The allowlist is yours to change, and a Manager can change it too.** Edit
`permissions.allow` or `permissions.deny` in your own `.claude/settings.json`;
rite rewrites its own file and never touches yours. But a steered Manager can
write yours, so review changes to it as you would code. If that is not a trade
you want on a machine, do not run `rite start <manager>` there — Workers, the
loop and everything else are unaffected.

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

**Nothing opens a session on a schedule.** rite sets up the workspace
and the config; starting Claude is your explicit action. `rite loop` reports
a stalled queue but starts no worker.

`rite start <manager>` does chain sessions — it starts the next when the last
finishes cleanly — in the **foreground**, in your own terminal: your process,
attachable, and Ctrl-C ends the run. With no flags it runs until you stop it;
`--sessions` and `--minutes` together bound it. Nothing survives your shell.

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

**No gates on your code, outside the local pipeline.** Your Claude sessions
run your tests and linters because rite tells them to, and rite does not read
the results: no coverage threshold, no accessibility pass, no opinion on your
test strategy. The one place it does judge is the staged local-model pipeline,
where rite runs the ticket's named verify itself and the exit code decides
whether a subtask is accepted.

**A local Manager must declare its context window.** Ollama serves every
model at 4096 tokens by default, which is smaller than an agent's own system
prompt — so a Manager on that default fails in ways that look like a model
unable to call tools and an agent losing its conversation history, rather
than like a setting. So rite refuses a local Manager with no
`context_window` (at least 32768), whatever its agent, and pins the declared
window into the model it runs; `rite doctor` says which window each Manager
gets. See the guide.

**Claude and Ollama, and no provider abstraction you can extend.**
A Manager runs on Claude Code or, for a local model, on Goose; the Worker tier
`local:<class>` runs too — a GPU worker implemented a ticket in this release.
`CLAUDE.md` and `.claude/agents/` are first-class here rather than wrapped.
Support for another tool means someone writing it into rite, so the list grows
one entry at a time and you cannot add one from the outside.

**Workers are interchangeable, so there is no capability routing.** Every
worker holds the same project-scoped credentials, so assignment picks
whichever is free rather than whichever *can*.

**Platforms.** Built and tested on **macOS 26.2**, where a sandboxed worker
has now taken a ticket through to a delivered branch in that smoke.
**Linux is thinner**, tested on Ubuntu 24.04 (ARM64): Claude and
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
