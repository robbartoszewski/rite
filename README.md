# rite

`rite` is a CLI for running several Claude Code sessions against one codebase
without them colliding. It hands out file claims, keeps a handover snapshot so
state survives a session dying, gives each session its own workspace, and
generates the `CLAUDE.md` every session reads on startup.

It sits underneath Claude Code. It does not call any model, and it never runs
your build.

## Example

Each session claims what it is about to touch. An overlapping claim is refused,
not merged:

```console
$ rite claim backend/src/billing --worker alpha --ticket RW-12
claimed 1 path(s) for alpha

$ rite claim backend/src/billing --worker beta --ticket RW-19
claim failed: path contention
  backend/src/billing overlaps backend/src/billing (held by alpha)
```

And one place to see where every session is:

```console
$ rite status
workers (2):
  alpha: modules=[backend, frontend]
  beta: modules=[backend, frontend] — STALLED

claims (2):
  alpha [RW-12]: backend/src/billing (0m)
  beta [RW-13]: frontend/src/checkout (0m)
```

Real output, trimmed. `rite status` also prints the handover snapshot, the
coordinator pool, this machine's burn rate, and board state. Two of the
coordination-cost counters report `not instrumented yet` rather than a zero.

## Install

**Not on PyPI yet** — that comes once there's been some feedback. Install from
the tagged source. Needs `uv` or `pipx`, `git`, and Python 3.11+.

Three ways, same result. Pick by how much you want to read first:

```bash
# 1. one-liner
curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.1.0/install.sh | sh

# 2. download, check, then run
curl -fsSLO https://raw.githubusercontent.com/robbartoszewski/rite/v0.1.0/install.sh
shasum -a 256 install.sh          # compare against the v0.1.0 release notes
sh install.sh

# 3. clone and read everything
git clone https://github.com/robbartoszewski/rite.git
cd rite && git checkout v0.1.0
less install.sh                   # 161 lines of sh
uv tool install .                 # or: pipx install .
```

*Option 1 is the fast path. If you would rather read a `curl | sh` before
running it — fair, for a tool that scans your repo for secrets — that is what
2 and 3 are for. The reasoning, the telemetry claim and the PyPI name
collision are in [`docs/install-notes.md`](docs/install-notes.md).*

## Quickstart

```bash
cd your-project
rite init                    # questionnaire — role, modules, tech, ticket backend
rite add worker alpha        # a workspace of its own, under workers/alpha/

rite claim backend/src --worker alpha --ticket RW-12   # before touching anything
rite prepare --worker alpha                            # sync that workspace
rite heartbeat --worker alpha --ticket RW-12           # what the watchdog reads
rite release --worker alpha                            # after the PR merges

rite status                  # what is happening now
rite doctor                  # tools, credentials, schedule — exits non-zero on problems
```

`rite init` writes `.rite/` (project state, context, KB, review checklist), a
generated `CLAUDE.md` and `.claude/`, a `pre-push` hook running the same secret
scan as `rite publish check`, and a GitHub Actions workflow running it over full
history. It installs no hook if your git config redirects hooks elsewhere, and
says so rather than leaving one git will never read.

`rite status` and `rite doctor` are read-only. Most commands' `--help` carries
real examples; `rite help` is a short tour of the ones used daily.

## Why you might not want it

- **It only helps if you run more than one session at once.** A single Claude
  Code session on a single repo needs none of this.
- **It does not run your build, tests, or linters.** It detects each module's
  commands, writes them into `CLAUDE.md`, and tells the session to run them —
  so it cannot report a test result, a coverage number, or an accessibility
  pass, and does not pretend to.
- **It does not schedule or supervise the model.** Starting sessions is an
  explicit command; nothing spawns one on your behalf.
- **It is single-machine.** One Owner, its Managers, their Workers, all on one
  box. Cross-machine coordination and failover are designed and not built — if
  the machine running the Owner goes down, a human restarts it.

## Where it runs

Developed and tested on **macOS 26.2**; the Quickstart above has been run end
to end there. **Linux is implemented and unverified on real hardware** — the
scheduler (cron) and credential store (Secret Service) paths are covered by
tests with the OS calls mocked, which is not the same as anyone having run
them. **Windows is not attempted.** Worker sandboxing defaults to Seatbelt and
is macOS-only; Docker, Podman and Tart are the cross-platform backends.

## Documentation

- [`docs/guide.md`](docs/guide.md) — the role model, adopting a repo that
  already has `.rite/`, credentials, what runs unattended, uninstalling, and
  the roadmap.
- [`SPEC.md`](SPEC.md) — the full design document, with decision records and
  failure-mode tables. Written for someone building it, not using it.

## License

MIT
