# B4d — does `GOOSE_MODE=auto` bypass approval, and does Goose run sandboxed?

**Measured 2026-09-24.** Goose 1.51.0, yoloAI seatbelt backend, Ollama
0.34.2, `qwen3:8b` served at a 32768-token window.

**Citation convention:** a bare `§` is a section of `SPEC.md`; this note's own
sections are written out.

**Both answers are yes.** One carries a setting that must be applied or Goose
does not start at all.

---

## 1. `GOOSE_MODE=auto` runs without approval — YES

Goose's docs contradict themselves. The permissions page says autonomous mode
lets it *"modify files, use extensions, and delete files without requiring
approval"*; the headless page says operations requiring approval *"will
either use default permissions or **fail**"*. **"Or fail" was the whole
question.**

Measured by asking for a deletion — the operation the permissions page names
— headless, with stdin closed:

| `GOOSE_MODE` | exit | seconds | file deleted | hung waiting for input |
|---|---|---|---|---|
| **`auto`** | **0** | 43 | **YES** — ran `rm victim.txt` | no |
| `approve` | **1** | 19 | no | **no** |

**`auto` is a bypass equivalent.** It executed a shell command that deletes a
file, unattended, with no prompt.

⚠ **And the "or fail" branch is better than feared.** Under `approve` in a
headless run Goose **fails fast and non-zero** rather than hanging. That
matters more than it looks: defect class 15 is *"a prompt is not an
exception, it is the absence of an answer"*, and a headless agent that
blocked on an approval nobody can give would be exactly that. It does not.

⚠ **Note the exit codes.** `approve` returned **1**. So Goose *can* exit
non-zero — which does **not** soften B4a's finding that it returns **0** for
a missing model and **0** for an unreachable provider. An exit code that is
sometimes meaningful is worse than one that never is, because it invites
being trusted.

---

## 2. Goose runs inside a yoloAI seatbelt sandbox — YES, with one setting

⚠ **Out of the box it does not run at all. It panics before doing anything:**

```
thread 'goose-cli-main' panicked at tracing-appender-0.2.5/src/rolling.rs:156:
initializing rolling file appender failed: InitError {
  context: "failed to create log file",
  source: Os { code: 1, kind: PermissionDenied, message: "Operation not permitted" } }
Error: goose-cli main thread panicked
```

**Goose creates a rolling log file at startup and panics when it cannot.**
Inside the sandbox `HOME` is still the host's `/Users/<user>`, whose Goose
log directory seatbelt denies. Not a degraded run — no run.

**With `HOME` pointed at a writable path inside the sandbox it works:**

```
HOME=$PWD/.goosehome  goose --version      ->  1.51.0
HOME=$PWD/.goosehome  GOOSE_MODE=auto  goose run -n ... -i instr.txt
  exit 0
  "The file `victim.txt` has been successfully deleted."
  after: victim.txt DELETED — it acted without approval
```

**So the design Robert asked about holds:** the sandbox is the boundary, the
agent runs unconstrained inside it, and `GOOSE_MODE=auto` is the setting that
makes it unconstrained.

### What else was established inside the sandbox

| | result |
|---|---|
| Goose binary reachable | yes — `/opt/homebrew/bin/goose`, seatbelt shares host reads and exec |
| Ollama endpoint from inside | **HTTP 200** — confirms RL-T1 for this case |
| write in its own workspace | yes; the workspace is a **copy** under `~/.yoloai/library/sandboxes/<name>/rw/work/…` |
| write to the host repo | **blocked** — no file appeared on the host |
| yoloAI knows `goose` as an agent | ⚠ **no** — its agents are aider, claude, codex, gemini, idle, opencode, shell, test |

⚠ **A correction I made to my own measurement.** A first probe wrote
successfully to the host path the workdir was copied from, and I nearly
reported that as an isolation failure. It is not: that path was under
`/private/tmp`, which the seatbelt profile allows. Writing to the rite
checkout was **blocked** and no file appeared. **The scoped test is the one
that means anything.**

⚠ **Goose also degraded gracefully on a second denial** —
`Failed to write /dev/stdout: Operation not permitted` — and carried on. So
the startup panic is specific to the log file, not to denial in general,
which is why one env var fixes it.

---

## 3. ⚠ What this buys, and what it does not

**The distinction that decides how much this is worth:**

- **Workers already run sandboxed.** For the Worker case this closes cleanly:
  the boundary exists, Goose runs inside it, and `auto` makes the agent
  unconstrained within it. The boundary does not care which engine it
  contains, which is the property a per-agent allowlist can never have.
- ⚠ **Managers do NOT run sandboxed.** A Manager runs unsandboxed in a tmux
  pane on the user's machine. So a local **Manager** gains nothing from this:
  `GOOSE_MODE=auto` there is an unconstrained agent on the host with the
  user's own file and network access.

**Closing the local-Manager case means sandboxing Managers**, which is a
substantially bigger change than a permission setting and is not in this
release. Stated so the answer is not read as broader than it is.

### If this is adopted, three things follow

1. **`HOME` must be set for a sandboxed Goose**, or it panics at startup with
   a message naming `tracing-appender` and nothing about configuration.
   Whatever wires this needs to own that, and `rite doctor` should probably
   say so rather than leaving the panic to be decoded.
2. **yoloAI has no `goose` agent**, so a sandboxed Goose runs under
   `--agent shell` / `idle` with rite driving it, or yoloAI gains one.
3. **Goose's config does not travel with a changed `HOME`.** Provider, model
   and endpoint have to arrive as environment variables —
   `GOOSE_PROVIDER`, `GOOSE_MODEL`, `OLLAMA_HOST` — which is how the
   measurement above was run and is what `goose_agent.py` already does.
