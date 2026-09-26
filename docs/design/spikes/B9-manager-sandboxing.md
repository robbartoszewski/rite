# B9 — can a Manager run inside a sandbox?

⚠ **UPDATED 2026-09-25: the broker shape below was BUILT, and one section
here is superseded.** `eb2a88e` added the broker and `4ebbbd7` applied the
profile, both observed end to end. **The `HOME` section is wrong as advice**
and is kept only as what was believed: redirecting `HOME` costs the Claude
login, so the profile grants the engine's own state paths and redirects
`TMPDIR` instead (see B4d's correction and `enclosure.py`,
`ENGINE_HOME_IS_THE_OPERATORS`). Two holes in the first built profile were
found the same day, the tmux server and unfiltered signals, and `9862b59`
closed both, measured before and after. What is still open (`~/.claude`
readable whole, other projects' transcripts included, and the unconfined
network) is in `../V070_RELEASE_PLAN.md`, part 0 and track SB. *Since:
`~/.claude` is not granted (`8a61989`, SB4 closed), and the network is
v0.8.0's egress work (`../V080_RELEASE_PLAN.md`, track EG).*

**Robert put Manager sandboxing into v0.6.0**, on an argument that is sound:
his permission decision — the allowlist as the secure default — holds for
Claude and cannot hold for Goose, because `GOOSE_MODE` is whole-session with
no per-command concept. The sandbox is the only boundary that works for both
and it is engine-independent. Workers already have it; Managers do not.

**Answer: not as specified.** A sandboxed Manager cannot start a sandboxed
Worker, by any route yoloAI offers today. Measured 2026-09-24, by running
rather than by recalling.

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

⚠ **`B9` is taken as the next free number in the B series** (B8 is closed,
nothing else claims it). If another session has claimed it meanwhile, this
note's content is what matters and the id can be changed.

---

## ⚠ FIRST, A CORRECTION — "seatbelt refuses to nest" is FALSE

**That sentence has been carried and cited in this project, including in the
brief that commissioned this spike.** It is wrong, and the true rule is
stranger and narrower.

`sandbox-exec` inside `sandbox-exec` works fine when both profiles are
permissive. What fails is applying a **different** profile inside a
restrictive one. Measured against a real yoloAI `(deny default)` Worker
profile as the outer sandbox:

| inner profile | result |
|---|---|
| exact copy of the outer | **works** |
| copy **+ a comment line** | **works** |
| copy **with two `allow` lines reordered** | **works** |
| copy **with one `allow` REMOVED** — strictly narrower | ⚠ `sandbox_apply: Operation not permitted`, exit 71 |
| copy + one extra `allow` — wider | `sandbox_apply: Operation not permitted` |
| a minimal `(deny default)` profile | `sandbox_apply: Operation not permitted` |
| `(allow default)` | `sandbox_apply: Operation not permitted` |
| **another real yoloAI sandbox's profile** | `sandbox_apply: Operation not permitted` |

⚠ **The row that makes this worth writing down is the fourth.** A strictly
NARROWER profile is refused. So this is not "a nested sandbox may not widen
its restrictions", which is the rule anyone would guess and which several of
us did. Comment and reordering changes pass, so it is not textual identity
either.

**The rule that fits every row: you may only re-apply a SEMANTICALLY
EQUIVALENT profile.** It behaves like an idempotent no-op — the same
compiled profile is accepted, anything else is refused, in either direction.

### The consequence, so nobody re-derives it

**Two real yoloAI Worker profiles differ in 38 lines**, because every sandbox
scopes its paths to its own id:

    < (allow file-read* file-write* (subpath ".../sandboxes/rite-claim-a-71978-be9133/rw"))
    > (allow file-read* file-write* (subpath ".../sandboxes/rite-claim-b-71978-f64b3c/rw"))

**So a Manager profile and a Worker profile can never be equivalent**, and
the equivalence rule is the only way in. Run directly: sandbox B's real
profile inside sandbox A's real profile → `Operation not permitted`.

**A seatbelt-sandboxed Manager structurally cannot start a seatbelt-sandboxed
Worker.** Not "hard" — the operation is refused by the kernel.

## The Docker route — checked, closed

Not assumed from the seatbelt result. Inside a Docker-backed yoloAI sandbox:

| tool | present? |
|---|---|
| `claude`, `git` | yes |
| `yoloai` | **no** |
| `rite` | **no** |
| `goose` | **no** |
| `sandbox-exec` | no (it is Linux) |

- **No `/var/run/docker.sock`**, so the `docker` CLI that *is* present cannot
  reach any daemon.
