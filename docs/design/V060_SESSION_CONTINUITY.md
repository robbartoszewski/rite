# Continuing a Manager between invocations — 0.6.0

**Status: DECIDED by Robert, 2026-09-21.** Recorded, not proposed. The
mechanics at the end are open; the decision is not.

> `rite start X --fresh` = new session
> `rite start X` = continue the last designated Manager session (no
> guessing, it's stored in a file for each manager separately inside
> `.rite/user`)

**Continuation becomes the default, and starting over becomes explicit.**

---

## What this changes

Continuity exists today **within** one `rite start` and is dropped
**between** invocations. Verified by reading the supervisor:

| | today | decided |
|---|---|---|
| cycle 1 of an invocation | fresh context (`resume_from = ""`, `supervise.py`) | continues the designated session |
| cycles 2..n | `--resume <id>`, id from the transcript the supervisor just saw | unchanged in spirit; the id comes from the designation |
| a second `rite start` tomorrow | fresh again, silently | continues where the Manager left off |
| starting over | the only behaviour there is | `--fresh`, typed |

So the work a Manager did yesterday is reachable today by `rite start
planner`, and the user who wants a clean slate says so.

## ⚠ The shape of the answer, which is the part worth keeping

I had raised this as an ambiguity and asked which of several transcripts a
Manager should resume, and whether it should be allowed to pick up a
weeks-old conversation. **Robert did not answer that question. He removed
it.**

The id is *designated* and stored per Manager, so there is nothing to
choose from and no heuristic to tune. Today's `_default_resume_id` reaches
for `latest_session_id(root, since=...)` — the newest transcript touched
after the cycle began — and its own docstring explains the `since` scoping
as a guard against resuming "the newest file on disk, which could be last
week's". That guard exists because the rule guesses. A designation does not
need it.

This is the same move as **"prefer a property the input must satisfy over a
list to reject"** (`bc783b5`), one level up: rather than refine the rule
that picks among candidates, arrange for there to be one candidate. The
anchor rule reached the same shape after two blacklists —
`V060_ANCHOR_LEGIBILITY.md` records that walk — and the name rule reached
it after `:` and `.` were each found by hand.

A rule that chooses is a rule that can choose wrong, and a wrong resume is
silent: the Manager carries on confidently in the wrong conversation, which
looks exactly like carrying on in the right one.

## Where the designation lives

`.rite/user/`, which is where **per-Manager instance state** already lives —
`user_dir()` is documented as "per-user, per-machine runtime state. Never
committed", and `.rite/*` is gitignored with a short named re-include list
that does not include it.

That is consistent with the existing separation, and the separation is the
reason it is right:

| what | where | committed |
|---|---|---|
| a Manager's **profile** — engine, duties, model | `coordination.manager_roles` in `config.yaml` | yes — a team agrees what a `planner` is |
| a Manager's **instance** — pid, session, and now its designated id | `.rite/user/<name>.json` | **no** — a shared config claiming a session id on somebody else's laptop is worse than saying nothing |

One file per Manager, as Robert specified, matching `instance_path()`'s
existing `<name>.json` layout. Two Managers on one machine designate
independently; the same project on two machines designates independently,
which is correct — a provider session id is not portable between them.

## Open mechanics, for whoever builds it

⚠ **These are mechanics, not the decision.** None of them reopens whether
continuation is the default.

1. **When is the designation written?** Candidates: when a cycle starts,
   when one ends cleanly, or on every transcript the supervisor observes.
   They differ for a run that crashes mid-cycle — writing at start
   designates a session that may have died young; writing at clean end
   designates nothing at all when the run is killed, which is the case a
   user most wants to resume from. Worth deciding against the ending
   classifier that already distinguishes `finished`, `quit`, `crashed` and
   `unclear`.

2. **What happens when the file is missing?** First ever run for a Manager
   is the ordinary case and must not be an error. The question is whether a
   *missing* designation is silently equivalent to `--fresh`, or is said out
   loud — "no designated session for `planner`; starting a new one" — which
   is the direction the rest of the release has gone every time.

3. **What happens when it names a session the provider no longer knows?**
   Transcripts are pruned, machines are reimaged, providers expire ids. This
   is the one that must not fail silently: `--resume` against an unknown id
   is what "each cycle began a FRESH context with the ticket half-done and
   no memory of it" already described, and the supervisor's existing refusal
   for a missing transcript ("continuing would start a FRESH context rather
   than carry the work on. Refused rather than silently restarting") is the
   precedent to follow rather than re-derive.

4. **Does `--fresh` re-designate, or skip once?** Both are defensible and
   they differ permanently. If `--fresh` rewrites the designation, the new
   session becomes the thing tomorrow's bare `rite start` continues. If it
   only skips, tomorrow returns to the older conversation and the fresh one
   is orphaned — recoverable only by whatever names sessions. Robert's
   wording says what `--fresh` *starts*, not what it *designates*, so this
   is genuinely unsettled.

5. **Is the designation inspectable and settable?** `rite status` already
   names a Manager's session. Whether a user can read the designated id, and
   whether they can point a Manager at a different one by hand, follows from
   the same argument that made `--manager` explicit as well as defaulted:
   the default serves the common case, and the flag is how somebody drives
   it from outside.
