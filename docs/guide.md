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

**On a fresh clone, `rite handover show` is empty and `rite status`'s claims,
workers and handover sections are — and that is not a bug.** (The rest of
`rite status` still reports: the project, its modules, the coordinator pool,
and this machine's burn rate.) Claims, heartbeats and handover snapshots are
runtime state: gitignored by design
(SPEC §8.11), never committed, meaningful only on the machine that wrote
them. So `rite status` cannot see a colleague's claims and will not stop you
claiming a path they are already working on — cross-machine coordination is
designed and not built (see [Roadmap](#roadmap)). On a shared project, ask
them. On your own machine across sessions, which is what rite is for today,
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
rite sandbox start alpha --ticket RW-12     # opening prompt: "Work ticket RW-12."
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

## Roadmap

What this doesn't do yet — being straight about it rather than implying
otherwise:

- **Claude-native, on purpose.** `CLAUDE.md`, `.claude/agents/`, and Claude
  Code sessions are first-class concepts here, not hidden behind a provider
  abstraction. rite does not coordinate any other AI tool, and there's no
  plan to add one — an abstraction layer would weaken every integration
  point to the lowest common denominator that Claude Code's actual session
  and config model doesn't need.
- **Cross-machine failover is not built.** Everything above is single-
  machine: one Owner process, its Managers, their Workers. If the machine
  running the Owner goes down, there is no automatic promotion of a
  replacement — a human restarts it. Multi-machine leader election and a
  cross-machine coordination repo are designed but not implemented.
- **Burn-rate reporting is account-wide, not per-project.** It reads your
  own `~/.claude/projects/` transcripts across every project on the
  machine — because that's how Anthropic's weekly quota actually works —
  and reports the raw rate and totals. It deliberately reports **no
  percentage of quota and no exhaustion warning**: nothing local exposes the
  size of an Anthropic weekly quota, so any such percentage would be measured
  against a number you supplied rather than against your real limit. (It does
  split the total into cache reads versus new tokens, as a share of the
  total — a different thing.) It never
  recommends a Worker count or throttles anything; the schedule you set is
  the only thing that controls concurrency.
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

