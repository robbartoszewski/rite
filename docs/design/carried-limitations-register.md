# Carried limitations register — rite

**Purpose:** a limitation that is *not cleared* by a run must be visibly outstanding at the moment that run is called a success. This file exists so that "Phase 1 confirmed working" cannot be read as covering things it never touched.

**Rule:** the Phase 1 gate verdict (`rite-on-rite-phase1-preregistration.md` §5) may not be reported without this register attached. Every entry stays OPEN until a named run clears it, with the evidence cited.

**Created:** 2026-09-13, before the rite-on-rite run. **Status:** all entries OPEN.

---

## Status change — L1 is now under test, not carried

`LIMITATIONS.md` L1 (from the old Bentora setup) was recorded as *"claims answer a PATH question; sessions ask a WORK question"*, and in the previous draft of the pre-registration I listed it as a limitation that rite-on-rite structurally could not reach, because L1 in the Bentora setup fired only across repos.

**That was wrong, and the correction matters.** Reading rite's actual claim code (`src/rite_ai/claims/ledger.py`):

- A claim is `{paths, worker, ticket, timestamp}`. There is no repo or module field.
- `paths_overlap()` compares **bare path strings, lexically**, with no module qualification.
- `ticket` is stored and **never compared** — no `claim.ticket ==` exists anywhere.
- Each worker gets its **own clone** per module (`workspace/manage.py:279`), so there is no shared tree for claims to protect.

So L1 does not survive this run untouched. **It is the central property the run tests** — see §4 of the pre-registration. L1's entry below is therefore `UNDER TEST`, not `CARRIED`, and the gate will report a verdict on it.

| ID | Limitation | Status |
|---|---|---|
| **L1** | Claims answer a path question; sessions ask a work question | **UNDER TEST** in rite-on-rite — verdict required |

---

## D1 — rite cannot manage a module that is itself a rite project

**Found 2026-09-13 while designing the run, before it started. This is a product defect, not an experimental control. It needs a ticket.**

`_find_project_root()` (`cli/main.py:12-18`) walks `[cwd, *cwd.parents]` — **cwd first** — for the first directory named `.rite`, with no depth limit and a silent fallback to cwd. Worker clones sit at `<project>/workers/<name>/<module>/`. And `cli/init/scaffold.py:378-388` makes **every rite-scaffolded project track nine paths under `.rite/`** (`brief.yaml`, `modules.yaml`, `config.yaml`, `review-checklist.md`, `gitleaks.toml`, `gitleaksignore`, `.schema_version`, `context/`, `kb/`), each re-included by a `!` line after `.rite/*`.

So any repo that is or was a rite project carries a tracked `.rite/` into every clone of itself. Clone it as a module and **every rite command run inside it adopts the module's own `.rite/` as the project root.**

**Verified for rite's own repo against `.git/index`:** `.rite/review-checklist.md` and `.rite/gitleaksignore` are tracked; `config.yaml`, `modules.yaml`, `brief.yaml`, `.schema_version` are not. Two tracked files are sufficient — the directory exists in the clone, and that is the only thing the marker tests.

**Impact.** Every worker gets a private `.rite/claims.json`. No shared ledger, no mutual exclusion, no contention ever detected. **It fails silently and presents as a flawless run**: zero collisions, zero refusals, zero force-releases.

**Blast radius beyond this experiment.** Phase 1's own Definition of Done includes *"Bentora can be configured as a rite project (dogfood validation)"* (`IMPLEMENTATION_PLAN.md:958`). A Bentora project whose modules are themselves rite projects hits this identically — so the deferred Bentora run would also have produced a clean, meaningless result.

**Suggested fix, two parts:**
1. **Harden the marker** — require a project-identifying file (`.rite/modules.yaml`) rather than a bare `.rite/` directory. Fixes rite-on-rite today; does not fix a module that is a fully scaffolded project.
2. **Add an explicit override** — no `RITE_PROJECT_ROOT` env var or `--project-root` flag exists. Adding one fixes the general case *and* fixes test isolation, which currently depends on 142 hand-written `monkeypatch.chdir(tmp_path)` calls with no autouse fixture in `conftest.py`.

**Ready-made regression test:** clone rite into `workers/w/rite/` under a project root, `cd` there, assert the resolved project root is the outer project. Mirrors the existing `TestTheSuiteDoesNotWriteIntoThisRepository` idiom — assert the property, do not trust the fixture.

