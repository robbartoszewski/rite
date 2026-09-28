# CU1b — Cursor as a Worker, the review convention, and whether Claude's allowance is the limit

**Measured 2026-09-27**, macOS (arm64), Cursor CLI `2026.09.26-dd393fe` (the
version CU1 pinned), yoloAI 0.11.0 on the `seatbelt` backend, rite at
`origin/main` `1a8bf9d` (v0.6.0 plus its release notes). The key was read at
run time from `~/.cursor-api-key`, Robert's file.

**Citation convention:** a bare `§` is a section of `SPEC.md`; CU1 is
`spikes/CU1-cursor-cli.md`.

It answers three questions, and each answer bears on a decision Robert has open:

1. Does the review convention `rite init` ships survive a Cursor Worker?
   (Open decision 1.)
2. Can rite run Cursor inside a yoloAI Worker sandbox itself, or does it need
   yoloAI to ship a Cursor agent? (Open decision 2.)
3. Is Claude's allowance actually the throughput limit that the case for
   Cursor assumes?

---

## 1. The review convention, in a headless Cursor turn: it holds

**What the convention is**, read from the code rather than assumed. §7.1
says rite "reads none of this at runtime", and that is exact. A Worker's
`CLAUDE.md` (`workspace/manage.py`, `render_worker_claude_md`) says to run
`/review`. `/review` (`templates/commands/review.md`) says to spawn the
`reviewer-*` agents (`templates/agents/`), each declaring
`tools: Read, Grep, Glob, Bash`. None of it is checked by rite for any engine.
The only enforced gate on a Worker's output is the publish gate (a git
pre-push hook plus CI), which does not know which engine ran. So the question
is whether Cursor follows the same three files, not whether it passes a check.

**Fixture:** a git repository holding `CLAUDE.md` and `AGENTS.md`, each with a
rule that stamps a token into every reply. It also holds two sub-agents: one
in `.cursor/agents/` and one in `.claude/agents/`, the directory rite
generates. Each was told to print its own token, then say whether it knew a
token beginning `PARENT`. The parent prompt carried `PARENT-ONLY-93`, with an
instruction not to pass it on, and forbade reading `.cursor/` or `.claude/`.
Run as `agent -p --trust --output-format stream-json < prompt`, outside any
sandbox, with its own `CURSOR_CONFIG_DIR`.

| what | result |
|---|---|
| sub-agents listed | both custom ones, beside Cursor's built-ins (`generalPurpose`, `explore`, `bugbot`, `security-review`, ...) |
| `.cursor/agents/cu-reviewer.md` | ran, as a `taskToolCall` with `subagentType: {custom: {name: "cu-reviewer"}}` and its own `agentId`; printed its token |
| **`.claude/agents/cc-reviewer.md`** | **ran the same way**, and printed its token |
| fresh context | **both said `NO-PARENT-TOKEN`**: neither saw the parent's secret |
| `CLAUDE.md` and `AGENTS.md` | **both applied**, in the parent and inside each sub-agent |
| **`/cc-review` from `.claude/commands/cc-review.md`**, given as the whole prompt | **ran**: the reply was the command body's token |
| a sub-agent declaring `tools: Read, Grep, Glob`, told to create a file | **no file was created**; it said it had only Read, Grep and Glob. Run twice, once with nothing in the parent prompt about files |

Exit 0 each time; about 30 s for the two-sub-agent turn.

⚠ **What the `tools:` row does not show.** Claude Code enforces `tools:`.
For Cursor, a model that read the frontmatter as an instruction and obeyed
it looks the same as a runtime that removed the tool. n=2, and no attempt
was made to tell them apart.

⚠ **An earlier static reading was wrong in the other direction.** A search
of the CLI bundle for `.claude/commands` found nothing, and the command ran
anyway. Strings in a bundle are not behaviour.

**Not measured:**
- the convention inside a Worker sandbox (section 2 ran plain turns);
- on a real ticket;
- the reviewer model: Cursor reported `model: "default"`, the account's choice.

---

## 2. Cursor inside a yoloAI Worker sandbox, driven by rite: it works

yoloAI 0.11.0 is third-party (`kstenerud/yoloai`). Its agent list is fixed
(aider, claude, codex, gemini, opencode, plus `idle` and `shell`), and a user
cannot define another. Extensions (`yoloai x`) are shell wrappers around
`yoloai` itself. So the route that needs nothing upstream is an `idle`
sandbox that rite drives. rite already creates `idle` sandboxes for its
self-test (`sandbox.verify_sandbox`).

**Fixture:** a repository under `$HOME`, not under a granted temporary
directory. The key was copied to a 0600 file in a 0700 directory, also under
`$HOME`, and mounted into the sandbox **read-only, with `-d`**. **No `--env`**:
nothing about the key was on any command line.

    yoloai new --backend seatbelt --agent idle -d <keydir> cu-spike <repo>:copy-all

