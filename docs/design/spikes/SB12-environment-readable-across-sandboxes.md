# SB12 — a process's environment is readable from any sandbox on the machine

**Measured 2026-09-28** on macOS 26 (Darwin 25.2), yoloAI 0.11.0 on the
`seatbelt` backend, rite's Manager profile from `main` at `a75469d`. **Fake
credentials only**; no real credential was used, read or placed anywhere.
Every sandbox was confirmed active by a write outside it being refused.

**Why this exists.** CU1b section 4 found that a Cursor key in a Manager's
engine environment is readable from other sandboxes. That raised a question
that is not about Cursor at all: rite already hands every Worker every
project credential by yoloAI's `--env` (§5.3.4, D-31). Does the same read
reach those, on shipped v0.6.0?

**The read.** `sysctl(KERN_PROCARGS2, pid)` returns a process's argv and
environment. A 20-line Python reader, run inside each sandbox, reports only
whether a needle is present, and whether it is in argv or in the environment.
It never prints a value. It works on any process of the same user that is
not an Apple platform binary: macOS hides the environment of those
(`/bin/sleep` read as absent even from outside, which is why the first Cursor
stand-in misled).

---

## 1. Worker credentials delivered by `--env`: readable from other Workers and from Managers

**Worker A** was created in rite's shape, one `--env` per credential:
`yoloai new --backend seatbelt --agent idle --env GITHUB_TOKEN=<fake> --env JIRA_API_TOKEN=<fake> wa <dir>:copy-all`.
`idle`, not `claude`, so no real Claude login could be drawn in. yoloAI's
delivery of `--env` is the same either way; with `claude`, the agent runs
under the same tmux, so it holds them too (inferred). **Worker B** was a
second sandbox with no credentials, used as a reader.

| where the fakes ended up | from Worker B's sandbox | from a rite Manager's sandbox |
|---|---|---|
| **environment** of Worker A's yoloAI `tmux` server (on the sandbox's socket) | **readable** | **readable (in env, not argv)** |
| **environment** of `tmux wait-for yoloai-exit` in Worker A | **readable** | **readable** |
| **environment** of a Python process under the same parent in Worker A | **readable** | **readable** |
| files in Worker A's sandbox directory: `ro/secrets/GITHUB_TOKEN`, `ro/secrets/JIRA_API_TOKEN`, `rw/logs/agent.log`, `rw/logs/sandbox.jsonl`, `rw/home/.zsh_history` | refused | refused |
| `yoloai new`'s **argv**, during creation (D-31, known) | — | readable by every local account via `ps` |

Worker B's reader did not separate argv from environment. The Manager's did,
and it reported "in env" for the same processes.

**So, on shipped v0.6.0:** while a Worker runs, every credential rite gave it
(the project's GitHub token, JIRA, Slack, and, by yoloAI's own route, the
Claude login) is readable by **every other Worker and every sandboxed
Manager** on the machine. The files are protected; the process environment
is not. Unsandboxed processes of the same user could always read it; the
point is that the sandboxes do not stop it.

---

## 2. Can a rite Manager profile stop the read? Not by anything tried

Each variant was appended to, or edited into, a copy of the Manager's real
profile. The target was Worker A's tmux server. Controls in every variant:
Python starts, and a write inside the project succeeds.

| variant | read of Worker A's environment |
|---|---|
| unmodified (control) | readable |
| a. `(deny sysctl-read (sysctl-name "kern.procargs2"))`, last | readable |
| b. `(deny process-info* (target others))` | readable |
| a + b | readable |
| d. `(deny sysctl-read (sysctl-name-prefix "kern.proc"))` | readable |
| e. `(deny process-info*)` | readable |
| c. the blanket `(allow sysctl-read)` removed | not testable: `os.uname()` and `os.cpu_count()` fail inside, so engines would break too |
| **f. explicit sysctl allowlist (`hw.*`, `machdep.*`, `kern.os*`, `kern.version`, `kern.hostname`, `kern.argmax`), `kern.procargs2` not in it** | **readable**. Controls pass: Python and `uname`, Cursor's `node`, `git`, rite |

