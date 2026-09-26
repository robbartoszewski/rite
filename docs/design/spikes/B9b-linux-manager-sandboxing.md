# B9b — Linux Manager sandboxing: what was measured

Follows `B9-manager-sandboxing.md`, which covers macOS. This is the Linux
half: whether the same boundary can be built, whether it can be walked out
of, and what it costs an operator.

**Everything below was measured, not read from documentation.** Where a fact
is inferred or untested it says so.

## Where it was measured

| Environment | Kernel | Arch | Landlock ABI | Notes |
|---|---|---|---|---|
| Ubuntu 24.04.4 VM (Parallels) | 7.0.0-31-generic | **aarch64** | **8** | unprivileged user `parallels` |
| Docker Desktop container (macOS host) | 6.12.68-linuxkit | aarch64 | **6** | default container, no added privileges |
| **GitHub Actions `ubuntu-latest`** | **6.17.0-1022-azure** | **x86_64** | **7** | CI, unprivileged `runner` |

## Does anything differ between aarch64 and x86_64?

**No.** Run 36203530284 on `ubuntu-latest`, all nine probes passed on x86_64
with **no skips**, across Python 3.11/3.12/3.13:

| Probe | aarch64 (ABI 8 / 6) | x86_64 (ABI 7) |
|---|---|---|
| Boundary holds 4/4 | pass | pass |
| Nesting: identical | pass | pass |
| Nesting: **narrower** applies and takes effect | pass | pass |
| Nesting: **wider** cannot grant back | pass | pass |
| Stack depth ≥ 2 (kernel limit 16) | pass | pass |
| Boundary survives `fork`+`exec`, child narrows | pass | pass |
| Escape 2 (signals) CLOSED by scoping, with control | pass | pass |
| 🔴 Escape 1 (socket) still OPEN | pass (open) | pass (open) |
| Kernel struct sizes (packed, 12/24 bytes) | pass | pass |

The three things that could plausibly have differed, and did not:

* **The syscall numbers.** 444/445/446 are hardcoded. A wrong number returns
  no ABI, and the x86_64 runner reported ABI 7 — so they are right there.
* **The packed struct.** `landlock_path_beneath_attr` is a packed u64 + s32 =
  12 bytes; unpacked it is 16 on every LP64 platform and the kernel reads the
  fd out of the wrong bytes. Asserted directly, and it holds on both.
* **Landlock's semantics** — intersection, the layer limit, `connect(2)` not
  being governed — are generic LSM logic, and behaved identically.

⚠ **The AppArmor posture is the same trap on both.** The runner reports
`apparmor_restrict_unprivileged_userns = 1` and
`unprivileged_userns_clone = 1`, and `unshare --user --map-root-user` is
**refused** — exactly as on the VM. So the bubblewrap route is unavailable on
a stock GitHub runner too, for the same reason, and the knob that *looks*
like the answer says the opposite of the truth on both architectures.
`bwrap` is not installed there at all.

⚠ **Neither environment has a GPU**, so nothing here says anything about
local-model work.

## 1. Can the boundary be built?

**Yes, with Landlock, and without any privilege.** Measured on both
environments: project readable and writable, another project denied, `$HOME`
denied — 4 of 4 checks, as an ordinary user, with no namespace and no kernel
setting changed.

**bubblewrap could not start at all** on the Ubuntu VM as an unprivileged
user: `bwrap: setting up uid map: Permission denied`, matching
`unshare --user --map-root-user` failing with `Operation not permitted`.
The cause is `apparmor_restrict_unprivileged_userns = 1`, Ubuntu's default.

⚠ `/proc/sys/kernel/unprivileged_userns_clone` reads **1** on that machine.
Reading only that knob gives the wrong answer. The AppArmor one decides it.

Both work as root. So the bubblewrap route exists, but it asks the operator
to loosen a kernel security setting — a product cost, not a technical
blocker.

## 2. Nesting — the question that bit macOS

On macOS a sandboxed process could re-enter `sandbox-exec` only with a
**semantically equivalent** profile. A *narrower* one failed too, which
nothing documented. Linux is better, and in the way that matters:

