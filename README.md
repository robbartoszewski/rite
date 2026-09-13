# rite

> **Set it running in the evening, review it in the morning — the rite way.**

`rite` runs many Claude Code sessions against one codebase without them
colliding: each works as a named *worker* and claims the files it is about to
touch, and the second worker to claim the same ones is refused.

The point is where your hours go. Once you have it set up, the work that
needs you concentrates into a couple of hours — settling requirements, making
the calls, reviewing what came back — while the sessions keep going in
between. rite is both halves of that: the CLI that hands out the claims, and
the `CLAUDE.md`, agents and commands it installs, which are what tell your
sessions to claim and who to ask.

- **Claim exclusion is measured, not asserted.** A four-minute soak of six
  concurrent workers: 132,321 grants, zero lost and zero held twice. The same
  harness against the previous lock produced 309 lost updates in 10 seconds.
- **Questions go to the person who owns that area**, named by expertise tag
  in your config, rather than to whichever session is at the keyboard.
- **A secret scan runs on pre-push and in CI**, over full history. Suppressing
  a finding takes a written reason — there is no global off switch.
- **Workers can run sandboxed** (off by default; Seatbelt on macOS, Docker,
  Podman or Tart elsewhere) with a scoped token, not with permissions
  bypassed.
- **50 numbered decisions** in [`SPEC.md`](SPEC.md), each with the question it
  answers and the reasoning — including the ones that did not survive review.

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

## Quickstart

`rite init` asks a few questions, then writes `.rite/`, the `CLAUDE.md` and
`.claude/` your sessions run from, and the secret scan in both places.

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

`rite status` and `rite doctor` are read-only. `rite help` tours the rest.

## Install

**Not on PyPI yet.** Needs `uv` or `pipx`, `git`, and Python 3.11+.

```bash
curl -fsSL https://raw.githubusercontent.com/robbartoszewski/rite/v0.1.0/install.sh | sh
```

*That is a `curl | sh` for a tool that scans your repo for secrets, so two
slower paths — verify the checksum first, or clone and read it — are in
[`docs/install-notes.md`](docs/install-notes.md), with the reasoning. Nothing
phones home.*

## Why you might not want it

**Nothing starts a session for you.** rite sets up the workspace and the
config; starting Claude is your explicit action.

**No gates on your code.** Your sessions run your tests and linters — that is
what rite tells them to do — but rite does not read the results, so there is
no coverage threshold, no accessibility pass, and no opinion on your test
strategy.

**One machine.** Coordinating across machines is designed and not built.

**Claude only, deliberately.** `CLAUDE.md` and `.claude/agents/` are
first-class here rather than behind a provider abstraction, and no other tool
is planned.

**Workers are interchangeable, so there is no capability routing.** Every
worker holds the same project-scoped credentials, so assignment picks
whichever is free rather than whichever *can*.

**Tested on macOS 26.2**, where everything above has been run end to end.
Linux is implemented but unverified on real hardware. Windows is not
attempted.

## Documentation

[`docs/guide.md`](docs/guide.md) — roles, per-worker checkouts, handover,
credentials, adopting an existing repo, uninstalling, the roadmap.
[`SPEC.md`](SPEC.md) — the design document, written for someone building it.

## License

MIT
