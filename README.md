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
- **52 numbered decisions** in [`SPEC.md`](SPEC.md), each with the question it
  answers and the reasoning.

**If you run one session at a time you do not need this**, and it coordinates
sessions on one machine only.

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
Beyond one scan at init for how the spec cites decisions, rite does not read
it, so it cannot tell you whether it is current. A sandboxed worker cannot read
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

`rite status` and `rite doctor` are read-only. `rite help` tours the rest.

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
curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.3.0/install.sh | sh
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
(`--take-rite "<section>"`). `rite doctor` says whether a project is behind.

## Planned — not built

Nothing in this section exists yet. Each item is designed, the design is in
[`SPEC.md`](SPEC.md), and the code is not written.

- **More than one machine on a project.** Planned: machines share claims and
  the Owner role through a git repository, and if the Owner's machine goes
  away, the highest-priority machine still running takes over. Designed in
  SPEC §2.4 and §3.3.
- **Questions sent to the person who owns the area.** Planned: you list people
  and the areas they own, and rite matches each question to one of them,
  breaks ties, and reroutes when that person's machine stops responding.
  Designed in SPEC §4, which is written in the present tense; none of it is
  built.

## Why you might not want it

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

**Nothing starts a session for you.** rite sets up the workspace and the
config; starting Claude is your explicit action — opening a session, or typing
`rite sandbox start`.

**No gates on your code.** Your sessions run your tests and linters — that is
what rite tells them to do — but rite does not read the results, so there is
no coverage threshold, no accessibility pass, and no opinion on your test
strategy.

**One machine.** Coordinating across machines is not built; see
[Planned](#planned--not-built).

**Claude only, deliberately.** `CLAUDE.md` and `.claude/agents/` are
first-class here rather than behind a provider abstraction, and no other tool
is planned.

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
