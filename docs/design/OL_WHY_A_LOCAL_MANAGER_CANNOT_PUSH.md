# Can the harness push on a validated verdict? — §5.1.1 and RL-11

**Status: analysis, for Robert's decision. Nothing is changed by this note.**
RL-11 is not amended, §5.1.1 is not amended, self-push is not built. 2026-10-02.

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

**The model being evaluated (Robert's framing).** The LLM posts a VERDICT —
approve/reject, a decision, auditable. rite's deterministic harness then
VALIDATES that verdict (gates pass, the reviewer is a genuinely independent
engine) and performs the push itself. The LLM never runs `git push`; the harness
does, only as the mechanical consequence of a validated verdict.

---

## 0. The answer in one line

⚠ **That model is not merely permissible — it is how rite already works, and
`publishing/requests.py` is where it is written down.** A Claude Manager does not
push. It cannot even commit. It asks, with a two-field request, and rite's
host-side code validates and performs everything including the push. Nothing on
that path is Claude-shaped: neither `requests.py` nor `deliver.py` contains an
engine check.

---

## 1. What §5.1.1 prohibits, verbatim — and why

The prohibition:

> **rite never writes to a remote and never rewrites history.** No `git push`,
> `--force`, `reset --hard`, `rebase`, `filter-branch`, `update-ref` or
> `cherry-pick` appears in any git invocation in the package; the only
> repository-mutating verbs are `clone`, `fetch --prune`, `checkout`,
> `checkout -b` and `merge --ff-only`, and every one of them runs against a
> worker's own clone under `workers/<name>/` — **with one exception, below.**

The stated purpose, in §5.1.1's own opening words:

> **The bounding rule for pointing rite at a live commercial repo.** These are
> **properties, not features**, and `tests/test_blast_radius.py` asserts them.

And the invariant it is protecting, named in the same paragraph:

> **Merging stays a human action, so the blast area is new changes rather than
> project history.**

**So the underlying concern is BLAST RADIUS — bounded, recoverable damage — and
not any of the three candidates.** It is not accountability; it is not "an LLM
must not hold repo write"; it is not "rite must not act without a human". The
evidence is the company `push` keeps in that list: `--force`, `reset --hard`,
`rebase`, `filter-branch`, `update-ref`, `cherry-pick` — every one a
history-rewriting verb. `push` belongs there because a push cannot be undone
from the operator's side, not because pushing is a decision an LLM may not
trigger.

The same axis is named repeatedly elsewhere in SPEC when §5.1.1 is cited:
*"spent quota is the one damage no cleanup reverses (§5.1.1)"*. **The principle
is irreversibility, not authorship.**

⚠ **The one genuine human-in-the-loop clause is about MERGING, not pushing** —
"merging stays a human action" — and even that has a gated exception (§4 below).

## 2. Does §5.1.1 forbid verdict → harness validates → harness pushes?

**No. §5.1.1's own exception describes that shape.**

> **And `rite deliver` writes to a remote, in the same file only, under a
> strategy that permits it.** Under `publish.strategy: push` it pushes the
> collected ticket branch onto the module's branch; under `pull_request` it
> pushes the ticket branch and opens a pull request with `gh pr create
> --draft`. **Only a draft, only on a repository the operator owns, only
> against its default branch** […] — checked BEFORE the push, since a branch
> pushed to someone else's repository has already gone upstream […] **Both run
> only after rite's publish gate passed on exactly the commits being sent (a
> gate that could not run is not a pass)**, with the Worker's own token, and
> **never with `--force`**.

Read the conditions as a list and the shape is unmistakable: a permitting
strategy, a gate that passed on *exactly* these commits, an ownership check
performed *before* the push, a draft only, the default branch only, no force.
**The exception is not "rite may push". It is "rite may push as the mechanical
consequence of checks that passed."** That is the model in the brief, already
written into the specification.

⚠ **So §5.1.1 was aimed at the narrower case: rite performing an irreversible
remote write with no validated decision behind it.** A validated-verdict
pipeline is what its exception already licenses.