| Case | Measured | |
|---|---|---|
| Identical ruleset re-applied | applied; boundary intact | works |
| **Narrower** ruleset re-applied | applied **and took effect** — subdirectory writable, parent no longer | **works** |
| **Wider** ruleset re-applied | applied, but the outer denial still stood | **cannot grant back** |
| Rulesets stacked by one process | **16** | needs 2 |
| Inherited across `fork`+`exec`, child narrows further | `inherited=True child_narrowed=True` | works |

Landlock rulesets **intersect**: a nested one can only ever remove access.
That is the property confinement rests on, and it was checked rather than
assumed.

So the Manager-boundary-with-a-Worker-boundary-inside-it shape works on
Linux, and survives `exec`. The constraint that made macOS awkward is absent.

## 3. The two escapes macOS shipped with

### Escape 2 — signals: **closed**

`LANDLOCK_SCOPE_SIGNAL` (ABI 6+) refused `kill()` against a process the
boundary did not start; the target survived. The control — the same
restriction with everything scoped *except* signals — killed its own target,
so the result discriminates rather than merely failing.

### Escape 1 — sockets: **open, and Landlock cannot close it**

The macOS fix was a filesystem deny on the socket's path. Landlock's
filesystem rules govern opening files, not `connect(2)`.

Measured in one process, with the same ruleset, as a control:

* `listdir()` of the directory — **denied**
* writing into it — **denied**
* `connect()` to a unix socket **inside that same directory — SUCCEEDED**,
  and a helper outside the boundary acted on the connection

So the deny is real for files and absent for sockets. **The macOS fix for
escape 1 has no Landlock equivalent.**

## 4. Does a container close escape 1?

Tested in the real shape — a **tmux control socket**, which is what rite
actually uses — with two containers: `A` holding the tmux server and a
private directory, `B` standing in for the confined Manager.

| Configuration | Result |
|---|---|
| `B` **without** the socket volume | `/sock` does not exist; nothing to connect to — **closed** |
| `B` **with** the socket volume, same uid as the server | `touch` ran in `A` and the file appeared in `A`'s private directory, which `B` never had mounted — **escape works** |
| `B` with the socket volume, **different** uid | refused: tmux prints `access not allowed` |

**A container closes escape 1 only when the tmux control socket is not
mounted into it** — that is, only when the Manager's tmux server lives
*inside* the boundary. rite today runs the Manager's pane in a tmux server
owned by the operator, outside any container. In that configuration the
socket must be mounted, and the escape is identical to the macOS one: the
command executes in the server's context, with the server's reach.

⚠ **The uid refusal is not a boundary.** It is tmux's own same-uid check, and
it disappeared the moment both sides ran as the same uid — which is exactly
rite's configuration, since the Manager runs as the operator and so does the
operator's tmux. This is the same shape as the warning already recorded in
`managers/session.py`: a safety property resting on something that was not
chosen for safety.

⚠ **`tmux` exits 0 when it cannot reach the server.** Measured: with no
socket present it printed `error creating /sock/t.sock` and still returned
rc=0. Any check that reads tmux's exit code as success is wrong.

### Landlock inside a container

Works, with **no added privileges** — not `--privileged`, no `--cap-add`, no
loosened AppArmor. Measured at ABI 6 on the container kernel, as root (uid 0)
and as an ordinary user (uid 1000): boundary 4/4, signal scoping closes
escape 2, socket escape still open. So the two mechanisms compose, and the
socket hole is a Landlock property rather than an artefact of one machine.

## 5. What it costs an operator

| Mechanism | Privilege needed | Setting to loosen |
|---|---|---|
| Landlock | **none** | none |
| Landlock inside a container | none beyond running a container | none |
| bubblewrap, unprivileged | — | `apparmor_restrict_unprivileged_userns` |
| bubblewrap as root | root | none |

**NOT established:** whether an unprivileged user on that Ubuntu VM can use
Docker at all. The daemon was reachable (server 29.7.2), but only checked as
**root** via `prlctl exec`. Docker normally requires root or membership of the
`docker` group, and `docker` group membership is effectively root-equivalent
on the host — so if the container route is chosen, that cost needs measuring
before it is claimed. The VM's Parallels guest agent wedged before it could
be checked.

