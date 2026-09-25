# Linux sandboxing spike

**One command, on the Linux box, then paste the output back:**

```sh
bash tools/linux_sandbox_spike/spike.sh > spike-output.txt 2>&1
```

Add `SPIKE_LIVE_AUTH=1` in front to also ask `claude` and `goose` to answer
one short prompt — that proves they authenticate, and spends a little quota.
It is off by default.

**Read the top of `spike.sh` before running it.** It states exactly what it
touches: one `mktemp -d` directory it removes on exit, its own tmux server on
a private socket, and its own `sleep` processes. No sudo, no installs, no
network, no writes anywhere else, and it never reads or prints a credential —
only whether one is present.

It uses `python3` — already present on a default Ubuntu, Debian, Fedora or
RHEL — to call the three Landlock syscalls through `ctypes`. That needs no
compiler and no privilege, and Landlock can only restrict the process that
asks for it and that process's children: it cannot reach the shell that
started it, and it cannot grant anything. Where `python3` is absent, every
Landlock check reports `N/A` rather than guessing.

## What it answers

1. **What exists** — kernel, Landlock as an active LSM, `bwrap`, `unshare`,
   whether unprivileged user namespaces are permitted (several distros
   restrict them), yoloAI's backends, Docker, and which engines are installed.
2. **Whether the boundary can be built** — the macOS profile's properties one
   at a time: project readable and writable, another project denied, `$HOME`
   denied, `git`/`rite` still runnable. Asked of **Landlock** and of
   **bubblewrap** separately, because they fail independently: on a host that
   restricts unprivileged user namespaces, bubblewrap cannot start at all
   while Landlock still works.
3. **The two escapes macOS shipped with** — can a confined process drive a
   tmux server living outside the boundary, and can it signal a process it did
   not start. Each has a control that shows *why* the result came out as it
   did. The socket escape is also asked of Landlock directly, because the
   macOS fix for it was a filesystem deny and Landlock's filesystem rules do
   not govern `connect(2)`.

## Reading the output

Every check prints what it **tried**, what **happened**, and one of:

* `PASS` — tried, and it held.
* `FAIL` — tried, and it did not. This is a result, not a broken script.
* `N/A` — could not be tried here, **and it says why**.

⚠ **`N/A` is load-bearing.** If `bwrap` cannot start at all — which is the
case in a default Docker container — every "this must be refused" check would
otherwise pass for the wrong reason: the command failing because no sandbox
was created, not because a boundary refused it. That flaw was in the first
draft of this script and was caught by running it. Each such check now
distinguishes a refusal *inside* a sandbox from a sandbox that never started.

## What it deliberately does not establish

* The **Landlock ABI version** where `python3` is missing — it is read by
  calling `landlock_create_ruleset(NULL, 0, VERSION)`, so without an
  interpreter to call it the kernel release is the only proxy.
* Whether yoloAI's Linux backends actually **confine**; only whether they are
  reported available.
* Anything about a Manager's real workload. No engine is run inside a sandbox
  here — only the boundary's own properties.
