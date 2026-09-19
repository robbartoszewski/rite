# Phase 1 friction audit — ranked for a third-party tester

**Verified at `origin/main` `e5cb048`, 2026-09-13**, from an isolated worktree,
by running each item rather than reading it. Nothing fixed.

**Ranked for:** a stranger on their own machine running an iOS/SwiftUI/TCA app,
a Node backend and three workers, with no knowledge of rite and no one to ask.
Order is how early it bites, then silent before loud, then knowledge required.
Non-critical items went to `FUTURE_IMPROVEMENTS.md` as lines, not here.

## Retired since the first pass — already fixed on `main`

- Swift and Node package-manager detection; `xcodebuild` build/test commands are
  now detected, choosing an available simulator rather than a hardcoded one.
- `rite doctor` shows the resolved commands per module.
- `modules.yaml` refuses unknown keys loudly with did-you-mean, including inside
  `commands:` (`tset` → "did you mean 'test'?").
- The README's sandbox-default claim now matches D-51.

---

## Status at `003b8a9`, 2026-09-18 — every ranked item closed

Verified by running each on current `main`, not by reading. Re-verified
2026-09-18 against `a29ffc3` — all six still hold — and now enforced by
`tests/test_friction_audit_stays_fixed.py`, which walks one generated
project and checks each item. If this table and that file ever disagree,
the file is right.

| # | State |
|---|---|
| 1 | **Fixed.** `install.sh` and the README pin `v0.3.0`; `rite doctor` now prints the build it is running, read from PEP 610 install metadata: `rite 0.3.0 (installed from v0.3.0, commit 05b91d88e83e)` (`7674096`). |
| 2 | **Fixed** (`25f3776`). `brief.yaml`, `config.yaml` and `worker.yml` refuse unknown keys with did-you-mean, `what.notes` and `enriched:` accepted as retired, every writer stops before writing (round-tripped with a typo in each file; all three untouched). `instructions:` in `worker.yml` is refused naming `claude_instructions` rather than aliased — one key, one name. |
| 3 | **Fixed** (`c49005c`). `rite doctor` reports an exported `ANTHROPIC_API_KEY`, what inherits it, and that Claude Code prefers it to the subscription login (docs cited). Information, not a problem; the value is never printed. |
| 4 | **Fixed** (`3cd9057`). The board refusal shows the YAML and names `rite credential set jira` for the `workers` role; `rite init` with JIRA ends by pointing at that command. `init` still writes `projects: {}` — it never asks for a key, which is now said rather than left to be discovered. |
| 5 | **Fixed** (`200d330`). A worker with no heartbeat and no claims reads "not started"; the watchdog exits 0 on it. Holding claims with no heartbeat is still a stall, and an unreadable ledger counts nobody as not started. |
| 6 | **Fixed** (`c67efbc`, then `003b8a9` 2026-09-18). `--yes` names the spec it adopted and how to undo it, the `none` ticket-backend fallback has its reasoning recorded, and every resolved answer is now printed before anything is written, each with its source (typed / `--config` / detected / `--yes` default). The ticket-backend line names the interactive default it did not take. The INTERACTIVE review screen with line-number edits remains parked (see FUTURE_IMPROVEMENTS `init/redesign`) — this is a non-interactive report and pre-empts none of it. |

