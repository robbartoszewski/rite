---
description: Bring this session up on the project and start working the board — orient, take the next ready ticket, keep going. The Claude-app half of `rite start`.
---

Start working this project. Do not wait to be told which ticket.

**Why this command exists when `CLAUDE.md` already says all of it.**
`CLAUDE.md` is context: it is loaded before you read anything and it describes
how this project works. It cannot start you. Something still has to be typed,
and without this the thing typed is a paragraph the user has to compose from
memory, differently every time. This is the trigger; `CLAUDE.md` is the
standing behaviour. Neither replaces the other, and this file deliberately
does **not** restate what `CLAUDE.md` says about working the queue — two
copies of one instruction drift, and the copy that drifts is the one nobody
is looking at.

## 1. Orient before deciding anything

```
rite start
```

Read what it prints rather than skimming it. It carries the previous session's
handover snapshot **and its age**, any open blockers, schedule problems,
active claims, whether the scheduler is installed, and whether the queue
watcher is running. A stale snapshot and a fresh one need different things
from you, and the age is the only thing that distinguishes them.

If it says generated files are behind this rite, say so to the user before you
start work — your own instructions being out of date is worth one sentence,
and it is the reason a fix can be released and never reach the project that
reported the bug.

## 2. Read the board before touching it

```
rite status
rite board list --label scheduled
```

`rite status` is what is in flight, claimed, or stalled. The board is what is
waiting. You need both: a ticket that looks ready and is already claimed is
the most common way two sessions end up on one file.

## 3. Take the next ticket

If `$ARGUMENTS` names a ticket, work that one. Otherwise take the next ready
ticket whose paths nothing else holds — **check the claims, not just the
labels.** A ticket refused because its files are held is not a ticket nobody
started; it is a ticket that will refuse you too.

Then follow `## Ticket workflow` in `CLAUDE.md`: claim, work, run that
module's own test and lint commands as written, verify your own fix, review,
PR, merge, release.

## 4. When that one is done, look again

**`## Working the queue` in `CLAUDE.md` governs from here, and it is the
authority — not this file.** Read it rather than this paragraph: it says when
to take the next thing, when to stop, and what to say when you do. It is not
summarised here on purpose. A summary of a standing instruction is a second
copy of it, the two drift, and the one that drifts is the one nobody is
looking at — which is the same defect this project keeps finding in its own
documents.

## What this command does not do

**It does not make the session permanent.** It starts work, and the standing
instructions keep you moving across tickets while you are running. Nothing
restarts you when you stop — not this command, not `rite loop`, which watches
the queue and reports but starts no session (SPEC §9.12). If you hit a limit,
finish the handover rather than the ticket: `rite handover write` and then
release what you hold, so the next session starts from a record instead of
from an interrupted claim.

**It does not start other sessions to clear a backlog.** Whether to start a
Worker is decided by `## Working the queue` in `CLAUDE.md`. Read the rule
there; it is not repeated here, in any wording.
