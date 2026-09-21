# Continuing a Manager between invocations — 0.6.0

**Status: DECIDED by Robert, 2026-09-21.** Recorded, not proposed. The
mechanics at the end are open; the decision is not.

> `rite start X --fresh` = new session
> `rite start X` = continue the last designated Manager session (no
> guessing, it's stored in a file for each manager separately inside
> `.rite/user`)

**Continuation becomes the default, and starting over becomes explicit.**

**Addendum, decided 2026-09-21:**

> if designated manager session doesn't exist, `rite start X` is
> effectively `rite start X --fresh`

**There is no error state here.** Nothing to continue is not a problem for
the user to clear before they may work — it is a reason to do the obvious
useful thing. That settles two of the mechanics below; see "Settled by the
addendum".

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

## Settled by the addendum: nothing to continue means start fresh

Two cases that were open collapse into one rule, and neither is an error:

| state | behaviour |
|---|---|
| **no designation at all** — first ever run for this Manager, or the file was pruned with the rest of `.rite/user/` | start fresh |
| **a designation naming a session the provider no longer knows** — transcripts pruned, machine reimaged, id expired | start fresh |

Robert's sentence names the second directly. The first follows by the same
principle and was never a candidate for an error anyway: a first run has
nothing to continue by definition, and a Manager that refused to start until
its user cleared a missing file would be unusable on day one.

⚠ **This settles the INVOCATION-START case and should not be read as
changing the mid-run refusal.** They are different moments. When a cycle
ends mid-run and no transcript is found, the supervisor today refuses —
*"continuing would start a FRESH context rather than carry the work on.
Refused rather than silently restarting"* — and that refusal protects work
already in flight: a ticket is half-done and a fresh context would redo or
abandon it. At invocation start there is no such work to lose, so refusing
would cost the user a command and protect nothing. A builder should keep
both behaviours rather than making one of them consistent with the other.

### Recommended, not decided: say so when it happens

**This is mine, not Robert's, and it is his call.**

A run that starts fresh *because the designated session was gone* should
say so. Silence makes it identical, from the user's side, to the
continuation they asked for — so a user who believes they are carrying on
yesterday's conversation gets a Manager with no memory of it and nothing
anywhere explaining why. That is the confident-wrong-answer shape this
release spent its length removing: `pid=0`, `rite status` calling a live
session gone, `liveness` answering `known=True` about a session it could not
address.

**The precedent is the timezone, and it is exact.** `resolve_zone` returns
three distinguishable answers, and `describe()` states which one applies:

    schedule in Europe/Warsaw (from config)
    schedule in Europe/Warsaw (machine local)
    schedule in Europe/Warsaw (machine local — schedule.timezone 'Not/AZone'
      is not a known timezone and was ignored)

D-48 was relaxed to let the zone default, and the relaxation was only
acceptable because the default is announced — "a default that is never
stated is the same silent-wrong-clock D-48 was written against". The same
sentence applies here with one word changed. It also gives the shape: an
unset designation and a designation that was *rejected* should not print the
same line, because "you never had one" and "the one you had is gone" are
different facts to the person reading.

Robert has been consistent about wanting fallbacks loud, so I expect
agreement — but it is recorded as open rather than assumed.

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

2. **Does `--fresh` re-designate, or skip once?** Both are defensible and
   they differ permanently. If `--fresh` rewrites the designation, the new
   session becomes the thing tomorrow's bare `rite start` continues. If it
   only skips, tomorrow returns to the older conversation and the fresh one
   is orphaned — recoverable only by whatever names sessions. Robert's
   wording says what `--fresh` *starts*, not what it *designates*, so this
   is genuinely unsettled.

3. **Is the designation inspectable and settable?** `rite status` already
   names a Manager's session. Whether a user can read the designated id, and
   whether they can point a Manager at a different one by hand, follows from
   the same argument that made `--manager` explicit as well as defaulted:
   the default serves the common case, and the flag is how somebody drives
   it from outside.

---

## Carried to 0.6.0 from the round-one review of the designation

Found by attacking the failure and boundary cases; one was fixed in
v0.5.1 (a malformed designation wedged `rite start` with a traceback) and
these were not.

1. **Nothing checks that a designated id belongs to THIS project or THIS
   Manager** — `supervise` reads the id and passes it to `--resume`
   unexamined, so a file naming another Manager's session would continue
   that conversation and the announcement would correctly say "continuing".
   The means already exist and whoever picks this up should not start from
   scratch: `project_transcript_dir(root)` enumerates this project's
   transcripts and `_stated_session_id` reads the id out of each, so
   membership is answerable without a new mechanism. ⚠ **Honest limit: a
   cross-project resume was NOT demonstrated** — the finding is the missing
   check, not an observed wrong resume, and it needs a hand-edit or a bug
   to reach.

2. **The two files under `.rite/user/` are separated safely by accident.**
   `running_instances()` globs `*.json`, which `<name>.designated.json`
   matches; it is skipped only because `read_instance` validates the stem
   through `name_problem(..., must_be_a_tmux_target=True)`, which rejects
   the `.` in `planner.designated`. Verified working today. The separation
   of the two files is deliberate and well-argued; its safety rests on an
   unrelated tmux-naming rule about dots, so a non-`.json` suffix or an
   explicit skip in the glob would make it structural rather than
   incidental.

3. **"Designated whatever the ending" has one unstated exception.** The
   Ctrl-C write is guarded by `if cycles:`, so an interrupt arriving before
   the first cycle is appended designates nothing. That looks correct —
   there is no conversation to come back to yet — but it is the one path
   where the stated rule does not hold, and an unstated exception is how the
   next person is surprised.
