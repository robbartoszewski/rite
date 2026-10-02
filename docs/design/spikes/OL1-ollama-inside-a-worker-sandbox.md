# OL1 — can a Worker sandbox reach Ollama, and run the harness there?

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

**Measured 2026-10-02. yoloAI 0.11.0 (seatbelt), Goose 1.51.0, Ollama 0.34.2,
macOS 26.2, model `rite-ctx32768-qwen3.8-latest:latest` (the window rite pins).**

This is step 1 of the v0.7.0 Ollama track. The local tier has only ever run
on the HOST; a rendered Worker *profile* was known to reach the daemon, but
no process had been measured running the harness from inside a real Worker
sandbox. Everything below is a measurement; where a thing was not measured it
says so.

---

## 0. The answer

⚠ **The network reaches. The blocker was the filesystem, and it is one
environment variable.**

| question | result |
|---|---|
| TCP to `localhost:11434` from inside | **HTTP 200**, 23 models listed |
| TCP to `127.0.0.1:11434` from inside | **HTTP 200**, body read |
| `goose` / `uv` / `python3` / `rite` on PATH inside | **all four present** |
| `goose run` with a tool loop, against Ollama, inside | ⚠ **panics** — then **works** with `GOOSE_PATH_ROOT` |
| rite's own `engine_probe` inside | **reachable, model present, agent installed, window 32768** |

**Nothing in the seatbelt profile stands between a Worker and the Ollama
daemon.** The loopback grant is there, and the daemon on the host answers a
sandboxed client exactly as it answers the host.

## 1. Method, and why it is a real Worker

rite does not render the Worker's seatbelt profile — yoloAI does
(`runtime/seatbelt/profile.go`). A Worker therefore IS
`yoloai new --backend seatbelt`, launched with the clean home
`sandbox.worker_home()` and `--data-dir`, which is what
`sandbox/__init__.py` builds. The probe reproduced that launch and changed
only `--agent`: `idle` instead of `claude`, so no Claude session was spent on
a question about the network.

**Control, so "it worked" is not "it was never sandboxed".** From inside:

```
cat /Users/<operator>/.ssh/id_ed25519   ->  Operation not permitted
```

The sandbox was enforcing while every probe below returned 200.

## 2. The blocker, exactly

A first `goose run` inside died before it reached the model:

```
Warning: Failed to initialize logging: Failed to create log directory:
  ".../.local/state/goose/logs/cli/2026-10-02"
thread 'goose-cli-main' panicked at session/session_manager.rs:935:22:
Failed to secure session database directory:
  Os { code: 1, kind: PermissionDenied, message: "Operation not permitted" }
```

**`~/.local` is granted READ and not WRITE inside a Worker.** Measured, per
directory:

| path | inside a Worker |
|---|---|
| `$HOME/.local/state`, `$HOME/.local/share`, `$HOME/.config/goose` | **denied** |
| `$TMPDIR` | writable |
| the workdir copy | writable |

That read grant is the one `worker_home()` already documents and depends on —
it is why `rite`'s Python can load at all. Goose needs to WRITE its config,
its session sqlite and its logs, and under the operator's home it cannot.

**The fix is `GOOSE_PATH_ROOT`**, a knob Goose carries (found in its own
strings, alongside `goose requires a home dir`). It relocates all four paths
together:

```
Config dir:            <root>/config
Config yaml:           <root>/config/config.yaml
Sessions DB (sqlite):  <root>/data/sessions/sessions.db
Logs dir:              <root>/state/logs
```

⚠ **CORRECTION, 2026-10-02, same day.** This note first said the variable
"also closes a known defect" — the Manager that declared `qwen3:8b` and ran
`qwen3-vl:8b-instruct` from the operator's global
`~/.config/goose/config.yaml`. **That reading was wrong twice over, and
acting on it would have broken working Managers.**

- The model-mismatch defect is **already closed** by `GOOSE_MODEL` and
  `GOOSE_PROVIDER`, which win over `config.yaml`. A path root removes the
  fallback's *source* as well; it is not what fixed it.
- **A Manager must not be given one.** `enclosure.ENGINE_HOME_IS_THE_OPERATORS`
  records a measurement: a local Manager's profile grants `~/.config/goose`
  and `~/.local/share/goose` by exact path instead of redirecting `HOME`,
  because redirecting `HOME` took Claude Code's login away from every Claude
  Manager. That same store **holds the conversation handle each resumed cycle
  names**. Moving the root would hand a running Manager an empty session store
  and lose the conversation it is part-way through.

So `GOOSE_PATH_ROOT` is a **Worker-only** value: required inside a sandbox,
deliberately absent on the host. Removing the fallback source is a genuine
second benefit, and it is worth having inside a Worker — it is not worth a
Manager's conversation.

## 3. The turn that worked

With `GOOSE_PATH_ROOT` set to a writable path, and the environment
`goose_environment()` already builds (`GOOSE_PROVIDER=ollama`,
`GOOSE_MODEL`, `OLLAMA_HOST`, `GOOSE_CONTEXT_LIMIT=32768`,
`GOOSE_MODE=auto`), inside the sandbox:

- Goose opened a session against `ollama rite-ctx32768-qwen3.8-latest:latest`;
- it called `write` (`PROOF.txt`), then `shell` (`cat PROOF.txt`) to check
  itself — **the tool loop ran, not just the completion**;
