# rite local — what it actually looks like

Worked configurations and output for [`RITE_LOCAL_DESIGN.md`](RITE_LOCAL_DESIGN.md).
**Nothing is built; this is what the design says these would be.** It exists
because a design is easiest to judge from the config a person would type and the
output they would read — and writing it found one gap in the spec already
(RL-42: a `local:*` Manager had no declared endpoint, model or agent).

---

## 1. Today's project, after adoption: nothing changes

```yaml
# .rite/config.yaml — a solo developer, one Manager
project:
  name: example-app
managers:
  mbp: {}
```

No `duties:` — a lone Manager holds every duty (RL-34). No `strategies:` — the
project is `solo`, which is what rite does today: the session answers what the
spec settles and asks the person otherwise (RL-21). **Adopting rite local
requires no edit to an existing project.**

---

## 2. Robert's three tiers, one machine

```yaml
managers:
  lead:
    preset: lead                    # claude; decide, board, spec, plan-review, integrate, execute
  planner:
    preset: planner                 # local:large; decompose, step-review, execute
    endpoint: http://localhost:11434/v1
    model: qwen3:32b
    agent: opencode
  exec-1:
    preset: executor                # local:small; execute
    endpoint: http://localhost:11434/v1
    model: qwen3:4b
    agent: opencode
  exec-2:
    preset: executor
    endpoint: http://localhost:11434/v1
    model: qwen3:4b
    agent: opencode
```

Still no `strategies:` block. Resolution (§8.5) gives:

| Manager | `questions` | From |
|---|---|---|
| `lead` | `answer-or-escalate` | profile `solo`, the row for a Manager holding `decide` |
| `planner` | `relay-to-owner` | profile `solo`, the row for a Manager without `decide` |
| `exec-1`, `exec-2` | `relay-to-decomposer` | preset `executor`'s default, which outranks the profile |

Things this configuration already guarantees, without anyone asking for them:

- `lead` is the only Owner-eligible Manager — `decide` + `board`, engine not
  `human` (RL-32).
- `planner` cannot approve its own decompositions: plan review needs a different
  Manager **and** engine, so it is `lead`'s (RL-6). `rite doctor` refuses a
  config where no such Manager exists.
- Neither local Manager can push (RL-11); `integrate` is `lead`'s.
- Neither can escalate to a person directly — their values are relays, so a small
  model's uncertainty reaches a person only through a `decide` holder (§8.9.1).

---

## 3. A team, once SPEC §4 expertise routing exists

```yaml
managers:
  lead:      {preset: lead, expertise: [infrastructure]}
  alice:       {preset: lead, expertise: [billing, compliance]}
  pm:        {preset: pm, engine: human, expertise: [business]}
  planner:   {preset: planner, endpoint: http://gpu.local:8000/v1, model: qwen3:32b, agent: opencode}
  exec-1:    {preset: executor, endpoint: http://localhost:11434/v1, model: qwen3:4b, agent: opencode}
strategies:
  profile: team
  questions:
    allowed: [relay-to-owner, relay-to-decomposer, answer-or-escalate]
  escalation:
    outstanding: 1
    per_hour: 6
```

`team` sends questions to the Owner, who distributes by expertise. `pm` on a
`human` engine answers business questions through the board and **cannot be
Owner** (RL-32) — it holds no session to renew a lease, and `rite doctor` says so
as a note rather than an error, because `lead` and `alice` are eligible.

`escalate-to-user` is absent from `allowed`, so no Manager escalates to its own
person; questions go through the Owner. That is a project's call, not rite's.

**Note this config spans machines** (`gpu.local`), which is Q1: it needs Phase 2's
cross-machine core, and §6.5's scope split buys much less, because the executor
tier's traffic becomes inter-machine by definition.

---

## 4. `rite help strategies` — rendered from the registry

```
$ rite help strategies

Strategies are named policies for situations that recur. Your project uses
profile: solo (no strategies block — this is rite's default).

questions — a question a Manager cannot settle, raised by its own Workers
  answer-or-escalate   answer it if the spec settles it, else ask your person
                       (needs the 'decide' duty)            [solo, for deciders]
  relay-to-owner       send it to the Owner                 [solo (non-deciders), team, org]
  relay-to-decomposer  send it to whoever decomposed the subtask  [executor preset]
  escalate-to-user     ask your person                      [allowed in solo, team]

owner-questions — a question that has reached the Owner
  answer-or-escalate       as above, asking the Owner's person        [solo]
  distribute-by-expertise  route to the best expertise match          [team, org]
                           — NOT AVAILABLE in this build: needs SPEC §4 expertise routing
  escalate-to-user         ask the Owner's person

escalation-exhausted — you want a person and the budget is spent
  park                      hold the question, take other work        [solo, team, org]
  stop                      take no new work until it is answered
  proceed-with-assumption   record the assumption, mark the work provisional
                            (needs 'decide')

... expert-unavailable, task-refused, blocked-work: same shape.

A person is asked at most 1 question at a time, and at most 6 per hour.
Past that, questions wait in `rite status` for you to pull.
```

---

## 5. `rite doctor` — the strategies block