**The boundary, checked before anything was believed** (through `yoloai exec`):

| probe | result |
|---|---|
| write outside the sandbox, beside the fixture | `Operation not permitted` |
| write into the key's directory | `Operation not permitted` |
| write into the working copy | allowed |
| read the key file | allowed (70 bytes) |

**Turns:**

| run | exit | result |
|---|---|---|
| turn 1, `HOME` untouched | **1** | `EPERM ... mkdir '~/.cursor/projects/Users-robbartoszewski-yoloai-library-sandbox-...'`: loud, no hang |
| turn 1, `HOME` and `CURSOR_CONFIG_DIR` pointed into the sandbox's own `rw/home` | **0** | `NOTED`; `session_id` = **the rite-generated UUID**; the file it was asked to create is in the sandbox's copy and **not** in the original |
| turn 2, same UUID | **0** | **`LYNX47`**: continuity holds inside the Worker's sandbox |

The shape of each turn, with the key read from the file into the one process
that needs it:

    HOME=<sandbox>/rw/home CURSOR_CONFIG_DIR=<sandbox>/rw/home/.cursor-rite \
    CURSOR_API_KEY="$(cat <keydir>/cursor_api_key)" \
      agent -p --trust --force --resume <uuid> --output-format json < prompt

**Where the key was, afterwards:**

| place | key present? |
|---|---|
| `yoloai sandbox <name> terminal-snapshot` | no |
| `yoloai sandbox <name> log --raw` | no |
| any file under `~/.yoloai/library/sandboxes/<name>/` | no |

⚠ **Contrast with the route the plan costed (CW2).** That route delivers the
key with `--env`, as every Worker credential is delivered today (D-31). yoloAI
then puts it on its own argv and in the pane's launch line (the redaction in
`sandbox.redact_secrets` exists because of that). The file route puts it in
neither.

⚠ **The keychain advice appears here too.** The CLI printed *"The keychain
item is stuck. Delete it and sign in again: `security
delete-generic-password -s cursor-access-token ...`"* and carried on. It is
the same wrong advice CU1 section 7 found under rite's Manager profile.

### `worker-server` survives `stop` AND `destroy`

The turn left an `index.js worker-server` process with parent pid 1, whose
working directory was the sandbox's copy.

| step | `worker-server` |
|---|---|
| after the turns | alive |
| `yoloai stop` + 5 s | **alive** |
| `yoloai destroy --abandon-unapplied` + 5 s | **alive**, with its working directory deleted under it |

So **nothing in yoloAI ends it**. It was killed by hand.

**It does end itself, after about five minutes idle.** Measured separately,
outside any sandbox. One turn, then its `worker-server` was left untouched and
polled every 10 s. It exited on its own at **300 s** (n=1). Whether it still carries the seatbelt profile is inferred,
not measured: it descends from the sandboxed process.

### The chat record carries its creation time

`$CURSOR_CONFIG_DIR/chats/<md5 of the workspace path>/<uuid>/meta.json`
(the md5 was re-derived and matches):

    {"schemaVersion":1,"createdAtMs":1790539800070,"hasConversation":true,
     "updatedAtMs":1790539826028,"cwd":"<workspace>"}