## 6. Prerequisite gap found on the way

`rite doctor` does check tmux, but reported only what its absence does to the
loop, ending "`rite loop run` still works in a terminal you leave open" —
which reads as *tmux is optional*. It is not: `managers/session.py` and
`pool/__init__.py` each run every session in a tmux pane and answer "tmux not
found" without one. On a fresh Ubuntu image with no tmux, doctor's only
signal was the loop line and the absence surfaced later as a Manager that
would not start. The wording is fixed; whether a missing tmux should make
doctor **exit non-zero** is a behaviour change and was left undecided.

## Recommendation

**Linux Manager sandboxing is achievable, and the mechanism is Landlock —
not bubblewrap.** It needs no privilege, survives Ubuntu's userns
restriction, nests correctly including narrowing, and closes the signal
escape natively.

**The socket escape cannot be closed by a profile in rite's current
architecture** — but it IS closed by confining the tmux server, which §7
measured. Landlock does not govern `connect(2)`, and a container only
helps if the tmux server moves *inside* the boundary — which is an
architectural change to how rite runs Managers, not a profile setting.

## 7. Option 2 was measured, and it works

Added after the first version of this note recommended waiting. The reason
the socket escape matters is not that `connect(2)` succeeds — it is that the
thing reached is **unconfined**. So the question is whether confining the
tmux server closes it, and that is measurable rather than arguable.

Measured in a container, using rite's own `landlock.wrap` launcher:

| | Result |
|---|---|
| **Control** — tmux server started outside any boundary | a confined client drove it and the file appeared outside: `CONTROL_WROTE` |
| **The server started INSIDE the boundary** | the same client, the same socket, the same command — **nothing appeared outside** |

So escape 1 **is** closable on Linux, today, with the code in `landlock.py`:
start the Manager's tmux server through the same boundary as the Manager. The
client can still reach the socket — Landlock does not stop that and nothing
here claims it does — but what it reaches can no longer write outside the
project, so the reach buys nothing.

### And it stays usable, which is the other half

A boundary that closes the hole by breaking rite would not be an option. So
the same confined server was measured for the things rite actually asks of
it, from OUTSIDE the boundary where the supervisor lives:

| Asked of the confined tmux server | Result |
|---|---|
| `has-session -t =mgr` (liveness) | rc=0 |
| `display-message -p '#{pane_pid}'` | answered — the pid `record_instance` stores |
| `display-message -p '#{pane_dead}'` | answered — how a dead pane is detected |
| the pane writing **in its project** (its real work) | allowed |
| the pane writing **outside** | refused |
| the pane after both | still alive |

So the supervisor's whole liveness machinery keeps working — the socket lives
in the project, and a process outside the boundary is not restricted when it
connects to it. The Manager can do its work and cannot reach past it.

⚠ One measurement here was wrong before it was right, and the wrong version
looked like a finding: the first attempt ran the pane as
`sh -c "while true; do sleep 1; done"`, which does not read stdin, so
`send-keys` went nowhere and the project write reported NO. That reads as
"the boundary denies the Manager its own project". It was the test, not the
boundary.

⚠ **This is a change in where tmux is started, not a new mechanism.** It is
still architectural: `rite loop start` and the coordinator pool start tmux
outside any boundary today, and a Manager's pane lives in that server. The
same reasoning applies to macOS, where the escape is currently closed by
denying the socket path instead.

So the decision is not "can Linux be done" but **which of three**:

1. **Ship Linux with the socket escape documented** as a known limitation —
   the honest version of what macOS shipped unknowingly.
2. **Move the Manager's tmux server inside the boundary** — now MEASURED to
   close it (§7), and the only route that does. Closes it on macOS too.
3. **Wait**, and ship Linux when (2) is built.

The blast radius is not unlimited in any case: what the escape reaches is
whatever the tmux server's own context can reach. On macOS that was the
operator's full permissions. Under option 2 it would be the boundary itself.
