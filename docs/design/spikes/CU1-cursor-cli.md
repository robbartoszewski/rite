# CU1 — does Cursor's CLI do what its docs say?

**Measured 2026-09-27** on macOS (arm64), Cursor CLI `2026.09.26-dd393fe`,
account on the **Pro** tier (`agent about`), model `auto`. The desktop app was
also installed; one test ran with it quit. rite's Manager profile came from
`origin/main` at `15312e3`.

**Citation convention:** a bare `§` is a section of `SPEC.md`; this note's own
sections are written out.

**The three answers that could have ended the track are all acceptable.** One
of them removes the design the plan was written around: rite does not need to
ask Cursor for a handle at all.

---

## 1. The binary

`agent`, and `cursor-agent` as a legacy name, both symlinks in `~/.local/bin`
to `~/.local/share/cursor-agent/versions/<version>/cursor-agent`. The CLI
installs no `cursor` command, and does not need the desktop app (section 4).

---

## 2. The prompt arrives on stdin — YES

`agent -p --trust --output-format json < prompt.txt`, no prompt argument:
answered correctly, exit 0, 6.8 s. `ps` sampled during the run never showed the
prompt text. So Cursor fits `launch_command`'s existing stdin redirect, and
CUQ3 does not arise.

`--output-format json` prints one object with `result`, `session_id` and
`usage` (`inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`).

---

## 3. The handle: rite can choose it, and `create-chat` should not be called

**`create-chat` never contacts Cursor.** It printed a UUID and exited 0 in every
case:

| condition | exit | stdout |
|---|---|---|
| normal | 0 | a UUID |
| outbound network denied (`sandbox-exec`) | 0 | a UUID |
| `CURSOR_API_ENDPOINT=https://127.0.0.1:9` | 0 | a UUID |
| `--api-key bogus` | 0 | a UUID |
| `CURSOR_API_KEY=bogus` | 0 | a UUID |
| fresh, empty `CURSOR_CONFIG_DIR` | **did not exit** (20 s+ twice, ~5 min once) | a UUID, printed before the hang |

It writes nothing: a minted id that no turn uses appears nowhere on disk. So its
success says nothing about the account, the service or the conversation, and it
can hang after printing. ⚠ **A mint call that sometimes returns and sometimes
hangs depends on timing, and is not used.**

**An id rite generates works the same way.** Two-turn token test, in the
commands rite would build:

| handle | turn 1 | turn 2 asked for the token | result |
|---|---|---|---|
| from `create-chat` | `-p --resume <id>`, told `MARMOT77` | `-p --resume <id>` | **`MARMOT77`** |
| `uuid4()` from rite | `-p --resume <id>`, told `OCELOT31` | `-p --resume <id>` | **`OCELOT31`** |
| `uuid4()`, desktop app quit | told `HERON52` | same | **`HERON52`** |
| `abc$(touch PWNED)` | — | — | exit **1**, *"Persistent-session chat ID must be a UUID"*, nothing created |

**So Cursor is not a third handle direction.** It is Goose's: rite chooses the
handle and says it on every launch, the first included. The constraint is that
it must be a UUID. What the plan costed for "the engine mints up front" (a
second engine invocation, parsing its stdout, the supervisor holding Cursor's
credential, a mint-then-record partial failure) does not arise.

### ⚠ What does arise: an unknown handle starts fresh, silently

`-p --resume <a UUID Cursor has never seen>` exits **0**, reports
`"subtype":"success"`, and runs in a **new, empty** chat (asked for the token,
it answered `NONE`). That is the defect `_default_resume_id` documents,
arriving by a third road: a continuation that did not continue, identical from
outside to one that did.

Where the chat is, measured: `$CURSOR_CONFIG_DIR/chats/<md5 of the workspace
path>/<chat id>/` holding `meta.json` and `store.db` (SQLite). The directory
exists when the turn's process exits. **Before a continuation cycle, rite must
check that directory and refuse if it is absent**, never pass the id through.
The layout is undocumented, so the refusal must name the path it looked for.

---

## 4. The credential, and the desktop app

**The CLI does not use the app's login.** The app's keychain item is
`Cursor Safe Storage` (created when the app first ran); `agent login` created
its own, `cursor-access-token` and `cursor-refresh-token`, both in the login
keychain. With the app quit (no Cursor.app process), headless turns ran and
resumed (the `HERON52` row above).