```
strategies
  profile           solo                     (default — no strategies block)
  questions
    lead            answer-or-escalate       from profile solo (holds decide)
    planner         relay-to-owner           from profile solo (no decide)
    exec-1          relay-to-decomposer      from preset executor
    exec-2          relay-to-decomposer      from preset executor
  owner-questions   answer-or-escalate       from profile solo
  expert-unavailable escalate-to-user        from profile solo
  escalation-exhausted park                  from profile solo
  escalation        1 outstanding, 6/hour    default

  note    exec-1, exec-2: relay-to-decomposer applies only to subtask work;
          a question raised outside a decomposition goes to the Owner.
  note    one decide holder (lead): every question that lead cannot settle
          reaches your person. That is expected for a solo project.
  inert   task-refused, blocked-work: no effect until Phase 2 routing exists.
```

Three things this output is doing on purpose: every value names **where it came
from**, so a surprise is traceable rather than mysterious; `inert` says which
settings do nothing yet, so nobody writes one and assumes it works; and the notes
are the static checks of §8.7.2, not warnings invented per run.

---

## 6. `rite status` — questions and the escalation budget

The **team** configuration of §3, seen by Robert, who is `lead` and currently
Owner.

```
questions
  #41  "Should retries be idempotent?"   with alice   2h   routed by expertise: billing
  #44  "Which currency rounds?"          escalated  40m  outstanding — your answer needed
  #45  "Does #44 cover the tax table?"   escalated  38m  aggregated into #44
  #47  "Is ABC-88's intent per-tenant?"  held       12m  waiting for budget — planner parked

escalations to you: 1 outstanding, 1 held, 3 of 6 this hour used
```

`#41` is routed, not escalated: `alice` declared `billing` expertise and holds
`decide`, so it costs Robert nothing. `#45` aggregated into `#44` (RL-38) because
they share a ticket, so they are one interruption rather than two. `#47` arrived
past the ceiling and is **held for him to pull** rather than queued as a prompt
(RL-39); `planner` parked it and took other work (RL-40). **Nothing in this
output can bury `rite stop`** (RL-41).

---

## 7. An end-to-end trace

A ticket through the three-tier configuration of §2, including the parts that go
wrong — which is the half worth reading.

1. **`lead` assigns** `ABC-88` to `planner` (duty routing: `decompose`).
2. **`planner` decomposes** it into four subtasks, each with a scope and a verify
   command, using the ticket's spec-digest slice.
3. **`lead` reviews the plan** (RL-6 — different Manager, different engine). It
   rejects subtask 3: its verify is `pytest -k billing` on a suite with no
   billing test, so the check cannot fail (RL-7). Back to `planner` with reasons.
4. **`planner` re-decomposes**; `lead` approves. Only now is anything released.
5. **`exec-1` takes subtask 1**, runs it, runs its verify, commits to a local
   branch. `rite` runs the verify itself rather than trusting the report.
6. **The endpoint goes down** mid-subtask on `exec-2`'s machine — Ollama was
   restarted. The agent's process dies; the harness releases the claim and
   records the subtask as **not attempted** (RL-47). `exec-2` stops taking work
   and `rite status` says why. **No attempt is consumed**, so this does not count
   toward RL-10's budget and does not look like a model that cannot work. When
   the endpoint returns, the subtask is picked up from the handover record.
7. **`exec-2` takes subtask 2** and hits a question: "does this endpoint need the
   idempotency key?" Its strategy is `relay-to-decomposer` → `planner`.
8. **`planner` clarifies** — it wrote the subtask, and a clarification is
   authorship, not a decision (RL-31). It cannot change the subtask's scope.
9. **Subtask 3 raises a question `planner` cannot clarify**: the ticket's intent
   is ambiguous. `planner` declines to the Owner (S3). `lead` holds
   `answer-or-escalate`, the spec does not settle it, so it escalates to Robert —
   **one outstanding escalation** (RL-37).
10. **Two more questions arrive** while Robert is asleep. Both share `ABC-88`, so
   they aggregate into one waiting item (RL-38) and are **held**, not prompted
   (RL-39). `escalation-exhausted: park` means both Managers mark their tickets
   blocked and take other work; nobody guesses (RL-40).
11. **Robert wakes**, sees one outstanding question and two held, answers them.
12. **All four subtasks pass, and the branches are composed** in decomposition
    order (RL-46). Subtasks 2 and 4 both edited the same lines of one file — a
    conflict. Composition does **not** resolve it: the decomposition returns to
    plan review with the conflicting paths named, because two subtasks that
    conflict should not have been separate. The in-flight work is withdrawn —
    claims released, branches kept and named, open questions closed as
    superseded (RL-33).
13. **`planner` re-slices** those two subtasks; `lead` approves; they are
    executed and composed cleanly.
14. **The parent ticket's verify runs on the composed branch** (RL-8) — and
    fails. The parts were each right; together they miss the ticket. Back to
    **plan review**, not to `planner`, because asking the author of a mistake to
    find it is the failure this design is built against.
15. **Third return to plan review on this ticket** — step 3's rejected verify,
    step 12's conflict, and now this. With the default budget of three (RL-10,
    configurable, default recorded beside the value) that is spent: the
    decomposition is marked **suspect** rather than retried, and `lead` looks at
    the ticket itself rather than at the slicing.
16. **`lead` integrates**: terminating check on the branch as a whole, then push
    and PR. rite assembled the handoff; the session pushed. No local engine has
    pushed anything (RL-11).

**What the trace shows that the decision table does not:** every gate that fires
here fires on a *different* Manager and engine from the one that made the
mistake; the only human interruption in sixteen steps is one aggregated
escalation; and the one infrastructure fault costs nothing but time — it is not
charged to the model, the subtask, or the decomposition's budget.

**It also shows the design admitting defeat once**, at step 15, which is the
point of a budget: the ticket goes back to a person rather than round the loop a
fourth time.
