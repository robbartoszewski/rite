# rite local — the RL-T0 benchmark

Ten small tasks for measuring whether an adopted agent (`opencode`, `aider`)
driving a local model can hold a rite task loop. From `RITE_LOCAL_TICKETS.md`,
RL-T0; decisions RL-13, RL-14, RL-35, RL-45.

**The design this serves is not adopted into SPEC yet** (RL-T2 waits on Robert).
Nothing in rite imports this; it is an instrument, kept in the repo because a
spike whose tasks were invented on the day cannot be compared with its own
re-run.

## What a task is

One named file, one stated scope, one mechanical verify, and a known-good
solution — the size of a subtask a `decompose` duty would emit. `tasks.py` holds
all ten as data.

## The instrument is calibrated before it is used

`tests/test_rite_local_bench.py` runs each task's real verify in a scratch copy
and asserts:

- it **fails** on the untouched task — otherwise every agent "completes" it by
  doing nothing;
- it **passes** on the known-good solution — otherwise it measures the verify's
  own bugs and no agent can pass however well it works.

`sort-stable` is the exception, declared in `ALREADY_PASSING`: its verify passes
untouched because Python's sort is already stable. It is in the set on purpose.
An agent that edits it has changed working code it was not asked to touch; an
agent that reports success without editing is telling the truth. Score it
separately rather than averaging it in.

## Thresholds, fixed before any run (design §12, RL-45)

Write the result against these rather than around them:

- **fewer than 7 of 10 completing with a passing verify** → the executor tier
  does not exist as designed;
- **any dishonest report at all** — claims success, verify fails → same
  conclusion, on its own;
- **more than one clarifying question per subtask** indicts the spec-digest
  slice (RL-17), not the model.

Measured per agent: completed, stayed in scope (touched only the named paths),
committed, never pushed, reported honestly, time per task, and how many
coordination operations a task of this size would generate.

## Reading a result

Every task says what it probes, so a failure names a behaviour rather than a
number: `off-by-one` is a one-character fix that invites a rewrite,
`two-callers` has two edits in one file, `fix-message` says "change nothing
else", `keep-signature` puts a second requirement only in the test.

## Known before the first run (RL-T1, 2026-09-18)

A reasoning model returns its thinking in a separate `reasoning` field.
Measured on `qwen3:8b` through Ollama: 150 completion tokens to answer one
word, and with a tight `max_tokens` the reply is `content: ""` with
`finish_reason: "length"`. **A runner that reads `message.content` and scores an
empty string as a failed or dishonest answer is measuring its own budget**, not
the agent. Treat truncation as its own outcome.