**Neither login works inside rite's Manager profile.**

| profile | exit | what it said |
|---|---|---|
| rite's, unchanged | **126** | `agent: Operation not permitted` (the binary is not granted) |
| plus read of `~/.local/share/cursor-agent`, read/write of `~/.cursor` | **1** | *"Authentication required. Please run 'agent login' first, or set CURSOR_API_KEY environment variable."* |

Loud in both cases. This is Claude's keychain finding again (`claude_login.py`),
so **the API key is the route (CUQ2, answered by measurement)**. A stored login
also depends on a refresh token rite does not control.

**`CURSOR_CONFIG_DIR` relocates config and chats.** Pointed at an empty
directory, a turn wrote `cli-config.json` and `chats/` there. So a per-Manager
directory works as `CLAUDE_CONFIG_DIR` does, and keeps a Manager out of
`~/.cursor`, which the app and the CLI share (the app keeps `extensions/` and
`projects/` entries there, and its main state in
`~/Library/Application Support/Cursor`).

**Not measured:** an authenticated turn inside the profile, which needs an API
key; whether an API key expires.

---

## 5. Unattended operation

| condition | exit | behaviour |
|---|---|---|
| untrusted directory, no `--trust` | **1** | *"Workspace Trust Required ... Pass --trust, --yolo, or -f"*. No hang |
| `--trust`, asked to run `touch` | 0 | the shell command was **refused**; the agent created the file with its Write tool instead, and said so only in prose |
| `--trust --force`, same | 0 | ran `touch` |

So refusals are per command (`per_command_refusals` is plausibly True). A
refusal still ends in exit 0 `success`, consistent with R7. Where a refusal is
recorded is not measured (`store.db` not read).

⚠ **Every `-p` run leaves a detached `index.js worker-server` process** for its
workspace, parent pid 1, still alive minutes after the turn exited. It outlives
the pane, so the adapter must end it when the Manager stops.

---

## 6. Cost: what these runs used, and what they do not show

**Measured:** **11** headless turns that reached the model, totalling about
**143k tokens** by the CLI's own `usage` (46k uncached input, 96k cache reads,
under 1k output). Ten were short one-line prompts of about 9–10k tokens each;
one was a tool-using turn of about 38.6k. Four more runs failed before any
model request (untrusted directory, non-UUID id, and both sandbox profiles).
Together they used **about 1% of the Pro allowance**, per the account's
dashboard, and nothing billed separately.

⚠ The 1% is the dashboard's figure for the account, so it also includes
anything else used that day; it is an upper bound for these runs. And the
dashboard's token count is not the CLI's: a Cursor staff reply on their forum
says billing counts `inputTokens + cacheReadTokens + cacheWriteTokens`, where
the CLI's `inputTokens` is uncached only.

⚠ **That is a fact about these runs, not about a Manager.** They were
single-shot, tiny-context and heavily cached. It does not license "a Manager
is cheap", and a naive "11 turns is 1%, so about 1,100 turns a month" is a
stand-in, not the property. (Reported in conversation first as "about 22
turns"; the run log says 11.)

**What CU6 must observe, from one real cycle, before anyone builds on cost:**

1. **Turns per Manager cycle.** One `-p` invocation is one cycle for rite, but
   Cursor's agent loop runs many model requests inside it. Count requests per
   cycle from the dashboard, and from `usage` in the JSON output.
2. **Context growth.** Tokens per cycle on cycle 1 and on cycle N of one
   conversation, split into uncached and cache reads, to see whether a resumed
   conversation re-bills its history and how caching holds up as it grows.
3. **The allowance per day.** Percentage of the Pro allowance per cycle, times
   a realistic number of cycles per day, against a month. That answers whether
   Pro covers a Manager run daily, or whether Cursor is in practice an
   on-demand-billing engine.
4. **Exhaustion.** What the CLI does when the allowance runs out with on-demand
   off: exit code, output, and whether it hangs. Not provokable cheaply, so
   recorded as not measured until it happens.

---

## Not measured

- An authenticated turn inside the Manager profile (needs an API key; CU4).
- Exit codes for a missing model and an unreachable service during a turn.
- Cursor's own `--sandbox enabled` nested inside rite's profile.
- `agent --resume <id>` interactively (R4).
- Which hosts it contacts (EG0). `--help` names `https://api2.cursor.sh` as the
  default endpoint.
- Linux.
