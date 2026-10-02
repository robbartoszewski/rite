# Why a local Manager cannot push — and whether it could

**Status: analysis, for Robert's question. Nothing is changed by this note.**
RL-11 is not amended here and self-push is not built. 2026-10-02.

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

---

## 0. The answer

⚠ **RL-11's stated reason is not true any more, and the timeline is why.**

> **RL-11** | Pushing | *Local engines commit to a local task branch and stop.
> `integrate` — a Claude session or a person — pushes and opens the PR* |
> **"SPEC §5.1.1 forbids rite's code a push; the harness is rite's code.** It
> also places the terminating check before anything leaves the machine."
> — `RITE_LOCAL_DESIGN.md` line 1153

| event | when |
|---|---|
| RL-11's rationale written | **2026-09-19** |
| PB1 gave `rite deliver` a push | **2026-09-29** (`4242d48`) |

The premise was **true when it was written** and has been **false for ten
days**. RL-11 was never revisited, and `config/managers.py` still enforces it
while repeating the superseded reason.

**So the bar is NOT "rite's deterministic harness must not be the actor that
pushes."** rite's own code is an actor that pushes, by design, in one audited
file.

## 1. What §5.1.1 actually says

The sentence that used to be the whole rule:

> **rite never writes to a remote and never rewrites history.** No `git push`,
> `--force`, `reset --hard`, `rebase`, `filter-branch`, `update-ref` or
> `cherry-pick` appears in any git invocation in the package […] — **with one
> exception, below.**

And the exception, added by PB1:

> **And `rite deliver` writes to a remote, in the same file only, under a
> strategy that permits it.** Under `publish.strategy: push` it pushes the
> collected ticket branch onto the module's branch; under `pull_request` it
> pushes the ticket branch and opens a pull request with `gh pr create
> --draft`. **Only a draft, only on a repository the operator owns, only
> against its default branch** […] — checked BEFORE the push, since a branch
> pushed to someone else's repository has already gone upstream […] Both run
> only after rite's publish gate passed on exactly the commits being sent (a
> gate that could not run is not a pass), with the Worker's own token, and
> never with `--force`.

Enforced by name, not by prose:

```python
# tests/test_blast_radius.py
ALLOWED_GIT = {("push", PUBLISHING)}       # git push in exactly one file
ALLOWED_GH_PR = {PUBLISHING, MERGING}
```

`publishing/deliver.py` does push (≈ lines 325, 343). The real rule is
therefore **"a push happens in exactly one audited place, under enumerated
conditions, proven by a test that enumerates argv"** — not "rite does not
push".

## 2. Who pushes today — there are TWO paths, not one

**Path 1 — rite's own code, host-side, engine-agnostic.**
`rite deliver` collects the Worker's ticket branch into the project's checkout
and, under a permitting strategy, pushes it. Its own docstring is explicit that
this is rite and not a model:

> every strategy starts with the same step, **done here by rite, not by a
> model**: collect each module's `<ticket>` branch into the project's checkout

⚠ **There is no engine check anywhere in `deliver.py`.** Searching it for
`engine`, `claude` or `local:` returns nothing. It does not know, and does not
ask, which engine produced the commits.

**Path 2 — a Manager's own agent loop, with its own token.**

> **a repository-scoped, short-lived token, minted outside the sandbox** […]
> The token **already serves `git push` over HTTPS as well as `gh`**, so SSH
> added no capability — C6/C26, Robert, 2026-09-26,
> `managers/github_access.py`

A sandboxed Manager can therefore run `git push` itself, as a tool action,
using `gh auth git-credential` and `GH_CONFIG_DIR`. ⚠ **Minting that token is
also engine-agnostic** — no `engine` / `is_local` check in
`github_access.py`.

A **Worker** has neither path: §5.1.1 says Workers hold no GitHub token,
"since rite pushes and opens the pull request on the host".

## 3. Robert's question, answered precisely

> *Is a Claude Manager allowed only because its own AGENT loop does the push,
> not rite's code?*

**No — and the question contains a true half worth separating.**

- It is **not** why a Claude Manager is allowed. Path 1 exists, is rite's code,
  and is engine-agnostic. A Claude integrator going through `rite deliver` is
  not pushing with its agent loop at all.
- But a Claude Manager **can** also push with its agent loop (path 2), and
  nothing about that path is Claude-shaped. The credential mechanism is
  environment and git config — `GH_CONFIG_DIR`, `gh auth git-credential`,
  `GIT_CONFIG_*` — none of it specific to one engine.

> *Could a LOCAL agent (Goose) perform the push as its own tool action,
> symmetric with how Claude does it, WITHOUT rite's harness being the pusher?*

**Yes, and it needs no new mechanism.** Goose runs shell commands — measured in
`spikes/OL1-ollama-inside-a-worker-sandbox.md`, where its tool loop ran
`shell: cat E2E.txt`. A `git push` is the same kind of action, and the token
path that makes it work is engine-agnostic.

**So GPU-exclusive self-push does not cross §5.1.1 by either path.** Path 1 is
expressly permitted; path 2 is not rite's code at all, which is the thing
§5.1.1 bounds.

## 4. What it would take, and the one thing that actually changes

**The only code barrier is a single check** (`config/managers.py`, ≈ 712):

```python
if INTEGRATE in held[role.name] and role.is_local:
    problems.append(f"manager {role.name} holds integrate on a {role.engine} engine…")
```

Lifting that is the whole mechanical change. Which is precisely why the
decision should not be made on mechanics.

⚠ **RL-11's SECOND reason survives, and it is the one that matters:** *"It also
places the terminating check before anything leaves the machine."* That is
about ORDER, not about who pushes, and the two paths differ sharply on it:

| | gate on exactly the commits sent? |
|---|---|
| **Path 1** (`rite deliver`) | **Yes, by construction** — "a gate that could not run is not a pass" |
| **Path 2** (agent self-push) | **Only if the `pre-push` hook runs** |

And the hook is not a guarantee. `rite init` installs one and says so honestly
when it cannot, but a global `core.hooksPath` stops git reading it — which is
the state of **this machine** (see `rite-gate-does-not-run-on-push-here`), and
`cli/init/__init__.py` has a comment about exactly that failure: claiming
coverage is *"`✓ Created .git/hooks/pre-push` for a hook git will never read"*.

⚠ **This cuts against Claude Managers too, today.** Path 2 is ungated here for
any engine. A Claude Manager with a push-capable token can already push without
the publish gate on this machine. That is not an argument for letting local
engines do it; it is an argument that path 2 is the weaker path for everyone.

## 5. The recommendation this analysis supports

**If GPU-exclusive self-push is wanted, route it through path 1 and not path
2.** Then:

- nothing is built — `rite deliver` already does it, engine-agnostically;
- the terminating check is preserved by construction, which is RL-11's
  surviving reason;
- §5.1.1's bounds (draft only, operator-owned repo, default branch, no
  `--force`, no merge without `auto_merge` and `--match-head-commit`) apply
  unchanged, because they live in the file that does the pushing;
- the change is one refusal lifted in `config/managers.py`, plus RL-11 rewritten
  to say what the rule now is.

**What a local `integrate` holder would still own is the JUDGEMENT, not the
push** — deciding the work is finished. Nothing in §5.1.1 bounds the quality of
that decision; it bounds the damage of a wrong one. That is the question worth
putting to Robert, and it is a different question from the one RL-11 answered.

⚠ **Not recommended: amending RL-11 as a side effect of the Ollama track.** Its
first reason is stale, its second is sound, and the two have to be separated by
whoever owns the decision rather than by whoever noticed.