## 3. Then OL8 can self-integrate — and what the residual risk actually is

**Yes. An all-Ollama fleet can self-integrate with no human and no Claude under
this model, and §5.1.1 needs no change.**

The mechanism exists and is engine-agnostic:

- `publishing/requests.py` — a Manager asks. **No engine check in the file.**
- `publishing/deliver.py` — rite validates and pushes. **No engine check in the
  file** (searched for `engine`, `claude`, `local:`; nothing).
- `tests/test_blast_radius.py` already sanctions the push by name:
  `ALLOWED_GIT = {("push", PUBLISHING)}`.

**The only barrier is one refusal** (`config/managers.py`, ≈ 712):

```python
if INTEGRATE in held[role.name] and role.is_local:
    problems.append(f"manager {role.name} holds integrate on a {role.engine} engine…")
```

Its rationale is stale. RL-11 says *"SPEC §5.1.1 forbids rite's code a push; the
harness is rite's code"* — written **2026-09-19**, while PB1 gave `rite deliver`
its push on **2026-09-29** (`4242d48`). The premise was true when written and has
been false since. RL-11 was never revisited.

**What changes: RL-11's wording and that one check. Not §5.1.1.**

**The residual risk, stated plainly.** The harness can validate everything
mechanical — gate passed on exactly these commits, reviewer independent,
repository owned, branch default, draft only. ⚠ **It cannot validate whether the
work is actually finished.** §5.1.1 bounds the *damage* of a wrong verdict; it
does not bound the *quality* of one. That is the real question in front of
Robert, and it is a different question from the one RL-11 answered.

Two things make it a smaller risk than it was last week:

- **RL-6 independence is now genuinely enforced.** Until 2026-10-02 the reviewer
  need only differ by engine *string*, so `local:large` could review
  `local:small` on one model. It now compares the MODEL, folding rite's own
  window pin onto its base. A verdict validated against *that* independence rule
  means something; validated against the old one it did not.
- **The blast area is a draft pull request on a repository the operator owns,
  against its default branch, never force-pushed.** A wrong verdict produces a
  draft PR to close.

## 4. How the Claude integrator pushes today — the decisive evidence

**Not the agent. The harness, on the agent's output.** `publishing/requests.py`,
verbatim:

> A sandboxed Manager cannot reach a Worker's sandbox copy, and **D10 measured
> that it cannot even commit, so it ASKS**, the way it asks for a Worker
> (`managers/broker`), and **the supervisor delivers on the host with
> `deliver.deliver`, in process**.

> **The request carries two values and no others:** which Worker and which
> ticket. Everything else (the strategy, the squash, where the work goes) is
> **rite's, from config the Manager's request cannot set.** Unknown keys are
> refused, not ignored.

And `deliver.py` on who does the work:

> every strategy starts with the same step, **done here by rite, not by a
> model**: collect each module's `<ticket>` branch into the project's checkout

**So the Claude integrator posts a two-field decision and rite's deterministic
code does the rest, push included.** That is verdict → harness validates →
harness pushes, shipped. ⚠ **Local does not need a new mechanism. It needs
parity.**

**One honest caveat.** A separate capability exists and should not be confused
with this path: a sandboxed Manager holds a repo-scoped one-hour token that
*"already serves `git push` over HTTPS as well as `gh`"* (C6/C26, Robert,
2026-09-26, `managers/github_access.py`), so an agent *could* push as a tool
action. That is not how integrate works, and it is the weaker path for any
engine, because it is gated only by the `pre-push` hook — which a global
`core.hooksPath` stops git reading, the state of this machine. **If self-integrate
is wanted, it should go through the request path, which gates by construction,
and not through the token.**

## 5. What this note does not do

It does not amend RL-11, amend §5.1.1, or build self-push. RL-11's first reason
is stale and its second — *"it places the terminating check before anything
leaves the machine"* — is sound and is satisfied by the request path. Separating
them belongs to whoever owns the decision, not to whoever noticed.