**Reading:** seatbelt does not appear to mediate `KERN_PROCARGS2` through
`sysctl-read` names or `process-info`. That is not exhaustive: an
undocumented operation may exist. But no profile change rite could make was
found to stop the read.

⚠ **And a Worker's profile is yoloAI's, not rite's**, so even a working deny
in rite's Manager profile would leave Worker-to-Worker reads open.

---

## 3. One observation from CU1b section 4, now explained

The first Cursor probe once reported the fake key on a process's argv, and
50 ms sampling did not reproduce it. **Explained and measured:** the shell
that runs each of the measuring session's tool commands receives the whole
command text on its argv (`/bin/zsh -c source … && eval '<command>'`). A
marker in a command's text was found on that shell's argv. The first probe
wrote its script, fake key included, with a heredoc in the same command, so
the fake sat on that shell's argv for as long as the command ran. The later
sampler excluded exactly that line by name. It was the measuring session's
own shell, not rite's route.

⚠ **The lesson for real secrets:** anything written into a tool command's
text, heredocs included, is on argv. Scripts that hold even a fake secret
are now written in one command and run from another.

---

## 4. A process owned by a DIFFERENT user: the kernel refuses the read

**Measured 2026-09-28**, same machine and method. The reader now reports the
argument and environment counts, or the kernel's refusal with its errno, so
"refused" cannot be confused with "readable but empty". No fake credential is
needed here: the question is whether the read succeeds at all.

**Targets:** three processes owned by **root** that are NOT Apple platform
binaries, so their environment is not hidden for that reason: Docker's
`com.docker.vmnetd` and two Parallels services (`prl_disp_service`,
`prl_naptd`). **Positive control:** Cursor's `node`, run as the operator.

| target | from outside (unsandboxed) | from a rite Manager's sandbox | from a yoloAI Worker's sandbox |
|---|---|---|---|
| operator's own `node` (control) | readable, 63 environment entries | readable, 63 | readable, 62 |
| root, `com.docker.vmnetd` | **refused, errno 22** | **refused, errno 22** | **refused, errno 22** |
| root, `prl_disp_service` | **refused, errno 22** | **refused, errno 22** | **refused, errno 22** |
| root, `prl_naptd` | **refused, errno 22** | **refused, errno 22** | **refused, errno 22** |

Both sandboxes were confirmed active by a refused write outside them.

**What this shows:** for a caller that is not root, the kernel refuses
`KERN_PROCARGS2` on a process of another user, even with no sandbox at all.
So separating agents by OS user closes the read. Of everything tried, it is
the only thing that does.

**What it does NOT show, stated so it is not over-read:**
- The targets were **root**, not a second ordinary user. That the same
  refusal applies between two ordinary users is expected from the check being
  a uid comparison, but it is **inferred, not measured**. Measuring it needs a
  second macOS account, which is an administrator's action, and Robert's.
- **One user per agent.** Agents that share one OS user can still read each
  other. So "agents as a different user" protects the operator's own
  processes, but Worker-from-Worker needs a user per Worker (or per project).
- **How rite would run an agent as another user**, and what breaks. yoloAI's
  seatbelt backend runs as the invoking user. Running as another needs
  privilege (sudo rules or a launchd job), file ownership for the working
  copy and the credential files, and a git and gh identity per user. None of
  it is designed or measured.
- **Linux**, where the equivalent is `/proc/<pid>/environ` and is also
  uid-checked (inferred).

## Not measured

- Linux (`/proc/<pid>/environ`, and whether Landlock's policy governs it);
  the VMs were suspended.
- A real `--agent claude` Worker (see section 1).
- The Docker backend, where Workers are in containers, not same-user
  processes.
