# What a `-p` Manager may DO — the permission mode is rite's parameter now

**Status: FINDING recorded. Robert's decision is PENDING.** Nothing is
built and no launch path was touched.

Second scope created by Finding B's Option 1, and a sibling of
`CREDENTIAL_HANDLING_FOR_UNATTENDED_RUNS.md`: that one is about the token
`claude -p` needs to *authenticate*, this one about the permission it needs
to *act*. They arrive from the same choice and should be read together.

---

## What was measured

Tonight's acceptance run, recorded here because it is the evidence:

**`claude -p` has no tool permissions by default.** The Manager read its
prompt, said it could not write a file without approval, and exited 0. The
supervisor read that as a clean finish — correctly, by its own rules, since
the process ended with status 0 and nobody was attached — and resumed.

**A working loop around a Manager that cannot act.** Every mechanism this
release built behaved exactly as designed: the session started, the prompt
arrived, the ending was classified, the next cycle began. The only thing
absent was the work.

⚠ This is the release's own defect class arriving one level up. Each part
reported success about the thing it measures, and nothing measured whether
the Manager could do anything.

## What settles it

Robert supplied the documentation. Resuming from a terminal **without**
`-p` restores the permission mode the session was in — and that restoration
explicitly **excludes** `-p`, along with the session picker and `/resume`.

The consequence is the finding:

> **There is no path under Option 1 where a User grants permissions once,
> interactively, and the unattended cycles inherit them.**

So **every `-p` invocation must be passed `--permission-mode` or
`--dangerously-skip-permissions` explicitly, every cycle.** It cannot be
inherited from an earlier interactive session, and it cannot be deferred to
the user "setting it up once".

**That makes the permission mode a parameter rite has to own**, in the same
way `--resume <id>` became one: not a thing that happens to be right, but a
value composed at the launch site and passed on every cycle.

## ⚠ Both halves, because neither alone is true

**It is not purely a regression introduced by `-p`.** An *interactive*
Manager running unattended would hit a permission prompt with nobody there
to answer it, and hang. That is equally useless, and this project already
has the phrasing for exactly this shape, from the keychain hang
(`tests/conftest.py`): *"a prompt is not an exception. It is the absence of
an answer"* — no output, no timeout, no failure.

**And it is not "this was always fine".** What `-p` changed is real: the
failure moved from something that quietly did not work into a **decision
somebody must make deliberately**. Before, the mode was invisible and the
run hung; now the mode is absent and the run completes having done nothing.
The second is worse to diagnose and better to fix, because it forces the
question into the open where it can be answered once.

Recorded this way deliberately: read as "new bug" it looks like a reason to
back out `-p`, and read as "always been fine" it looks like nothing needs
deciding. Neither is the case.

## ⚠ The security asymmetry, stated plainly

This is what makes `--dangerously-skip-permissions` a **real** choice rather
than an obvious one:

| | where it runs |
|---|---|
| **Worker** | inside a yoloAI sandbox, with an explicit `--env` list and a stated reachable set (SPEC §5.3) |
| **Manager** | **unsandboxed**, as a plain `tmux new-session` with `cwd` = the project root, on the user's own machine |

A Worker granted broad permissions is bounded by the sandbox. A Manager
granted broad permissions is bounded by nothing but the user's filesystem
permissions. The same flag means two different things in the two places,
and only one of them has a container under it.

That is not an argument against skipping permissions for a Manager — an
unattended Manager that must stop and ask is not unattended. It is an
argument that the choice must be **stated and owned**, not defaulted into by
whoever writes the launch line.

## Recommended, not decided — this is mine, and it is Robert's call

**1. The mode belongs in the per-Manager gitignored configuration**, beside
the other per-Manager attributes in `.rite/user/` (see
`V060_SESSION_CONTINUITY.md`), **not as a rite-wide constant.**

- Different Managers doing different work warrant different trust. A
  `planner` that reads and proposes and a Manager that edits and pushes are
  not the same risk, and today `ManagerRole` carries `duties` precisely
  because they differ.
- A rite-wide constant makes **every user inherit whatever we would have
  picked tonight**, which is the wrong way round for a security decision
  with no sandbox under it.
- It belongs on the **gitignored** side rather than in committed
  `manager_roles`, for the reason that split already exists: a trust level
  is per-person and per-machine. A committed
  `--dangerously-skip-permissions` would impose one operator's risk appetite
  on every clone of the project, which is the same objection as a committed
  session id — worse, because it is not merely wrong elsewhere, it is
  dangerous elsewhere.

**2. rite should STATE the mode it launched with**, rather than leaving it
invisible. Same reasoning as the loud timezone fallback: D-48 was relaxed to
let the zone default, and that was acceptable only because the default is
announced — *"a default that is never stated is the same silent-wrong-clock
D-48 was written against"* (`schedule/__init__.py`). A permission mode is a
larger fact about a run than a timezone, and a user who cannot see which one
they got cannot tell a Manager that chose not to act from one that was not
allowed to.

Both are recorded as open. Robert has been consistent about wanting
fallbacks and defaults loud, so I expect agreement on the second, but
neither is decided here.

## What would show this wrong

- A permission mode that **does** survive into `-p` by some path not in the
  documentation Robert supplied. That would collapse the finding, and it is
  the first thing to try to falsify.
- Evidence that a Manager with no tool permissions is still useful for some
  duty — a reviewer that only reads, say. That would not change the finding
  but would change whether one mode can serve every Manager.