- the file existed on disk afterwards with exactly the requested line;
- **71 seconds**, 20:02:39 → 20:03:50.

The instruction file was created with `mktemp` OUTSIDE the workspace, as
`GooseAgent.run` does, and Goose read it — so that detail of the adapter
needs no change for the sandbox.

**rite's own preflight passes inside too.** `engine_probe._probe` against
`http://localhost:11434`:

```
reachable        = True        detail          = 'answering, 23 model(s)'
model_present    = True        agent_installed = True
context_window   = 32768       context_detail  = ''
```

So `GooseAgent._preflight` would not block a turn inside a Worker, and the
window rite pinned is read back correctly from inside.

## 4. What this did NOT measure

- **A full `rite local step` inside a Worker.** The decisive primitives were
  measured separately — loopback reach, the Goose tool loop, and rite's
  preflight — but `run_subtask` end to end inside a sandbox needs a project
  and an APPROVED plan in there, which is the automation work itself.
- ~~Where `GOOSE_PATH_ROOT` should point.~~ **Measured afterwards, same day —
  see section 5.**
- **Concurrency.** One sandbox, one model. Two Ollama Workers against one
  32 GB daemon is a sizing question this says nothing about.
- **Whether the daemon should be reachable at all.** This note measures that
  it IS. A Worker that can reach `localhost` can reach any other loopback
  service on the machine, which is a property of the yoloAI profile and not
  something rite grants or can revoke here.

---

## 5. Where a Worker's path root points · measured after section 4

The first probe used `/tmp/goose-root` only because it is writable. **That is
the wrong place, for a measured reason:** the temp root is granted to every
sandbox on this machine, so one Worker's Goose session store — its
conversation — would be readable by another. That is the leak
`enclosure.engine_tmp` already measured for a Manager, arriving by a second
road.

Candidates, probed from inside `ol2-w1`:

| candidate | inside a Worker |
|---|---|
| `<sandbox>/rw/rite`, `<sandbox>/rw/goose` | **writable** |
| `$HOME/.rite-goose`, `$HOME/.cache/rite` | denied |
| `$TMPDIR/rite-goose`, `/tmp/rite-goose` | writable — **and shared, so rejected** |
| the workspace | writable — **rejected**, see below |

**The sandbox's own state directory is the answer**, and it was measured
isolated rather than assumed. From `ol2-w1`, against `ol2-w2`:

```
list w2's rw layer    ->  the directory name is visible
write into it         ->  denied
read w2's work/       ->  Operation not permitted
```

So a root under the sandbox's own layer is per-Worker by construction, stable
across `exec` calls, destroyed with the sandbox, and outside the repo — which
the workspace is not: Goose state written there would appear in `yoloai diff`
and in the tree the committer inspects, the same objection `GooseAgent.run`
already makes about where it puts its instruction file.

⚠ **It does couple rite to yoloAI's on-disk layout**, and there is a better
accessor than the one this note first named. `yoloai files <name> path` prints
the host path of the sandbox's exchange directory — a documented command whose
whole purpose is to hand a caller a path into the sandbox — so the layer is
simply its parent:

```
yoloai files ol5-w1 path   ->  .../library/sandboxes/ol5-w1/rw/files
layer                      ->  .../library/sandboxes/ol5-w1/rw
```

The first version derived the same place from `sandbox info --json`'s
`config_path` (`<sandbox>/ro/runtime-config.json`, so grandparent plus `rw`).
That works and is **incidental**: the field exists to name a config file, not
to describe the layout. `sandbox.sandbox_state_dir` uses the documented one, in
one function, so a layout change is a one-line fix rather than a hunt.

rite's root goes under `<layer>/rite/goose` rather than `<layer>/goose`, so a
future yoloAI directory of that name cannot collide with it.

---

## 6. End to end, with rite driving · measured after section 5

The whole path, exercised through rite's own objects rather than a shell
approximation of them: `GooseAgent` with `launch=exec_launcher(sandbox)` and
`GOOSE_PATH_ROOT` from `goose_path_root(sandbox)`, given a real `Context` and
`Subtask`.

```
GOOSE_PATH_ROOT = .../sandboxes/ol5-w1/rw/rite/goose
elapsed 86s   claimed_success: True   infrastructure_fault: False
```

Verified on disk rather than taken from the report, which is RL-7's whole
point:

- `E2E.txt` exists inside the sandbox with exactly the requested line;
- the root holds `config/`, `data/` and `state/` — the four paths relocated;
- ⚠ **`yoloai diff` shows only `E2E.txt`.** Goose's state did not reach the
  workspace, which is the requirement that ruled the workspace out as a
  location in the first place.

Two details of the launcher that the measurement settled:

- **`yoloai exec` eats the command's own flags.** `yoloai exec <box> curl -s …`
  fails with *"unknown shorthand flag: 's' in -s"*, and so does `sh -c`. The
  `--` separator is not cosmetic.
- **A host tempfile IS readable from inside** (`cat` of a `mktemp` file written
  on the host returned its contents), so `GooseAgent.run`'s existing
  instruction file needs no change to work in a sandbox. ⚠ It does sit in the
  shared temp root, so the task text — not a credential, but project content —
  is readable by every other sandbox on the machine. `yoloai files put` is the
  route that avoids it; left as a follow-up rather than done quietly here.