The `enriched.follow_ups` orphaned reader (§1's table) is gone: no template
reads it, and `rite doctor` reports a brief that still carries the section.

---

## 1. The silently-discarded-input pattern — one root or four?

**Two of the four share a root. The other two are different defects.**

| instance | what it actually is |
|---|---|
| unknown config keys | **no declared schema for the file** |
| `test:` in `modules.yaml` | **no declared schema for the file** — and now solved by `commands:` |
| `enriched.follow_ups` in `brief.yaml` | **not a discard.** A template (`reviewer-decisions.md`) reads it; nothing in `src` ever writes it. An orphaned reader. |
| `--yes` deciding differently | **not a discard.** A decision made and not shown back (see #6). |

**The common root is real, and the fix for it has already been proven.** The
`_unknown_key` mechanism that landed for `modules.yaml` is the root fix. It
simply hasn't been applied to the other three files a person hand-edits:
`config.yaml`, `brief.yaml` and `worker.yml`. Extending that one mechanism is
worth more than any number of per-key patches.

The other two need their own fixes: a writer or removal for `follow_ups`, and a
resolved-answers summary for `--yes`.

---

## 2. Ranked

| # | What bites | Silent? | Recovers alone? |
|---|---|---|---|
| 1 | The one-liner installs a tool with **none** of today's fixes | **yes** | **no** |
| 2 | Typos in `config.yaml` / `brief.yaml` / `worker.yml` vanish | **yes** | **no** |
| 3 | An exported `ANTHROPIC_API_KEY` bills per token | **yes** | **no** |
| 4 | `init` writes `projects: {}`; the one-command fix is never mentioned | loud | only if told |
| 5 | A never-started worker is `STALLED`; watchdog exits 1 on minute one | loud, **false** | slowly |
| 6 | `--yes` adopts a spec silently and diverges once on the board | **yes** | mostly |

### 1. The one-liner installs the pre-fix tool
**Experience.** The README's install line pins `v0.1.0`, and `install.sh` on
`main` still defaults to it. That tag is `7ff25f8`, **14 commits behind
`main`**, and every one of the fixes above is absent from it:

- Swift detection — absent
- the `commands:` key — absent
- unknown-key refusal — absent
- `init`'s sandbox question — absent
- the README correction — absent

So a tester following the README gets `languages: javascript` for their iOS app,
no way to record a test command, and silent typo discards — exactly the tool the
fixes replaced. They cannot know. The tag has also been re-pointed once already.

**Fix.** Point `v0.1.0` at a commit at or after `e5cb048`, or give the guide an
explicit install-from-`main` line, and have `rite doctor` print the installed
commit.

### 2. Typos in three of the four hand-edited files still vanish
**Experience.** Measured at `e5cb048`. `doctor` and `status` print no line about
any of these:

| file | wrote | rite used |
|---|---|---|
| `config.yaml` | `project: BEN` | `projects` = `{}` |
| `config.yaml` | `interval: 5` | `interval_minutes` = 10 |
| `config.yaml` | `window: [...]` | `windows` = `[]` |
| `brief.yaml` | `root_brach: develop` | `root_branch` = `main` |
| `worker.yml` | `instructions: "..."` | dropped — the file key is `claude_instructions`, though the CLI flag is `--instructions` |

The tester has a working project that quietly isn't the one they configured.

**Fix.** Apply `_unknown_key` to those three parsers and to `doctor`; accept
`instructions` as an alias.

### 3. Per-token billing is inherited silently
**Experience.** `ANTHROPIC_API_KEY` appears nowhere in `src/`. A tester who has
it exported runs three workers overnight on per-token billing, and nothing
tells them.

**Already ticketed twice** in `FUTURE_IMPROVEMENTS.md` (sessions inherit it;
`doctor` doesn't warn). Still unbuilt, and still the costliest silent failure a
third party can hit.

**Fix.** The existing `doctor` ticket.

### 4. `projects: {}` — still written, and the fix is never pointed to
**Experience.** `rite init --config … --yes` with JIRA still writes
`projects: {}` (measured). Board commands then refuse, naming the key but not
its shape.

`rite credential set jira` asks for the board key and writes it in one command,
but neither `init` nor the board error mentions it. A `FUTURE_IMPROVEMENTS` note
says `init` now writes all three roles; that did not hold on this path, and a
correction line has been added.

**Fix.** Name `rite credential set jira` in `init`'s closing output and in the
board refusal.

### 5. A worker nobody has started reads as dead
**Experience.** `rite add worker w1`, then `rite status` → `w1 … — STALLED`.
`rite watchdog` exits **1** with *"stalled — no heartbeat ever recorded"*. On
minute one that reads as broken, and a watchdog on a schedule reports it every
cycle until the first heartbeat.

This is distinct from the recorded case of a *working* worker never told to
heartbeat.

**Fix.** A `not started` state that doesn't count as a stall.

### 6. `--yes` decides without saying so
**Experience.** Checked every field against the interactive default.

- **Spec auto-adopt** (`questionnaire.py:116-119`) **matches** the interactive
  default (`confirm(…, default=True)`), but prints nothing — measured, zero lines
  about the spec. The defect is the silence, not the choice.
- **`ticket_backend` is the only real divergence.** Interactive defaults to
  `jira`; `--yes` uses the file's only `fallback="none"`, with no comment or
  rationale. Board commands then refuse loudly, so it is recoverable.

Critical only if the guide uses `--yes`.

**Fix.** Before writing, print every resolved answer and its source (typed /
detected / default / `--yes`). Record why `none` is the `--yes` default where it
is decided.

---

## 3. Deliberate design decisions, not friction

- **Sandbox enabled under `--yes` without yoloAI installed.** Documented in
  `_resolve_sandbox` as *"the honest state rather than a silent No"*; `doctor`
  reports it at exit 1.
- **`modules.yaml` refusing unknown keys.** The intended behaviour, and the
  model for #2.
- **"Record it under `commands:` … don't guess" for undetected commands.**
  Correct; now backed by a supported key.
- **The simulator destination is chosen from the devices actually available**,
  with a message when there are none.
- **A missing gitleaks blocks the push** (exit 3, with install instructions) —
  verified with a real ref on stdin. Still deliberate, and unchanged: rite does
  not reimplement secret detection, so it cannot call a tree safe without it.
  What WAS wrong is fixed in `a2d108c` (2026-09-18): the early return meant
  none of rite's own rules ran either — the built-in hardcoded-path rules
  §11.3 calls never optional, the user's declared patterns, the kb/
  cross-reference — so a machine without gitleaks got no answer rather than a
  partial one, and met those findings only after installing it. They run now
  and are reported under `partial_findings`, which no exit code reads as a
  verdict. Two adversarial review rounds, twelve defects, all pinned.
- **`init` refusing to overwrite an existing `.rite/`.** Correct; the friction is
  only the aborted-`init` case, now a `FUTURE_IMPROVEMENTS` line.
- **`fallback="none"` for `--yes`** — plausibly deliberate (JIRA needs a site
  `--yes` cannot supply), but the reasoning is recorded nowhere.

## Not verified

- ~~No iOS build or test was run inside a sandbox; only the toolchain's
  reachability.~~ **Run 2026-09-18 (`2dbc8c9`)**, seatbelt backend, Xcode 26.6,
  iOS 26.4 simulator, SwiftPM package declaring only `.iOS(.v17)`: with rite's
  generated command, `xcodebuild build` reaches BUILD SUCCEEDED and
  `xcodebuild test` reaches TEST SUCCEEDED inside the sandbox. Raw `xcodebuild`
  fails first — "Could not resolve package dependencies: sandbox-exec:
  sandbox_apply: Operation not permitted" — so the flags in `detect.py` are
  what make it work, and the env-var form of them does not. Still not covered:
  the tart backend, an `.xcodeproj` app with a UI test target, and macro-using
  packages.
- ~~Publish-gate secret *detection* was not tested; the one planted string was
  malformed.~~ **Verified 2026-09-17** against a generated project with
  gitleaks 8.30.1: a committed `ghp_…` token is caught in history (exit 2, value
  masked), stays caught after the file is deleted, and one in a commit message
  is caught by the commit-message relay. Two notes for whoever tests it next:
  a planted string must be HIGH-ENTROPY — `AKIAIOSFODNN7EXAMPLE` (AWS's own doc
  example) and `ghp_` + 36 repeated letters are both invisible to gitleaks, and
  checking gitleaks directly first is what tells a bad fixture from a broken
  gate. And gitleaks rules apply to COMMITTED content only: a secret in a
  tracked file that is merely staged or modified reports clean until it is
  committed (rite's own pattern rules do scan the tracked working tree, but
  they are path/user-declared patterns, not secret regexes). That is consistent
  with a pre-push gate — nothing uncommitted can be pushed — but `rite publish
  check` run by hand is not an "is my working tree clean of secrets" check.
- ~~Multi-line paste overflow was not replayed; the finding is the
  rehearsal's.~~ **Replayed and fixed 2026-09-18 (`d923217`).** Through a pty,
  it was not overflow: pasting three lines at `Project name?` answered the NEXT
  question with line two, which was never shown, and left line three queued —
  input silently ACCEPTED as answers nobody saw. Second defect found in the
  same replay: a paste arriving in chunks >50ms apart recorded only its first
  line and the rest reached the shell after init exited. Both fixed; the pty
  replay is now a test.