⚠ **UPDATED 2026-09-25: both suggested fixes are in the code.** The marker is a project-identifying file rather than the bare `.rite/` directory, and `RITE_PROJECT_ROOT` overrides the search (`cli/main.py`, `_find_project_root`, `PROJECT_ROOT_ENV`). So the sentence above saying no override exists is stale. **The entry stays OPEN** under this register's rule, until a named run clears it with evidence.

**Status:** OPEN — ticket required. Worked around for the run by a single sanctioned `PATH` wrapper (pre-registration §6.3). **If the fix lands before the run starts, prefer it and drop the wrapper.**

---

## Entries carried into the Bentora run — OPEN

### C1 — Cross-repo path aliasing is untested
rite-on-rite is one repo. rite's claim namespace has no module dimension at all, so the *multi-repo* form of the aliasing problem — the one the Bentora ledger documented — is not exercised by a single-repo run. R1 (§4) tests the namespace within one repo; it does not test what happens when eight repos share a flat string namespace.
**Clears only by:** the Bentora run, multi-repo, with R1's canonicalisation check applied across repos.

### C2 — The board / tracker half of Phase 1 is untested
`.docs/PHASE2_TICKETS.md` records: *"Not on the board yet — no JIRA site is configured on this machine; the recorded safe write target is `RT`, never `SCRUM` or `RW`."* Phase 1's own Definition of Done requires `rite board create/move/list/query/label/link` working against a real JIRA board including cross-project blocked-by links (RW → SCRUM). If the dogfood runs without a real board, that entire DoD clause is unexercised.
**Clears only by:** a run against a real JIRA site, or explicit descoping of the board from Phase 1.

### C3 — Does it work for someone who did not write it
The users are the authors. Every gap is filled by the person who designed the thing that has the gap.
**Clears only by:** a run with a participant who has not read the source.

### C4 — Multi-machine, multi-operator, scale
One machine, one operator. Phase 2 exists precisely to add leader election, state sync and owner failover; none of it is built (`src/rite_ai/coordination/`, `owner/`, `manager/`, `routing/` do not exist).
**Clears only by:** Phase 2 completion plus its own validation run.

### C5 — Crash, restart, long horizon
Days, not weeks. No deliberate crash or restart test is in scope.

### C6 — Installation and onboarding
The orchestrator installs rite by hand from a tag. `rite init` on a fresh machine by a fresh operator is untested.

### C7 — A human Manager
The Manager is a Claude session. It tolerates ambiguity a person would not and gives up in different places. Its question count is neither an upper nor a lower bound on a human's.

### C8 — `flock` may be a no-op, and exclusion silently vanishes
`claims/ledger.py:63-94` warns to stderr and proceeds if `flock` is unavailable on the filesystem, in which case *"two workers can be granted the same path here and both told 'claimed'"*. Any run whose state root sits on such a filesystem tests nothing about exclusion.
**Mitigation in this run:** record the state root's filesystem type before starting (§6 of the pre-registration). This does not clear the entry; it only establishes whether the run was affected.

### C9 — Claim identity is unverified
`rite claim --worker <anything>` succeeds for an unregistered name, with a warning and never a refusal (`cli/main.py:556-583`). The claim namespace is not bound to the workspace namespace, so the ledger's account of *who* holds a path is advisory.

### C10 — Refused claims are not durably recorded
Contention increments a bare integer in `.rite/coordination-cost.json` with no worker, path, ticket or timestamp, and only from the CLI path (`coordination_cost/__init__.py:9-14, 42-47`). rite cannot, on its own, tell you what contended or when.
**Mitigation in this run:** capture stderr of every `rite claim` invocation. This is scaffolding around rite, not a property of rite, and the entry stays open.

### C11 — Placeholder defaults are unmeasured
`SPEC.md:1936-1948` states plainly that `heartbeat.interval_minutes: 10`, `stall_threshold: 3`, `watchdog.interval_minutes: 5`, `pool.warn_threshold: 0.5` and `sandbox.max_concurrent_workers: 5` are unjustified — *"a user tuning them is not departing from a recommendation, because there isn't one."* A run that passes at these defaults has not validated them.
**Partially clears by:** this run, which is instructed to emit measured inter-activity gaps as evidence toward real defaults (§5, W5). Measurement ≠ validation; the entry narrows rather than closes.

---

## How to use this file

When the Phase 1 gate is reported, paste this table beside the verdict:

```
PHASE 1 GATE: ____        Register version: ____ (commit)
OPEN: D1 C1 C2 C3 C4 C5 C6 C7 C8 C9 C10 C11
D1: fixed before run / worked around by PATH wrapper   ← which one changes what was tested
L1: verdict ____ (under test this run)
```

A gate reported without the OPEN list is a gate that will be over-read.
