# rite

`rite` coordinates several Claude Code sessions working on one codebase at
once. Each session runs as a named *worker* and claims the files it is about
to touch; the second worker to claim the same ones is refused.

rite is both halves of that: the CLI that hands out the claims, and the
`CLAUDE.md`, agents and slash commands it installs — which are what tell your
sessions to claim in the first place. So the commands below are ones your
sessions run themselves.

**If you run one session at a time you do not need this**, and it coordinates
sessions on one machine only.

## Example

```console
$ rite claim backend/src/billing --worker alpha --ticket RW-12
claimed 1 path(s) for alpha

$ rite claim backend/src/billing --worker beta --ticket RW-19
claim failed: path contention
  backend/src/billing overlaps backend/src/billing (held by alpha)
```

`rite status` shows who holds what, and which workers have gone quiet.

rite also scans your repo for secrets before anything leaves the machine —
the `pre-push` hook and the CI workflow `rite init` writes.

## Quickstart

`rite init` asks a few questions, then writes `.rite/` (project state), the
`CLAUDE.md` and `.claude/` your sessions run from, and the secret scan in two
places: a `pre-push` hook and a GitHub Actions workflow.

```bash
cd your-project
rite init
rite add worker alpha        # a checkout of its own, under workers/alpha/

rite claim backend/src --worker alpha --ticket RW-12   # before touching anything
rite prepare --worker alpha                            # sync that checkout
rite heartbeat --worker alpha --ticket RW-12           # "still alive"
rite release --worker alpha                            # after the PR merges

rite status                  # what is happening now
rite doctor                  # tools and credentials — non-zero on problems
```

`rite status` and `rite doctor` are read-only. Most commands' `--help` carries
real examples; `rite help` is a short tour of the ones used daily.

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
2 and 3 are for. Nothing phones home; the reasoning, and the PyPI name
collision, are in [`docs/install-notes.md`](docs/install-notes.md).*

## Scope

**Nothing starts a session for you.** rite sets up the workspace and the
config; starting Claude is always your explicit action.

**No gates on your code.** Your sessions run your tests and linters — that is
what rite tells them to do — but rite does not read the results, so there is
no coverage threshold, no accessibility pass, and no opinion on your test
strategy.

**Coordinating across machines is designed and not built.**

**Tested on macOS 26.2**, where everything above has been run end to end.
Linux is implemented but unverified on real hardware. Windows is not
attempted.

## Documentation

- [`docs/guide.md`](docs/guide.md) — the roles, per-worker checkouts, handover
  snapshots, credentials, adopting a repo that already has `.rite/`,
  uninstalling, the roadmap.
- [`SPEC.md`](SPEC.md) — the full design document. Written for someone
  building it, not using it.

## License

MIT
