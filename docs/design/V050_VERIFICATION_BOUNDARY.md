# v0.5.0 — what was verified, where, and what was not

**For the release notes.** A release that states its own verification
boundary is worth more than one implying coverage it does not have.

## The boundary

**GitHub Actions minutes were exhausted for September**, so from 2026-09-19
until roughly 2026-09-30 there is no CI. Everything below was verified
**locally on macOS (darwin 25.2.0), CPython 3.14.3**, and nowhere else.

So the accurate sentence is *"3350 passed on macOS/py3.14"*, not *"the suite
is green"*. The second implies a matrix that did not run.

## Why the difference is not cosmetic, on this codebase specifically

This week produced two defects that **only** a non-macOS run would have
caught, both found because CI went red:

- the git state layer classified a lost ref race as a permanent refusal,
  because **Linux git and macOS git report the same condition with different
  strings** (`incorrect old value` / `reference already exists` versus `stale
  info`). A platform split inside an error-string comparison;
- two doctor tests asserted exit 0 while their fixtures left
  `sandbox.enabled` true, so they silently required yoloAI to be installed —
  true on the developer's machine, false in a clean environment.

Both are the defect class this release is largely about: **absence of a
complaint is not evidence of correctness when nothing is running the check.**
With CI gone, that now applies to the release itself.

## Unconfirmed on Linux

Ranked by how likely the platform actually matters:

| Area | Why it is platform-sensitive |
|---|---|
| `loop/session.py` — tmux lifecycle | Shells out to `tmux`, parses nothing but relies on `has-session` exit codes and a shell redirect in the session command. The new tests use **real tmux** and skip where it is absent — so on a Linux box without tmux they silently do not run |
| `claims` exclusion | Rests on `flock`, whose behaviour differs by filesystem and is a documented no-op inside some sandboxes |
| `sandbox/` capacity counting | Parses `yoloai ls --json`; yoloAI is macOS-first here and the `agent`/`status` vocabulary is undocumented |
| `verdicts.py` — masked exit codes | Pure string analysis, no platform surface. Low risk |
| `claims/suspect.py`, `loop/intents.py` | Filesystem and JSON only. Low risk |
| Everything touching `subprocess` error text | The CAS bug's whole class. Any comparison against a tool's message is a platform split waiting to happen |

## What local verification means here, given it is the only gate

Three rules followed for every change in this release, because with CI gone a
weak local check protects nothing at all:

1. **Exit codes read directly, never through a pipe.** `pytest | tail` reports
   `tail`'s status; that put a commit with a failing test onto main earlier
   this week.
2. **The thing under test is not mocked.** `rite loop start` passed every test
   it had while starting nothing, because those tests mocked tmux. The
   replacements use the real binary.
3. **The gate is invoked correctly.** `python -m rite_ai.gate` with no
   subcommand prints help and exits 0; the command is `... gate check`.

## What to do when CI returns

Re-run the full matrix before anything else ships, and treat the first red as
expected rather than as a regression — roughly eleven days of Linux-invisible
changes will land at once.