- yoloAI's documented Docker-in-Docker option, `--isolation
  container-privileged`, gives `Privileged=true` and **still mounts no
  socket**. Verified with `docker inspect`.

So a containerised Manager cannot start Workers either. And separately the
topology does not transfer: rite's Managers are macOS tmux sessions managing
a macOS project tree, and a Linux container has none of that.

## The MCP route — checked, closed, and this is the one rite's own design cites

`sandbox/__init__.py` records that the MCP path is *"a Manager session calls
those tools directly as its own MCP client, with no approval gate and no rite
code in between (§5.3.1)"*. That would put sandbox creation OUTSIDE the
sandbox, which is exactly what is needed.

**It does not work, for a mechanical reason:** `yoloai mcp serve` is
**stdio-only** — *"Start the yoloAI MCP server on stdin/stdout."* There is no
listen address. An MCP server spawned by a sandboxed Manager is a child
process **inside** the sandbox, so `sandbox_create` hits the same wall as
everything else.

**It could be bridged, and the bridge is the point.** Seatbelt is policy over
the real filesystem rather than a namespace, and `/tmp` is read+write in the
profile, so a FIFO could carry stdio across the boundary to a host-side
server. But whatever can write that FIFO can create sandboxes, and inside the
sandbox that is anything the Manager runs.

⚠ **So the bridge must VALIDATE, not relay. A FIFO bridge that relays rather
than validates is a privilege escalation with extra steps** — it hands the
sandboxed process the one capability the sandbox exists to remove. Once it
validates, it is a privileged broker, which is the option priced below under
a different name.

**Conclusion across all three routes: a sandboxed Manager can only ask
something outside to create sandboxes for it.**

## What a Manager's profile would have to permit — and the honest verdict

Beyond what a Worker gets:

- the **whole project tree** read+write — it manages the repository, not one
  workspace
- `rite` itself: the uv tool path and `~/.local/bin`
- `yoloai`, plus `~/.yoloai/library/sandboxes`, to start Workers
- the engine and its credentials — `~/.claude` and **the keychain** for
  Claude; `~/.config/goose` and `~/.local/share/goose` for Goose, the latter
  now holding the conversation handle
- `tmux` and its socket directory, because that is where Managers live
- git config and push credentials
- network to the ticket backend and to the engine endpoint

⚠ **And the finding that decides the question: the SHIPPED Worker profile
already contains `(allow network*)`.** Unrestricted network, in the profile
rite ships today, alongside read+write on `/tmp` and `/private/tmp` and read
on `/opt/homebrew` and `/Applications`. D-30's "seatbelt has no network
isolation" is not a footnote about a limitation rite might mitigate; it is a
line in the file.

**Is a boundary that permits all of that still worth having?**

**Yes — but a much weaker one than the Worker's, and it has to be sold as
weaker.** It still keeps the operator's home outside the named paths
unreadable: Documents, Desktop, SSH keys, browser profiles, **and other rite
projects on the same machine**. The `(deny file-write*)` rules on git config
and yoloAI internals still hold.

It does not buy network confinement, and it cannot constrain an agent
permitted to run `rite` and `yoloai`, because those tools do whatever the
operator can do.

> **A real reduction in blast radius and an unreal reduction in capability.**

If the goal is "the local tier does not ship unconstrained", this delivers
something. If the goal is "a Manager is contained the way a Worker is", it
does not, and no profile can.

## `HOME`, so it is not rediscovered

⚠ **SUPERSEDED — do not act on this section.** See the banner at the top:
redirecting `HOME` breaks the Claude login.

Goose panics at startup when it cannot write its log, because `HOME` still
points at the host home (B4d). The fix is to point `HOME` at the sandbox's
writable area — B4d used `HOME=$PWD/.goosehome`. One line, and without it
every Goose Manager dies immediately.

## Which backend

**Neither, for a Manager that starts Workers.** If the broker route below is
taken, **seatbelt** — a Manager must drive host tmux, host `rite` and a host
project tree, none of which survive containerisation, and the Manager's
network needs are wide enough that Docker's isolation would be spent on the
one axis seatbelt already concedes.

## Sizing

**The broker shape** — Manager sandboxed; it *requests* a Worker; the
supervisor, on the host, validates and executes `rite sandbox start`:

| | sittings |
|---|---|
| Manager seatbelt profile (yoloAI's `--dir` covers some) | 1–2 |
| `HOME` for the engine inside the sandbox | ½ |
| The broker: request channel, host-side validation, execution | 3–4 |
| Manager prompt: `rite sandbox start` becomes a request | ½ |
| Tests, plus the observation that a sandboxed Manager completes a cycle **and** a Worker actually starts | 1–2 |
| Docs, and the announcement that currently says "runs unsandboxed" | ½ |
| **total** | **6–9** |

The request channel is cheaper than it looks: the per-Manager mailbox already
exists and the supervisor already polls it every two seconds. The expensive
part is validation, for the reason the MCP section gives.

**A smaller shape:** sandbox only Managers whose duties do not include
starting Workers, and leave the rest on the allowlist — **2–3 sittings**. It
makes "is this Manager sandboxed" depend on its duties, which is a
per-Manager capability split and needs a decision rather than a default.

## ⚠ The ½-sitting piece worth doing whatever else is decided

**Make the announcement tell the truth per engine.** A Goose Manager is
currently told it has "no per-command allowlist" and runs unsandboxed. That
sentence is the entire boundary for the local tier, and it should say so
plainly rather than leaving a reader to infer it from an absence.

## What would change this answer

- yoloAI gaining a listen address for `mcp serve`, or mounting the docker
  socket into its containers. Both are upstream, not rite's to do.
- macOS changing `sandbox_apply`'s behaviour for nested profiles. The rule
  measured here is undocumented, so it could move without notice — which is
  itself a reason to keep the measurement rather than the conclusion.