`createdAtMs` is what lets CU3 say, **after** a turn, whether it continued the
chat rite meant or one Cursor created in its place (see the plan's CU3).

**Not measured:**
- the Docker backend, whose images have no `agent` binary;
- the pane: `idle` shows yoloAI's idle process, not Cursor, so `rite sandbox pane` sees nothing of the turn;
- a turn longer than a minute;
- Linux.

---

## 3. Is Claude's allowance the throughput limit? Partly: it binds when projects share the account

**Method.** Every Claude Code transcript under `~/.claude/projects`: 3,951
files, June to 27 September 2026. Entries were deduplicated by `uuid`, and
tokens by `message.id`; times are Europe/Warsaw. **A limit hit is counted
only from entries the client itself marks** (`isApiErrorMessage: true`,
`error: "rate_limit"`), never from text. A conversation discussing rate
limits therefore cannot count. The per-Manager Claude directories under
`~/Library/Application Support/rite/managers/` hold probes and tests only;
none has a limit event. The scripts are stdlib Python, read-only.

**Limit events, all time in these transcripts:**

| kind (the client's own words, reset time elided) | count |
|---|---|
| "You've hit your monthly spend limit" | 62 |
| "You've hit your session limit" (the 5-hour window) | 37 |
| "You're out of usage credits" | 34 |
| "You've hit your weekly limit" | 16 |

The session-limit hits are all before September. September's 33 are all
weekly (`rateLimitType: seven_day`, overage rejected): 1 on the 9th, 15 on
the 13th, 5 on the 20th and 12 on the 21st.

**Weeks, measured from the weekly reset (Wednesday 18:00):**

| week from | output tokens | rite / PapugaAI share | cap ran out |
|---|---|---|---|
| 26 Aug | 13.8M | 0 / 100% | no |
| 2 Sep | 17.2M | 0 / 100% | after 6.5 days |
| 9 Sep | 15.9M | 43 / 57% | **after 3.75 days** |
| 16 Sep | 12.2M | 60 / 36% | **after 3.96 days** |
| 23 Sep (to 27 Sep) | 6.6M | 91 / 1% | not after 4.2 days |

**The wall clock it cost.** After the cap ran out on 14 September and on
21 September, activity stopped. It resumed **at the reset minute**, 18:00 on
16 and 23 September, about 64 and 50 hours later. Resuming on the minute is
the strongest single sign that the work was waiting on the allowance. Over
the three weeks, that is about a quarter of rite's usual active hours.

**When the cap is not binding, the human is.** Across 71 rite sessions,
**71%** of the gaps between events are an assistant that reported and
stopped, waiting for the person. Only 7 of 889 such waits ended on a
question. Most of those hours are in waits of 1 to 12 hours. The model and
its tools account for about 17%.

**What this means for the case for Cursor:**
- **It holds, with a condition.** A second allowance recovers the days lost
  when the weekly cap runs out. That happened when rite shared the account
  with another project. rite alone has not yet run a week out.
- **It does not touch the larger cost.** That is the person's turnaround, and
  more output may lengthen it.
- **Cursor's own allowance is unmeasured for real work.** CU1's 11 small turns
  used about 1% of Pro. A Worker on a real ticket is CU6's to measure, and if
  Pro does not cover it, the second allowance is on-demand billing (CUQ4).

**What this cannot show:**
- whether output tokens track how the allowance is metered (the model mix changed);
- how work resumed after the short stops;
- anything about rite's own Workers or scheduler: `.rite/scheduler-last-tick` in the main checkout reads 12 September, and the work in these transcripts was interactive sessions, not rite-orchestrated;
- CI latency and merge contention, which the transcripts do not record.

---

## 4. Who can read a key in the engine's environment: everyone on the machine

**Measured 2026-09-28**, macOS 26 (Darwin 25.2). The key was a **fake**,
`FAKEKEY-CU4-PROBE-7f3a`; the real key was not used or read. The project
fixture was under `$HOME`, not a granted temporary directory. The profiles
were rite's real Manager profiles, from this tree at `a75469d`.

**The target** was launched exactly as CU4's merged route launches the engine:
`CURSOR_API_KEY="$(cat <copy>)" exec sandbox-exec -f <lead's profile> <engine>`.
It is read with `sysctl(KERN_PROCARGS2)`, which returns a process's argv and
environment, from a small Python reader.

⚠ **The engine binary matters, and the first stand-in was wrong.** macOS
hides the environment of Apple platform binaries: `/bin/sleep` as the target
read `False` from everywhere, and so did the control. With **Cursor's own
bundled `node`** (the process `agent` actually runs), the controls pass and
the key is visible:

| reader | result |
|---|---|
| control: a child with a known variable, read from outside | visible |
| control: a process reading its parent's variable | visible |
| from outside any sandbox | **visible** |
| from inside the **same** Manager's sandbox | **visible** |
| from inside a **sibling** Manager's sandbox (`helper`) | **visible** |
| from inside a **yoloAI Worker** sandbox (seatbelt, `idle`, boundary confirmed by a refused write outside) | **visible** |
| a child of the engine (what every command a Manager runs is) | **inherits it** |
| `ps eww` | shows nothing, even for the unsandboxed control: not a route on this macOS, and so not evidence either way |
| `/bin/ps` inside a Manager's profile | refused at exec; irrelevant, since the sysctl works from Python |
| tmux's session and global environment | absent |
| any process's argv, sampled every 50 ms through the launch | absent (0 hits) |

⚠ **One unexplained observation.** The first probe's argv check reported the
fake key present on some process's argv once. It did not reproduce: sampling
every 50 ms under the same launch found nothing. It is recorded as
unexplained, not as refuted.

**What this means for CU4 as merged (#41).** The key is kept off argv, out of
the pane's environment and out of logs, and the copy is unreadable from
inside any profile. **But once it is in the engine's environment, the
Manager, every sibling Manager and every Worker on the machine can read it.**
That fails the property the work is held to. It is not a window to narrow;
it goes to Robert.

**Wider than Cursor, inferred and not measured:** every credential a Worker
receives by `--env` today sits in a process environment, so the same call
should reach it from any sandbox.

Not measured: Linux (`/proc/<pid>/environ`; the VMs were suspended), and
whether Cursor strips the key from the commands it runs (that needs a working
key).

